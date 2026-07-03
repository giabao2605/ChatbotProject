"""Entity / Candidate Resolver — xu ly cau hoi KHONG kem ma ban ve.

Y tuong:
    Cau hoi tu nhien (ten san pham, vat lieu, kich thuoc) -> resolve ra danh sach
    candidate tai lieu (base_code / variant / version) tu cac doc da retrieve.
    - Neu chac chan 1 candidate  -> tra ve de RAG narrow & tra loi luon.
    - Neu nhieu candidate         -> tra danh sach de bot hoi lai (bang lua chon).
    - Neu khong du                -> de luong tra loi binh thuong (pass).

MODULE NAY THUAN: chi phu thuoc `re` + `unicodedata` (qua text_utils). KHONG
import qdrant / vectorstore / LLM -> unit test import duoc, khong tai model.

Lam viec trc tiep tren list `langchain_core.documents.Document` da retrieve,
nen KHONG can goi them mang / Qdrant, va dung duoc voi DU LIEU HIEN CO.
"""
import re
import unicodedata


# ---------------------------------------------------------------------------
# Helpers chuan hoa
# ---------------------------------------------------------------------------
def _strip_accents(text):
    if not text:
        return ""
    text = str(text)
    text = text.replace("\u0111", "d").replace("\u0110", "D")
    norm = unicodedata.normalize("NFD", text)
    return "".join(c for c in norm if unicodedata.category(c) != "Mn")


def _norm_text(text):
    """lowercase + bo dau + gom khoang trang."""
    t = _strip_accents(str(text or "")).lower()
    t = re.sub(r"\s+", " ", t).strip()
    return t


def _norm_dim(s):
    """Chuan hoa chuoi kich thuoc de so khop:
    '381 x 470 x 990.6 mm' -> '381x470x990.6'
    """
    if not s:
        return ""
    t = _strip_accents(str(s)).lower()
    t = t.replace("\u00d7", "x")            # ky tu nhan ×
    t = re.sub(r"\s*mm\b", "", t)
    t = re.sub(r"\s*[x]\s*", "x", t)
    t = re.sub(r"\s+", "", t)
    return t


# Vat lieu pho bien (mo rong tuy nha may). Match ca co/khong khoang trang.
_MATERIAL_PATTERNS = [
    r"inox\s*\d{3}",
    r"sus\s*\d{3}",
    r"ss\s*400",
    r"spcc",
    r"sphc",
    r"a\s*5052",
    r"al\s*6061",
    r"s50c",
    r"skd\s*11",
    r"scm\s*440",
    r"st\s*37",
    r"q235",
    r"\bsat\b",     # 'sat' (sát) sau khi bo dau
    r"\bthep\b",    # 'thep' (thép)
    r"\bnhom\b",    # 'nhom' (nhôm)
    r"\bdong\b",    # 'dong' (đòng) - than trong, co the trung 'dong' khac
]

_DIM_PATTERN = re.compile(
    r"\d+(?:\.\d+)?\s*[x\u00d7]\s*\d+(?:\.\d+)?(?:\s*[x\u00d7]\s*\d+(?:\.\d+)?)?\s*(?:mm)?",
    re.IGNORECASE,
)

# Pattern ma ban ve / part (dong bo voi extract_mechanical_codes ben service.py)
_CODE_PATTERNS = [
    re.compile(r"\b\d+\.\d+\.\d+\b", re.IGNORECASE),
    re.compile(r"\b[A-Z]{2,}[A-Z0-9-]*\d+[A-Z0-9-]*\b", re.IGNORECASE),
    re.compile(r"\b\d{3}-\d{3}\b", re.IGNORECASE),
]


def extract_no_code_constraints(question):
    """Trich cac rang buoc tu cau hoi KHONG co ma ban ve.

    Tra ve dict:
        {
          "dimensions": ["381x470x990.6", ...],   # da chuan hoa
          "materials":  ["inox 201", ...],         # da chuan hoa (norm)
          "quoted_names": ["khung sat + inox 201", ...],
          "free_terms": ["khung", "sat", ...],     # token co nghia, da bo dau
        }
    """
    q = str(question or "")
    qn = _norm_text(q)

    dimensions = []
    for m in _DIM_PATTERN.finditer(q):
        d = _norm_dim(m.group(0))
        # bo cac so qua ngan kieu '1x2' it y nghia? van giu, nhung phai co so
        if d and "x" in d:
            dimensions.append(d)

    materials = []
    for pat in _MATERIAL_PATTERNS:
        for m in re.finditer(pat, qn):
            val = re.sub(r"\s+", " ", m.group(0)).strip()
            if val and val not in materials:
                materials.append(val)

    # Ten dat trong ngoac kep "..." hoac “...” hoac '...'
    quoted_names = []
    for m in re.finditer(r"[\"\u201c\u201d'\u2018\u2019]([^\"\u201c\u201d'\u2018\u2019]{2,})[\"\u201c\u201d'\u2018\u2019]", q):
        name = _norm_text(m.group(1))
        if name:
            quoted_names.append(name)

    # Free terms: token >= 3 ky tu, bo stopword va bo cac token la so/ma
    stop = {
        "theo", "tai", "lieu", "cua", "cho", "san", "pham", "chi", "tiet",
        "la", "bao", "nhieu", "gi", "co", "va", "cac", "nhung", "voi",
        "dung", "sai", "kich", "thuoc", "khac", "do", "day", "vat",
        "luu", "y", "ban", "hay", "khi", "truoc", "sau", "den", "tu",
    }
    free_terms = []
    for tok in re.findall(r"[a-z0-9]+", qn):
        if len(tok) < 3 or tok in stop:
            continue
        if tok.isdigit():
            continue
        free_terms.append(tok)

    return {
        "dimensions": list(dict.fromkeys(dimensions)),
        "materials": list(dict.fromkeys(materials)),
        "quoted_names": list(dict.fromkeys(quoted_names)),
        "free_terms": list(dict.fromkeys(free_terms)),
    }


