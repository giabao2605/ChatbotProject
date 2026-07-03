import os
import re
import json
import urllib.parse  # Fix: phai import urllib.parse tuong minh (import urllib khong du)
import hashlib
from datetime import datetime
 
from sqlalchemy import create_engine, text
from dotenv import load_dotenv
from mech_chatbot.config.logging import logger
from mech_chatbot.config.settings import QDRANT_COLLECTION
from mech_chatbot.config.constants import SHARE_ALL_DEPARTMENT
 
load_dotenv()
 
SQL_SERVER = os.getenv("SQL_SERVER", r"localhost\SQLEXPRESS")
SQL_DATABASE = os.getenv("SQL_DATABASE", "Mech_Chatbot_DB")
SQL_DRIVER = os.getenv("SQL_DRIVER", "ODBC Driver 17 for SQL Server")
SQL_USERNAME = os.getenv("SQL_USERNAME")
SQL_PASSWORD = os.getenv("SQL_PASSWORD")
SQL_TRUSTED_CONNECTION = os.getenv("SQL_TRUSTED_CONNECTION", "yes").lower() in {"1", "true", "yes"}

if SQL_USERNAME and SQL_PASSWORD:
    conn_str = (
        f"DRIVER={SQL_DRIVER};"
        f"SERVER={SQL_SERVER};"
        f"DATABASE={SQL_DATABASE};"
        f"UID={SQL_USERNAME};"
        f"PWD={SQL_PASSWORD};"
        f"TrustServerCertificate=yes;"
    )
else:
    conn_str = (
        f"DRIVER={SQL_DRIVER};"
        f"SERVER={SQL_SERVER};"
        f"DATABASE={SQL_DATABASE};"
        f"Trusted_Connection=yes;"
    )

params = urllib.parse.quote_plus(conn_str)
 
try:
    engine = create_engine(
        f"mssql+pyodbc:///?odbc_connect={params}",
        pool_size=5,
        max_overflow=10,
        pool_pre_ping=True,
        pool_recycle=3600,
    )
    logger.info("Da khoi tao SQLAlchemy Engine thanh cong.")
except Exception as e:
    logger.error(f"Loi khoi tao SQLAlchemy Engine: {e}", exc_info=True)
    engine = None  # Fix #1: gan tuong minh de cac ham co the kiem tra
 
def _ensure_engine():
    """Fix #1: Bao loi ro rang thay vi NameError khi engine khong khoi tao duoc."""
    if engine is None:
        raise RuntimeError(
            "SQLAlchemy Engine chua san sang. Kiem tra connection string / ODBC driver / SQL Server."
        )
 
# ==========================================
# SANITIZATION HELPERS (dua len module-level)
# ==========================================
def _sanitize_text(val, max_len=None):
    if val is None:
        return None
    s = str(val).strip()
    if s.lower() in ("khong ro", "khong ro", "none", "null", "n/a", ""):
        return None
    if max_len and len(s) > max_len:
        s = s[:max_len]  # Fix: cat chuoi de tranh loi "String or binary data would be truncated"
    return s
 
def _sanitize_int(val, default=None):
    """Parse so nguyen tu chuoi.

    Tra ve None (khong phai 1) khi khong parse duoc, de caller tu quyet dinh
    co ghi NULL hay reject. default=1 truoc day co the am tham ghi so luong sai.
    """
    try:
        nums = re.findall(r"\d+", str(val))
        return int(nums[0]) if nums else default
    except Exception:
        return default
        
import unicodedata

def normalize_base_code(code):
    if not code:
        return ""
    code = str(code).lower().strip()
    code = ''.join(c for c in unicodedata.normalize('NFD', code) if unicodedata.category(c) != 'Mn')
    code = code.replace(".pdf", "").replace(".docx", "").replace(".xlsx", "")
    code = re.sub(r"[_\s]+", "-", code)
    return code
 
def _sanitize_date(val):
    """Fix: parse chat che; that bai tra None de tranh loi conversion cot DATE."""
    s = _sanitize_text(val)
    if not s:
        return None
    for fmt in ("%d/%m/%Y", "%Y-%m-%d", "%d-%m-%Y", "%Y/%m/%d", "%d/%m/%y"):
        try:
            return datetime.strptime(s, fmt).date().isoformat()
        except ValueError:
            continue
    return None
 
# FIX C6: gioi han kich thuoc input chat (chong payload GB lam sap DB). Co the chinh qua env.
MAX_USER_MSG_LEN = int(os.getenv("MAX_USER_MSG_LEN", "20000"))
MAX_BOT_MSG_LEN = int(os.getenv("MAX_BOT_MSG_LEN", "200000"))
 
def _cap_len(val, max_len):
    """C6: chi cat bot khi vuot gioi han, KHONG doi gia tri (khac _sanitize_text -> tranh bien 'null'/'none' thanh None)."""
    if val is None:
        return None
    s = str(val)
    if len(s) > max_len:
        logger.warning(f"Input vuot {max_len} ky tu, da cat bot de chong payload qua lon.")
        return s[:max_len]
    return s
 
# ==========================================
# CHAT HISTORY
# ==========================================

def save_chat_history(session_id, user_msg, bot_msg, image_path=None, ref_images=None, username=None):
    _ensure_engine()
    try:
        ref_images_json = json.dumps(ref_images or [], ensure_ascii=False)
        session_id  = _cap_len(session_id, 100)
        user_msg    = _cap_len(user_msg, MAX_USER_MSG_LEN)
        bot_msg     = _cap_len(bot_msg, MAX_BOT_MSG_LEN)
        image_path  = _cap_len(image_path, 500)
        username    = _cap_len(username, 255)

        with engine.begin() as conn:
            result = conn.execute(
                text("""
                    INSERT INTO LichSuChat
                        (SessionID, CauHoi_User, TraLoi_Bot, HinhAnhUpload, RefImages, Username)
                    OUTPUT INSERTED.ChatID
                    VALUES (:session_id, :user_msg, :bot_msg, :image_path, :ref_images, :username)
                """),
                {
                    "session_id": session_id,
                    "user_msg":   user_msg,
                    "bot_msg":    bot_msg,
                    "image_path": image_path,
                    "ref_images": ref_images_json,
                    "username":   username,
                },
            )
            row = result.fetchone()
            return row[0] if row else None
    except Exception as e:
        logger.error(f"Loi khi luu lich su chat: {e}", exc_info=True)
        return None


def save_answer_sources(chat_id, retrieved_docs):
    """P3-1: Luu cac tai lieu/chunk RAG da dung de sinh cau tra loi (truy vet nguon)."""
    if not chat_id or not retrieved_docs:
        return
    _ensure_engine()

    def _to_int(v):
        try:
            return int(v)
        except (TypeError, ValueError):
            return None

    def _to_float(v):
        try:
            return float(v)
        except (TypeError, ValueError):
            return None

    try:
        with engine.begin() as conn:
            for rank_no, d in enumerate(retrieved_docs, start=1):
                if not isinstance(d, dict):
                    continue
                is_cur = d.get("is_current")
                conn.execute(
                    text("""
                        INSERT INTO AnswerSource
                            (ChatID, DocID, FileName, VersionNo, VariantCode, ChunkRef, Score, RankNo, IsCurrent)
                        VALUES
                            (:cid, :doc_id, :fn, :vn, :vc, :chunk, :score, :rank_no, :is_cur)
                    """),
                    {
                        "cid": chat_id,
                        "doc_id": _to_int(d.get("doc_id")),
                        "fn": _cap_len(d.get("file_goc"), 500),
                        "vn": _to_int(d.get("version_no")),
                        "vc": _cap_len(d.get("variant_code"), 100),
                        "chunk": _cap_len(None if d.get("trang") is None else str(d.get("trang")), 200),
                        "score": _to_float(d.get("score")),
                        "rank_no": rank_no,
                        "is_cur": (None if is_cur is None else (1 if is_cur else 0)),
                    },
                )
    except Exception as e:
        logger.error(f"Loi khi luu nguon cau tra loi (AnswerSource): {e}", exc_info=True)


def get_all_sessions(username=None, is_admin=False):
    """Lay danh sach session chat.

    - User thuong chi thay session cua minh (loc theo Username).
    - Admin thay toan bo.
    """
    _ensure_engine()
    try:
        params = {}
        where_clause = ""
        if not is_admin:
            where_clause = "WHERE Username = :username"
            params["username"] = username

        with engine.connect() as conn:
            query = text(
                f"""
                SELECT SessionID, ThoiGianBatDau, CauHoiDauTien, Owner FROM (
                    SELECT SessionID,
                           CauHoi_User AS CauHoiDauTien,
                           ThoiGian AS ThoiGianBatDau,
                           Username AS Owner,
                           ROW_NUMBER() OVER (PARTITION BY SessionID ORDER BY ThoiGian ASC, ChatID ASC) AS rn
                    FROM LichSuChat
                    {where_clause}
                ) t
                WHERE rn = 1
                ORDER BY ThoiGianBatDau DESC
                """
            )
            result = conn.execute(query, params)
            sessions = result.fetchall()
            out = []
            for row in sessions:
                cau_hoi = row[2] or ""
                if len(cau_hoi) > 30:
                    label = cau_hoi[:30] + "..."
                else:
                    label = cau_hoi or "(Khong co tieu de)"
                out.append({"session_id": row[0], "thoi_gian": row[1], "cau_hoi": label, "owner": row[3]})
            return out
    except Exception as e:
        logger.error(f"Loi khi lay danh sach session: {e}", exc_info=True)
        return []
 

def get_chat_history(session_id, username=None, is_admin=False, user_clearance="confidential"):
    """Lay noi dung mot session chat, chi tra ve cua user hien tai (tru admin)."""
    _ensure_engine()
    try:
        params = {"session_id": session_id}
        user_filter = ""
        if not is_admin:
            user_filter = "AND Username = :username"
            params["username"] = username

        with engine.connect() as conn:
            query = text(
                f"""
                SELECT ChatID, CauHoi_User, TraLoi_Bot, HinhAnhUpload, DanhGia, RefImages
                FROM LichSuChat
                WHERE SessionID = :session_id
                {user_filter}
                ORDER BY ThoiGian ASC, ChatID ASC
                """
            )
            rows = conn.execute(query, params).fetchall()

            # P0-2 (bao mat lich su): AN cau tra loi dua tren tai lieu MAT vuot clearance HIEN TAI.
            # Gate tai thoi diem DOC: sau khi bi thu hoi quyen, user khong the doc lai noi dung mat qua lich su chat.
            _redact_ids = set()
            if not is_admin and rows:
                try:
                    _order = {"public": 0, "internal": 1, "confidential": 2}
                    _uorder = _order.get((user_clearance or "public"), 0)
                    _cids = [int(r[0]) for r in rows if r[0] is not None]
                    if _cids:
                        _in = ",".join(str(c) for c in _cids)
                        _lvl_rows = conn.execute(text(
                            "SELECT a.ChatID, MAX(CASE t.SecurityLevel WHEN 'confidential' THEN 2 "
                            "WHEN 'internal' THEN 1 ELSE 0 END) AS lvl "
                            "FROM AnswerSource a JOIN TaiLieu t ON a.DocID = t.DocID "
                            f"WHERE a.ChatID IN ({_in}) GROUP BY a.ChatID"
                        )).fetchall()
                        for _cid, _lvl in _lvl_rows:
                            if (_lvl or 0) > _uorder:
                                _redact_ids.add(_cid)
                except Exception as _e:
                    logger.error(f"Loi tinh redaction lich su chat: {_e}", exc_info=True)

            history = []
            for row in rows:
                # FIX C5: doc lai ref_images tu DB (JSON string -> list duong dan)
                try:
                    ref_images = json.loads(row[5]) if row[5] else []
                except (json.JSONDecodeError, TypeError):
                    ref_images = []
                history.append({"role": "user", "content": row[1], "image": row[3]})
                _assistant_content = row[2]
                _assistant_imgs = ref_images
                if row[0] in _redact_ids:
                    _assistant_content = (
                        "🔒 Nội dung câu trả lời này dựa trên tài liệu MẬT mà bạn hiện "
                        "không còn quyền xem. Nội dung đã được ẩn theo phân quyền."
                    )
                    _assistant_imgs = []
                history.append(
                    {
                        "role": "assistant",
                        "content": _assistant_content,
                        "chat_id": row[0],
                        "danh_gia": row[4],
                        "ref_images": _assistant_imgs,
                    }
                )
            return history
    except Exception as e:
        logger.error(f"Loi khi lay lich su chat: {e}", exc_info=True)
        return []
 

def clear_chat_history(session_id, username=None, is_admin=False):
    """Xoa session chat — user thuong chi xoa duoc session cua minh."""
    _ensure_engine()
    try:
        params = {"session_id": session_id}
        user_filter = ""
        if not is_admin:
            user_filter = "AND Username = :username"
            params["username"] = username

        with engine.begin() as conn:
            conn.execute(
                text(
                    f"""
                    DELETE FROM LichSuChat
                    WHERE SessionID = :session_id
                    {user_filter}
                    """
                ),
                params,
            )
    except Exception as e:
        logger.error(f"Loi khi xoa lich su chat: {e}", exc_info=True)
 
def _question_hash(q):
    """Chuan hoa cau hoi (bo dau, lowercase, gom khoang trang) roi bam sha256 -> gom cau trung."""
    try:
        s = unicodedata.normalize("NFKD", str(q or "")).encode("ascii", "ignore").decode("ascii")
        s = " ".join(s.lower().split())
        return hashlib.sha256(s.encode("utf-8")).hexdigest()
    except Exception:
        return None


def mark_feedback_stale_for_doc(doc_id, resolved_by_doc_id=None):
    """P3-2: Danh dau feedback cu (SourceDocID = doc_id) la 'stale' khi tai lieu duoc cap nhat
    metadata hoac bi superseded -> khong tinh vao diem chat luong ban moi."""
    if doc_id is None:
        return 0
    _ensure_engine()
    try:
        with engine.begin() as conn:
            res = conn.execute(text("""
                UPDATE FeedbackReview
                SET IsStale = 1, ResolvedByDocID = :rb, ResolvedAt = GETDATE()
                WHERE ISNULL(IsStale, 0) = 0
                  AND ISNULL(AddedToGoldenSet, 0) = 0
                  AND SourceDocID = :d
            """), {"d": doc_id, "rb": resolved_by_doc_id})
            return res.rowcount if res.rowcount is not None else 0
    except Exception as e:
        logger.error(f"Loi khi danh dau feedback stale (DocID {doc_id}): {e}", exc_info=True)
        return 0


# ============================================================
# P3-3: Diem chat luong tai lieu (DocQualityScore)
# P3-4: Golden Answer
# ============================================================
ROLE_WEIGHTS = {
    "admin": 3.0, "owner": 3.0, "superadmin": 3.0,
    "reviewer": 2.0, "manager": 2.0, "approver": 2.0,
    "engineer": 1.5, "ky_su": 1.5,
    "user": 1.0, "viewer": 1.0, "guest": 0.5,
}
QUALITY_HALF_LIFE_DAYS = 90.0
QUALITY_PRIOR = 0.6
QUALITY_SMOOTH_K = 2.0


def _role_weight(roles):
    w = 1.0
    for r in (roles or []):
        if r is None:
            continue
        w = max(w, ROLE_WEIGHTS.get(str(r).strip().lower(), 1.0))
    return w


def recompute_doc_quality_scores():
    """P3-3: Tinh lai diem chat luong moi tai lieu tu like/dislike, co trong so theo vai tro
    nguoi danh gia + time-decay, va BO QUA cac feedback da bi danh dau stale."""
    _ensure_engine()
    role_map = {}
    try:
        with engine.connect() as conn:
            rrows = conn.execute(text(
                "SELECT u.Username, r.RoleName FROM Users u "
                "LEFT JOIN UserRoles ur ON ur.UserID = u.UserID "
                "LEFT JOIN Roles r ON r.RoleID = ur.RoleID"
            )).fetchall()
        for uname, rname in rrows:
            if uname is None:
                continue
            role_map.setdefault(uname, [])
            if rname:
                role_map[uname].append(rname)
    except Exception as e:
        logger.warning(f"[quality] Khong doc duoc role map: {e}")

    with engine.connect() as conn:
        votes = conn.execute(text(
            "WITH primary_src AS ("
            "  SELECT a.ChatID, a.DocID, "
            "         ROW_NUMBER() OVER (PARTITION BY a.ChatID ORDER BY a.RankNo ASC, a.SourceID ASC) AS rn "
            "  FROM AnswerSource a WHERE a.DocID IS NOT NULL"
            ") "
            "SELECT ps.DocID, l.DanhGia, l.ThoiGian, l.Username "
            "FROM LichSuChat l "
            "JOIN primary_src ps ON ps.ChatID = l.ChatID AND ps.rn = 1 "
            "WHERE l.DanhGia IS NOT NULL "
            "  AND NOT EXISTS (SELECT 1 FROM FeedbackReview fr WHERE fr.ChatID = l.ChatID AND ISNULL(fr.IsStale,0) = 1)"
        )).fetchall()

    agg = {}
    now = datetime.now()
    for doc_id, danh_gia, thoi_gian, username in votes:
        if doc_id is None:
            continue
        rw = _role_weight(role_map.get(username, []))
        try:
            age_days = max(0.0, (now - thoi_gian).total_seconds() / 86400.0) if thoi_gian else 0.0
        except Exception:
            age_days = 0.0
        tw = 0.5 ** (age_days / QUALITY_HALF_LIFE_DAYS)
        w = rw * tw
        a = agg.setdefault(doc_id, {"like": 0, "dislike": 0, "wl": 0.0, "wd": 0.0, "n": 0})
        a["n"] += 1
        if int(danh_gia) == 1:
            a["like"] += 1
            a["wl"] += w
        else:
            a["dislike"] += 1
            a["wd"] += w

    with engine.begin() as conn:
        conn.execute(text("DELETE FROM DocQualityScore"))
        for doc_id, a in agg.items():
            wl, wd = a["wl"], a["wd"]
            quality = (wl + QUALITY_PRIOR * QUALITY_SMOOTH_K) / (wl + wd + QUALITY_SMOOTH_K)
            net = wl - wd
            conn.execute(text(
                "INSERT INTO DocQualityScore (DocID, LikeCount, DislikeCount, WeightedLike, WeightedDislike, QualityScore, NetScore, SampleSize, LastComputedAt) "
                "VALUES (:d, :lk, :dk, :wl, :wd, :q, :net, :n, GETDATE())"
            ), {"d": doc_id, "lk": a["like"], "dk": a["dislike"], "wl": round(wl, 4),
                "wd": round(wd, 4), "q": round(quality, 4), "net": round(net, 4), "n": a["n"]})
    logger.info(f"[quality] Da tinh lai diem chat luong cho {len(agg)} tai lieu.")
    return len(agg)


def get_doc_quality_ranking(limit=50, worst_first=True):
    """P3-3: Bang xep hang chat luong tai lieu (mac dinh diem thap nhat truoc de uu tien xu ly)."""
    _ensure_engine()
    order = "ASC" if worst_first else "DESC"
    with engine.connect() as conn:
        rows = conn.execute(text(
            "SELECT TOP (:lim) q.DocID, t.TenFile, t.VersionNo, t.IsCurrent, t.LifecycleStatus, "
            "       q.LikeCount, q.DislikeCount, q.WeightedLike, q.WeightedDislike, q.QualityScore, q.NetScore, q.SampleSize, q.LastComputedAt "
            "FROM DocQualityScore q LEFT JOIN TaiLieu t ON t.DocID = q.DocID "
            "ORDER BY q.QualityScore " + order + ", q.WeightedDislike DESC"
        ), {"lim": int(limit)}).fetchall()
    cols = ["doc_id", "file", "version_no", "is_current", "lifecycle_status", "like", "dislike", "wl", "wd", "quality", "net", "n", "computed_at"]
    out = []
    for r in rows:
        d = dict(zip(cols, r))
        d["reliable"] = (d.get("n") or 0) >= QUALITY_MIN_SAMPLE
        out.append(d)
    return out


def upsert_golden_answer(question, answer, source_doc_id=None, department=None, site=None, created_by="System", feedback_id=None):
    """P3-4: Luu cau tra loi da duoc chuyen gia duyet thanh Golden Answer (gom theo hash cau hoi)."""
    _ensure_engine()
    if not question or not answer:
        return None
    qhash = _question_hash(question)
    try:
        with engine.begin() as conn:
            conn.execute(text("UPDATE GoldenAnswer SET IsActive = 0 WHERE QuestionHash = :h AND IsActive = 1"), {"h": qhash})
            conn.execute(text(
                "INSERT INTO GoldenAnswer (FeedbackID, QuestionHash, QuestionText, GoldenAnswer, SourceDocID, Department, Site, CreatedBy, IsActive) "
                "VALUES (:fid, :h, :q, :a, :sd, :dept, :site, :cb, 1)"
            ), {"fid": feedback_id, "h": qhash, "q": _cap_len(question, 4000), "a": answer,
                "sd": source_doc_id, "dept": _cap_len(department, 100), "site": _cap_len(site, 100), "cb": _cap_len(created_by, 256)})
        logger.info(f"[golden] Da luu Golden Answer (hash={(qhash[:8] if qhash else '?')}).")
        return qhash
    except Exception as e:
        logger.error(f"[golden] Loi upsert Golden Answer: {e}", exc_info=True)
        return None


def find_golden_answer(question, department=None):
    """P3-4: Tra ve Golden Answer dang active khop cau hoi (theo hash cau hoi da chuan hoa)."""
    _ensure_engine()
    qhash = _question_hash(question)
    if not qhash:
        return None
    try:
        with engine.connect() as conn:
            row = conn.execute(text(
                "SELECT TOP 1 GoldenID, QuestionText, GoldenAnswer, SourceDocID, Department, Site "
                "FROM GoldenAnswer WHERE QuestionHash = :h AND IsActive = 1 ORDER BY CreatedAt DESC"
            ), {"h": qhash}).fetchone()
        if not row:
            return None
        return {"golden_id": row[0], "question": row[1], "answer": row[2],
                "source_doc_id": row[3], "department": row[4], "site": row[5]}
    except Exception as e:
        logger.error(f"[golden] Loi find Golden Answer: {e}", exc_info=True)
        return None



