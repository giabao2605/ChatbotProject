"""Tang B moi — Dialogue State Tracking (DST) co CAU TRUC.

Thay cho co che heuristic ke thua `current_part_ids` (de dinh ma cu sai). Module
nay nho:
  - `pending_candidates`: BO UNG VIEN vua hien thi trong bang chon (disambiguation)
  - `active_doc_refs`  : tai lieu/ma dang duoc ban toi
  - `active_topic`, `last_intent`

va GIAI QUYET tham chieu cua nguoi dung MOT CACH TAT DINH (khong ton LLM):
  - chon theo SO THU TU  : "1", "so 2", "cai thu ba", "#2"
  - chon theo MA/MODEL   : "9.3.03844", "model7"
  - chon theo TEN        : "ban ve truc vit M10x1.5"

MODULE THUAN: chi phu thuoc `re` + helper chuan hoa cua entity_resolver. KHONG
import qdrant / LLM -> unit-test import duoc, khong tai model.

Bat/tat bang bien moi truong ENABLE_CONV_STATE (mac dinh TAT de an toan).
"""
import os
import re

from mech_chatbot.rag.entity_resolver import _norm_text, _strip_accents, _norm_dim, has_explicit_code

FLAG_ENV = "ENABLE_CONV_STATE"

# Token qua chung, khong dung de khop TEN ung vien.
_GENERIC_TOK = {
    "ban", "banve", "ve", "tai", "lieu", "tailieu", "thuat", "ky", "kythuat",
    "version", "phien", "phienban", "model", "cua", "cho", "cai", "file",
    "document", "tra", "cuu", "xem", "chon", "lay", "so", "muc", "the",
}

# Tu chi so thu tu (tieng Viet, da bo dau).
_ORDINAL_WORDS = {
    "nhat": 1, "dau": 1, "dau tien": 1, "thu nhat": 1,
    "hai": 2, "nhi": 2, "thu hai": 2,
    "ba": 3, "thu ba": 3,
    "bon": 4, "tu": 4, "thu tu": 4,
    "nam": 5, "thu nam": 5,
}

_TRIGGER = r"(?:so|stt|muc|thu|dong|hang|option|phuong an|lua chon|chon|cai)"


def is_enabled():
    """True neu tinh nang Conversation-State duoc bat qua env."""
    raw = os.getenv(FLAG_ENV)
    if raw is None:
        return False
    return str(raw).strip().lower() in ("1", "true", "yes", "on", "y")


# ---------------------------------------------------------------------------
# Candidate helpers
# ---------------------------------------------------------------------------
_CAND_FIELDS = (
    "base_code", "variant_code", "version_no", "product_name",
    "dimensions", "materials", "file_goc", "key", "score", "num_docs",
)


def public_candidates(candidates):
    """Chuan hoa list candidate ve dang JSON-safe + gan index (1-based).

    Dung khi LUU bo ung vien vao ConversationContext de gui ve UI.
    """
    out = []
    for i, c in enumerate(candidates or [], start=1):
        c = c or {}
        item = {k: c.get(k) for k in _CAND_FIELDS if k in c}
        item["index"] = i
        out.append(item)
    return out


def describe_candidate(cand):
    """Sinh mo ta chinh xac (ten + kich thuoc + vat lieu) cho query-rewrite
    khi ung vien KHONG co base_code de neo bang ma."""
    cand = cand or {}
    parts = []
    for k in ("product_name", "dimensions", "materials"):
        v = str(cand.get(k) or "").strip()
        if v:
            parts.append(v)
    return " ".join(parts).strip()


def _cand_codes(cand):
    """Tap ma dinh danh (da chuan hoa, bo khoang trang) cua 1 ung vien."""
    codes = set()
    for k in ("base_code", "variant_code", "key"):
        v = str(cand.get(k) or "").strip()
        if not v or v.lower() == "default":
            continue
        nv = re.sub(r"\s+", "", _norm_text(v))
        if len(nv) >= 3:
            codes.add(nv)
    return codes


def _name_tokens(name):
    toks = []
    for t in _norm_text(name).split(" "):
        if len(t) >= 3 and t not in _GENERIC_TOK:
            toks.append(t)
    return toks


# ---------------------------------------------------------------------------
# Ordinal parsing
# ---------------------------------------------------------------------------
def _parse_ordinal(question, n):
    """Tra ve index (1-based) neu cau hoi chon theo so thu tu, nguoc lai None."""
    if n <= 0:
        return None
    q = str(question or "")
    qn = _norm_text(q)

    # 1) '#2'
    m = re.search(r"#\s*(\d{1,2})", q)
    if m:
        idx = int(m.group(1))
        if 1 <= idx <= n:
            return idx

    # 2) 'so 2', 'muc 3', 'chon 1', 'cai so 2'
    m = re.search(_TRIGGER + r"\s*#?\s*(\d{1,2})\b", qn)
    if m:
        idx = int(m.group(1))
        if 1 <= idx <= n:
            return idx

    # 3) chi la mot con so ('2'), hoac cau rat ngan co dung 1 con so
    toks = qn.split()
    digit_toks = [t for t in toks if t.isdigit()]
    if len(digit_toks) == 1 and len(toks) <= 3:
        idx = int(digit_toks[0])
        if 1 <= idx <= n:
            return idx

    # 4) tu chi thu tu, chi khi co trigger dung truoc HOAC cau rat ngan
    m = re.search(_TRIGGER + r"\s+(nhat|dau tien|dau|thu nhat|hai|nhi|thu hai|ba|thu ba|bon|tu|thu tu|nam|thu nam)\b", qn)
    if not m and len(toks) <= 4:
        for word in sorted(_ORDINAL_WORDS, key=len, reverse=True):
            if re.search(r"\b" + re.escape(word) + r"\b", qn):
                m = True
                _wd = word
                break
        else:
            _wd = None
    else:
        _wd = m.group(1) if m else None
    if _wd:
        idx = _ORDINAL_WORDS.get(_wd)
        if idx and 1 <= idx <= n:
            return idx
    return None