def has_explicit_code(question):
    """True neu cau hoi co chua mot ma ban ve / part ro rang."""
    for pat in _CODE_PATTERNS:
        if pat.search(str(question or "")):
            return True
    return False


# ---------------------------------------------------------------------------
# Scoring & grouping
# ---------------------------------------------------------------------------
def _doc_haystack(doc):
    """Gom cac truong metadata + noi dung de so khop."""
    md = getattr(doc, "metadata", {}) or {}
    parts = [
        md.get("ten_san_pham"),
        md.get("vat_lieu"),
        md.get("kich_thuoc_tong_the"),
        md.get("file_goc"),
        md.get("base_code"),
        md.get("variant_code"),
        md.get("noi_dung_goc") or getattr(doc, "page_content", ""),
    ]
    return _norm_text(" \u2502 ".join(str(p) for p in parts if p))


def _score_doc(doc, constraints):
    """Tinh diem khop giua 1 doc va rang buoc. Cang cao cang khop."""
    md = getattr(doc, "metadata", {}) or {}
    hay = _doc_haystack(doc)
    dim_field = _norm_dim(md.get("kich_thuoc_tong_the"))
    name_field = _norm_text(md.get("ten_san_pham"))
    mat_field = _norm_text(md.get("vat_lieu"))

    score = 0.0
    matched = {"dimensions": [], "materials": [], "quoted_names": [], "free_terms": []}

    # Kich thuoc: tin hieu manh nhat
    for d in constraints.get("dimensions", []):
        if d and (d in dim_field or d in _norm_dim(hay)):
            score += 3.0
            matched["dimensions"].append(d)

    # Vat lieu
    for mat in constraints.get("materials", []):
        if mat and (mat in mat_field or mat in hay):
            score += 1.5
            matched["materials"].append(mat)

    # Ten trong ngoac kep
    for name in constraints.get("quoted_names", []):
        if not name:
            continue
        _GENERIC_TOK = {"ban", "banve", "tai", "lieu", "tailieu", "thuat", "kythuat",
                        "version", "model", "cua", "cho", "cai", "file", "document",
                        "phien", "phienban", "ban ve"}
        toks = [t for t in name.split(" ") if len(t) >= 3 and t not in _GENERIC_TOK]
        if not toks:
            continue
        hit = sum(1 for t in toks if t in name_field or t in hay)
        if hit:
            score += 2.0 * (hit / len(toks))
            matched["quoted_names"].append(name)

    # Free terms (yeu hon)
    for term in constraints.get("free_terms", []):
        if term in name_field:
            score += 0.5
            matched["free_terms"].append(term)
        elif term in hay:
            score += 0.2

    return score, matched


def _candidate_key(md):
    base = (md.get("base_code") or "").strip()
    variant = (md.get("variant_code") or "default").strip()
    version = md.get("version_no")
    if base:
        key = base
    elif md.get("file_goc"):
        key = _strip_accents(str(md.get("file_goc"))).strip()
    else:
        key = "unknown"
    return (key, variant, version)