# ============================================================
# P3-5: Regression testing CRUD
# P3-6: Guardrails (cleanup, normalize, nguong mau)
# ============================================================
QUALITY_MIN_SAMPLE = 3


def normalize_question(q):
    """P3-6: chuan hoa cau hoi (bo dau, lowercase, gom khoang trang) de so khop on dinh."""
    try:
        s = unicodedata.normalize("NFKD", str(q or "")).encode("ascii", "ignore").decode("ascii")
        return " ".join(s.lower().split())
    except Exception:
        return str(q or "").strip().lower()


def add_regression_question(question, expected_doc_id=None, expected_keywords=None, department=None, site=None, created_by="System"):
    """P3-5: them 1 cau hoi vao bo hoi quy."""
    _ensure_engine()
    if not question or not str(question).strip():
        return None
    kw = expected_keywords
    if isinstance(expected_keywords, (list, tuple)):
        kw = ", ".join([str(x).strip() for x in expected_keywords if str(x).strip()])
    with engine.begin() as conn:
        row = conn.execute(text(
            "INSERT INTO RegressionQuestion (QuestionText, ExpectedDocID, ExpectedKeywords, Department, Site, CreatedBy, IsActive) "
            "OUTPUT INSERTED.RegQID "
            "VALUES (:q, :ed, :kw, :dept, :site, :cb, 1)"
        ), {"q": _cap_len(question, 2000), "ed": expected_doc_id, "kw": kw,
            "dept": _cap_len(department, 100), "site": _cap_len(site, 100), "cb": _cap_len(created_by, 256)}).fetchone()
    return row[0] if row else None


def list_regression_questions(active_only=True):
    """P3-5: liet ke cau hoi hoi quy."""
    _ensure_engine()
    q = ("SELECT RegQID, QuestionText, ExpectedDocID, ExpectedKeywords, Department, Site, IsActive, CreatedAt "
         "FROM RegressionQuestion")
    if active_only:
        q += " WHERE IsActive = 1"
    q += " ORDER BY CreatedAt DESC"
    cols = ["reg_qid", "question", "expected_doc_id", "expected_keywords", "department", "site", "is_active", "created_at"]
    with engine.connect() as conn:
        rows = conn.execute(text(q)).fetchall()
    return [dict(zip(cols, r)) for r in rows]


def set_regression_question_active(reg_qid, is_active):
    """P3-5: bat/tat 1 cau hoi hoi quy."""
    _ensure_engine()
    with engine.begin() as conn:
        conn.execute(text("UPDATE RegressionQuestion SET IsActive = :a WHERE RegQID = :id"),
                     {"a": 1 if is_active else 0, "id": reg_qid})
    return True


def save_regression_run(reg_qid, batch_id, answer_text, matched_doc_ids, doc_hit, keyword_hit, passed, duration_ms=None, error_text=None):
    """P3-5: luu ket qua 1 lan chay hoi quy."""
    _ensure_engine()
    mids = matched_doc_ids
    if isinstance(matched_doc_ids, (list, tuple)):
        mids = ",".join([str(x) for x in matched_doc_ids if x is not None])
    with engine.begin() as conn:
        conn.execute(text(
            "INSERT INTO RegressionRun (RegQID, RunBatchID, AnswerText, MatchedDocIDs, DocHit, KeywordHit, Passed, DurationMs, ErrorText) "
            "VALUES (:q, :b, :a, :m, :dh, :kh, :p, :dur, :err)"
        ), {"q": reg_qid, "b": _cap_len(batch_id, 64), "a": answer_text, "m": _cap_len(mids, 500),
            "dh": 1 if doc_hit else 0, "kh": 1 if keyword_hit else 0, "p": 1 if passed else 0,
            "dur": duration_ms, "err": _cap_len(error_text, 1000)})
    return True


def get_latest_regression_batch():
    """P3-5: lay batch hoi quy moi nhat."""
    _ensure_engine()
    with engine.connect() as conn:
        row = conn.execute(text("SELECT TOP 1 RunBatchID FROM RegressionRun ORDER BY CreatedAt DESC, RunID DESC")).fetchone()
    return row[0] if row else None


def get_regression_runs(batch_id=None):
    """P3-5: lay chi tiet cac lan chay cua 1 batch (mac dinh batch moi nhat)."""
    _ensure_engine()
    if batch_id is None:
        batch_id = get_latest_regression_batch()
    if not batch_id:
        return []
    cols = ["run_id", "reg_qid", "question", "passed", "doc_hit", "keyword_hit", "matched_doc_ids", "expected_doc_id", "duration_ms", "error", "answer", "created_at"]
    with engine.connect() as conn:
        rows = conn.execute(text(
            "SELECT rr.RunID, rr.RegQID, rq.QuestionText, rr.Passed, rr.DocHit, rr.KeywordHit, rr.MatchedDocIDs, rq.ExpectedDocID, rr.DurationMs, rr.ErrorText, rr.AnswerText, rr.CreatedAt "
            "FROM RegressionRun rr LEFT JOIN RegressionQuestion rq ON rq.RegQID = rr.RegQID "
            "WHERE rr.RunBatchID = :b ORDER BY rr.RunID ASC"
        ), {"b": batch_id}).fetchall()
    return [dict(zip(cols, r)) for r in rows]


def cleanup_dangling_records():
    """P3-6: don du lieu mo coi tham chieu toi Doc/Chat khong con ton tai."""
    _ensure_engine()
    counts = {}
    with engine.begin() as conn:
        r = conn.execute(text("UPDATE GoldenAnswer SET SourceDocID = NULL WHERE SourceDocID IS NOT NULL AND SourceDocID NOT IN (SELECT DocID FROM TaiLieu)"))
        counts["golden_source_nulled"] = r.rowcount if r.rowcount is not None else 0
        r = conn.execute(text("UPDATE FeedbackReview SET SourceDocID = NULL WHERE SourceDocID IS NOT NULL AND SourceDocID NOT IN (SELECT DocID FROM TaiLieu)"))
        counts["feedback_source_nulled"] = r.rowcount if r.rowcount is not None else 0
        r = conn.execute(text("DELETE FROM AnswerSource WHERE ChatID NOT IN (SELECT ChatID FROM LichSuChat)"))
        counts["answersource_orphan_deleted"] = r.rowcount if r.rowcount is not None else 0
        r = conn.execute(text("DELETE FROM DocQualityScore WHERE DocID NOT IN (SELECT DocID FROM TaiLieu)"))
        counts["quality_orphan_deleted"] = r.rowcount if r.rowcount is not None else 0
    logger.info(f"[guardrail] cleanup_dangling_records: {counts}")
    return counts


def update_chat_feedback(chat_id, danh_gia, voter_username=None):
    _ensure_engine()
    try:
        with engine.begin() as conn:
            # P3-6 guardrail: chi chu so huu chat moi duoc danh gia; idempotent (1 vote/ChatID)
            owner_row = conn.execute(text("SELECT Username, DanhGia FROM LichSuChat WHERE ChatID = :c"), {"c": chat_id}).fetchone()
            if owner_row is None:
                logger.warning(f"[vote] ChatID {chat_id} khong ton tai, bo qua danh gia.")
                return False
            chat_owner, current_vote = owner_row[0], owner_row[1]
            if voter_username and chat_owner and str(voter_username).strip().lower() != str(chat_owner).strip().lower():
                logger.warning(f"[vote] {voter_username} khong phai chu so huu ChatID {chat_id} (cua {chat_owner}); tu choi.")
                return False
            if current_vote is not None and int(current_vote) == int(danh_gia):
                return True
            conn.execute(
                text("UPDATE LichSuChat SET DanhGia = :danh_gia WHERE ChatID = :chat_id"),
                {"danh_gia": danh_gia, "chat_id": chat_id},
            )
            if danh_gia == -1:
                row = conn.execute(
                    text("SELECT CauHoi_User, TraLoi_Bot FROM LichSuChat WHERE ChatID = :chat_id"),
                    {"chat_id": chat_id}
                ).fetchone()
                if row:
                    # Check if already in FeedbackReview
                    exists = conn.execute(text("SELECT 1 FROM FeedbackReview WHERE ChatID = :c"), {"c": chat_id}).fetchone()
                    if not exists:
                        # P3-2: gan version + ngu canh cua nguon da dung sinh cau tra loi
                        src = conn.execute(text(
                            "SELECT TOP 1 DocID, VersionNo FROM AnswerSource "
                            "WHERE ChatID = :c AND DocID IS NOT NULL ORDER BY RankNo ASC, SourceID ASC"
                        ), {"c": chat_id}).fetchone()
                        src_doc_id = src[0] if src else None
                        src_ver = src[1] if src else None
                        dept = site = None
                        if src_doc_id is not None:
                            ds = conn.execute(text("SELECT ThuMuc, Site FROM TaiLieu WHERE DocID = :d"), {"d": src_doc_id}).fetchone()
                            if ds:
                                dept, site = ds[0], ds[1]
                        conn.execute(
                            text(
                                "INSERT INTO FeedbackReview "
                                "(ChatID, Question, BotAnswer, SourceDocID, DocVersionNo, ContextHash, Department, Site) "
                                "VALUES (:c, :q, :b, :sd, :sv, :ch, :dept, :site)"
                            ),
                            {"c": chat_id, "q": row[0], "b": row[1], "sd": src_doc_id, "sv": src_ver,
                             "ch": _question_hash(row[0]), "dept": _cap_len(dept, 100), "site": _cap_len(site, 100)}
                        )
        return True
    except Exception as e:
        logger.error(f"Loi khi cap nhat danh gia chat: {e}", exc_info=True)
        return False
 
# ==========================================
# DOCUMENT METADATA (Fix #1: tach reset / insert)
# ==========================================
def _get_or_create_doc(conn, file_name, thu_muc):
    # Fetch classification json tu IngestionJobs (neu co) de update metadata
    job = conn.execute(
        text("SELECT TOP 1 ClassificationJson, FilePath, UploadMetaJson FROM dbo.IngestionJobs WHERE TenFile = :f AND ThuMuc = :t ORDER BY CreatedAt DESC"),
        {"f": file_name, "t": thu_muc}
    ).fetchone()
    
    cls_data = {}
    if job and job[0]:
        try:
            import json
            cls_data = json.loads(job[0])
        except:
            pass
            
    base_code = cls_data.get("base_code")
    base_code = normalize_base_code(base_code) if base_code else None
    version_label = cls_data.get("version_label")
    version_no = cls_data.get("version_no", 1)
    variant_code = cls_data.get("variant_code") or "default"

    # GD2: luon ghi Domain + SecurityLevel vao TaiLieu (truoc day chi ghi Site/family).
    # Uu tien gia tri tu classification; fallback resolve theo phong ban (data-driven Departments).
    from mech_chatbot.ingestion.domain_registry import resolve_domain_by_department, resolve_security_by_department
    domain = cls_data.get("domain") or resolve_domain_by_department(thu_muc)
    security_level = cls_data.get("security_level") or resolve_security_by_department(thu_muc)
    
    f_id = None
    if base_code:
        f_row = conn.execute(text("SELECT FamilyID FROM DocumentFamily WHERE BaseCode = :b"), {"b": base_code}).fetchone()
        if f_row:
            f_id = f_row[0]
        else:
            # Auto-create DocumentFamily neu chua ton tai
            f_insert = conn.execute(
                text("INSERT INTO DocumentFamily (BaseCode, FamilyName) OUTPUT INSERTED.FamilyID VALUES (:b, :n)"),
                {"b": base_code, "n": base_code}
            )
            inserted = f_insert.fetchone()
            if inserted:
                f_id = inserted[0]

    row = conn.execute(
        text("SELECT DocID, LifecycleStatus, ReviewStatus, IsCurrent FROM TaiLieu WHERE TenFile = :f AND ThuMuc = :t"),
        {"f": file_name, "t": thu_muc},
    ).fetchone()
    
    if row:
        doc_id, lifecycle_status, review_status, is_current = row
        if lifecycle_status == 'published' and review_status == 'approved':
            raise ValueError(f"Tài liệu {file_name} đã được published. Không cho phép re-ingest để bảo toàn dữ liệu.")
        # Update metadata neu re-ingest draft/rejected
        conn.execute(
            text("""UPDATE TaiLieu SET 
                ReviewStatus = 'pending_review',
                FamilyID = :fid, BaseCode = :bc, VersionNo = :vn, VersionLabel = :vl, VariantCode = :vc,
                Site = :site, Domain = :domain, SecurityLevel = :seclvl, FilePath = :fp
                WHERE DocID = :d"""),
            {"d": doc_id, "fid": f_id, "bc": base_code, "vn": version_no, "vl": version_label, "vc": variant_code, "site": _resolve_site(thu_muc), "domain": domain, "seclvl": security_level, "fp": (job[1] if job else None)}
        )
        _apply_upload_meta_to_doc(conn, doc_id, (job[2] if job else None), domain)
        return doc_id
        
    res = conn.execute(
        text(
            """INSERT INTO TaiLieu (TenFile, ThuMuc, TrangThaiVector, ReviewStatus, FamilyID, BaseCode, VersionNo, VersionLabel, VariantCode, Site, Domain, SecurityLevel, FilePath) 
            OUTPUT INSERTED.DocID 
            VALUES (:f, :t, 1, 'pending_review', :fid, :bc, :vn, :vl, :vc, :site, :domain, :seclvl, :fp)"""
        ),
        {"f": file_name, "t": thu_muc, "fid": f_id, "bc": base_code, "vn": version_no, "vl": version_label, "vc": variant_code, "site": _resolve_site(thu_muc), "domain": domain, "seclvl": security_level, "fp": (job[1] if job else None)},
    )
    row = res.fetchone()
    new_doc_id = row[0] if row else None
    if new_doc_id is not None:
        _apply_upload_meta_to_doc(conn, new_doc_id, (job[2] if job else None), domain)
    return new_doc_id
 
def set_document_departments(conn, doc_id, dept_codes):
    """E1: ghi lai danh sach phong ban duoc chia se cho 1 tai lieu vao bang
    nhieu-nhieu dbo.PhongBanChiaSe (nguon su that). Xoa het dong cu cua doc_id
    roi insert lai tap moi. Chay TRONG transaction cua caller (nhan `conn`).
    dept_codes: list/tuple/set hoac chuoi CSV.
    """
    if doc_id is None:
        return
    codes = _split_csv_tokens(dept_codes)
    conn.execute(text("DELETE FROM dbo.PhongBanChiaSe WHERE DocID = :d"), {"d": doc_id})
    for code in codes:
        conn.execute(
            text("INSERT INTO dbo.PhongBanChiaSe (DocID, DeptCode) VALUES (:d, :c)"),
            {"d": doc_id, "c": code},
        )


def get_document_departments(doc_id):
    """E1: doc danh sach phong ban duoc chia se cua 1 tai lieu tu PhongBanChiaSe."""
    if doc_id is None:
        return []
    _ensure_engine()
    try:
        with engine.connect() as conn:
            rows = conn.execute(
                text("SELECT DeptCode FROM dbo.PhongBanChiaSe WHERE DocID = :d ORDER BY DeptCode"),
                {"d": doc_id},
            ).fetchall()
        return [r[0] for r in rows]
    except Exception as e:
        logger.error(f"get_document_departments loi doc_id={doc_id}: {e}", exc_info=True)
        return []


def update_document_classification(doc_id, domain=None, security_level=None, phong_ban=None):
    """GD5 fix ro ri: dong bo lai Domain/SecurityLevel/PhongBan cho TaiLieu sau khi co
    override tu form va escalation tu sensitive_scanner. Truoc day _get_or_create_doc ghi
    TaiLieu theo ClassificationJson (suy tu folder) nen khi override/escalate muc mat,
    TaiLieu lech voi payload Qdrant -> duong SQL BOM (t.SecurityLevel) co the lo tai lieu mat.
    """
    if doc_id is None:
        return
    _ensure_engine()
    try:
        sets = []
        params = {"d": doc_id}
        if domain is not None:
            sets.append("Domain = :domain")
            params["domain"] = domain
        if security_level is not None:
            sets.append("SecurityLevel = :seclvl")
            params["seclvl"] = security_level
        # E1: PhongBan da chuyen sang bang nhieu-nhieu dbo.PhongBanChiaSe (khong con cot CSV).
        if not sets and phong_ban is None:
            return
        with engine.begin() as conn:
            if sets:
                conn.execute(text("UPDATE TaiLieu SET " + ", ".join(sets) + " WHERE DocID = :d"), params)
            if phong_ban is not None:
                set_document_departments(conn, doc_id, phong_ban)
    except Exception as e:
        logger.error(f"Loi update_document_classification doc_id={doc_id}: {e}", exc_info=True)


def mark_document_ingest_failed(file_name, thu_muc, error_message=None):
    _ensure_engine()
    try:
        with engine.begin() as conn:
            row = conn.execute(
                text("""
                    SELECT DocID
                    FROM TaiLieu
                    WHERE TenFile = :f AND ThuMuc = :t
                """),
                {"f": file_name, "t": thu_muc}
            ).fetchone()

            if not row:
                return

            doc_id = row[0]

            conn.execute(text("DELETE FROM TaiLieuKyThuat WHERE DocID = :d"), {"d": doc_id})
            conn.execute(text("DELETE FROM BangKeVatTu WHERE DocID = :d"), {"d": doc_id})
            conn.execute(text("DELETE FROM DocumentPages WHERE DocID = :d"), {"d": doc_id})
            conn.execute(text("DELETE FROM TechnicalAttributes WHERE DocID = :d"), {"d": doc_id})

            conn.execute(
                text("""
                    UPDATE TaiLieu
                    SET LifecycleStatus = 'rejected',
                        ReviewStatus = 'rejected',
                        LyDoTuChoi = :msg,
                        TrangThaiVector = 0
                    WHERE DocID = :d
                """),
                {"d": doc_id, "msg": error_message or "Ingest failed"}
            )
    except Exception as e:
        logger.error(f"Loi mark_document_ingest_failed: {e}", exc_info=True)

def reset_document_metadata(file_name, thu_muc):
    """Fix #1: GOI MOT LAN truoc khi nap file. Xoa metadata cu, tra ve DocID dung chung."""
    _ensure_engine()
    try:
        with engine.begin() as conn:
            doc_id = _get_or_create_doc(conn, file_name, thu_muc)
            if doc_id is not None:
                conn.execute(text("DELETE FROM TaiLieuKyThuat WHERE DocID = :d"), {"d": doc_id})
                conn.execute(text("DELETE FROM BangKeVatTu WHERE DocID = :d"), {"d": doc_id})
                conn.execute(text("DELETE FROM DocumentPages WHERE DocID = :d"), {"d": doc_id})
                conn.execute(text("DELETE FROM TechnicalAttributes WHERE DocID = :d"), {"d": doc_id})
            return doc_id
    except Exception as e:
        logger.error(f"Loi reset metadata {file_name}: {e}", exc_info=True)
        if isinstance(e, ValueError) and "published" in str(e):
            raise e
        return None

def get_document_info(doc_id):
    _ensure_engine()
    try:
        with engine.connect() as conn:
            row = conn.execute(text("SELECT FamilyID, BaseCode, VersionNo, VersionLabel, VariantCode, VariantGroup, LifecycleStatus, ReviewStatus, IsCurrent, IsArchived, SupersedesDocID FROM TaiLieu WHERE DocID = :d"), {"d": doc_id}).fetchone()
            if row:
                return {
                    "family_id": row[0],
                    "base_code": row[1] or "",
                    "version_no": row[2] or 1,
                    "version_label": row[3] or "",
                    "variant_code": row[4] or "default",
                    "variant_group": row[5] or "",
                    "lifecycle_status": row[6] or "draft",
                    "review_status": row[7] or "pending_review",
                    "is_current": bool(row[8]),
                    "is_archived": bool(row[9]),
                    "supersedes_doc_id": row[10],
                }
            return {}
    except Exception as e:
        logger.error(f"Loi get_document_info {doc_id}: {e}", exc_info=True)
        return {}

def _normalize_doc_type_label(raw):
    """P2: chuan hoa nhan loai tai lieu ve dang hien thi chuan (tieng Viet co dau)."""
    try:
        from mech_chatbot.ingestion.doc_type_registry import canonical_label
        label = canonical_label(raw, "vi")
        if label:
            return _sanitize_text(label, 255)
    except Exception:
        pass
    return _sanitize_text(raw, 255) or "Khong ro"


def _prepare_metadata_params(info):
    ma = info.get("ma_doi_tuong", [])
    if not isinstance(ma, list):
        ma = [str(ma)] if ma and str(ma).strip() != "Khong ro" else []
    return {
        "trang_so": _sanitize_int(info.get("trang_so"), 1),  # default=1: so trang ban ve, hop le
        "loai_tai_lieu": _normalize_doc_type_label(info.get("loai_tai_lieu")),
        "ma_doi_tuong": json.dumps(ma, ensure_ascii=False),
        "ten_sp": _sanitize_text(info.get("ten_tai_lieu"), 500),
        "cong_doan": _sanitize_text(info.get("cong_doan"), 255),
        "vat_lieu": _sanitize_text(info.get("vat_lieu"), 255),
        "so_luong": _sanitize_int(info.get("so_luong"), None),
        "nguoi_lap": _sanitize_text(info.get("nguoi_lap"), 255),
        "ngay_ve": _sanitize_date(info.get("ngay_ve")),
        "dung_sai_day": _sanitize_text(info.get("dung_sai_day"), 100),
        "dung_sai_khac": _sanitize_text(info.get("dung_sai_khac"), 100),
        "kich_thuoc": _sanitize_text(info.get("kich_thuoc"), 100),
        "hdcv": _sanitize_text(info.get("hdcv")),
        "yckt": _sanitize_text(info.get("yckt")),
    }
 
