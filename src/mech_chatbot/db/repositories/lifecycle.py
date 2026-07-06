"""P1.1 auto-split tu db/repository.py (giu nguyen logic + comment goc).
Loi goi cheo module dung tham chieu _r_<module>.<ten> (tranh circular import).
KHONG sua tay truc tiep neu chua doc AGENTS; day la mot phan cua package db/repositories.
"""
from sqlalchemy import text
from ..engine import _ensure_engine, engine
from mech_chatbot.config.logging import logger
from ._shared import _cap_len
from . import audit as _r_audit
from . import qdrant as _r_qdrant
from . import semantic_cache as _r_semantic_cache

__all__ = [
    '_to_date',
    'classify_lifecycle',
    'get_lifecycle_overview',
    'mark_document_reviewed',
    'refresh_expired_status',
    'set_document_lifecycle',
]

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
            _r_audit.write_audit_log(username=reviewer, action="document_lifecycle_update", entity_type="TaiLieu",
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
            _r_audit.write_audit_log(username=reviewer, action="document_reviewed", entity_type="TaiLieu",
                            entity_id=doc_id, details={"next_review_days": int(next_review_days)})
        except Exception:
            pass
        return True
    except Exception as e:
        logger.error(f"mark_document_reviewed loi: {e}", exc_info=True)
        return False


def refresh_expired_status():
    """P1-7: dat EffectiveStatus = 'expired' cho tai lieu da qua ExpiryDate. Tra so dong cap nhat.
    P0#4: dong bo 'expired' xuong payload Qdrant + invalidate semantic cache de RAG loai ngay."""
    _ensure_engine()
    _WHERE = (
        "WHERE ExpiryDate IS NOT NULL AND ExpiryDate < CAST(GETDATE() AS DATE) "
        "AND ISNULL(EffectiveStatus, '') NOT IN ('expired', 'superseded')"
    )
    try:
        with engine.begin() as conn:
            rows = conn.execute(text("SELECT DocID FROM TaiLieu " + _WHERE)).fetchall()
            ids = [r[0] for r in rows]
            if ids:
                conn.execute(text("UPDATE TaiLieu SET EffectiveStatus = 'expired' " + _WHERE))
        # Dong bo payload Qdrant cho tung doc (best-effort)
        for _did in ids:
            try:
                _r_qdrant.update_qdrant_metadata(_did, {"effective_status": "expired"})
            except Exception as _qe:
                logger.warning(f"refresh_expired_status: dong bo Qdrant loi doc {_did}: {_qe}")
        if ids:
            _r_semantic_cache._invalidate_semantic_cache("lifecycle.expired")
        return len(ids)
    except Exception as e:
        logger.error(f"refresh_expired_status loi: {e}", exc_info=True)
        return 0
