"""P1.1 auto-split tu db/repository.py (giu nguyen logic + comment goc).
Loi goi cheo module dung tham chieu _r_<module>.<ten> (tranh circular import).
KHONG sua tay truc tiep neu chua doc AGENTS; day la mot phan cua package db/repositories.
"""
from sqlalchemy import text
from ..engine import _ensure_engine, engine
from mech_chatbot.config.logging import logger
from ._shared import _cap_len
from . import audit as _r_audit
from . import semantic_cache as _r_semantic_cache

__all__ = [
    'count_pending_access_requests',
    'create_access_request',
    'get_grant_history',
    'get_user_access_requests',
    'list_access_requests',
    'list_users_with_access',
    'resolve_access_request',
    'revoke_user_clearance',
    'revoke_user_department',
]

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
            _r_audit.write_audit_log(username=username, action="access_request_create",
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
            _r_audit.write_audit_log(username=reviewer_username, action=f"access_request_{decision}",
                            entity_type="AccessRequests", entity_id=request_id,
                            details={"target_user": target_uname, "applied": applied},
                            user_id=reviewer_id)
        except Exception:
            pass
        _r_semantic_cache._invalidate_semantic_cache("access_request.resolve")
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
            _r_audit.write_audit_log(username=actor_username, action="clearance_revoke",
                            entity_type="UserSecurityClearance", entity_id=user_id,
                            details={"from": old_level, "to": new_level, "reason": reason},
                            user_id=actor_id)
        except Exception:
            pass
        _r_semantic_cache._invalidate_semantic_cache("user.clearance_revoke")
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
            _r_audit.write_audit_log(username=actor_username, action="department_revoke",
                            entity_type="UserDepartments", entity_id=user_id,
                            details={"department": dept, "removed": removed, "reason": reason},
                            user_id=actor_id)
        except Exception:
            pass
        _r_semantic_cache._invalidate_semantic_cache("user.dept_revoke")
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
