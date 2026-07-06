"""P1.1 auto-split tu db/repository.py (giu nguyen logic + comment goc).
Loi goi cheo module dung tham chieu _r_<module>.<ten> (tranh circular import).
KHONG sua tay truc tiep neu chua doc AGENTS; day la mot phan cua package db/repositories.
"""
from sqlalchemy import text
from ..engine import _ensure_engine, engine
from mech_chatbot.config.logging import logger

__all__ = [
    'write_audit_log',
]

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