def save_document_page(doc_id, file_name, page_no, text_extract, local_ocr_text, vision_summary, local_ocr_confidence, extraction_status, image_path):
    _ensure_engine()
    try:
        with engine.begin() as conn:
            conn.execute(
                text("""
                    INSERT INTO dbo.DocumentPages (
                        DocID, FileName, PageNo, TextExtract, LocalOCRText, 
                        VisionSummary, LocalOCRConfidence, ExtractionStatus, ImagePath
                    ) VALUES (
                        :d, :f, :p, :t, :o, :v, :c, :s, :i
                    )
                """),
                {
                    "d": doc_id,
                    "f": file_name,
                    "p": page_no,
                    "t": text_extract,
                    "o": local_ocr_text,
                    "v": vision_summary,
                    "c": local_ocr_confidence,
                    "s": extraction_status,
                    "i": image_path
                }
            )
    except Exception as e:
        logger.error(f"Loi luu DocumentPages cho {file_name} trang {page_no}: {e}", exc_info=True)

def save_technical_attributes(doc_id, file_name, page_no, attributes):
    if not attributes:
        return
    _ensure_engine()
    try:
        with engine.begin() as conn:
            for attr in attributes:
                conn.execute(
                    text("""
                        INSERT INTO dbo.TechnicalAttributes (
                            DocID, FileName, PageNo, AttributeType, AttributeName, 
                            AttributeValue, Unit, SourceText, Confidence, ExtractedBy
                        ) VALUES (
                            :d, :f, :p, :at, :an, :av, :u, :st, :c, :eb
                        )
                    """),
                    {
                        "d": doc_id,
                        "f": file_name,
                        "p": page_no,
                        "at": attr.get("AttributeType", ""),
                        "an": attr.get("AttributeName", ""),
                        "av": str(attr.get("AttributeValue", ""))[:500],
                        "u": attr.get("Unit"),
                        "st": attr.get("SourceText"),
                        "c": attr.get("Confidence"),
                        "eb": attr.get("ExtractedBy")
                    }
                )
    except Exception as e:
        logger.error(f"Loi luu TechnicalAttributes cho {file_name}: {e}", exc_info=True)

def save_document_attributes(doc_id, domain, attributes):
    """Luu metadata domain phi co khi vao DocumentAttributes (ke_toan, nhan_su, chung...)."""
    if not attributes:
        return
    _ensure_engine()
    try:
        with engine.begin() as conn:
            for attr in attributes:
                conn.execute(
                    text("""
                        INSERT INTO dbo.DocumentAttributes (DocID, Domain, AttributeKey, AttributeValue, Confidence, ExtractedBy)
                        VALUES (:d, :dom, :k, :v, :c, :eb)
                    """),
                    {
                        "d": doc_id,
                        "dom": domain,
                        "k": str(attr.get("key", ""))[:150],
                        "v": str(attr.get("value", "")),
                        "c": attr.get("confidence"),
                        "eb": attr.get("extracted_by", "regex"),
                    },
                )
    except Exception as e:
        logger.error(f"Loi luu DocumentAttributes cho doc {doc_id}: {e}", exc_info=True)


def verify_technical_attribute(attr_id, verified_by, correct_value=None):
    _ensure_engine()
    try:
        with engine.begin() as conn:
            if correct_value is not None:
                conn.execute(
                    text("""
                        UPDATE dbo.TechnicalAttributes
                        SET HumanVerified = 1,
                            VerifiedBy = :v,
                            VerifiedAt = GETDATE(),
                            AttributeValue = :val
                        WHERE AttributeID = :id
                    """),
                    {"id": attr_id, "v": verified_by, "val": str(correct_value)[:500]}
                )
            else:
                conn.execute(
                    text("""
                        UPDATE dbo.TechnicalAttributes
                        SET HumanVerified = 1,
                            VerifiedBy = :v,
                            VerifiedAt = GETDATE()
                        WHERE AttributeID = :id
                    """),
                    {"id": attr_id, "v": verified_by}
                )
    except Exception as e:
        logger.error(f"Loi verify_technical_attribute: {e}", exc_info=True)

def get_technical_attributes_for_rag(file_name):
    _ensure_engine()
    try:
        with engine.connect() as conn:
            # Ưu tiên lấy dòng được human verified, sau đó mới đến confidence cao
            rows = conn.execute(
                text("""
                    SELECT AttributeType, AttributeValue, Unit, HumanVerified, ExtractedBy
                    FROM (
                        SELECT *,
                               ROW_NUMBER() OVER(
                                   PARTITION BY AttributeType 
                                   ORDER BY HumanVerified DESC, Confidence DESC
                               ) as rn
                        FROM dbo.TechnicalAttributes
                        WHERE FileName = :f
                    ) t
                    WHERE rn = 1
                """),
                {"f": file_name}
            ).fetchall()
            
            result = {}
            for r in rows:
                result[r[0]] = {
                    "value": r[1],
                    "unit": r[2],
                    "human_verified": bool(r[3]),
                    "extracted_by": r[4]
                }
            return result
    except Exception as e:
        logger.error(f"Loi get_technical_attributes_for_rag {file_name}: {e}", exc_info=True)
        return {}

def save_page_metadata(file_name, thu_muc, info, doc_id=None):
    """Fix #1: Chi INSERT 1 dong cho 1 trang. KHONG xoa du lieu trang khac."""
    _ensure_engine()
    try:
        with engine.begin() as conn:
            if doc_id is None:
                doc_id = _get_or_create_doc(conn, file_name, thu_muc)
            p = _prepare_metadata_params(info)
            p["doc_id"] = doc_id
            conn.execute(
                text(
                    """
                    INSERT INTO TaiLieuKyThuat (
                        DocID, TrangSo, LoaiTaiLieu, MaDoiTuong, TenSanPham, CongDoan,
                        VatLieu, SoLuong, NguoiLap, NgayVe, DungSaiDay, DungSaiKhac,
                        KichThuocTongThe, HDCV, YCKT
                    ) VALUES (
                        :doc_id, :trang_so, :loai_tai_lieu, :ma_doi_tuong, :ten_sp, :cong_doan,
                        :vat_lieu, :so_luong, :nguoi_lap, :ngay_ve, :dung_sai_day, :dung_sai_khac,
                        :kich_thuoc, :hdcv, :yckt
                    )
                    """
                ),
                p,
            )
            return doc_id
    except Exception as e:
        logger.error(
            f"Loi save_page_metadata {file_name} trang {info.get('trang_so')}: {e}",
            exc_info=True,
        )
        return None
 
def save_document_metadata(file_name, thu_muc, info):
    """Tuong thich nguoc cho file 1 trang (non-PDF): reset + insert mot lan."""
    doc_id = reset_document_metadata(file_name, thu_muc)
    return save_page_metadata(file_name, thu_muc, info, doc_id=doc_id)

import re

def normalize_material_name(raw):
    """P2: uy quyen cho material_registry (tu dien DB). Fallback logic cu neu loi."""
    if not raw:
        return None
    try:
        from mech_chatbot.ingestion.material_registry import normalize_material
        return normalize_material(raw)
    except Exception:
        s = str(raw).strip().lower()
        s = s.replace("inox", "stainless steel")
        s = s.replace("sus304", "sus 304")
        s = s.replace("ss304", "sus 304")
        s = re.sub(r"\s+", " ", s)
        return s

def save_bom_records(doc_id, trang_so, records):
    """Luu danh sach cac vat tu cua bang ke vao SQL"""
    if not doc_id or not records:
        return
    _ensure_engine()
    try:
        with engine.begin() as conn:
            for rec in records:
                conn.execute(
                    text("""
                        INSERT INTO BangKeVatTu (DocID, TrangSo, MaHang, TenVatTu, VatLieu, NormalizedMaterial, SoLuong, GhiChu, Unit, Confidence, RawRowJson, SourceTableIndex)
                        VALUES (:doc_id, :trang_so, :ma_hang, :ten, :vat_lieu, :normalized_material, :sl, :ghi_chu, :unit, :conf, :raw, :idx)
                    """),
                    {
                        "doc_id": doc_id,
                        "trang_so": trang_so,
                        "ma_hang": _sanitize_text(rec.get("ma_hang"), 255),
                        "ten": _sanitize_text(rec.get("ten_vat_tu"), 500),
                        "vat_lieu": _sanitize_text(rec.get("vat_lieu"), 255),
                        "normalized_material": _sanitize_text(normalize_material_name(rec.get("vat_lieu")), 255),
                        "sl": _sanitize_int(rec.get("so_luong"), None),
                        "ghi_chu": _sanitize_text(rec.get("ghi_chu"), 4000),
                        "unit": _sanitize_text(rec.get("don_vi"), 50),
                        "conf": rec.get("confidence", None),
                        "raw": rec.get("raw_row_json", None),
                        "idx": rec.get("source_table_index", None)
                    }
                )
    except Exception as e:
        logger.error(f"Loi save_bom_records cho doc_id {doc_id}, trang {trang_so}: {e}", exc_info=True)

def search_bom_by_code(
    ma_hang_list,
    version_policy="current_only",
    detected_versions=None,
    user_department=None,
    user_roles=None,
    allowed_departments=None,
    max_security_level=None,
):
    """Tim kiem bang ke vat tu tren SQL theo ma hang hoac ma doi tuong (parent assembly).

    Su dung CONTAINS() neu Full-Text Index da duoc cai dat tren BangKeVatTu,
    fallback ve LIKE '%...%' neu Full-Text Search khong kha dung.
    """
    if not ma_hang_list:
        return []
    if not user_roles:
        logger.warning("Deny SQL BOM search because user_roles is empty.")
        return []
    _ensure_engine()
    try:
        with engine.connect() as conn:
            # Kiem tra Full-Text Index co kha dung khong (1 lan, nhe)
            ft_row = conn.execute(text(
                """SELECT COUNT(1) FROM sys.fulltext_indexes fi
                   JOIN sys.objects o ON fi.object_id = o.object_id
                   WHERE o.name = 'BangKeVatTu'"""
            )).scalar()
            use_fulltext = (ft_row or 0) > 0

            # Tao dieu kien OR cho tung ma
            conditions = []
            params = {}
            for i, m in enumerate(ma_hang_list):
                if use_fulltext:
                    # CONTAINS dung double-quote de tim cum tu chinh xac hon
                    # prefix search: "ma*" khop maHang bat dau bang ma
                    params[f"m{i}"] = f'"{m}*"'
                    conditions.append(f"""
                    (
                        CONTAINS(b.MaHang, :m{i})
                        OR EXISTS (
                            SELECT 1 FROM TaiLieuKyThuat tk
                            WHERE tk.DocID = b.DocID
                            AND tk.MaDoiTuong LIKE :ml{i}
                        )
                    )
                    """)
                    params[f"ml{i}"] = f"%{m}%"   # MaDoiTuong la NVARCHAR(MAX), FT ko ho tro
                else:
                    params[f"m{i}"] = f"%{m}%"
                    conditions.append(f"""
                    (
                        b.MaHang LIKE :m{i}
                        OR EXISTS (
                            SELECT 1 FROM TaiLieuKyThuat tk
                            WHERE tk.DocID = b.DocID
                            AND tk.MaDoiTuong LIKE :m{i}
                        )
                    )
                    """)

            filter_sql = "1=1"
            if version_policy in ["current_only", "all_current_variants"]:
                filter_sql += " AND t.LifecycleStatus = 'published' AND t.ReviewStatus = 'approved' AND t.IsCurrent = 1"
            elif version_policy == "specific_version":
                filter_sql += " AND t.LifecycleStatus IN ('published', 'archived', 'superseded') AND t.ReviewStatus = 'approved'"
                if detected_versions:
                    filter_sql += f" AND t.VersionNo = {int(detected_versions[0])}"
            elif version_policy == "compare_versions":
                filter_sql += " AND t.LifecycleStatus IN ('published', 'archived', 'superseded') AND t.ReviewStatus = 'approved'"
                if detected_versions:
                    vers_str = ",".join(str(int(v)) for v in detected_versions)
                    filter_sql += f" AND t.VersionNo IN ({vers_str})"
            else:
                filter_sql += " AND t.LifecycleStatus = 'published' AND t.ReviewStatus = 'approved' AND t.IsCurrent = 1"

            # RBAC: chi dung allowed_departments tu UserDepartments, khong tu dong them department
            if not user_roles or "admin" not in user_roles:
                allowed = list(allowed_departments or [])
                if SHARE_ALL_DEPARTMENT not in allowed:
                    allowed.append(SHARE_ALL_DEPARTMENT)

                dept_conditions = []
                for i, dept in enumerate(allowed):
                    key = f"dept{i}"
                    # E1: chia se nhieu phong qua bang nhieu-nhieu dbo.PhongBanChiaSe.
                    # Khop chinh xac DeptCode (khong con substring match nhu CSV cu).
                    dept_conditions.append(
                        "(t.ThuMuc = :" + key + " OR EXISTS (SELECT 1 FROM dbo.PhongBanChiaSe pbc WHERE pbc.DocID = t.DocID AND pbc.DeptCode = :" + key + "))"
                    )
                    params[key] = dept

                filter_sql += " AND (" + " OR ".join(dept_conditions) + ")"

                # GD5 muc 1: RBAC chieu thu 2 — muc mat (security_level).
                # Truoc day duong SQL BOM CHI loc phong ban (ThuMuc) ma KHONG loc SecurityLevel
                # -> user clearance thap van moi duoc du lieu BOM tu tai lieu 'confidential'
                # qua nga SQL (trong khi nga Qdrant da chan). Dong bo logic voi _security_filter
                # / _allowed_levels ben rag/service.py: cho xem cac muc <= clearance, mac dinh 'internal'.
                _LEVEL_ORDER = {"public": 0, "internal": 1, "confidential": 2}
                _max_order = _LEVEL_ORDER.get((max_security_level or "public"), 0)
                _sec_levels = [lvl for lvl, o in _LEVEL_ORDER.items() if o <= _max_order]
                sec_conditions = []
                for i, lvl in enumerate(_sec_levels):
                    key = f"sec{i}"
                    sec_conditions.append(f"t.SecurityLevel = :{key}")
                    params[key] = lvl
                # GD5 muc 5: tai lieu THIEU muc mat coi nhu confidential. Chi cho NULL/rong khi
                # user co clearance confidential; nguoc lai an di (dong bo voi _security_filter Qdrant).
                _allow_empty_sec = "confidential" in _sec_levels
                if _allow_empty_sec:
                    filter_sql += " AND (t.SecurityLevel IS NULL OR LTRIM(RTRIM(t.SecurityLevel)) = '' OR " + " OR ".join(sec_conditions) + ")"
                else:
                    filter_sql += " AND (t.SecurityLevel IS NOT NULL AND LTRIM(RTRIM(t.SecurityLevel)) <> '' AND (" + " OR ".join(sec_conditions) + "))"

            query = text(f"""
                SELECT DISTINCT b.MaHang, b.TenVatTu, b.VatLieu, b.SoLuong, b.GhiChu, t.TenFile, t.VersionNo
                FROM BangKeVatTu b
                JOIN TaiLieu t ON b.DocID = t.DocID
                WHERE {filter_sql} AND (
                    {" OR ".join(conditions)}
                )
            """)

            result = conn.execute(query, params).fetchall()
            return result
    except Exception as e:
        logger.error(f"Loi search_bom_by_code: {e}", exc_info=True)
        return []


# ==========================================
# BACKGROUND JOBS
# ==========================================
def create_ingestion_job(file_name, file_path, thu_muc, uploaded_by=None,
                         domain=None, security_level=None, cong_doan=None,
                         site=None, phong_ban=None, upload_meta=None):
    """Tao IngestionJob. GD4: luu kem phan loai chon tu form upload
    (domain / security_level / cong_doan / site / phong_ban) de worker dung
    lam override thay vi chi suy tu folder. Cac tham so nay deu optional;
    neu None thi ingest se tu suy theo folder (backward compatible).
    PhongBan mac dinh = thu_muc neu khong truyen rieng.
    upload_meta: dict metadata nhap luc upload (common fields + domain attrs),
    luu JSON vao IngestionJobs.UploadMetaJson de worker ap xuong TaiLieu.
    """
    import json as _json
    _ensure_engine()

    # P0.2 / P4.1: Server-side guard — block phong ban disabled HOAC archived.
    # Phan biet ro "bang chua co" (legacy OK) vs "DB loi thuc su" (nen block).
    # Check Status truoc (mo hinh moi), fallback IsActive (mo hinh cu).
    try:
        with engine.connect() as _chk:
            _dept_row = _chk.execute(
                text("SELECT IsActive, Status FROM dbo.Departments WHERE DeptCode = :c"),
                {"c": thu_muc},
            ).fetchone()
            if _dept_row is not None:
                _is_active, _status = _dept_row[0], (_dept_row[1] or 'active')
                # Uu tien Status (mo hinh moi P2); fallback IsActive neu Status NULL
                _blocked = (
                    _status.lower() in ('disabled', 'archived')
                    or (not _is_active and _status.lower() not in ('active',))
                )
                if _blocked:
                    logger.warning(
                        f"[P4.1] Blocked create_ingestion_job: phong ban '{thu_muc}'"
                        f" Status='{_status}' IsActive={_is_active}."
                        f" file='{file_name}' uploaded_by='{uploaded_by}'"
                    )
                    return None
    except Exception as _chk_err:
        import sqlalchemy.exc as _sa_exc
        if isinstance(_chk_err, (_sa_exc.ProgrammingError, _sa_exc.OperationalError)):
            # Bang Departments chua ton tai (DB legacy) -> fallback an toan, cho qua
            logger.debug(f"[P4.1] Bang Departments chua co, bo qua check cho '{thu_muc}'")
        else:
            # Loi DB thuc su -> block de tranh tao job vao phong loi
            logger.error(f"[P4.1] Loi DB khi kiem tra phong ban '{thu_muc}': {_chk_err}")
            return None

    _upload_meta_json = (_json.dumps(upload_meta, ensure_ascii=False) if upload_meta else None)
    try:
        with engine.begin() as conn:
            result = conn.execute(
                text(
                    """
                    INSERT INTO dbo.IngestionJobs
                        (TenFile, FilePath, ThuMuc, Status, UploadedBy,
                         Domain, SecurityLevel, PhongBan, CongDoan, Site, UploadMetaJson)
                    OUTPUT INSERTED.JobID
                    VALUES (:f, :p, :t, 'pending', :u,
                            :dom, :sec, :pb, :cd, :site, :upload_meta_json)
                    """
                ),
                {
                    "f": file_name, "p": file_path, "t": thu_muc, "u": uploaded_by,
                    "dom": domain, "sec": security_level,
                    "pb": (",".join(str(x).strip() for x in phong_ban if str(x).strip()) if isinstance(phong_ban, (list, tuple, set)) else phong_ban) or thu_muc, "cd": cong_doan, "site": site,
                    "upload_meta_json": _upload_meta_json,
                }
            )
            row = result.fetchone()
            job_id = row[0] if row else None
            if job_id:
                write_audit_log(uploaded_by or "System", "upload", "IngestionJobs", job_id, {
                    "file_name": file_name, "thu_muc": thu_muc,
                    "domain": domain, "security_level": security_level,
                    "cong_doan": cong_doan, "site": site, "phong_ban": phong_ban or thu_muc,
                })
            return job_id
    except Exception as e:
        logger.error(f"Loi tao IngestionJob: {e}", exc_info=True)
        return None

def update_ingestion_job(job_id, status, error_message=None):
    _ensure_engine()
    try:
        with engine.begin() as conn:
            conn.execute(
                text(
                    """
                    UPDATE dbo.IngestionJobs
                    SET Status = :s,
                        ErrorMessage = :e,
                        UpdatedAt = GETDATE()
                    WHERE JobID = :id
                    """
                ),
                {"s": status, "e": error_message, "id": job_id}
            )
    except Exception as e:
        logger.error(f"Loi cap nhat IngestionJob {job_id}: {e}", exc_info=True)

def update_ingestion_report(job_id, report):
    _ensure_engine()
    try:
        import json
        with engine.begin() as conn:
            conn.execute(text("""
                UPDATE dbo.IngestionJobs
                SET ExtractionReport = :report,
                    QualityScore = :score,
                    QualityStatus = :status,
                    UpdatedAt = GETDATE()
                WHERE JobID = :id
            """), {
                "id": job_id,
                "report": json.dumps(report, ensure_ascii=False),
                "score": report.get("quality_score"),
                "status": report.get("quality_status")
            })
        return True
    except Exception as e:
        logger.error(f"Loi cap nhat report cho job {job_id}: {e}", exc_info=True)
        return False
def get_pending_job(worker_id="worker-1"):
    _ensure_engine()
    try:
        with engine.begin() as conn:
            # Atomic picking:
            # - READPAST: bỏ qua job đang bị worker khác lock
            # - UPDLOCK: giữ update lock cho dòng được chọn
            # - ROWLOCK: ưu tiên lock cấp dòng
            result = conn.execute(
                text(
                    """
                    WITH CTE AS (
                        SELECT TOP 1
                            JobID,
                            TenFile,
                            FilePath,
                            ThuMuc,
                            Status,
                            RetryCount,
                            MaxRetry,
                            LockedBy,
                            LockedAt,
                            ProgressPercent,
                            Domain,
                            SecurityLevel,
                            PhongBan,
                            CongDoan,
                            Site,
                            CreatedAt,
                            UpdatedAt
                        FROM dbo.IngestionJobs WITH (READPAST, UPDLOCK, ROWLOCK)
                        WHERE (
                            (
                                Status IN ('pending', 'pending_retry')
                                AND (
                                    LockedAt IS NULL
                                    OR LockedAt < DATEADD(minute, -15, GETDATE())
                                )
                                AND ISNULL(RetryCount, 0) < ISNULL(MaxRetry, 3)
                            )
                            OR (
                                Status = 'waiting_quota'
                                AND NextRetryAt IS NOT NULL
                                AND NextRetryAt <= GETDATE()
                            )
                            OR (
                                Status IN ('classifying', 'extracting', 'embedding')
                                AND LockedAt < DATEADD(minute, -15, GETDATE())
                                AND ISNULL(RetryCount, 0) < ISNULL(MaxRetry, 3)
                            )
                        )
                        -- P1.5: uu tien theo Priority (nho hon = uu tien hon), roi FIFO theo CreatedAt
                        ORDER BY ISNULL(Priority, 100) ASC, CreatedAt ASC
                    )
                    UPDATE CTE
                    SET Status = 'classifying',
                        LockedBy = :worker_id,
                        LockedAt = GETDATE(),
                        ProgressPercent = 5,
                        UpdatedAt = GETDATE()
                    OUTPUT
                        inserted.JobID,
                        inserted.TenFile,
                        inserted.FilePath,
                        inserted.ThuMuc,
                        inserted.Domain,
                        inserted.SecurityLevel,
                        inserted.PhongBan,
                        inserted.CongDoan,
                        inserted.Site;
                    """
                ),
                {"worker_id": worker_id}
            )

            row = result.fetchone()

            if row:
                return {
                    "job_id": row[0],
                    "ten_file": row[1],
                    "file_path": row[2],
                    "thu_muc": row[3],
                    "domain": row[4],
                    "security_level": row[5],
                    "phong_ban": row[6],
                    "cong_doan": row[7],
                    "site": row[8],
                }

            return None

    except Exception as e:
        logger.error(f"Loi lay pending job: {e}", exc_info=True)
        return None

