"""P1.1 auto-split tu db/repository.py (giu nguyen logic + comment goc).
Loi goi cheo module dung tham chieu _r_<module>.<ten> (tranh circular import).
KHONG sua tay truc tiep neu chua doc AGENTS; day la mot phan cua package db/repositories.
"""
import os
from sqlalchemy import text
from ..engine import _ensure_engine, engine
from mech_chatbot.config.logging import logger
from . import audit as _r_audit
from . import qdrant as _r_qdrant
from . import semantic_cache as _r_semantic_cache

__all__ = [
    '_CATALOG_CACHE_TTL',
    '_catalog_cache',
    '_catalog_cache_get',
    '_catalog_cache_invalidate',
    '_catalog_cache_put',
    '_departments_support_status',
    '_normalize_department_status',
    '_replace_department_token_list',
    '_resolve_site',
    '_split_csv_tokens',
    'archive_department',
    'get_department_summary',
    'get_user_sites',
    'list_known_departments',
    'list_known_sites',
    'reassign_department_data',
    'set_department_status',
    'set_user_clearance',
    'set_user_departments',
    'set_user_sites',
    'upsert_department',
    'upsert_site',
]

# =====================================================================
# P1 HELPERS — quan ly phong ban/site dong, RBAC site, hang doi, dashboard
# =====================================================================

def _resolve_site(thu_muc):
    """Xac dinh site code cho mot phong ban (uu tien Departments.Site neu co).
    An toan: moi loi deu fallback ve mapping mac dinh / 'HQ'."""
    try:
        from mech_chatbot.db.registry_ports import resolve_site_by_department
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


# ==========================================================================
# Perf (GD2): cache ngan han cho DANH MUC TINH (khong phu thuoc quyen user).
# Giam truy van lap o ca repository lan UI (Streamlit rerun). TTL ngan +
# invalidate tuong minh khi CRUD danh muc de tranh du lieu "ma".
# ==========================================================================
_catalog_cache = {}
_CATALOG_CACHE_TTL = float(os.getenv("CATALOG_CACHE_TTL", "60"))


def _catalog_cache_get(key):
    import time
    ent = _catalog_cache.get(key)
    if ent is not None and (time.time() - ent[1]) < _CATALOG_CACHE_TTL:
        return ent[0]
    return None


def _catalog_cache_put(key, value):
    import time
    _catalog_cache[key] = (value, time.time())


def _catalog_cache_invalidate(prefix=""):
    if not prefix:
        _catalog_cache.clear()
        return
    for k in [k for k in _catalog_cache if k.startswith(prefix)]:
        _catalog_cache.pop(k, None)


def list_known_departments(active_only=True):
    """Danh muc phong ban (bang Departments). Tra ve list dict.

    Backward compatible:
    - DB cu: chi co IsActive -> map sang status active/disabled.
    - DB moi: uu tien cot Status, nhung van giu IsActive de code cu tiep tuc chay.
    """
    _ck = f"depts:{bool(active_only)}"  # Perf (GD2)
    _c = _catalog_cache_get(_ck)
    if _c is not None:
        return list(_c)
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
        result = [
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
        _catalog_cache_put(_ck, result)
        return list(result)
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
        _catalog_cache_invalidate("depts:")  # Perf (GD2): danh muc phong ban doi -> xoa cache
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
        _r_audit.write_audit_log(actor or "System", "department_status", "Departments", None, {
            "code": code,
            "from": current_status,
            "to": target_status,
            "force": bool(force),
        })
        _catalog_cache_invalidate("depts:")  # Perf (GD2)
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
            ok_meta = _r_qdrant.update_qdrant_metadata(doc_id, {
                "phong_ban_quyen": phong_ban_quyen,
                "department": (phong_ban_quyen[0] if phong_ban_quyen else target_code),
            })
            if not ok_meta:
                qdrant_failures.append(doc_id)

        # 5) Disable phong nguon sau khi move
        status_res = set_department_status(source_code, "disabled", actor=actor, force=True)
        _r_audit.write_audit_log(actor or "System", "department_reassign", "Departments", None, {
            "source": source_code,
            "to": target_code,
            "move_users": bool(move_users),
            "docs": len(updated_doc_payloads),
            "qdrant_failures": qdrant_failures,
        })
        _r_semantic_cache._invalidate_semantic_cache("dept.reassign")
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
    _ck = f"sites:{bool(active_only)}"  # Perf (GD2)
    _c = _catalog_cache_get(_ck)
    if _c is not None:
        return list(_c)
    _ensure_engine()
    where = "WHERE IsActive = 1" if active_only else ""
    try:
        with engine.connect() as conn:
            rows = conn.execute(text(
                f"SELECT SiteCode, SiteName, IsActive FROM dbo.Sites {where} ORDER BY SiteCode"
            )).fetchall()
        result = [{"code": r[0], "name": r[1], "is_active": bool(r[2])} for r in rows]
        _catalog_cache_put(_ck, result)
        return list(result)
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
        _catalog_cache_invalidate("sites:")  # Perf (GD2)
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
            _vals = [{"uid": user_id, "s": s} for s in (sites or []) if s]
            if _vals:
                conn.execute(text("INSERT INTO dbo.UserSites (UserID, Site) VALUES (:uid, :s)"), _vals)  # Perf (GD1): bulk insert
        _r_semantic_cache._invalidate_semantic_cache("user.sites")
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
            _vals = [{"uid": user_id, "d": d} for d in (departments or []) if d]
            if _vals:
                conn.execute(text("INSERT INTO dbo.UserDepartments (UserID, Department) VALUES (:uid, :d)"), _vals)  # Perf (GD1): bulk insert
        _r_semantic_cache._invalidate_semantic_cache("user.departments")
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
        _r_semantic_cache._invalidate_semantic_cache("user.clearance_set")
        return True
    except Exception as e:
        logger.error(f"set_user_clearance loi: {e}", exc_info=True)
        return False
