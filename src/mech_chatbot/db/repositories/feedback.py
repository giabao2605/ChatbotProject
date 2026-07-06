"""P1.1 auto-split tu db/repository.py (giu nguyen logic + comment goc).
Loi goi cheo module dung tham chieu _r_<module>.<ten> (tranh circular import).
KHONG sua tay truc tiep neu chua doc AGENTS; day la mot phan cua package db/repositories.
"""
import hashlib
import unicodedata
from datetime import datetime
from sqlalchemy import text
from ..engine import _ensure_engine, engine
from mech_chatbot.config.logging import logger
from ._shared import _cap_len

__all__ = [
    'QUALITY_HALF_LIFE_DAYS',
    'QUALITY_MIN_SAMPLE',
    'QUALITY_PRIOR',
    'QUALITY_SMOOTH_K',
    'ROLE_WEIGHTS',
    '_question_hash',
    '_role_weight',
    'add_regression_question',
    'cleanup_dangling_records',
    'ensure_regression_question',
    'find_golden_answer',
    'get_doc_quality_ranking',
    'get_latest_regression_batch',
    'get_regression_runs',
    'list_regression_questions',
    'mark_feedback_stale_for_doc',
    'normalize_question',
    'recompute_doc_quality_scores',
    'save_regression_run',
    'set_regression_question_active',
    'update_chat_feedback',
    'upsert_golden_answer',
]

 
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