def mark_job_failed(job_id, error_message):
    _ensure_engine()
    lower_msg = str(error_message).lower()
    if (
        "[quota_exceeded]" in lower_msg
        or "quota exceeded" in lower_msg
        or "resource_exhausted" in lower_msg
        or "free_tier_requests" in lower_msg
    ):
        return mark_job_waiting_quota(job_id, error_message)
    try:
        with engine.begin() as conn:
            conn.execute(text("""
                UPDATE dbo.IngestionJobs
                SET RetryCount = ISNULL(RetryCount, 0) + 1,
                    Status = CASE 
                        WHEN :e LIKE '%[AUTH_ERROR]%' THEN 'failed'
                        WHEN ISNULL(RetryCount, 0) + 1 >= ISNULL(MaxRetry, 3) THEN 'failed'
                        ELSE 'pending_retry'
                    END,
                    ErrorMessage = :e,
                    LockedBy = NULL,
                    LockedAt = NULL,
                    ProgressPercent = 0,
                    UpdatedAt = GETDATE()
                WHERE JobID = :id
            """), {"id": job_id, "e": error_message})
    except Exception as e:
        logger.error(f"Loi danh dau job fail {job_id}: {e}", exc_info=True)

def mark_job_waiting_quota(job_id, error_message, retry_after_hours=24):
    _ensure_engine()
    try:
        with engine.begin() as conn:
            conn.execute(text("""
                UPDATE dbo.IngestionJobs
                SET Status = 'waiting_quota',
                    FailureType = 'gemini_quota',
                    ErrorMessage = :e,
                    NextRetryAt = DATEADD(hour, :h, GETDATE()),
                    LockedBy = NULL,
                    LockedAt = NULL,
                    ProgressPercent = 0,
                    UpdatedAt = GETDATE()
                WHERE JobID = :id
            """), {
                "id": job_id,
                "e": error_message,
                "h": retry_after_hours
            })
    except Exception as e:
        logger.error(f"Loi danh dau waiting_quota job {job_id}: {e}", exc_info=True)

# ==========================================
# PHAN QUAN LY VONG DOI & REVIEW (PHASE 3)
# ==========================================

def write_audit_log(username, action, entity_type=None, entity_id=None, details=None, user_id=None):
    _ensure_engine()
    import json
    try:
        with engine.begin() as conn:
            conn.execute(text("""
                INSERT INTO AuditLog (UserID, Username, Action, EntityType, EntityID, Details)
                VALUES (:uid, :username, :action, :etype, :eid, :details)
            """), {
                "uid": user_id,
                "username": username,
                "action": action,
                "etype": entity_type,
                "eid": entity_id,
                "details": json.dumps(details or {}, ensure_ascii=False)
            })
    except Exception as e:
        logger.error(f"Loi write_audit_log: {e}", exc_info=True)

_qdrant_client_singleton = None

def _get_qdrant_client():
    """QdrantClient nhẹ, KHÔNG nạp model RAG (torch/onnxruntime) vào tiến tr��nh hiện tại.
    Dùng cho thao tác admin (publish/reject/archive) gọi từ Streamlit để tránh crash native."""
    global _qdrant_client_singleton
    if _qdrant_client_singleton is None:
        from qdrant_client import QdrantClient
        _qdrant_client_singleton = QdrantClient(
            url=os.getenv("QDRANT_URL"),
            api_key=os.getenv("QDRANT_API_KEY"),
            timeout=120,
        )
    return _qdrant_client_singleton

def update_qdrant_metadata(doc_id, metadata_updates):
    """Cap nhat payload metadata cho tat ca Qdrant points cua doc_id.

    Dung cursor pagination thay vi limit=10000 co dinh de xu ly tai lieu
    co so luong chunk tuy y (> 10k trang).
    """
    from qdrant_client import models
    client = _get_qdrant_client()
    BATCH = 500   # so points lay moi lan scroll
    try:
        total_updated = 0
        next_offset = None
        found_any = False

        while True:
            scroll_res = client.scroll(
                collection_name=QDRANT_COLLECTION,
                scroll_filter=models.Filter(
                    must=[models.FieldCondition(key="metadata.doc_id", match=models.MatchValue(value=doc_id))]
                ),
                limit=BATCH,
                offset=next_offset,
                with_payload=True,
            )
            points, next_offset = scroll_res

            if not points:
                break  # het du lieu hoac khong co points nao

            found_any = True
            ids_to_update = []
            for p in points:
                meta = p.payload.get("metadata", {}) if p.payload else {}
                meta.update(metadata_updates)
                # Batch set_payload theo tung point (Qdrant chua ho tro batch update payload)
                client.set_payload(
                    collection_name=QDRANT_COLLECTION,
                    payload={"metadata": meta},
                    points=[p.id],
                )
                ids_to_update.append(p.id)

            total_updated += len(ids_to_update)

            if next_offset is None:
                break  # het trang

        if not found_any:
            logger.warning(
                f"update_qdrant_metadata: khong co Qdrant points cho DocID {doc_id}. "
                "Tai lieu co the chua embed hoac da bi xoa truoc do."
            )
            return True  # giu True de khong lam gay publish/reject flow

        logger.info(f"Updated Qdrant payload cho {total_updated} chunks cua DocID {doc_id}")
        return True
    except Exception as e:
        logger.error(f"Loi update Qdrant payload cho DocID {doc_id}: {e}", exc_info=True)
        return False

def update_document_full_metadata(doc_id, base_code=None, version_no=None, version_label=None,
                                  variant_code=None, variant_group=None, loai_tai_lieu=None,
                                  domain=None, security_level=None, site=None, cong_doan=None,
                                  reviewer="System"):
    """Cap nhat lai 'ma ban ve'/version/variant cho 1 tai lieu da duyet/tu choi.
    Dong bo ca SQL (TaiLieu + TaiLieuKyThuat + FamilyID) lan Qdrant payload."""
    _ensure_engine()
    norm_base = normalize_base_code(base_code) if base_code else base_code
    with engine.begin() as conn:
        conn.execute(text("""
            UPDATE TaiLieu
            SET BaseCode = :bc, VersionNo = :vn, VersionLabel = :vl,
                VariantCode = :vc, VariantGroup = :vg
            WHERE DocID = :id
        """), {"bc": norm_base, "vn": version_no, "vl": version_label,
               "vc": variant_code, "vg": variant_group, "id": doc_id})
        # GD4b: cho phep chinh phan loai linh hoat da phong ban (chi cap nhat khi co gia tri)
        if domain is not None:
            conn.execute(text("UPDATE TaiLieu SET Domain = :d WHERE DocID = :id"), {"d": domain, "id": doc_id})
        if security_level is not None:
            conn.execute(text("UPDATE TaiLieu SET SecurityLevel = :s WHERE DocID = :id"), {"s": security_level, "id": doc_id})
        if site is not None:
            conn.execute(text("UPDATE TaiLieu SET Site = :st WHERE DocID = :id"), {"st": (site or None), "id": doc_id})
        if loai_tai_lieu is not None:
            loai_tai_lieu = _normalize_doc_type_label(loai_tai_lieu)
            conn.execute(text("UPDATE TaiLieuKyThuat SET LoaiTaiLieu = :l WHERE DocID = :id"),
                         {"l": loai_tai_lieu, "id": doc_id})
        if norm_base:
            f_row = conn.execute(text("SELECT FamilyID FROM DocumentFamily WHERE BaseCode = :b"), {"b": norm_base}).fetchone()
            if f_row:
                conn.execute(text("UPDATE TaiLieu SET FamilyID = :fid WHERE DocID = :id"), {"fid": f_row[0], "id": doc_id})
            else:
                conn.execute(text("INSERT INTO DocumentFamily (BaseCode, FamilyName) VALUES (:b, :n)"), {"b": norm_base, "n": f"Family {norm_base}"})
                f_row2 = conn.execute(text("SELECT FamilyID FROM DocumentFamily WHERE BaseCode = :b"), {"b": norm_base}).fetchone()
                conn.execute(text("UPDATE TaiLieu SET FamilyID = :fid WHERE DocID = :id"), {"fid": f_row2[0], "id": doc_id})

    qmeta = {}
    if norm_base is not None: qmeta["base_code"] = norm_base
    if version_no is not None: qmeta["version_no"] = version_no
    if variant_code is not None: qmeta["variant_code"] = variant_code
    if variant_group is not None: qmeta["variant_group"] = variant_group
    if loai_tai_lieu is not None: qmeta["loai_tai_lieu"] = loai_tai_lieu
    if domain is not None: qmeta["domain"] = domain
    if security_level is not None: qmeta["security_level"] = security_level
    if site is not None: qmeta["site"] = (site or None)
    if cong_doan is not None: qmeta["cong_doan"] = (cong_doan or None)

    ok = update_qdrant_metadata(doc_id, qmeta) if qmeta else True
    # P3-2: metadata da doi -> feedback cu cua tai lieu nay tro thanh stale
    mark_feedback_stale_for_doc(doc_id, resolved_by_doc_id=doc_id)
    write_audit_log(reviewer, "update_metadata", "TaiLieu", doc_id,
                    {"base_code": norm_base, "version": version_no, "variant": variant_code})
    return ok


def delete_document_completely(doc_id, reviewer="System"):
    """Xoa VINH VIEN toan bo du lieu cua 1 tai lieu (safe 3-buoc).

    Quy trinh an toan de tranh tai lieu 'ma':
      1. SQL soft-delete: danh dau LifecycleStatus='deleting', IsCurrent=0
         (tai lieu bi an khoi RAG nhung SQL van con — co the rollback)
      2. Xoa vector Qdrant
         - Neu Qdrant loi -> rollback buoc 1 (khoi phuc LifecycleStatus + IsCurrent)
      3. SQL hard-delete (xoa han row)
         - Neu loi o buoc nay: vector da mat, SQL con trang thai 'deleting'
           -> khong xuat hien trong RAG, co the retry delete_document_completely() an toan.
    """
    _ensure_engine()

    # Doc thong tin + trang thai hien tai (can de rollback)
    with engine.connect() as conn:
        row = conn.execute(
            text("SELECT TenFile, ThuMuc, LifecycleStatus FROM TaiLieu WHERE DocID = :id"),
            {"id": doc_id}
        ).fetchone()

    if not row:
        logger.warning(f"delete_document_completely: khong tim thay DocID {doc_id}")
        return False

    ten_file, thu_muc, prev_status = row[0], row[1], row[2]

    # ------------------------------------------------------------------
    # Buoc 1: SQL soft-delete — danh dau 'deleting'
    # ------------------------------------------------------------------
    try:
        with engine.begin() as conn:
            conn.execute(
                text("UPDATE TaiLieu SET LifecycleStatus = 'deleting', IsCurrent = 0 WHERE DocID = :id"),
                {"id": doc_id}
            )
        logger.info(f"[delete] 1/3 OK — soft-delete DocID {doc_id} (prev: {prev_status})")
    except Exception as e:
        logger.error(f"[delete] 1/3 THAT BAI (soft-delete) DocID {doc_id}: {e}", exc_info=True)
        return False

    # ------------------------------------------------------------------
    # Buoc 2: Xoa vector Qdrant
    # Neu loi -> rollback buoc 1
    # ------------------------------------------------------------------
    try:
        from qdrant_client import models
        client = _get_qdrant_client()
        client.delete(
            collection_name=QDRANT_COLLECTION,
            points_selector=models.FilterSelector(
                filter=models.Filter(
                    must=[models.FieldCondition(key="metadata.doc_id", match=models.MatchValue(value=doc_id))]
                )
            )
        )
        logger.info(f"[delete] 2/3 OK — da xoa Qdrant points cua DocID {doc_id}")
    except Exception as e:
        logger.error(
            f"[delete] 2/3 THAT BAI (Qdrant) DocID {doc_id}: {e}. Dang rollback soft-delete.",
            exc_info=True
        )
        try:
            with engine.begin() as conn:
                conn.execute(
                    text("UPDATE TaiLieu SET LifecycleStatus = :s, IsCurrent = 1 WHERE DocID = :id"),
                    {"s": prev_status or "published", "id": doc_id}
                )
            logger.info(f"[delete] Rollback OK — DocID {doc_id} khoi phuc ve '{prev_status}'")
        except Exception as rb_err:
            logger.error(
                f"[delete] ROLLBACK THAT BAI DocID {doc_id}: {rb_err}. "
                "Tai lieu bi ket o trang thai 'deleting' — can xu ly thu cong!",
                exc_info=True
            )
        return False

    # ------------------------------------------------------------------
    # Buoc 3: SQL hard-delete
    # Neu loi: vector da mat, SQL con trang thai 'deleting' (an khoi RAG)
    # -> co the retry bang cach goi lai ham nay
    # ------------------------------------------------------------------
    try:
        with engine.begin() as conn:
            conn.execute(text("DELETE FROM DocumentPages       WHERE DocID = :id"), {"id": doc_id})
            conn.execute(text("DELETE FROM TechnicalAttributes WHERE DocID = :id"), {"id": doc_id})
            # TaiLieuKyThuat + BangKeVatTu tu dong xoa theo ON DELETE CASCADE
            conn.execute(text("DELETE FROM TaiLieu             WHERE DocID = :id"), {"id": doc_id})
            if ten_file and thu_muc:
                conn.execute(
                    text("DELETE FROM dbo.IngestionJobs WHERE TenFile = :f AND ThuMuc = :t"),
                    {"f": ten_file, "t": thu_muc}
                )
        logger.info(f"[delete] 3/3 OK — hard-delete SQL DocID {doc_id} ({ten_file})")
    except Exception as e:
        logger.error(
            f"[delete] 3/3 THAT BAI (SQL hard-delete) DocID {doc_id}: {e}. "
            "Vector Qdrant DA XOA. Ban ghi SQL con trang thai 'deleting'. "
            f"Retry: delete_document_completely({doc_id})",
            exc_info=True
        )
        return False

    write_audit_log(reviewer, "delete_document", "TaiLieu", doc_id, {"ten_file": ten_file, "thu_muc": thu_muc})
    return True

def get_doc(doc_id):
    _ensure_engine()
    with engine.connect() as conn:
        row = conn.execute(text("SELECT DocID, FamilyID, BaseCode, VersionNo, VariantCode, LifecycleStatus, IsCurrent FROM TaiLieu WHERE DocID = :d"), {"d": doc_id}).fetchone()
        if row:
            return type('Doc', (object,), {
                'DocID': row[0], 'FamilyID': row[1], 'BaseCode': row[2], 
                'VersionNo': row[3], 'VariantCode': row[4],
                'LifecycleStatus': row[5], 'IsCurrent': bool(row[6])
            })
    return None

def find_current_docs(base_code, variant_code=None):
    _ensure_engine()
    query = "SELECT DocID FROM TaiLieu WHERE BaseCode = :b AND IsCurrent = 1 AND LifecycleStatus = 'published'"
    params = {"b": base_code}
    if variant_code:
        query += " AND VariantCode = :v"
        params["v"] = variant_code
        
    with engine.connect() as conn:
        rows = conn.execute(text(query), params).fetchall()
        return [get_doc(r[0]) for r in rows]

def publish_as_new_version(doc_id, reviewer="System"):
    doc = get_doc(doc_id)
    if not doc: return False
    
    old_docs = find_current_docs(base_code=doc.BaseCode, variant_code=doc.VariantCode)
    old_id = old_docs[0].DocID if old_docs else None
    
    with engine.begin() as conn:
        for old in old_docs:
            conn.execute(text("""
                UPDATE TaiLieu SET IsCurrent = 0, IsArchived = 1, LifecycleStatus = 'superseded', ArchivedAt = GETDATE() WHERE DocID = :id
            """), {"id": old.DocID})
            ok_old = update_qdrant_metadata(old.DocID, {
                "is_current": False,
                "is_archived": True,
                "lifecycle_status": "superseded"
            })
            if not ok_old:
                raise RuntimeError(f"Update Qdrant metadata that bai cho old DocID {old.DocID}")
            
        conn.execute(text("""
            UPDATE TaiLieu SET IsCurrent = 1, IsArchived = 0, LifecycleStatus = 'published', ReviewStatus = 'approved',
                PublishedAt = GETDATE(), NgayDuyet = GETDATE(), NguoiDuyet = :rev, ReviewedBy = :rev,
                SupersedesDocID = :old_id, TrangThai = 'published'
            WHERE DocID = :id
        """), {"id": doc.DocID, "rev": reviewer, "old_id": old_id})
        
        ok_new = update_qdrant_metadata(doc.DocID, {
            "doc_status": "published",
            "lifecycle_status": "published",
            "review_status": "approved",
            "is_current": True,
            "is_archived": False,
            "published_at": datetime.now().isoformat(),
            "supersedes_doc_id": old_id
        })
        if not ok_new:
            raise RuntimeError(f"Update Qdrant metadata that bai cho DocID {doc.DocID}")
        
    # P3-2: tai lieu cu da bi thay the -> feedback dislike cu cua chung tro thanh stale
    for _old in old_docs:
        mark_feedback_stale_for_doc(_old.DocID, resolved_by_doc_id=doc.DocID)
    write_audit_log(reviewer, "publish_new_version", "TaiLieu", doc.DocID, {"base_code": doc.BaseCode, "version": doc.VersionNo, "superseded": old_id})
    return True

def publish_as_new_variant(doc_id, reviewer="System"):
    doc = get_doc(doc_id)
    if not doc: return False
    
    with engine.begin() as conn:
        conn.execute(text("""
            UPDATE TaiLieu SET IsCurrent = 1, IsArchived = 0, LifecycleStatus = 'published', ReviewStatus = 'approved',
                PublishedAt = GETDATE(), NgayDuyet = GETDATE(), NguoiDuyet = :rev, ReviewedBy = :rev, TrangThai = 'published'
            WHERE DocID = :id
        """), {"id": doc.DocID, "rev": reviewer})
        
        ok_var = update_qdrant_metadata(doc.DocID, {
            "doc_status": "published",
            "lifecycle_status": "published",
            "review_status": "approved",
            "is_current": True,
            "is_archived": False,
            "published_at": datetime.now().isoformat()
        })
        if not ok_var:
            raise RuntimeError(f"Update Qdrant metadata that bai cho DocID {doc.DocID}")
        
    write_audit_log(reviewer, "publish_variant", "TaiLieu", doc.DocID, {"base_code": doc.BaseCode, "variant": doc.VariantCode})
    return True

def publish_as_standalone(doc_id, reviewer="System"):
    return publish_as_new_variant(doc_id, reviewer=reviewer)

def reject_document(doc_id, reviewer="System"):
    with engine.begin() as conn:
        conn.execute(text("""
            UPDATE TaiLieu SET LifecycleStatus = 'rejected', ReviewStatus = 'rejected', NguoiDuyet = :rev, ReviewedBy = :rev
            WHERE DocID = :id
        """), {"id": doc_id, "rev": reviewer})
        
        ok_rej = update_qdrant_metadata(doc_id, {
            "lifecycle_status": "rejected",
            "review_status": "rejected"
        })
        if not ok_rej:
            raise RuntimeError(f"Update Qdrant metadata that bai cho DocID {doc_id}")
        
    write_audit_log(reviewer, "reject_document", "TaiLieu", doc_id, {})
    return True

def archive_document(doc_id, reviewer="System"):
    with engine.begin() as conn:
        conn.execute(text("""
            UPDATE TaiLieu SET IsCurrent = 0, IsArchived = 1, LifecycleStatus = 'archived', ArchivedAt = GETDATE()
            WHERE DocID = :id
        """), {"id": doc_id})
        
        ok_arch = update_qdrant_metadata(doc_id, {
            "is_current": False,
            "is_archived": True,
            "lifecycle_status": "archived"
        })
        if not ok_arch:
            raise RuntimeError(f"Update Qdrant metadata that bai cho DocID {doc_id}")
        
    write_audit_log(reviewer, "archive_document", "TaiLieu", doc_id, {})
    return True

def rollback_to_version(base_code, version_no, variant_code="default", reviewer="System"):
    """
    [DEPRECATED] Chuyen sang dung rollback_to_version_by_family.
    Wrapper tam thoi de giu tuong thich nguoc.
    """
    logger.warning("rollback_to_version (BaseCode) is deprecated. Use rollback_to_version_by_family instead.")
    _ensure_engine()
    try:
        with engine.connect() as conn:
            row = conn.execute(text("SELECT TOP 1 FamilyID FROM TaiLieu WHERE BaseCode = :bc"), {"bc": base_code}).fetchone()
            if not row or not row[0]:
                logger.error(f"Cannot find FamilyID for BaseCode {base_code}")
                return False
            return rollback_to_version_by_family(row[0], version_no, variant_code, reviewer)
    except Exception as e:
        logger.error(f"Loi wrapper rollback: {e}")
        return False