# ---------------------------------------------------------------------------
# Core: resolve_selection
# ---------------------------------------------------------------------------
def resolve_selection(question, pending_candidates):
    """Xac dinh nguoi dung dang chon ung vien nao trong bang da hien.

    Tra ve dict:
      {
        "matched": bool,
        "candidate": <candidate dict> | None,
        "match_type": "ordinal" | "code" | "name" | None,
        "score": float,
      }
    Uu tien: ordinal -> code -> name. Neu ten khong tach duoc ro rang -> khong khop
    (de luong disambiguation binh thuong hoi lai).
    """
    pending = list(pending_candidates or [])
    none = {"matched": False, "candidate": None, "match_type": None, "score": 0.0}
    if not pending:
        return none

    q = str(question or "")
    qn = _norm_text(q)
    qn_ns = re.sub(r"\s+", "", qn)

    # 1) ORDINAL
    idx = _parse_ordinal(q, len(pending))
    if idx is not None:
        return {"matched": True, "candidate": pending[idx - 1], "match_type": "ordinal", "score": 1.0}

    # 2) CODE / MODEL
    for c in pending:
        for code in _cand_codes(c):
            if code in qn_ns:
                return {"matched": True, "candidate": c, "match_type": "code", "score": 1.0}

    # 3) NAME (+ dimensions)
    # Chi coi la "chon theo ten" khi bang chung DU MANH -> tranh khop nham do dong
    # am sau khi bo dau (vd "bang LUONG" vs "chat LUONG" deu thanh "luong").
    scored = []
    for c in pending:
        toks = _name_tokens(c.get("product_name"))
        name_hits = sum(1 for t in toks if t in qn)
        dim = re.sub(r"\s+", "", _norm_dim(c.get("dimensions")))
        dim_hit = bool(dim and len(dim) >= 4 and dim in qn_ns)
        s = float(name_hits) + (3.0 if dim_hit else 0.0)
        scored.append((s, name_hits, dim_hit, len(toks), c))
    scored.sort(key=lambda x: x[0], reverse=True)
    top_s, top_hits, top_dim, top_ntok, top_c = scored[0]
    second_s = scored[1][0] if len(scored) > 1 else 0.0
    margin = top_s - second_s
    # (a) trung kich thuoc dac trung; (b) trung >= 2 token ten & bo xa ung vien 2;
    # (c) go gan het ten (>=3 token & phu kin ten ung vien). KHONG chap nhan khop
    # 1 token chung chung (nguon goc bug "luong").
    accept = (
        (top_dim and margin >= 2.0)
        or (top_hits >= 2 and margin >= 2.0)
        or (top_hits >= 3 and top_hits >= top_ntok and top_s > second_s)
    )
    if accept:
        return {"matched": True, "candidate": top_c, "match_type": "name", "score": top_s}

    return none


# ---------------------------------------------------------------------------
# ConversationContext
# ---------------------------------------------------------------------------
class ConversationContext:
    """State hoi thoai co cau truc (JSON-safe qua to_dict/from_dict)."""

    __slots__ = ("active_doc_refs", "pending_candidates", "active_topic", "last_intent")

    def __init__(self, active_doc_refs=None, pending_candidates=None,
                 active_topic=None, last_intent="new"):
        self.active_doc_refs = list(active_doc_refs or [])
        self.pending_candidates = list(pending_candidates or [])
        self.active_topic = active_topic
        self.last_intent = last_intent or "new"

    @classmethod
    def from_dict(cls, d):
        d = d or {}
        return cls(
            active_doc_refs=d.get("active_doc_refs"),
            pending_candidates=d.get("pending_candidates"),
            active_topic=d.get("active_topic"),
            last_intent=d.get("last_intent", "new"),
        )

    def to_dict(self):
        return {
            "active_doc_refs": list(self.active_doc_refs),
            "pending_candidates": list(self.pending_candidates),
            "active_topic": self.active_topic,
            "last_intent": self.last_intent,
        }

    def set_pending(self, candidates):
        self.pending_candidates = public_candidates(candidates)
        self.last_intent = "await_selection"

    def clear_pending(self):
        self.pending_candidates = []

    def note_active(self, part_ids):
        if part_ids:
            self.active_doc_refs = list(part_ids)


