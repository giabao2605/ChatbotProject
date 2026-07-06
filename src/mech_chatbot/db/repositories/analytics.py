"""P1.1 auto-split tu db/repository.py (giu nguyen logic + comment goc).
Loi goi cheo module dung tham chieu _r_<module>.<ten> (tranh circular import).
KHONG sua tay truc tiep neu chua doc AGENTS; day la mot phan cua package db/repositories.
"""
from sqlalchemy import text
from ..engine import _ensure_engine, engine
from mech_chatbot.config.logging import logger
from ._shared import _cap_len, _sanitize_int

__all__ = [
    '_NO_ANSWER_MARKERS',
    '_strip_accents_sql',
    '_tf',
    'count_docs_by_department',
    'dashboard_by_department',
    'get_observability',
    'get_usage_analytics',
    'save_rag_trace_summary',
]

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