def rollback_to_version_by_family(family_id, version_no, variant_code="default", reviewer="System"):
    _ensure_engine()
    try:
        with engine.begin() as conn:
            current_rows = conn.execute(text("""
                SELECT DocID FROM TaiLieu
                WHERE FamilyID = :fid
                  AND VariantCode = :vc
                  AND IsCurrent = 1
                  AND LifecycleStatus = 'published'
            """), {
                "fid": family_id,
                "vc": variant_code
            }).fetchall()

            for row in current_rows:
                old_doc_id = row[0]
                conn.execute(text("""
                    UPDATE TaiLieu
                    SET IsCurrent = 0,
                        IsArchived = 1,
                        LifecycleStatus = 'superseded',
                        ArchivedAt = GETDATE()
                    WHERE DocID = :id
                """), {"id": old_doc_id})

                ok_rb_old = update_qdrant_metadata(old_doc_id, {
                    "is_current": False,
                    "is_archived": True,
                    "lifecycle_status": "superseded"
                })
                if not ok_rb_old:
                    raise RuntimeError(f"Update Qdrant metadata that bai cho DocID {old_doc_id}")

            target = conn.execute(text("""
                SELECT DocID FROM TaiLieu
                WHERE FamilyID = :fid
                  AND VariantCode = :vc
                  AND VersionNo = :vn
            """), {
                "fid": family_id,
                "vc": variant_code,
                "vn": version_no
            }).fetchone()

            if not target:
                return False

            target_doc_id = target[0]

            conn.execute(text("""
                UPDATE TaiLieu
                SET IsCurrent = 1,
                    IsArchived = 0,
                    LifecycleStatus = 'published',
                    ReviewStatus = 'approved',
                    ReviewedBy = :rev,
                    NguoiDuyet = :rev,
                    PublishedAt = GETDATE()
                WHERE DocID = :id
            """), {
                "id": target_doc_id,
                "rev": reviewer
            })

            ok_rb_new = update_qdrant_metadata(target_doc_id, {
                "is_current": True,
                "is_archived": False,
                "lifecycle_status": "published",
                "review_status": "approved"
            })
            if not ok_rb_new:
                raise RuntimeError(f"Update Qdrant metadata that bai cho DocID {target_doc_id}")

            write_audit_log(reviewer, "rollback", "TaiLieu", target_doc_id, {"family_id": family_id, "target_version": version_no})
            return True

    except Exception as e:
        logger.error(f"Loi rollback_to_version_by_family: {e}", exc_info=True)
        return False



# =====================================================================
# P1 HELPERS — quan ly phong ban/site dong, RBAC site, hang doi, dashboard
# =====================================================================

def _resolve_site(thu_muc):
    """Xac dinh site code cho mot phong ban (uu tien Departments.Site neu co).
    An toan: moi loi deu fallback ve mapping mac dinh / 'HQ'."""
    try:
        from mech_chatbot.ingestion.site_registry import resolve_site_by_department
        db_site = None
        try:
            with engine.connect() as conn:
                r = conn.execute(text("SELECT Site FROM dbo.Departments WHERE DeptCode = :c"), {"c": thu_muc}).fetchone()
                db_site = r[0] if r else None
        except Exception:
            db_site = None
        return resolve_site_by_department(thu_muc, db_site=db_site)
    except Exception:
        return "HQ"


def _departments_support_status():
    _ensure_engine()
    try:
        with engine.connect() as conn:
            row = conn.execute(text("""
                SELECT 1
                FROM sys.columns
                WHERE object_id = OBJECT_ID('dbo.Departments')
                  AND name = 'Status'
            """)).fetchone()
            return bool(row)
    except Exception:
        return False


def _normalize_department_status(status=None, is_active=True):
    st = str(status or "").strip().lower()
    if st in ("active", "disabled", "archived"):
        return st
    return "active" if is_active else "disabled"


def _split_csv_tokens(value):
    out = []
    if value is None:
        return out
    values = value if isinstance(value, (list, tuple, set)) else [value]
    for raw in values:
        for part in str(raw).split(","):
            p = part.strip()
            if p and p not in out:
                out.append(p)
    return out


def _replace_department_token_list(value, old_code, new_code=None):
    old_code = str(old_code or "").strip()
    new_code = str(new_code or "").strip() or None
    out = []
    for token in _split_csv_tokens(value):
        if token == old_code:
            if new_code and new_code not in out:
                out.append(new_code)
        elif token not in out:
            out.append(token)
    return ",".join(out) if out else None


def list_known_departments(active_only=True):
    """Danh muc phong ban (bang Departments). Tra ve list dict.

    Backward compatible:
    - DB cu: chi co IsActive -> map sang status active/disabled.
    - DB moi: uu tien cot Status, nhung van giu IsActive de code cu tiep tuc chay.
    """
    _ensure_engine()
    supports_status = _departments_support_status()
    if supports_status:
        where = "WHERE Status = 'active'" if active_only else ""
        sql = f"""
            SELECT DeptCode, DeptName, Domain, Site, IsActive, Status, DisabledAt, ArchivedAt
            FROM dbo.Departments {where}
            ORDER BY DeptCode
        """
    else:
        where = "WHERE IsActive = 1" if active_only else ""
        sql = f"""
            SELECT DeptCode, DeptName, Domain, Site, IsActive,
                   CASE WHEN IsActive = 1 THEN 'active' ELSE 'disabled' END AS Status,
                   CAST(NULL AS DATETIME) AS DisabledAt,
                   CAST(NULL AS DATETIME) AS ArchivedAt
            FROM dbo.Departments {where}
            ORDER BY DeptCode
        """
    try:
        with engine.connect() as conn:
            rows = conn.execute(text(sql)).fetchall()
        return [
            {
                "code": r[0],
                "name": r[1],
                "domain": r[2],
                "site": r[3],
                "is_active": (str(r[5]).lower() == "active") if r[5] is not None else bool(r[4]),
                "status": (str(r[5]).lower() if r[5] else ("active" if r[4] else "disabled")),
                "disabled_at": r[6],
                "archived_at": r[7],
            }
            for r in rows
        ]
    except Exception as e:
        logger.error(f"list_known_departments loi: {e}", exc_info=True)
        return []


def upsert_department(code, name=None, domain=None, site=None, is_active=True, status=None):
    """Them moi hoac cap nhat 1 phong ban (idempotent theo DeptCode).

    status uu tien hon is_active. Luon dong bo ca Status va IsActive de backward-compatible.
    """
    _ensure_engine()
    if not code:
        return False
    resolved_status = _normalize_department_status(status=status, is_active=is_active)
    resolved_is_active = 1 if resolved_status == "active" else 0
    supports_status = _departments_support_status()
    try:
        with engine.begin() as conn:
            if supports_status:
                conn.execute(text("""
                    MERGE dbo.Departments AS tgt
                    USING (SELECT :c AS DeptCode) AS src ON tgt.DeptCode = src.DeptCode
                    WHEN MATCHED THEN UPDATE SET
                        DeptName = :n,
                        Domain = :d,
                        Site = :site,
                        IsActive = :a,
                        Status = :st,
                        DisabledAt = CASE
                            WHEN :st = 'disabled' AND (tgt.Status IS NULL OR tgt.Status <> 'disabled') THEN GETDATE()
                            WHEN :st <> 'disabled' THEN NULL
                            ELSE tgt.DisabledAt
                        END,
                        ArchivedAt = CASE
                            WHEN :st = 'archived' AND (tgt.Status IS NULL OR tgt.Status <> 'archived') THEN GETDATE()
                            WHEN :st <> 'archived' THEN NULL
                            ELSE tgt.ArchivedAt
                        END
                    WHEN NOT MATCHED THEN INSERT (DeptCode, DeptName, Domain, Site, IsActive, Status, DisabledAt, ArchivedAt)
                        VALUES (
                            :c, :n, :d, :site, :a, :st,
                            CASE WHEN :st = 'disabled' THEN GETDATE() ELSE NULL END,
                            CASE WHEN :st = 'archived' THEN GETDATE() ELSE NULL END
                        );
                """), {"c": code, "n": name, "d": domain, "site": site, "a": resolved_is_active, "st": resolved_status})
            else:
                conn.execute(text("""
                    MERGE dbo.Departments AS tgt
                    USING (SELECT :c AS DeptCode) AS src ON tgt.DeptCode = src.DeptCode
                    WHEN MATCHED THEN UPDATE SET DeptName = :n, Domain = :d, Site = :site, IsActive = :a
                    WHEN NOT MATCHED THEN INSERT (DeptCode, DeptName, Domain, Site, IsActive)
                        VALUES (:c, :n, :d, :site, :a);
                """), {"c": code, "n": name, "d": domain, "site": site, "a": resolved_is_active})
        return True
    except Exception as e:
        logger.error(f"upsert_department loi: {e}", exc_info=True)
        return False


def get_department_summary(code):
    """Thong ke nhanh 1 phong ban phuc vu disable/archive/reassign UI."""
    _ensure_engine()
    if not code:
        return None
    supports_status = _departments_support_status()
    try:
        with engine.connect() as conn:
            if supports_status:
                dept = conn.execute(text("""
                    SELECT DeptCode, DeptName, Domain, Site, IsActive, Status, DisabledAt, ArchivedAt
                    FROM dbo.Departments WHERE DeptCode = :c
                """), {"c": code}).fetchone()
            else:
                dept = conn.execute(text("""
                    SELECT DeptCode, DeptName, Domain, Site, IsActive,
                           CASE WHEN IsActive = 1 THEN 'active' ELSE 'disabled' END AS Status,
                           CAST(NULL AS DATETIME) AS DisabledAt,
                           CAST(NULL AS DATETIME) AS ArchivedAt
                    FROM dbo.Departments WHERE DeptCode = :c
                """), {"c": code}).fetchone()
            if not dept:
                return None
            users = conn.execute(text("SELECT COUNT(*) FROM dbo.UserDepartments WHERE Department = :c"), {"c": code}).fetchone()
            jobs = conn.execute(text("""
                SELECT COUNT(*) FROM dbo.IngestionJobs
                WHERE ThuMuc = :c
                  AND Status IN ('pending', 'pending_retry', 'pending_review', 'extracting', 'embedding', 'classifying', 'publishing')
            """), {"c": code}).fetchone()
            docs = conn.execute(text("SELECT COUNT(*) FROM dbo.TaiLieu WHERE ThuMuc = :c AND LifecycleStatus <> 'deleting'"), {"c": code}).fetchone()
            shared_docs = conn.execute(text("""
                SELECT COUNT(*) FROM dbo.TaiLieu t
                WHERE t.ThuMuc <> :c AND t.LifecycleStatus <> 'deleting'
                  AND EXISTS (SELECT 1 FROM dbo.PhongBanChiaSe pbc WHERE pbc.DocID = t.DocID AND pbc.DeptCode = :c)
            """), {"c": code}).fetchone()
        return {
            "code": dept[0],
            "name": dept[1],
            "domain": dept[2],
            "site": dept[3],
            "is_active": (str(dept[5]).lower() == "active") if dept[5] is not None else bool(dept[4]),
            "status": (str(dept[5]).lower() if dept[5] else ("active" if dept[4] else "disabled")),
            "disabled_at": dept[6],
            "archived_at": dept[7],
            "users": int(users[0] or 0),
            "pending_jobs": int(jobs[0] or 0),
            "docs": int(docs[0] or 0),
            "shared_docs": int(shared_docs[0] or 0),
        }
    except Exception as e:
        logger.error(f"get_department_summary loi code={code}: {e}", exc_info=True)
        return None


def set_department_status(code, status, actor="System", force=False):
    """Chuyen trang thai phong ban active/disabled/archived.

    archived chi cho phep khi khong con user va job pending, tru khi force=True.
    Phong ban da archived khong cho mo lai qua UI de tranh vo lifecycle.
    """
    summary = get_department_summary(code)
    if not summary:
        return {"ok": False, "message": f"Khong tim thay phong ban '{code}'."}
    current_status = summary.get("status") or ("active" if summary.get("is_active") else "disabled")
    target_status = _normalize_department_status(status=status, is_active=(status == "active"))
    if current_status == "archived" and target_status != "archived":
        return {"ok": False, "message": f"Phong ban '{code}' da archived va khong mo lai qua flow nay."}
    if target_status == "archived" and not force:
        if (summary.get("users") or 0) > 0 or (summary.get("pending_jobs") or 0) > 0:
            return {
                "ok": False,
                "message": (
                    f"Khong the archive phong '{code}' khi con {summary.get('users', 0)} user "
                    f"va {summary.get('pending_jobs', 0)} job dang xu ly."
                ),
                "summary": summary,
            }
    ok = upsert_department(
        code,
        name=summary.get("name"),
        domain=summary.get("domain"),
        site=summary.get("site"),
        status=target_status,
        is_active=(target_status == "active"),
    )
    if ok:
        write_audit_log(actor or "System", "department_status", "Departments", code, {
            "from": current_status,
            "to": target_status,
            "force": bool(force),
        })
        return {"ok": True, "status": target_status, "summary": get_department_summary(code)}
    return {"ok": False, "message": f"Cap nhat trang thai phong '{code}' that bai."}


def archive_department(code, actor="System", force=False):
    """Shortcut cho set_department_status(..., 'archived')."""
    return set_department_status(code, "archived", actor=actor, force=force)


def reassign_department_data(source_code, target_code, actor="System", move_users=True):
    """Chuyen toan bo du lieu phong ban A -> B, sau do disable A.

    Bao gom: TaiLieu.ThuMuc/PhongBan, IngestionJobs.ThuMuc/PhongBan,
    UserDepartments (+ Users.Department de dong bo UI), va payload Qdrant.
    """
    _ensure_engine()
    source_code = (source_code or "").strip()
    target_code = (target_code or "").strip()
    if not source_code or not target_code:
        return {"ok": False, "message": "Source/target department la bat buoc."}
    if source_code == target_code:
        return {"ok": False, "message": "Khong the reassign cung 1 phong ban."}

    src = get_department_summary(source_code)
    tgt = get_department_summary(target_code)
    if not src or not tgt:
        return {"ok": False, "message": "Khong tim thay phong nguon hoac dich."}
    if (tgt.get("status") or "active") != "active":
        return {"ok": False, "message": f"Phong dich '{target_code}' phai o trang thai active."}
    if (src.get("status") or "active") == "archived":
        return {"ok": False, "message": f"Phong nguon '{source_code}' da archived, khong reassign qua flow nay."}

    updated_doc_payloads = []
    qdrant_failures = []
    try:
        with engine.begin() as conn:
            # 1) TaiLieu (E1: chia se phong ban nam o bang dbo.PhongBanChiaSe)
            doc_rows = conn.execute(text("""
                SELECT DISTINCT t.DocID, t.ThuMuc
                FROM dbo.TaiLieu t
                WHERE t.ThuMuc = :src
                   OR EXISTS (SELECT 1 FROM dbo.PhongBanChiaSe pbc WHERE pbc.DocID = t.DocID AND pbc.DeptCode = :src)
            """), {"src": source_code}).fetchall()
            for row in doc_rows:
                doc_id, thu_muc = row[0], row[1]
                new_thu_muc = target_code if thu_muc == source_code else thu_muc
                if new_thu_muc != thu_muc:
                    conn.execute(text("UPDATE dbo.TaiLieu SET ThuMuc = :t WHERE DocID = :id"),
                                 {"t": new_thu_muc, "id": doc_id})
                # Remap junction src -> target, tranh trung PK
                conn.execute(text("DELETE FROM dbo.PhongBanChiaSe WHERE DocID = :id AND DeptCode = :src"),
                             {"id": doc_id, "src": source_code})
                conn.execute(text(
                    "IF NOT EXISTS (SELECT 1 FROM dbo.PhongBanChiaSe WHERE DocID = :id AND DeptCode = :dst) "
                    "INSERT INTO dbo.PhongBanChiaSe (DocID, DeptCode) VALUES (:id, :dst)"),
                    {"id": doc_id, "dst": target_code})
                # Bao dam phong chu (ThuMuc moi) luon co trong junction
                conn.execute(text(
                    "IF NOT EXISTS (SELECT 1 FROM dbo.PhongBanChiaSe WHERE DocID = :id AND DeptCode = :own) "
                    "INSERT INTO dbo.PhongBanChiaSe (DocID, DeptCode) VALUES (:id, :own)"),
                    {"id": doc_id, "own": new_thu_muc})
                new_depts = [r[0] for r in conn.execute(text(
                    "SELECT DeptCode FROM dbo.PhongBanChiaSe WHERE DocID = :id ORDER BY DeptCode"),
                    {"id": doc_id}).fetchall()]
                updated_doc_payloads.append((doc_id, new_depts))

            # 2) IngestionJobs
            job_rows = conn.execute(text("""
                SELECT JobID, ThuMuc, PhongBan
                FROM dbo.IngestionJobs
                WHERE ThuMuc = :src OR (PhongBan IS NOT NULL AND ',' + REPLACE(PhongBan, ' ', '') + ',' LIKE :lk)
            """), {"src": source_code, "lk": f"%,{source_code},%"}).fetchall()
            for row in job_rows:
                job_id, thu_muc, phong_ban = row[0], row[1], row[2]
                new_thu_muc = target_code if thu_muc == source_code else thu_muc
                new_phong_ban = _replace_department_token_list(phong_ban, source_code, target_code)
                if not new_phong_ban and new_thu_muc:
                    new_phong_ban = new_thu_muc
                conn.execute(text("UPDATE dbo.IngestionJobs SET ThuMuc = :t, PhongBan = :pb WHERE JobID = :id"),
                             {"t": new_thu_muc, "pb": new_phong_ban, "id": job_id})

            # 3) RBAC users
            moved_users = 0
            if move_users:
                user_rows = conn.execute(text("SELECT DISTINCT UserID FROM dbo.UserDepartments WHERE Department = :src"),
                                         {"src": source_code}).fetchall()
                user_ids = [r[0] for r in user_rows]
                conn.execute(text("DELETE FROM dbo.UserDepartments WHERE Department = :src"), {"src": source_code})
                for uid in user_ids:
                    exists = conn.execute(text("SELECT 1 FROM dbo.UserDepartments WHERE UserID = :uid AND Department = :dst"),
                                          {"uid": uid, "dst": target_code}).fetchone()
                    if not exists:
                        conn.execute(text("INSERT INTO dbo.UserDepartments (UserID, Department) VALUES (:uid, :dst)"),
                                     {"uid": uid, "dst": target_code})
                conn.execute(text("UPDATE dbo.Users SET Department = :dst WHERE Department = :src"),
                             {"dst": target_code, "src": source_code})
                moved_users = len(user_ids)
            else:
                moved_users = 0

        # 4) Qdrant payload (ngoai transaction SQL)
        for doc_id, phong_ban_quyen in updated_doc_payloads:
            ok_meta = update_qdrant_metadata(doc_id, {
                "phong_ban_quyen": phong_ban_quyen,
                "department": (phong_ban_quyen[0] if phong_ban_quyen else target_code),
            })
            if not ok_meta:
                qdrant_failures.append(doc_id)

        # 5) Disable phong nguon sau khi move
        status_res = set_department_status(source_code, "disabled", actor=actor, force=True)
        write_audit_log(actor or "System", "department_reassign", "Departments", source_code, {
            "to": target_code,
            "move_users": bool(move_users),
            "docs": len(updated_doc_payloads),
            "qdrant_failures": qdrant_failures,
        })
        return {
            "ok": True,
            "source": source_code,
            "target": target_code,
            "moved_docs": len(updated_doc_payloads),
            "moved_users": moved_users,
            "qdrant_failures": qdrant_failures,
            "status_result": status_res,
        }
    except Exception as e:
        logger.error(f"reassign_department_data loi {source_code}->{target_code}: {e}", exc_info=True)
        return {"ok": False, "message": str(e)}


def list_known_sites(active_only=True):
    """Danh muc khu/site (bang Sites)."""
    _ensure_engine()
    where = "WHERE IsActive = 1" if active_only else ""
    try:
        with engine.connect() as conn:
            rows = conn.execute(text(
                f"SELECT SiteCode, SiteName, IsActive FROM dbo.Sites {where} ORDER BY SiteCode"
            )).fetchall()
        return [{"code": r[0], "name": r[1], "is_active": bool(r[2])} for r in rows]
    except Exception as e:
        logger.error(f"list_known_sites loi: {e}", exc_info=True)
        return []


def upsert_site(code, name=None, is_active=True):
    _ensure_engine()
    if not code:
        return False
    try:
        with engine.begin() as conn:
            conn.execute(text("""
                MERGE dbo.Sites AS tgt
                USING (SELECT :c AS SiteCode) AS src ON tgt.SiteCode = src.SiteCode
                WHEN MATCHED THEN UPDATE SET SiteName = :n, IsActive = :a
                WHEN NOT MATCHED THEN INSERT (SiteCode, SiteName, IsActive) VALUES (:c, :n, :a);
            """), {"c": code, "n": name, "a": 1 if is_active else 0})
        return True
    except Exception as e:
        logger.error(f"upsert_site loi: {e}", exc_info=True)
        return False


def get_user_sites(user_id):
    """Danh sach site user duoc phep. List rong = KHONG gioi han theo site."""
    _ensure_engine()
    try:
        with engine.connect() as conn:
            rows = conn.execute(text("SELECT Site FROM dbo.UserSites WHERE UserID = :uid"), {"uid": user_id}).fetchall()
        return [r[0] for r in rows]
    except Exception:
        return []


def set_user_sites(user_id, sites):
    """Thay toan bo danh sach site cua user (replace)."""
    _ensure_engine()
    try:
        with engine.begin() as conn:
            conn.execute(text("DELETE FROM dbo.UserSites WHERE UserID = :uid"), {"uid": user_id})
            for s in (sites or []):
                if s:
                    conn.execute(text("INSERT INTO dbo.UserSites (UserID, Site) VALUES (:uid, :s)"), {"uid": user_id, "s": s})
        return True
    except Exception as e:
        logger.error(f"set_user_sites loi: {e}", exc_info=True)
        return False