# ---------------------------------------------------------------------------
# KH-2: Neo tai lieu tong quat + nhan dien cau tiep dien (continuation)
# ---------------------------------------------------------------------------

# Tu khang dinh / dong y mo dau cho cau tiep dien (da bo dau).
_AFFIRM = {
    "ok", "oke", "okie", "okla", "okay", "k",
    "u", "um", "uh", "uhm", "vang", "da",
    "co", "duoc", "dc", "roi", "rui", "yes", "y", "yeah", "uhu",
}

# Cum tu bao hieu nguoi dung muon LAM TIEP tren ngu canh truoc.
_CONT_MARKERS = [
    "trich", "liet ke", "liet ra", "chi tiet", "cu the", "day du",
    "noi ro", "noi them", "giai thich", "lam ro", "cho xem", "xem tiep",
    "tiep tuc", "tiep di", "the con", "con lai", "con nua", "ra di",
    "lam di", "dua ra", "them thong tin", "ro hon", "phan con lai",
    "phan tiep", "nhu tren", "vua roi", "vua noi",
    "no ", "cai do", "cai nay", "cai kia", "cai ay", "ban truoc",
    "ban do", "phien ban truoc", "con ", "thi sao", "the sao",
]


def is_continuation(question):
    """True neu cau hoi kha nang cao la 'lam tiep' tren tai lieu dang hoi.

    Dung de quyet dinh co neo lai active_doc_refs hay khong khi cau khong kem
    ma/tai lieu moi. Cham theo: tu khang dinh mo dau, cum tiep dien, hoac cau
    rat ngan (<= 4 tu).
    """
    qn = _norm_text(str(question or ""))
    if not qn:
        return False
    toks = qn.split()
    if toks and toks[0] in _AFFIRM:
        return True
    if any(m in qn for m in _CONT_MARKERS):
        return True
    # Bo quy tac "cau <=4 tu -> tiep dien": gay NEO NHAM khi cau ngan lai la CHU DE
    # MOI (vd "bang luong thang 06"). Cau tiep dien khong tu khoa se do tang LLM
    # (context_action == continue) xu ly ben service.py.
    return False


def _doc_ref_code(doc):
    """Lay ma dinh danh tai lieu (base_code, fallback file_goc) tu 1 doc."""
    md = getattr(doc, "metadata", {}) or {}
    base = str(md.get("base_code") or "").strip()
    if base:
        return base
    fg = md.get("file_goc")
    if fg:
        return _strip_accents(str(fg)).strip()
    return ""


def dominant_doc_refs(docs, top_n=8):
    """Tra ve [ma tai lieu troi nhat] trong cac doc vua dung de tra loi.

    Dung lam mo neo cho cau tiep dien o luot sau (KH-2, sua V4). Toi da 1 ma
    (xuat hien nhieu nhat trong top_n doc dau); rong neu khong doc nao co ma.
    """
    if not docs:
        return []
    counts = {}
    order = []
    for doc in docs[:top_n]:
        code = _doc_ref_code(doc)
        if not code:
            continue
        if code not in counts:
            counts[code] = 0
            order.append(code)
        counts[code] += 1
    if not counts:
        return []
    best = max(order, key=lambda c: (counts[c], -order.index(c)))
    return [best]


# ---------------------------------------------------------------------------
# KH-3: History window + rolling summary helpers
# ---------------------------------------------------------------------------
HISTORY_SUMMARY_FLAG_ENV = "ENABLE_HISTORY_SUMMARY"
# So message nguyen van giu o cuoi (cua so verbatim). 12 msg ~ 6 luot.
HISTORY_WINDOW_MSGS = 12
# Chi tom tat lai khi overflow moi vuot phan da tom tat >= buoc nay (chan cost).
SUMMARY_REFRESH_STEP = 2


def history_summary_enabled():
    """True neu bat tom tat hoi thoai luy tien qua env ENABLE_HISTORY_SUMMARY."""
    raw = os.getenv(HISTORY_SUMMARY_FLAG_ENV)
    if raw is None:
        return False
    return str(raw).strip().lower() in {"1", "true", "yes", "y", "on"}


def split_history_for_summary(chat_history, window_msgs=HISTORY_WINDOW_MSGS):
    """Chia lich su thanh (overflow, recent).

    - recent: <= window_msgs message cuoi -> giu NGUYEN VAN trong prompt.
    - overflow: cac message cu hon -> se duoc go vao tom tat luy tien.
    Cua so nguyen van du lon nen hoi thoai ngan/vua KHONG can tom tat (khong co gap).
    """
    hist = list(chat_history or [])
    if window_msgs is None or window_msgs < 0:
        return [], hist
    if len(hist) <= window_msgs:
        return [], hist
    return hist[:-window_msgs], hist[-window_msgs:]


def needs_summary_refresh(overflow_len, summary_covered, step=SUMMARY_REFRESH_STEP):
    """True neu can (tao lai) tom tat: co overflow moi vuot phan da tom tat >= step."""
    if overflow_len <= 0:
        return False
    if summary_covered <= 0:
        return True
    return (overflow_len - summary_covered) >= step