def resolve_candidates_from_docs(
    docs,
    constraints,
    max_candidates=5,
    min_score=2.0,
    margin=1.5,
    floor_score=1.0,
):
    """Gom docs thanh candidate va quyet dinh.

    Tra ve dict:
      {
        "decision": "single" | "ambiguous" | "pass",
        "selected": candidate | None,
        "selected_docs": [Document, ...],   # docs thuoc candidate duoc chon
        "candidates": [candidate, ...],      # top N (cho UI hoi lai)
      }

    candidate = {
        "key": str, "base_code": str, "variant_code": str, "version_no": any,
        "product_name": str, "dimensions": str, "materials": str,
        "file_goc": str, "score": float, "num_docs": int,
      }

    Quy tac:
      - 1 nhom                                     -> single.
      - top - second >= margin VA top >= min_score -> single.
      - co rang buoc nhung KHONG khop gi (top < floor_score) -> insufficient.
      - co rang buoc nhung khong tach duoc         -> ambiguous (top N).
      - khong co rang buoc nao                     -> ambiguous (de hoi lai).
    """
    groups = {}
    _n_docs = len(docs or [])
    for _idx, doc in enumerate(docs or []):
        md = getattr(doc, "metadata", {}) or {}
        key = _candidate_key(md)
        g = groups.setdefault(key, {"docs": [], "max_s": 0.0, "hits": 0, "best_idx": _idx, "md": md})
        s, _ = _score_doc(doc, constraints)
        g["docs"].append(doc)
        # KH-4: KHONG cong don theo so chunk (tranh thien lech PDF lon nhieu chunk).
        if s > g["max_s"]:
            g["max_s"] = s
        if s > 0:
            g["hits"] += 1
        if _idx < g["best_idx"]:
            g["best_idx"] = _idx

    if not groups:
        return {"decision": "pass", "selected": None, "selected_docs": list(docs or []), "candidates": []}

    def _to_candidate(key, g):
        md = g["md"]
        base, variant, version = key
        _max_s = g.get("max_s", 0.0)
        _hits = g.get("hits", 0)
        _best_idx = g.get("best_idx", 0)
        # KH-4: diem = diem chunk khop TOT NHAT (khong phai tong) + bonus nho khi nhieu
        # chunk cung ho (co tran) + rank_bonus theo vi tri retrieval (bam do lien quan cau hoi).
        _multi_bonus = min(max(_hits - 1, 0), 3) * 0.15
        _rank_bonus = 0.0
        if _n_docs > 0:
            _rank_bonus = round(max(0.0, (_n_docs - _best_idx) / float(_n_docs)), 3)
        _final = round(_max_s + _multi_bonus + _rank_bonus, 3)
        return {
            "key": md.get("base_code") or md.get("file_goc") or str(base),
            "base_code": md.get("base_code") or "",
            "variant_code": md.get("variant_code") or "default",
            "version_no": md.get("version_no"),
            "product_name": md.get("ten_san_pham") or "",
            "dimensions": md.get("kich_thuoc_tong_the") or "",
            "materials": md.get("vat_lieu") or "",
            "file_goc": md.get("file_goc") or "",
            "score": _final,
            "_match": round(_max_s, 3),
            "num_docs": len(g["docs"]),
            "_docs": g["docs"],
        }

    cands = [_to_candidate(k, g) for k, g in groups.items()]
    cands.sort(key=lambda c: c["score"], reverse=True)

    has_constraints = any(
        constraints.get(k) for k in ("dimensions", "materials", "quoted_names", "free_terms")
    )

    # Chi 1 nhom -> chot luon
    if len(cands) == 1:
        top = cands[0]
        return {
            "decision": "single",
            "selected": _public(top),
            "selected_docs": top["_docs"],
            "candidates": [_public(top)],
        }

    top, second = cands[0], cands[1]
    if has_constraints and top["_match"] >= min_score and (top["score"] - second["score"]) >= margin:
        return {
            "decision": "single",
            "selected": _public(top),
            "selected_docs": top["_docs"],
            "candidates": [_public(c) for c in cands[:max_candidates]],
        }

    # Co mo ta nhung KHONG tai lieu nao khop (diem qua thap) -> xin them thong tin.
    if has_constraints and top["_match"] < floor_score:
        return {
            "decision": "insufficient",
            "selected": None,
            "selected_docs": [],
            "candidates": [_public(c) for c in cands[:max_candidates]],
        }

    return {
        "decision": "ambiguous",
        "selected": None,
        "selected_docs": [],
        "candidates": [_public(c) for c in cands[:max_candidates]],
    }


def _public(cand):
    """Bo field noi bo (_docs) khoi candidate truoc khi tra ra ngoai."""
    return {k: v for k, v in cand.items() if not k.startswith("_")}


def build_candidate_table_markdown(candidates):
    """Tao bang Markdown cho nguoi dung chon tai lieu."""
    if not candidates:
        return ""
    lines = [
        "| # | Mã / Model | Tên sản phẩm | Kích thước | Phiên bản |",
        "|---|---|---|---|---|",
    ]
    for i, c in enumerate(candidates, start=1):
        code = c.get("base_code") or c.get("file_goc") or "-"
        variant = c.get("variant_code") or ""
        if variant and variant != "default":
            code = f"{code} / {variant}"
        name = (c.get("product_name") or "-").replace("|", "\\|")
        dim = (c.get("dimensions") or "-").replace("|", "\\|")
        ver = c.get("version_no")
        ver = f"v{ver}" if ver not in (None, "") else "-"
        lines.append(f"| {i} | {code} | {name} | {dim} | {ver} |")
    return "\n".join(lines)