def set_user_departments(user_id, departments):
    """Thay toan bo danh sach phong ban cua user (replace)."""
    _ensure_engine()
    try:
        with engine.begin() as conn:
            conn.execute(text("DELETE FROM dbo.UserDepartments WHERE UserID = :uid"), {"uid": user_id})
            for d in (departments or []):
                if d:
                    conn.execute(text("INSERT INTO dbo.UserDepartments (UserID, Department) VALUES (:uid, :d)"), {"uid": user_id, "d": d})
        return True
    except Exception as e:
        logger.error(f"set_user_departments loi: {e}", exc_info=True)
        return False


def set_user_clearance(user_id, max_level):
    """Dat muc mat toi da cho user (public/internal/confidential)."""
    _ensure_engine()
    if max_level not in ("public", "internal", "confidential"):
        return False
    try:
        with engine.begin() as conn:
            conn.execute(text("""
                MERGE dbo.UserSecurityClearance AS tgt
                USING (SELECT :uid AS UserID) AS src ON tgt.UserID = src.UserID
                WHEN MATCHED THEN UPDATE SET MaxLevel = :lvl
                WHEN NOT MATCHED THEN INSERT (UserID, MaxLevel) VALUES (:uid, :lvl);
            """), {"uid": user_id, "lvl": max_level})
        return True
    except Exception as e:
        logger.error(f"set_user_clearance loi: {e}", exc_info=True)
        return False


def set_job_priority(job_id, priority):
    """Dat do uu tien cho job (nho hon = uu tien hon). Vd: 10 = gap, 100 = thuong."""
    _ensure_engine()
    try:
        with engine.begin() as conn:
            conn.execute(text(
                "UPDATE dbo.IngestionJobs SET Priority = :p, UpdatedAt = GETDATE() WHERE JobID = :id"
            ), {"p": int(priority), "id": job_id})
        return True
    except Exception as e:
        logger.error(f"set_job_priority loi: {e}", exc_info=True)
        return False


def cancel_job(job_id, canceled_by="System"):
    """Huy 1 job dang cho/loi. Khong huy job dang chay giua chung neu da publish."""
    _ensure_engine()
    try:
        with engine.begin() as conn:
            res = conn.execute(text("""
                UPDATE dbo.IngestionJobs
                SET Status = 'rejected',
                    CanceledBy = :by, CanceledAt = GETDATE(),
                    ErrorMessage = ISNULL(ErrorMessage, '') + ' [Huy boi ' + :by + ']',
                    LockedBy = NULL, LockedAt = NULL, UpdatedAt = GETDATE()
                WHERE JobID = :id
                  AND Status NOT IN ('published', 'pending_review', 'publishing')
            """), {"by": canceled_by, "id": job_id})
        return res.rowcount > 0
    except Exception as e:
        logger.error(f"cancel_job loi: {e}", exc_info=True)
        return False


def requeue_job(job_id):
    """Dua lai job ve 'pending' (retry thu cong)."""
    _ensure_engine()
    try:
        with engine.begin() as conn:
            conn.execute(text("""
                UPDATE dbo.IngestionJobs
                SET Status = 'pending', ErrorMessage = NULL, FailureType = NULL,
                    NextRetryAt = NULL, RetryCount = 0, LockedBy = NULL, LockedAt = NULL,
                    ProgressPercent = 0, CanceledBy = NULL, CanceledAt = NULL, UpdatedAt = GETDATE()
                WHERE JobID = :id
            """), {"id": job_id})
        return True
    except Exception as e:
        logger.error(f"requeue_job loi: {e}", exc_info=True)
        return False


def queue_eta_seconds():
    """Uoc luong ETA (giay) de don het hang doi = so job cho * thoi gian TB/job gan day.
    Tra ve dict {pending, avg_seconds, eta_seconds}."""
    _ensure_engine()
    try:
        with engine.connect() as conn:
            pending = conn.execute(text(
                "SELECT COUNT(*) FROM dbo.IngestionJobs WHERE Status IN ('pending','pending_retry','waiting_quota')"
            )).scalar() or 0
            # Thoi gian TB xu ly cua 50 job published gan nhat
            avg = conn.execute(text("""
                SELECT AVG(CAST(DATEDIFF(second, CreatedAt, UpdatedAt) AS FLOAT))
                FROM (
                    SELECT TOP 50 CreatedAt, UpdatedAt FROM dbo.IngestionJobs
                    WHERE Status = 'published' AND UpdatedAt IS NOT NULL
                    ORDER BY UpdatedAt DESC
                ) x
            """)).scalar()
        avg = float(avg) if avg else 90.0  # mac dinh 90s/job neu chua co lich su
        return {"pending": int(pending), "avg_seconds": round(avg, 1), "eta_seconds": int(pending * avg)}
    except Exception as e:
        logger.error(f"queue_eta_seconds loi: {e}", exc_info=True)
        return {"pending": 0, "avg_seconds": 0, "eta_seconds": 0}


def dashboard_by_department():
    """Thong ke theo phong ban (P1.6): so tai lieu, cho duyet, job loi."""
    _ensure_engine()
    try:
        with engine.connect() as conn:
            doc_rows = conn.execute(text("""
                SELECT ISNULL(ThuMuc, '(khong ro)') AS Dept,
                       COUNT(*) AS OwnedTotal,
                       SUM(CASE WHEN ReviewStatus = 'pending_review' THEN 1 ELSE 0 END) AS Pending,
                       SUM(CASE WHEN ReviewStatus = 'approved' AND LifecycleStatus = 'published' THEN 1 ELSE 0 END) AS Published,
                       SUM(CASE WHEN SecurityLevel = 'confidential' THEN 1 ELSE 0 END) AS Confidential
                FROM TaiLieu
                WHERE LifecycleStatus <> 'deleting'
                GROUP BY ThuMuc
            """)).fetchall()
            # P4.4: STRING_SPLIT yeu cau SQL Server compat level >= 130.
            # Wrap rieng de fallback an toan neu DB cu khong ho tro.
            try:
                shared_rows = conn.execute(text("""
                    SELECT pbc.DeptCode AS Dept, COUNT(DISTINCT pbc.DocID) AS SharedAccess
                    FROM dbo.PhongBanChiaSe pbc
                    JOIN dbo.TaiLieu d ON d.DocID = pbc.DocID
                    WHERE d.LifecycleStatus <> 'deleting'
                      AND pbc.DeptCode <> ISNULL(d.ThuMuc, '')
                    GROUP BY pbc.DeptCode
                """)).fetchall()
            except Exception as _shared_err:
                logger.warning(f"[E1] Loi dem shared_access tu PhongBanChiaSe: {_shared_err}. Bo qua.")
                shared_rows = []
            job_rows = conn.execute(text("""
                SELECT ISNULL(ThuMuc, '(khong ro)') AS Dept,
                       SUM(CASE WHEN Status IN ('failed','waiting_quota') THEN 1 ELSE 0 END) AS Failed,
                       SUM(CASE WHEN Status IN ('pending','pending_retry','classifying','extracting','embedding','publishing') THEN 1 ELSE 0 END) AS Running
                FROM dbo.IngestionJobs GROUP BY ThuMuc
            """)).fetchall()
        jobs = {r[0]: {"failed": int(r[1] or 0), "running": int(r[2] or 0)} for r in job_rows}
        shared = {str(r[0]).strip(): int(r[1] or 0) for r in shared_rows}
        out = []
        for r in doc_rows:
            dept = r[0]
            dept_key = str(dept).strip()
            j = jobs.get(dept, {"failed": 0, "running": 0})
            owned_total = int(r[1] or 0)
            shared_total = int(shared.get(dept_key, 0))
            out.append({
                "department": dept,
                "owned_total": owned_total,
                "shared_access": shared_total,
                "total": owned_total + shared_total,
                "pending_review": int(r[2] or 0),
                "published": int(r[3] or 0),
                "confidential": int(r[4] or 0),
                "failed_jobs": j["failed"],
                "running_jobs": j["running"],
            })
        out.sort(key=lambda x: x["total"], reverse=True)
        return out
    except Exception as e:
        logger.error(f"dashboard_by_department loi: {e}", exc_info=True)
        return []


def count_docs_by_department():
    """A3: dem so tai lieu (TaiLieu) theo phong ban (ThuMuc).

    Tra ve dict {ten_phong_ban: so_luong}. Dung de hien cot 'So tai lieu'
    trong quan ly phong ban va canh bao khi tat phong dang con tai lieu.
    """
    _ensure_engine()
    try:
        with engine.connect() as conn:
            rows = conn.execute(text("""
                SELECT ISNULL(ThuMuc, '(khong ro)') AS Dept, COUNT(*) AS Total
                FROM TaiLieu
                WHERE LifecycleStatus <> 'deleting'
                GROUP BY ThuMuc
            """)).fetchall()
        return {r[0]: int(r[1] or 0) for r in rows}
    except Exception as e:
        logger.error(f"count_docs_by_department loi: {e}", exc_info=True)
        return {}


# ============================ P2: MATERIAL DICTIONARY ============================
# CRUD cho tu dien ma vat tu / dong nghia (quan tri qua UI trang 'materials').
# Sau moi thay doi -> refresh cache cua material_registry de co hieu luc ngay.

def _refresh_material_cache():
    try:
        from mech_chatbot.ingestion.material_registry import refresh_cache
        refresh_cache()
    except Exception:
        pass


def list_materials():
    """Tra ve list vat lieu kem dong nghia: [{material_id, code, display, category, is_active, synonyms:[...]}]."""
    _ensure_engine()
    with engine.connect() as conn:
        mats = conn.execute(text(
            "SELECT MaterialID, CanonicalCode, DisplayName, Category, IsActive "
            "FROM dbo.MaterialDictionary ORDER BY CanonicalCode"
        )).fetchall()
        syns = conn.execute(text(
            "SELECT SynonymID, MaterialID, Synonym, IsActive FROM dbo.MaterialSynonym ORDER BY Synonym"
        )).fetchall()
    syn_by_mat = {}
    for sid, mid, syn, act in syns:
        syn_by_mat.setdefault(mid, []).append(
            {"synonym_id": sid, "synonym": syn, "is_active": bool(act)}
        )
    return [
        {
            "material_id": m[0], "code": m[1], "display": m[2], "category": m[3],
            "is_active": bool(m[4]), "synonyms": syn_by_mat.get(m[0], []),
        }
        for m in mats
    ]


def upsert_material(code, display=None, category=None, is_active=True, material_id=None):
    """Them moi hoac cap nhat 1 vat lieu chuan. Match theo material_id (sua) hoac CanonicalCode (them)."""
    _ensure_engine()
    code = (code or "").strip()
    if not code:
        return False
    display = (display or code).strip()
    act = 1 if is_active else 0
    try:
        with engine.begin() as conn:
            if material_id:
                conn.execute(text(
                    "UPDATE dbo.MaterialDictionary SET CanonicalCode=:c, DisplayName=:d, "
                    "Category=:cat, IsActive=:a, UpdatedAt=GETDATE() WHERE MaterialID=:id"
                ), {"c": code, "d": display, "cat": category, "a": act, "id": material_id})
            else:
                exists = conn.execute(text(
                    "SELECT MaterialID FROM dbo.MaterialDictionary WHERE CanonicalCode=:c"
                ), {"c": code}).fetchone()
                if exists:
                    conn.execute(text(
                        "UPDATE dbo.MaterialDictionary SET DisplayName=:d, Category=:cat, "
                        "IsActive=:a, UpdatedAt=GETDATE() WHERE MaterialID=:id"
                    ), {"d": display, "cat": category, "a": act, "id": exists[0]})
                else:
                    conn.execute(text(
                        "INSERT INTO dbo.MaterialDictionary (CanonicalCode, DisplayName, Category, IsActive) "
                        "VALUES (:c, :d, :cat, :a)"
                    ), {"c": code, "d": display, "cat": category, "a": act})
        _refresh_material_cache()
        return True
    except Exception as e:
        logger.error(f"upsert_material loi cho '{code}': {e}", exc_info=True)
        return False


def delete_material(material_id):
    """Xoa 1 vat lieu (dong nghia tu xoa theo CASCADE)."""
    _ensure_engine()
    try:
        with engine.begin() as conn:
            conn.execute(text("DELETE FROM dbo.MaterialDictionary WHERE MaterialID=:id"), {"id": material_id})
        _refresh_material_cache()
        return True
    except Exception as e:
        logger.error(f"delete_material loi cho id {material_id}: {e}", exc_info=True)
        return False


def add_material_synonym(material_id, synonym):
    """Them 1 tu dong nghia cho vat lieu (bo qua neu trung)."""
    _ensure_engine()
    synonym = (synonym or "").strip()
    if not material_id or not synonym:
        return False
    try:
        with engine.begin() as conn:
            exists = conn.execute(text(
                "SELECT SynonymID FROM dbo.MaterialSynonym WHERE MaterialID=:m AND Synonym=:s"
            ), {"m": material_id, "s": synonym}).fetchone()
            if not exists:
                conn.execute(text(
                    "INSERT INTO dbo.MaterialSynonym (MaterialID, Synonym, IsActive) VALUES (:m, :s, 1)"
                ), {"m": material_id, "s": synonym})
        _refresh_material_cache()
        return True
    except Exception as e:
        logger.error(f"add_material_synonym loi: {e}", exc_info=True)
        return False


def delete_material_synonym(synonym_id):
    """Xoa 1 tu dong nghia."""
    _ensure_engine()
    try:
        with engine.begin() as conn:
            conn.execute(text("DELETE FROM dbo.MaterialSynonym WHERE SynonymID=:id"), {"id": synonym_id})
        _refresh_material_cache()
        return True
    except Exception as e:
        logger.error(f"delete_material_synonym loi cho id {synonym_id}: {e}", exc_info=True)
        return False


# ============================ P2-6: USAGE ANALYTICS ============================
# Bao cao su dung tu LichSuChat: cau hoi pho bien, ti le 'khong tim thay',
# danh gia like/dislike, xu huong theo ngay, tai lieu duoc tham chieu nhieu.

# Cum tu cho thay bot KHONG tra loi duoc (de tinh ti le 'khong tim thay').
_NO_ANSWER_MARKERS = [
    "khong tim thay", "khong co thong tin", "khong co du lieu", "khong tim duoc",
    "khong du can cu", "khong du du kien", "khong xac dinh", "chua co thong tin",
    "vui long kiem tra lai ma", "ngoai pham vi",
]


def _strip_accents_sql(s):
    import unicodedata
    if s is None:
        return ""
    s = unicodedata.normalize("NFD", str(s))
    s = "".join(c for c in s if unicodedata.category(c) != "Mn")
    return s.replace("\u0111", "d").replace("\u0110", "D").lower().strip()


def get_usage_analytics(days=30, top_n=10):
    """Tong hop thong ke su dung trong 'days' ngay gan nhat.
    Tra ve dict: totals, no_answer, ratings, top_questions, daily, top_documents.
    """
    _ensure_engine()
    import json as _json
    out = {
        "days": days,
        "total_questions": 0, "total_sessions": 0, "total_users": 0,
        "no_answer_count": 0, "no_answer_rate": 0.0,
        "likes": 0, "dislikes": 0,
        "top_questions": [], "daily": [], "top_documents": [],
    }
    try:
        with engine.connect() as conn:
            # Totals
            row = conn.execute(text(
                "SELECT COUNT(*) AS q, COUNT(DISTINCT SessionID) AS s, "
                "COUNT(DISTINCT Username) AS u, "
                "SUM(CASE WHEN DanhGia = 1 THEN 1 ELSE 0 END) AS likes, "
                "SUM(CASE WHEN DanhGia = -1 THEN 1 ELSE 0 END) AS dislikes "
                "FROM LichSuChat "
                "WHERE ThoiGian >= DATEADD(day, -:d, GETDATE())"
            ), {"d": days}).fetchone()
            if row:
                out["total_questions"] = int(row[0] or 0)
                out["total_sessions"] = int(row[1] or 0)
                out["total_users"] = int(row[2] or 0)
                out["likes"] = int(row[3] or 0)
                out["dislikes"] = int(row[4] or 0)

            # Daily trend
            daily = conn.execute(text(
                "SELECT CAST(ThoiGian AS DATE) AS d, COUNT(*) AS c "
                "FROM LichSuChat WHERE ThoiGian >= DATEADD(day, -:d, GETDATE()) "
                "GROUP BY CAST(ThoiGian AS DATE) ORDER BY d"
            ), {"d": days}).fetchall()
            out["daily"] = [{"date": str(r[0]), "count": int(r[1])} for r in daily]

            # Lay cau hoi + tra loi de tinh no-answer & top questions (python-side de bo dau)
            rows = conn.execute(text(
                "SELECT CauHoi_User, TraLoi_Bot, RefImages FROM LichSuChat "
                "WHERE ThoiGian >= DATEADD(day, -:d, GETDATE())"
            ), {"d": days}).fetchall()

        from collections import Counter
        q_counter = Counter()
        doc_counter = Counter()
        no_answer = 0
        for cau_hoi, tra_loi, ref_images in rows:
            # Top questions (chuan hoa: bo dau, ha thuong, gom khoang trang)
            qn = _strip_accents_sql(cau_hoi)
            if qn:
                q_counter[qn[:200]] += 1
            # No-answer detection
            an = _strip_accents_sql(tra_loi)
            if any(m in an for m in _NO_ANSWER_MARKERS):
                no_answer += 1
            # Tai lieu duoc tham chieu nhieu (tu RefImages JSON)
            if ref_images:
                try:
                    refs = _json.loads(ref_images)
                    for rf in (refs or []):
                        name = str(rf)
                        base = name.replace("\\", "/").split("/")[-1]
                        if base:
                            doc_counter[base] += 1
                except Exception:
                    pass

        out["no_answer_count"] = no_answer
        if out["total_questions"] > 0:
            out["no_answer_rate"] = round(100.0 * no_answer / out["total_questions"], 1)
        out["top_questions"] = [
            {"question": q, "count": c} for q, c in q_counter.most_common(top_n)
        ]
        out["top_documents"] = [
            {"document": d, "count": c} for d, c in doc_counter.most_common(top_n)
        ]
        return out
    except Exception as e:
        logger.error(f"get_usage_analytics loi: {e}", exc_info=True)
        return out


# ==========================================================================
# P0: METADATA TONG QUAT DA PHONG BAN (common fields + DocumentAttributes)
# ==========================================================================

# Map: key trong upload_meta/JSON  ->  ten cot tren TaiLieu
_COMMON_META_COLS = {
    "title": "Title",
    "summary": "Summary",
    "tags": "Tags",
    "doc_number": "DocNumber",
    "issued_date": "IssuedDate",
    "effective_date": "EffectiveDate",
    "expiry_date": "ExpiryDate",
    "review_date": "ReviewDate",
    "owner_signer": "OwnerSigner",
    "language": "DocLanguage",
    "effective_status": "EffectiveStatus",
}


def _clean_meta_value(v):
    """Chuan hoa gia tri: '' -> None, strip chuoi."""
    if v is None:
        return None
    if isinstance(v, str):
        v = v.strip()
        return v or None
    return v


def _apply_upload_meta_to_doc(conn, doc_id, upload_meta_json, domain):
    """Ap metadata nhap luc upload (JSON tren IngestionJobs) xuong TaiLieu +
    DocumentAttributes. Chay TRONG cung transaction cua _get_or_create_doc.

    JSON dang: {"title":..., "summary":..., ..., "attributes": {key: value}}
    - Cac key common -> cot TaiLieu (chi ghi de khi co gia tri, tranh xoa du lieu).
    - attributes -> DocumentAttributes (ExtractedBy='manual'), thay the ban manual cu.
    """
    if not upload_meta_json:
        return
    try:
        import json as _json
        meta = _json.loads(upload_meta_json)
    except Exception:
        return
    if not isinstance(meta, dict):
        return
    try:
        sets, params = [], {"d": doc_id}
        for k, col in _COMMON_META_COLS.items():
            if k in meta:
                val = _clean_meta_value(meta.get(k))
                if val is not None:
                    sets.append(f"{col} = :{k}")
                    params[k] = val
        if sets:
            conn.execute(text("UPDATE TaiLieu SET " + ", ".join(sets) + " WHERE DocID = :d"), params)

        attrs = meta.get("attributes") or {}
        if isinstance(attrs, dict) and attrs:
            dom = (domain or "generic")
            # Thay the cac attribute nhap tay truoc do cho doc nay (giu attribute do AI/regex boc tach)
            conn.execute(text("DELETE FROM DocumentAttributes WHERE DocID = :d AND ExtractedBy = 'manual'"), {"d": doc_id})
            for ak, av in attrs.items():
                av = _clean_meta_value(av)
                if av is None:
                    continue
                conn.execute(text("""
                    INSERT INTO DocumentAttributes (DocID, Domain, AttributeKey, AttributeValue, ExtractedBy)
                    VALUES (:d, :dom, :k, :v, 'manual')
                """), {"d": doc_id, "dom": dom, "k": str(ak)[:150], "v": str(av)})
    except Exception as e:
        logger.error(f"Loi _apply_upload_meta_to_doc doc_id={doc_id}: {e}", exc_info=True)


def get_document_attributes(doc_id, domain=None):
    """Tra ve dict {AttributeKey: AttributeValue} cho doc (uu tien ban manual)."""
    if doc_id is None:
        return {}
    _ensure_engine()
    try:
        with engine.connect() as conn:
            q = "SELECT AttributeKey, AttributeValue, ExtractedBy FROM DocumentAttributes WHERE DocID = :d"
            params = {"d": doc_id}
            if domain:
                q += " AND Domain = :dom"
                params["dom"] = domain
            q += " ORDER BY CASE WHEN ExtractedBy = 'manual' THEN 0 ELSE 1 END, AttrID DESC"
            rows = conn.execute(text(q), params).fetchall()
        out = {}
        for k, v, _by in rows:
            if k not in out:  # ban manual (sort truoc) thang the
                out[k] = v
        return out
    except Exception as e:
        logger.error(f"Loi get_document_attributes doc_id={doc_id}: {e}", exc_info=True)
        return {}


def get_document_metadata(doc_id):
    """Tra ve dict metadata tong quat (common fields) + 'attributes' cho 1 doc.
    Dung cho form chinh sua o Kho tai lieu / Duyet."""
    if doc_id is None:
        return {}
    _ensure_engine()
    out = {}
    try:
        with engine.connect() as conn:
            row = conn.execute(text("""
                SELECT Title, Summary, Tags, DocNumber, IssuedDate, EffectiveDate,
                       ExpiryDate, ReviewDate, OwnerSigner, DocLanguage, EffectiveStatus, Domain
                FROM TaiLieu WHERE DocID = :d
            """), {"d": doc_id}).fetchone()
        if row:
            (out["title"], out["summary"], out["tags"], out["doc_number"], out["issued_date"],
             out["effective_date"], out["expiry_date"], out["review_date"], out["owner_signer"],
             out["language"], out["effective_status"], out["domain"]) = row
    except Exception as e:
        logger.error(f"Loi get_document_metadata doc_id={doc_id}: {e}", exc_info=True)
    out["attributes"] = get_document_attributes(doc_id)
    return out


def set_document_attributes(doc_id, domain, attrs, extracted_by="manual"):
    """Upsert cac attribute nhap tay cho 1 doc. attrs = dict {key: value}.
    Thay the toan bo ban 'manual' cu (key bo trong -> xoa)."""
    if doc_id is None:
        return False
    _ensure_engine()
    dom = (domain or "generic")
    try:
        with engine.begin() as conn:
            conn.execute(text("DELETE FROM DocumentAttributes WHERE DocID = :d AND ExtractedBy = :by"),
                         {"d": doc_id, "by": extracted_by})
            for ak, av in (attrs or {}).items():
                av = _clean_meta_value(av)
                if av is None:
                    continue
                conn.execute(text("""
                    INSERT INTO DocumentAttributes (DocID, Domain, AttributeKey, AttributeValue, ExtractedBy)
                    VALUES (:d, :dom, :k, :v, :by)
                """), {"d": doc_id, "dom": dom, "k": str(ak)[:150], "v": str(av), "by": extracted_by})
        return True
    except Exception as e:
        logger.error(f"Loi set_document_attributes doc_id={doc_id}: {e}", exc_info=True)
        return False


def update_document_common_metadata(doc_id, reviewer="System", attributes=None, domain=None, **fields):
    """Cap nhat metadata tong quat (common fields) cho 1 doc da ton tai.

    fields nhan cac key trong _COMMON_META_COLS (title, summary, tags, doc_number,
    issued_date, effective_date, expiry_date, review_date, owner_signer, language,
    effective_status). Gia tri None -> BO QUA (khong ghi de); chuoi rong -> xoa (NULL).
    attributes (dict) -> ghi vao DocumentAttributes (ban manual).
    Dong bo mot phan xuong Qdrant payload (title/doc_number/tags/effective_status).
    """
    if doc_id is None:
        return False
    _ensure_engine()
    try:
        sets, params = [], {"d": doc_id}
        for k, col in _COMMON_META_COLS.items():
            if k in fields:
                v = fields.get(k)
                # phan biet: None = bo qua; '' = set NULL (xoa)
                if v is None:
                    continue
                if isinstance(v, str):
                    v = v.strip()
                    params[k] = v or None
                else:
                    params[k] = v
                sets.append(f"{col} = :{k}")
        if sets:
            with engine.begin() as conn:
                conn.execute(text("UPDATE TaiLieu SET " + ", ".join(sets) + " WHERE DocID = :d"), params)

        if attributes is not None:
            set_document_attributes(doc_id, domain, attributes, extracted_by="manual")

        # Dong bo nhe xuong Qdrant (chi field huu ich cho loc/hien thi)
        qmeta = {}
        for qk in ("title", "doc_number", "tags", "effective_status"):
            if qk in params:
                qmeta[qk] = params[qk]
        if qmeta:
            try:
                update_qdrant_metadata(doc_id, qmeta)
            except Exception as _qe:
                logger.warning(f"update_document_common_metadata: dong bo Qdrant loi doc {doc_id}: {_qe}")

        write_audit_log(reviewer, "update_common_metadata", "TaiLieu", doc_id,
                        {"fields": list(params.keys()), "has_attributes": attributes is not None})
        return True
    except Exception as e:
        logger.error(f"Loi update_document_common_metadata doc_id={doc_id}: {e}", exc_info=True)
        return False


# ============================================================================
# P1: Cau hinh ung dung (AppSettings) + metadata tong quat cho RAG
# ============================================================================
_APP_SETTINGS_DEFAULTS = {"expiry_warning_days": "30", "rag_general_top_k": "30"}
_app_settings_cache = {"data": None, "ts": 0.0}
_APP_SETTINGS_TTL = 30  # giay


def get_all_app_settings(use_cache=True):
    """Doc toan bo cau hinh tu bang AppSettings (co cache ngan). Luon tra ve day du
    cac key mac dinh ke ca khi DB chua co dong tuong ung."""
    import time
    if use_cache and _app_settings_cache["data"] is not None and (time.time() - _app_settings_cache["ts"]) < _APP_SETTINGS_TTL:
        return dict(_app_settings_cache["data"])
    result = dict(_APP_SETTINGS_DEFAULTS)
    if engine is not None:
        try:
            with engine.connect() as conn:
                rows = conn.execute(text("SELECT SettingKey, SettingValue FROM dbo.AppSettings")).fetchall()
            for k, v in rows:
                if v is not None:
                    result[k] = v
        except Exception as e:
            logger.warning(f"get_all_app_settings loi: {e}")
    _app_settings_cache["data"] = dict(result)
    _app_settings_cache["ts"] = time.time()
    return result


def get_app_setting(key, default=None):
    val = get_all_app_settings().get(key)
    if val is None or val == "":
        if default is not None:
            return default
        return _APP_SETTINGS_DEFAULTS.get(key)
    return val


def get_app_setting_int(key, default=0):
    try:
        return int(str(get_app_setting(key, default)).strip())
    except Exception:
        return default


def set_app_setting(key, value, updated_by="System"):
    """Upsert mot cau hinh va xoa cache."""
    if engine is None:
        return False
    with engine.begin() as conn:
        conn.execute(text("""
            MERGE dbo.AppSettings AS tgt
            USING (SELECT :k AS SettingKey) AS src
            ON tgt.SettingKey = src.SettingKey
            WHEN MATCHED THEN
                UPDATE SET SettingValue = :v, UpdatedAt = GETDATE(), UpdatedBy = :by
            WHEN NOT MATCHED THEN
                INSERT (SettingKey, SettingValue, UpdatedAt, UpdatedBy)
                VALUES (:k, :v, GETDATE(), :by);
        """), {"k": key, "v": str(value), "by": updated_by})
    _app_settings_cache["data"] = None
    return True


def get_common_metadata_for_rag(doc_ids):
    """Lay metadata tong quat (Title/Summary/Tags/DocNumber/cac moc ngay/EffectiveStatus...)
    cho danh sach DocID, phuc vu RAG. Tra ve {DocID(int): {..}}."""
    out = {}
    ids = []
    for d in (doc_ids or []):
        try:
            ids.append(int(d))
        except Exception:
            continue
    ids = list(dict.fromkeys(ids))
    if not ids or engine is None:
        return out
    try:
        keys, params = [], {}
        for i, did in enumerate(ids):
            kk = f"id{i}"
            params[kk] = did
            keys.append(f":{kk}")
        q = """
            SELECT DocID, Title, Summary, Tags, DocNumber, IssuedDate, EffectiveDate,
                   ExpiryDate, OwnerSigner, EffectiveStatus, DocLanguage
            FROM TaiLieu
            WHERE DocID IN (__IN_CLAUSE__)
        """.replace("__IN_CLAUSE__", ", ".join(keys))
        with engine.connect() as conn:
            rows = conn.execute(text(q), params).fetchall()
        for r in rows:
            out[r[0]] = {
                "title": r[1], "summary": r[2], "tags": r[3], "doc_number": r[4],
                "issued_date": r[5], "effective_date": r[6], "expiry_date": r[7],
                "owner_signer": r[8], "effective_status": r[9], "language": r[10],
            }
    except Exception as e:
        logger.warning(f"get_common_metadata_for_rag loi: {e}")
    return out


# ==========================================================================
# P0-2: ACCESS REQUEST WORKFLOW (yeu cau cap quyen tai lieu mat / phong ban)
# ==========================================================================
def create_access_request(user_id, username, request_type, requested_level=None,
                          requested_dept=None, question_text=None, reason=None):
    """Tao yeu cau cap quyen. De-dup: neu da co request PENDING trung (cung user +
    type + level/dept) thi KHONG tao moi. request_type: 'security' | 'department'.
    Tra ve dict {"request_id": id, "created": bool} hoac None neu loi.
    """
    _ensure_engine()
    try:
        with engine.begin() as conn:
            existing = conn.execute(text("""
                SELECT TOP 1 RequestID FROM dbo.AccessRequests
                WHERE UserID = :uid AND Status = 'pending' AND RequestType = :rt
                  AND ISNULL(RequestedLevel, '') = ISNULL(:lvl, '')
                  AND ISNULL(RequestedDept, '')  = ISNULL(:dept, '')
            """), {"uid": user_id, "rt": request_type, "lvl": requested_level, "dept": requested_dept}).fetchone()
            if existing:
                return {"request_id": existing[0], "created": False}
            row = conn.execute(text("""
                INSERT INTO dbo.AccessRequests
                    (UserID, Username, RequestType, RequestedLevel, RequestedDept, QuestionText, Reason, Status)
                OUTPUT INSERTED.RequestID
                VALUES (:uid, :uname, :rt, :lvl, :dept, :q, :reason, 'pending')
            """), {"uid": user_id, "uname": username, "rt": request_type,
                    "lvl": requested_level, "dept": requested_dept,
                    "q": _cap_len(question_text, 4000), "reason": _cap_len(reason, 2000)}).fetchone()
        rid = row[0] if row else None
        try:
            write_audit_log(username=username, action="access_request_create",
                            entity_type="AccessRequests", entity_id=rid,
                            details={"request_type": request_type, "level": requested_level, "dept": requested_dept},
                            user_id=user_id)
        except Exception:
            pass
        return {"request_id": rid, "created": True}
    except Exception as e:
        logger.error(f"create_access_request loi: {e}", exc_info=True)
        return None


def list_access_requests(status="pending", limit=200):
    """Danh sach yeu cau (cho reviewer/admin). status='all' de lay tat ca."""
    _ensure_engine()
    try:
        where = "WHERE Status = :st" if status and status != "all" else ""
        params = {"st": status} if status and status != "all" else {}
        with engine.connect() as conn:
            rows = conn.execute(text(f"""
                SELECT TOP {int(limit)} RequestID, UserID, Username, RequestType, RequestedLevel,
                       RequestedDept, QuestionText, Reason, Status, ReviewerUsername, ReviewNote,
                       ReviewedAt, CreatedAt
                FROM dbo.AccessRequests {where}
                ORDER BY CreatedAt DESC
            """), params).fetchall()
        cols = ["request_id", "user_id", "username", "request_type", "requested_level",
                "requested_dept", "question_text", "reason", "status", "reviewer_username",
                "review_note", "reviewed_at", "created_at"]
        return [dict(zip(cols, r)) for r in rows]
    except Exception as e:
        logger.error(f"list_access_requests loi: {e}", exc_info=True)
        return []


def get_user_access_requests(user_id, limit=50):
    """Lich su yeu cau cua chinh user."""
    _ensure_engine()
    try:
        with engine.connect() as conn:
            rows = conn.execute(text(f"""
                SELECT TOP {int(limit)} RequestID, RequestType, RequestedLevel, RequestedDept,
                       QuestionText, Status, ReviewerUsername, ReviewNote, ReviewedAt, CreatedAt
                FROM dbo.AccessRequests WHERE UserID = :uid ORDER BY CreatedAt DESC
            """), {"uid": user_id}).fetchall()
        cols = ["request_id", "request_type", "requested_level", "requested_dept", "question_text",
                "status", "reviewer_username", "review_note", "reviewed_at", "created_at"]
        return [dict(zip(cols, r)) for r in rows]
    except Exception as e:
        logger.error(f"get_user_access_requests loi: {e}", exc_info=True)
        return []


def count_pending_access_requests():
    _ensure_engine()
    try:
        with engine.connect() as conn:
            return conn.execute(text("SELECT COUNT(*) FROM dbo.AccessRequests WHERE Status = 'pending'")).scalar() or 0
    except Exception:
        return 0


def resolve_access_request(request_id, decision, reviewer_username, reviewer_id=None, review_note=None):
    """Duyet/tu choi 1 yeu cau. decision: 'approved' | 'rejected'.
    Khi approved -> ap quyen: security nang UserSecurityClearance; department them UserDepartments.
    Ghi audit. Tra ve dict {"ok": bool, "applied": str|None, "message": str}.
    """
    _ensure_engine()
    if decision not in ("approved", "rejected"):
        return {"ok": False, "message": "decision khong hop le"}
    target_uname = None
    applied = None
    try:
        with engine.begin() as conn:
            req = conn.execute(text("""
                SELECT UserID, Username, RequestType, RequestedLevel, RequestedDept, Status
                FROM dbo.AccessRequests WHERE RequestID = :rid
            """), {"rid": request_id}).fetchone()
            if not req:
                return {"ok": False, "message": "khong tim thay yeu cau"}
            if req[5] != "pending":
                return {"ok": False, "message": "yeu cau da duoc xu ly"}
            target_uid, target_uname, rtype, rlevel, rdept, _ = req

            if decision == "approved":
                if rtype == "security" and rlevel in ("public", "internal", "confidential"):
                    conn.execute(text("""
                        MERGE dbo.UserSecurityClearance AS tgt
                        USING (SELECT :uid AS UserID) AS src ON tgt.UserID = src.UserID
                        WHEN MATCHED AND tgt.MaxLevel <> :lvl THEN UPDATE SET MaxLevel = :lvl
                        WHEN NOT MATCHED THEN INSERT (UserID, MaxLevel) VALUES (:uid, :lvl);
                    """), {"uid": target_uid, "lvl": rlevel})
                    applied = f"clearance={rlevel}"
                elif rtype == "department" and rdept:
                    exists = conn.execute(text(
                        "SELECT 1 FROM dbo.UserDepartments WHERE UserID = :uid AND Department = :d"
                    ), {"uid": target_uid, "d": rdept}).fetchone()
                    if not exists:
                        conn.execute(text(
                            "INSERT INTO dbo.UserDepartments (UserID, Department) VALUES (:uid, :d)"
                        ), {"uid": target_uid, "d": rdept})
                    applied = f"department+{rdept}"

            conn.execute(text("""
                UPDATE dbo.AccessRequests
                SET Status = :st, ReviewerID = :rvid, ReviewerUsername = :rvuname,
                    ReviewNote = :note, ReviewedAt = GETDATE()
                WHERE RequestID = :rid
            """), {"st": decision, "rvid": reviewer_id, "rvuname": reviewer_username,
                    "note": _cap_len(review_note, 2000), "rid": request_id})
        try:
            write_audit_log(username=reviewer_username, action=f"access_request_{decision}",
                            entity_type="AccessRequests", entity_id=request_id,
                            details={"target_user": target_uname, "applied": applied},
                            user_id=reviewer_id)
        except Exception:
            pass
        return {"ok": True, "applied": applied, "message": "da xu ly"}
    except Exception as e:
        logger.error(f"resolve_access_request loi: {e}", exc_info=True)
        return {"ok": False, "message": str(e)}


# ==========================================================================
# P0-2 (bo sung): THU HOI / QUAN LY QUYEN + LICH SU CAP QUYEN
# ==========================================================================
def list_users_with_access(limit=1000):
    """Danh sach user kem clearance + phong ban (cho trang thu hoi/quan ly quyen)."""
    _ensure_engine()
    try:
        with engine.connect() as conn:
            users = conn.execute(text(f"""
                SELECT TOP {int(limit)} UserID, Username, DisplayName, Department, IsActive
                FROM dbo.Users ORDER BY Username
            """)).fetchall()
            clr = conn.execute(text("SELECT UserID, MaxLevel FROM dbo.UserSecurityClearance")).fetchall()
            deps = conn.execute(text("SELECT UserID, Department FROM dbo.UserDepartments ORDER BY Department")).fetchall()
        clr_map = {r[0]: r[1] for r in clr}
        dep_map = {}
        for uid, d in deps:
            dep_map.setdefault(uid, []).append(d)
        out = []
        for uid, uname, disp, dept, active in users:
            out.append({
                "user_id": uid, "username": uname, "display_name": disp,
                "department": dept, "is_active": bool(active),
                "max_level": clr_map.get(uid, "public"),
                "departments": dep_map.get(uid, []),
            })
        return out
    except Exception as e:
        logger.error(f"list_users_with_access loi: {e}", exc_info=True)
        return []


def revoke_user_clearance(user_id, new_level, actor_username, actor_id=None, reason=None):
    """Thu hoi / dieu chinh muc mat cua user ve new_level (public|internal|confidential). Ghi audit."""
    _ensure_engine()
    if new_level not in ("public", "internal", "confidential"):
        return {"ok": False, "message": "muc mat khong hop le"}
    try:
        with engine.begin() as conn:
            old = conn.execute(text("SELECT MaxLevel FROM dbo.UserSecurityClearance WHERE UserID = :uid"),
                               {"uid": user_id}).fetchone()
            old_level = old[0] if old else "public"
            conn.execute(text("""
                MERGE dbo.UserSecurityClearance AS tgt
                USING (SELECT :uid AS UserID) AS src ON tgt.UserID = src.UserID
                WHEN MATCHED THEN UPDATE SET MaxLevel = :lvl
                WHEN NOT MATCHED THEN INSERT (UserID, MaxLevel) VALUES (:uid, :lvl);
            """), {"uid": user_id, "lvl": new_level})
        try:
            write_audit_log(username=actor_username, action="clearance_revoke",
                            entity_type="UserSecurityClearance", entity_id=user_id,
                            details={"from": old_level, "to": new_level, "reason": reason},
                            user_id=actor_id)
        except Exception:
            pass
        return {"ok": True, "from": old_level, "to": new_level, "message": "da cap nhat"}
    except Exception as e:
        logger.error(f"revoke_user_clearance loi: {e}", exc_info=True)
        return {"ok": False, "message": str(e)}


def revoke_user_department(user_id, dept, actor_username, actor_id=None, reason=None):
    """Thu hoi quyen xem 1 phong ban cua user (xoa ban ghi UserDepartments). Ghi audit."""
    _ensure_engine()
    if not dept:
        return {"ok": False, "message": "thieu phong ban"}
    try:
        with engine.begin() as conn:
            res = conn.execute(text("DELETE FROM dbo.UserDepartments WHERE UserID = :uid AND Department = :d"),
                               {"uid": user_id, "d": dept})
        removed = getattr(res, "rowcount", 0) or 0
        try:
            write_audit_log(username=actor_username, action="department_revoke",
                            entity_type="UserDepartments", entity_id=user_id,
                            details={"department": dept, "removed": removed, "reason": reason},
                            user_id=actor_id)
        except Exception:
            pass
        return {"ok": True, "removed": removed, "message": "da thu hoi"}
    except Exception as e:
        logger.error(f"revoke_user_department loi: {e}", exc_info=True)
        return {"ok": False, "message": str(e)}


def get_grant_history(limit=100):
    """Lich su cap/thu hoi quyen, doc tu AuditLog (chi cac action lien quan quyen)."""
    _ensure_engine()
    actions = (
        "access_request_create", "access_request_approved", "access_request_rejected",
        "clearance_revoke", "department_revoke",
    )
    try:
        in_clause = ", ".join("'" + a + "'" for a in actions)
        with engine.connect() as conn:
            rows = conn.execute(text(f"""
                SELECT TOP {int(limit)} CreatedAt, Username, Action, EntityType, EntityID, Details
                FROM dbo.AuditLog
                WHERE Action IN ({in_clause})
                ORDER BY CreatedAt DESC
            """)).fetchall()
        cols = ["created_at", "username", "action", "entity_type", "entity_id", "details"]
        return [dict(zip(cols, r)) for r in rows]
    except Exception as e:
        logger.error(f"get_grant_history loi: {e}", exc_info=True)
        return []


# ==========================================================================
# P0-3: DOMAIN GLOSSARY / SYNONYM (tu dien dong nghia theo domain)
# ==========================================================================
def get_active_glossary(domains=None):
    """Cac muc glossary dang bat. domains=None -> tat ca; nguoc lai loc theo list domain."""
    _ensure_engine()
    try:
        with engine.connect() as conn:
            rows = conn.execute(text(
                "SELECT GlossaryID, Domain, Term, Synonyms, Expansion FROM dbo.DomainGlossary WHERE IsActive = 1"
            )).fetchall()
        dset = set(domains) if domains else None
        out = []
        for gid, domain, term, syn, exp in rows:
            if dset is not None and domain not in dset:
                continue
            try:
                syn_list = json.loads(syn) if syn else []
                if not isinstance(syn_list, list):
                    syn_list = [str(syn_list)]
            except Exception:
                syn_list = [s.strip() for s in str(syn or "").split(",") if s.strip()]
            out.append({"glossary_id": gid, "domain": domain, "term": term,
                        "synonyms": syn_list, "expansion": exp})
        return out
    except Exception as e:
        logger.error(f"get_active_glossary loi: {e}", exc_info=True)
        return []


def list_domain_glossary(domain=None, active_only=False):
    _ensure_engine()
    try:
        where = []
        params = {}
        if domain:
            where.append("Domain = :d"); params["d"] = domain
        if active_only:
            where.append("IsActive = 1")
        wc = ("WHERE " + " AND ".join(where)) if where else ""
        with engine.connect() as conn:
            rows = conn.execute(text(
                "SELECT GlossaryID, Domain, Term, Synonyms, Expansion, IsActive, CreatedAt "
                "FROM dbo.DomainGlossary " + wc + " ORDER BY Domain, Term"
            ), params).fetchall()
        out = []
        for gid, dm, term, syn, exp, active, created in rows:
            try:
                syn_list = json.loads(syn) if syn else []
                if not isinstance(syn_list, list):
                    syn_list = [str(syn_list)]
            except Exception:
                syn_list = [s.strip() for s in str(syn or "").split(",") if s.strip()]
            out.append({"glossary_id": gid, "domain": dm, "term": term, "synonyms": syn_list,
                        "expansion": exp, "is_active": bool(active), "created_at": created})
        return out
    except Exception as e:
        logger.error(f"list_domain_glossary loi: {e}", exc_info=True)
        return []


def upsert_glossary_term(term, domain, synonyms=None, expansion=None, is_active=True, glossary_id=None):
    _ensure_engine()
    if not term or not domain:
        return {"ok": False, "message": "thieu term hoac domain"}
    syn_json = json.dumps([s for s in (synonyms or []) if s], ensure_ascii=False)
    try:
        with engine.begin() as conn:
            if glossary_id:
                conn.execute(text("""
                    UPDATE dbo.DomainGlossary
                    SET Term = :t, Domain = :d, Synonyms = :s, Expansion = :e, IsActive = :a, UpdatedAt = GETDATE()
                    WHERE GlossaryID = :gid
                """), {"t": _cap_len(term, 255), "d": _cap_len(domain, 50), "s": syn_json,
                        "e": _cap_len(expansion, 1000), "a": 1 if is_active else 0, "gid": glossary_id})
                gid = glossary_id
            else:
                row = conn.execute(text("""
                    INSERT INTO dbo.DomainGlossary (Domain, Term, Synonyms, Expansion, IsActive)
                    OUTPUT INSERTED.GlossaryID
                    VALUES (:d, :t, :s, :e, :a)
                """), {"d": _cap_len(domain, 50), "t": _cap_len(term, 255), "s": syn_json,
                        "e": _cap_len(expansion, 1000), "a": 1 if is_active else 0}).fetchone()
                gid = row[0] if row else None
        return {"ok": True, "glossary_id": gid}
    except Exception as e:
        logger.error(f"upsert_glossary_term loi: {e}", exc_info=True)
        return {"ok": False, "message": str(e)}


def set_glossary_active(glossary_id, is_active):
    _ensure_engine()
    try:
        with engine.begin() as conn:
            conn.execute(text("UPDATE dbo.DomainGlossary SET IsActive = :a, UpdatedAt = GETDATE() WHERE GlossaryID = :gid"),
                         {"a": 1 if is_active else 0, "gid": glossary_id})
        return True
    except Exception as e:
        logger.error(f"set_glossary_active loi: {e}", exc_info=True)
        return False


def delete_glossary_term(glossary_id):
    _ensure_engine()
    try:
        with engine.begin() as conn:
            conn.execute(text("DELETE FROM dbo.DomainGlossary WHERE GlossaryID = :gid"), {"gid": glossary_id})
        return True
    except Exception as e:
        logger.error(f"delete_glossary_term loi: {e}", exc_info=True)
        return False


# ==========================================================================
# P1-4: OBSERVABILITY (RagTraceSummary) - luu tong hop + truy van dashboard
# ==========================================================================
def _tf(v):
    try:
        return float(v) if v is not None else None
    except (TypeError, ValueError):
        return None


def save_rag_trace_summary(trace_id, acc):
    """P1-4: luu 1 dong tong hop tracing vao RagTraceSummary (idempotent theo TraceID)."""
    _ensure_engine()
    if not trace_id:
        return
    try:
        with engine.begin() as conn:
            exists = conn.execute(text("SELECT 1 FROM dbo.RagTraceSummary WHERE TraceID = :tid"),
                                  {"tid": trace_id[:80]}).fetchone()
            if exists:
                return
            conn.execute(text("""
                INSERT INTO dbo.RagTraceSummary
                    (TraceID, Department, Roles, Model, Question, TokensIn, TokensOut, Cost,
                     FinalLatencyMs, ContextMs, IntentMs, HydeMs, GlossaryMs, RetrievalMs, RerankMs, GateMs, LlmMs,
                     Refusal, RefusalReason, DocsCount, RetrievalMode)
                VALUES
                    (:tid, :dept, :roles, :model, :q, :tin, :tout, :cost,
                     :flat, :cms, :ims, :hms, :gms, :rms, :rkms, :gtms, :lms,
                     :refusal, :rreason, :docs, :rmode)
            """), {
                "tid": trace_id[:80],
                "dept": _cap_len(acc.get("department"), 255),
                "roles": _cap_len(acc.get("roles"), 255),
                "model": _cap_len(acc.get("model"), 100),
                "q": _cap_len(acc.get("question"), 500),
                "tin": _sanitize_int(acc.get("tokens_in")),
                "tout": _sanitize_int(acc.get("tokens_out")),
                "cost": _tf(acc.get("cost")),
                "flat": _sanitize_int(acc.get("final_latency_ms")),
                "cms": _sanitize_int(acc.get("context_ms")),
                "ims": _sanitize_int(acc.get("intent_ms")),
                "hms": _sanitize_int(acc.get("hyde_ms")),
                "gms": _sanitize_int(acc.get("glossary_ms")),
                "rms": _sanitize_int(acc.get("retrieval_ms")),
                "rkms": _sanitize_int(acc.get("rerank_ms")),
                "gtms": _sanitize_int(acc.get("gate_ms")),
                "lms": _sanitize_int(acc.get("llm_ms")),
                "refusal": 1 if acc.get("refusal") else 0,
                "rreason": _cap_len(acc.get("refusal_reason"), 100),
                "docs": _sanitize_int(acc.get("docs_count")),
                "rmode": _cap_len(acc.get("retrieval_mode"), 50),
            })
    except Exception as e:
        logger.error(f"save_rag_trace_summary loi: {e}", exc_info=True)


def get_observability(days=30):
    """P1-4: tong hop cost/token/latency tu RagTraceSummary cho dashboard."""
    _ensure_engine()
    out = {"total_requests": 0, "total_cost": 0.0, "avg_latency_ms": 0, "refusal_rate": 0.0,
           "by_department": [], "daily": [], "step_latency": {}, "refusals": [], "top_costly": []}
    p = {"d": int(days)}
    try:
        with engine.connect() as conn:
            row = conn.execute(text("""
                SELECT COUNT(*), ISNULL(SUM(Cost),0), ISNULL(AVG(CAST(FinalLatencyMs AS FLOAT)),0),
                       ISNULL(SUM(CASE WHEN Refusal = 1 THEN 1 ELSE 0 END),0)
                FROM dbo.RagTraceSummary WHERE CreatedAt >= DATEADD(day, -:d, GETDATE())
            """), p).fetchone()
            total = row[0] or 0
            out["total_requests"] = total
            out["total_cost"] = round(float(row[1] or 0), 4)
            out["avg_latency_ms"] = int(row[2] or 0)
            out["refusal_rate"] = round(float(row[3] or 0) / total * 100, 1) if total else 0.0

            dep = conn.execute(text("""
                SELECT ISNULL(Department, '(khong ro)'), COUNT(*), ISNULL(SUM(TokensIn),0),
                       ISNULL(SUM(TokensOut),0), ISNULL(SUM(Cost),0), ISNULL(AVG(CAST(FinalLatencyMs AS FLOAT)),0)
                FROM dbo.RagTraceSummary WHERE CreatedAt >= DATEADD(day, -:d, GETDATE())
                GROUP BY Department ORDER BY SUM(Cost) DESC
            """), p).fetchall()
            out["by_department"] = [{"department": r[0], "requests": r[1], "tokens_in": int(r[2]),
                                     "tokens_out": int(r[3]), "cost": round(float(r[4]), 4),
                                     "avg_latency_ms": int(r[5])} for r in dep]

            daily = conn.execute(text("""
                SELECT CONVERT(varchar(10), CreatedAt, 23), COUNT(*), ISNULL(SUM(Cost),0)
                FROM dbo.RagTraceSummary WHERE CreatedAt >= DATEADD(day, -:d, GETDATE())
                GROUP BY CONVERT(varchar(10), CreatedAt, 23) ORDER BY CONVERT(varchar(10), CreatedAt, 23)
            """), p).fetchall()
            out["daily"] = [{"date": r[0], "requests": r[1], "cost": round(float(r[2]), 4)} for r in daily]

            s = conn.execute(text("""
                SELECT ISNULL(AVG(CAST(ContextMs AS FLOAT)),0), ISNULL(AVG(CAST(IntentMs AS FLOAT)),0),
                       ISNULL(AVG(CAST(HydeMs AS FLOAT)),0), ISNULL(AVG(CAST(GlossaryMs AS FLOAT)),0),
                       ISNULL(AVG(CAST(RetrievalMs AS FLOAT)),0), ISNULL(AVG(CAST(RerankMs AS FLOAT)),0),
                       ISNULL(AVG(CAST(GateMs AS FLOAT)),0), ISNULL(AVG(CAST(LlmMs AS FLOAT)),0)
                FROM dbo.RagTraceSummary WHERE CreatedAt >= DATEADD(day, -:d, GETDATE())
            """), p).fetchone()
            out["step_latency"] = {"context": int(s[0]), "intent": int(s[1]), "hyde": int(s[2]),
                                   "glossary": int(s[3]), "retrieval": int(s[4]), "rerank": int(s[5]),
                                   "gate": int(s[6]), "llm": int(s[7])}

            ref = conn.execute(text("""
                SELECT ISNULL(RefusalReason, '(khac)'), COUNT(*)
                FROM dbo.RagTraceSummary WHERE Refusal = 1 AND CreatedAt >= DATEADD(day, -:d, GETDATE())
                GROUP BY RefusalReason ORDER BY COUNT(*) DESC
            """), p).fetchall()
            out["refusals"] = [{"reason": r[0], "count": r[1]} for r in ref]

            top = conn.execute(text("""
                SELECT TOP 20 ISNULL(Question,''), ISNULL(Department,''), ISNULL(Cost,0),
                       ISNULL(TokensIn,0), ISNULL(TokensOut,0)
                FROM dbo.RagTraceSummary WHERE CreatedAt >= DATEADD(day, -:d, GETDATE())
                ORDER BY Cost DESC
            """), p).fetchall()
            out["top_costly"] = [{"question": r[0], "department": r[1], "cost": round(float(r[2]), 6),
                                  "tokens_in": int(r[3]), "tokens_out": int(r[4])} for r in top]
        return out
    except Exception as e:
        logger.error(f"get_observability loi: {e}", exc_info=True)
        return out


def ensure_regression_question(question, expected_doc_id=None, expected_keywords=None,
                              department=None, site=None, created_by="System"):
    """P1-6: them cau hoi vao bo hoi quy NEU CHUA co (dedupe theo cau hoi da chuan hoa).
    Dung khi tu dong nap Golden Answer -> regression, tranh trung khi reviewer luu lai nhieu lan.
    """
    _ensure_engine()
    if not question or not str(question).strip():
        return None
    target = normalize_question(question)
    try:
        with engine.connect() as conn:
            rows = conn.execute(text("SELECT RegQID, QuestionText FROM RegressionQuestion")).fetchall()
        for rid, qt in rows:
            if normalize_question(qt) == target:
                return rid
    except Exception as e:
        logger.error(f"ensure_regression_question kiem tra loi: {e}", exc_info=True)
    return add_regression_question(question, expected_doc_id=expected_doc_id,
                                   expected_keywords=expected_keywords, department=department,
                                   site=site, created_by=created_by)


# ==========================================================================
# P1-7: DOCUMENT LIFECYCLE (het han / nhac review / log lan review)
# ==========================================================================
def _to_date(v):
    from datetime import date, datetime
    if v is None:
        return None
    if isinstance(v, datetime):
        return v.date()
    if isinstance(v, date):
        return v
    try:
        return datetime.strptime(str(v)[:10], "%Y-%m-%d").date()
    except Exception:
        return None


def classify_lifecycle(expiry_date, review_date, today=None, soon_days=30, effective_status=None):
    """P1-7 (thuan, de test): phan loai vong doi -> expired | expiring_soon | needs_review | ok."""
    from datetime import date, timedelta
    today = today or date.today()
    exp = _to_date(expiry_date)
    rev = _to_date(review_date)
    stt = (effective_status or "").lower()
    if stt in ("expired", "superseded"):
        return "expired"
    if exp is not None and exp < today:
        return "expired"
    if exp is not None and exp <= today + timedelta(days=int(soon_days)):
        return "expiring_soon"
    if rev is not None and rev <= today:
        return "needs_review"
    return "ok"


def get_lifecycle_overview(soon_days=30):
    """P1-7: tong hop tai lieu hien hanh theo trang thai vong doi."""
    _ensure_engine()
    from datetime import date
    out = {"expired": [], "expiring_soon": [], "needs_review": [], "counts": {}}
    try:
        with engine.connect() as conn:
            rows = conn.execute(text(
                "SELECT DocID, TenFile, ThuMuc, VersionNo, EffectiveStatus, EffectiveDate, ExpiryDate, ReviewDate, LastReviewedAt "
                "FROM TaiLieu WHERE IsCurrent = 1 AND LifecycleStatus = 'published'"
            )).fetchall()
        today = date.today()
        for r in rows:
            cls = classify_lifecycle(r[6], r[7], today, soon_days, r[4])
            if cls == "ok":
                continue
            out[cls].append({
                "doc_id": r[0], "file": r[1], "dept": r[2], "version_no": r[3],
                "effective_status": r[4],
                "effective_date": str(r[5]) if r[5] else None,
                "expiry_date": str(r[6]) if r[6] else None,
                "review_date": str(r[7]) if r[7] else None,
                "last_reviewed_at": str(r[8]) if r[8] else None,
            })
        out["counts"] = {"expired": len(out["expired"]), "expiring_soon": len(out["expiring_soon"]),
                         "needs_review": len(out["needs_review"])}
        return out
    except Exception as e:
        logger.error(f"get_lifecycle_overview loi: {e}", exc_info=True)
        return out


def set_document_lifecycle(doc_id, effective_date=None, expiry_date=None, review_date=None, reviewer=None):
    """P1-7: cap nhat ngay hieu luc / het han / han review cho 1 tai lieu."""
    _ensure_engine()
    try:
        with engine.begin() as conn:
            conn.execute(text(
                "UPDATE TaiLieu SET EffectiveDate = :ed, ExpiryDate = :xd, ReviewDate = :rd WHERE DocID = :d"
            ), {"ed": effective_date, "xd": expiry_date, "rd": review_date, "d": doc_id})
        try:
            write_audit_log(username=reviewer, action="document_lifecycle_update", entity_type="TaiLieu",
                            entity_id=doc_id, details={"effective_date": effective_date,
                                                       "expiry_date": expiry_date, "review_date": review_date})
        except Exception:
            pass
        return True
    except Exception as e:
        logger.error(f"set_document_lifecycle loi: {e}", exc_info=True)
        return False


def mark_document_reviewed(doc_id, reviewer, next_review_days=180):
    """P1-7: danh dau da review (log lan review gan nhat) + dat han review ke tiep."""
    _ensure_engine()
    try:
        with engine.begin() as conn:
            conn.execute(text(
                "UPDATE TaiLieu SET LastReviewedAt = GETDATE(), LastReviewedBy = :rev, "
                "ReviewDate = DATEADD(day, :nd, CAST(GETDATE() AS DATE)) WHERE DocID = :d"
            ), {"rev": _cap_len(reviewer, 255), "nd": int(next_review_days), "d": doc_id})
        try:
            write_audit_log(username=reviewer, action="document_reviewed", entity_type="TaiLieu",
                            entity_id=doc_id, details={"next_review_days": int(next_review_days)})
        except Exception:
            pass
        return True
    except Exception as e:
        logger.error(f"mark_document_reviewed loi: {e}", exc_info=True)
        return False


def refresh_expired_status():
    """P1-7: dat EffectiveStatus = 'expired' cho tai lieu da qua ExpiryDate. Tra so dong cap nhat."""
    _ensure_engine()
    try:
        with engine.begin() as conn:
            res = conn.execute(text(
                "UPDATE TaiLieu SET EffectiveStatus = 'expired' "
                "WHERE ExpiryDate IS NOT NULL AND ExpiryDate < CAST(GETDATE() AS DATE) "
                "AND ISNULL(EffectiveStatus, '') NOT IN ('expired', 'superseded')"
            ))
        return getattr(res, "rowcount", 0) or 0
    except Exception as e:
        logger.error(f"refresh_expired_status loi: {e}", exc_info=True)
        return 0


# ==========================================================================
# P2-9: SEMANTIC CACHE
# ==========================================================================
def sc_put(question, embedding, answer, ref_text, ref_images, source_doc_ids, scope_sig, model, est_cost):
    _ensure_engine()
    try:
        with engine.begin() as conn:
            conn.execute(text("""
                INSERT INTO dbo.SemanticCache
                    (QuestionText, Embedding, Answer, RefText, RefImages, SourceDocIDs, ScopeSig, Model, EstCost)
                VALUES (:q, :emb, :a, :rt, :ri, :sd, :sc, :m, :ec)
            """), {"q": _cap_len(question, 2000), "emb": embedding, "a": answer, "rt": ref_text,
                    "ri": ref_images, "sd": source_doc_ids, "sc": _cap_len(scope_sig, 400),
                    "m": _cap_len(model, 100), "ec": est_cost})
    except Exception as e:
        logger.error(f"sc_put loi: {e}", exc_info=True)


def sc_get_candidates(scope_sig, ttl_hours, limit=300):
    _ensure_engine()
    try:
        with engine.connect() as conn:
            rows = conn.execute(text("""
                SELECT TOP (:lim) CacheID, Embedding, Answer, RefText, RefImages, SourceDocIDs, EstCost
                FROM dbo.SemanticCache
                WHERE ScopeSig = :sc AND CreatedAt >= DATEADD(hour, -:ttl, GETDATE())
                ORDER BY CreatedAt DESC
            """), {"lim": int(limit), "sc": scope_sig, "ttl": int(ttl_hours)}).fetchall()
        return [{"cache_id": r[0], "embedding": r[1], "answer": r[2], "ref_text": r[3],
                 "ref_images": r[4], "source_doc_ids": r[5], "est_cost": r[6]} for r in rows]
    except Exception as e:
        logger.error(f"sc_get_candidates loi: {e}", exc_info=True)
        return []


def sc_docs_all_current(doc_ids):
    _ensure_engine()
    ids = []
    for x in (doc_ids or []):
        try:
            ids.append(int(x))
        except Exception:
            pass
    ids = sorted(set(ids))
    if not ids:
        return True
    try:
        in_clause = ",".join(str(i) for i in ids)
        with engine.connect() as conn:
            row = conn.execute(text(
                "SELECT COUNT(*) FROM dbo.TaiLieu WHERE DocID IN (" + in_clause + ") "
                "AND IsCurrent = 1 AND LifecycleStatus = 'published'"
            )).fetchone()
        return (row[0] or 0) == len(ids)
    except Exception as e:
        logger.error(f"sc_docs_all_current loi: {e}", exc_info=True)
        return True


def sc_delete(cache_id):
    _ensure_engine()
    try:
        with engine.begin() as conn:
            conn.execute(text("DELETE FROM dbo.SemanticCache WHERE CacheID = :id"), {"id": cache_id})
    except Exception as e:
        logger.error(f"sc_delete loi: {e}", exc_info=True)


def sc_record_hit(cache_id, cost):
    _ensure_engine()
    try:
        with engine.begin() as conn:
            conn.execute(text("UPDATE dbo.SemanticCache SET HitCount = HitCount + 1, LastHitAt = GETDATE() WHERE CacheID = :id"),
                         {"id": cache_id})
    except Exception as e:
        logger.error(f"sc_record_hit loi: {e}", exc_info=True)


def sc_record_lookup(hit, cost_saved):
    _ensure_engine()
    try:
        with engine.begin() as conn:
            conn.execute(text("""
                MERGE dbo.SemanticCacheStat AS tgt USING (SELECT 1 AS StatID) AS src ON tgt.StatID = src.StatID
                WHEN MATCHED THEN UPDATE SET Lookups = Lookups + 1, Hits = Hits + :h, CostSaved = CostSaved + :c
                WHEN NOT MATCHED THEN INSERT (StatID, Lookups, Hits, CostSaved) VALUES (1, 1, :h, :c);
            """), {"h": 1 if hit else 0, "c": float(cost_saved or 0.0)})
    except Exception as e:
        logger.error(f"sc_record_lookup loi: {e}", exc_info=True)


def sc_clear_all():
    _ensure_engine()
    try:
        with engine.begin() as conn:
            res = conn.execute(text("DELETE FROM dbo.SemanticCache"))
            conn.execute(text("UPDATE dbo.SemanticCacheStat SET Lookups = 0, Hits = 0, CostSaved = 0 WHERE StatID = 1"))
        return getattr(res, "rowcount", 0) or 0
    except Exception as e:
        logger.error(f"sc_clear_all loi: {e}", exc_info=True)
        return 0


def sc_stats():
    _ensure_engine()
    out = {"entries": 0, "lookups": 0, "hits": 0, "hit_rate": 0.0, "cost_saved": 0.0}
    try:
        with engine.connect() as conn:
            out["entries"] = conn.execute(text("SELECT COUNT(*) FROM dbo.SemanticCache")).scalar() or 0
            row = conn.execute(text("SELECT Lookups, Hits, CostSaved FROM dbo.SemanticCacheStat WHERE StatID = 1")).fetchone()
        if row:
            lk = row[0] or 0
            ht = row[1] or 0
            out["lookups"] = lk
            out["hits"] = ht
            out["cost_saved"] = round(float(row[2] or 0), 4)
            out["hit_rate"] = round(ht / lk * 100, 1) if lk else 0.0
        return out
    except Exception as e:
        logger.error(f"sc_stats loi: {e}", exc_info=True)
        return out
