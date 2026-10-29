"""P0 refactor: pure authentication core (KHONG phu thuoc Streamlit / UI / i18n).

Truoc day toan bo logic auth nam trong `auth/service.py` va bi cot chat voi
Streamlit (`st.*`) + `ui.i18n`. Dieu do khien khong the goi xac thuc tu
API / worker / test ma khong keo theo Streamlit.

`authenticate_user` o day la logic thuan:
- Nhan (username, password) -> tra ve dict thong tin user hoac None.
- Chi phu thuoc DB engine, bcrypt, rate_limit, security_policy.
- Bao loi qua logger (KHONG dung st.error), khong biet gi ve UI.

Cac helper session/UI (login_screen, check_auth, get_current_user, logout...)
van nam o `auth/service.py` (bien gioi Streamlit) va re-export ham nay.
"""
import bcrypt
from sqlalchemy import text

from mech_chatbot.db.engine import engine
from mech_chatbot.db.evidence_snapshot import evidence_read_connection
from mech_chatbot.config.constants import SHARE_ALL_DEPARTMENT
from mech_chatbot.config.logging import logger
from mech_chatbot.auth import rate_limit
from mech_chatbot.auth.security_policy import resolve_clearance, DEFAULT_MAX_SECURITY_LEVEL

_VALID_PREFERRED_LANGUAGES = {"vi", "en"}
_DEFAULT_PREFERRED_LANGUAGE = "vi"


def _normalize_preferred_language(lang):
    value = str(lang or "").strip().lower()
    if value in _VALID_PREFERRED_LANGUAGES:
        return value
    return _DEFAULT_PREFERRED_LANGUAGE


def _load_user_preferred_language(conn, user_id):
    try:
        row = conn.execute(
            text("SELECT PreferredLanguage FROM Users WHERE UserID = :uid"),
            {"uid": user_id},
        ).fetchone()
        return _normalize_preferred_language(row[0] if row else None)
    except Exception:
        # DB chua chay migration van cho login binh thuong.
        return _DEFAULT_PREFERRED_LANGUAGE


def _strict_departments(conn, allowed):
    rows = conn.execute(text('SELECT DeptCode, Status, IsActive FROM Departments ORDER BY DeptCode')).fetchall()
    if any(not isinstance(code, str) or not code.strip() or status not in {'active','disabled','archived'}
           or type(active) not in (int, bool) or active not in (0,1) for code,status,active in rows):
        raise PermissionError('Actor profile unavailable')
    active_codes = {code for code,status,active in rows if status == 'active' and active == 1}
    if any(not isinstance(code, str) or code not in active_codes for code in allowed):
        raise PermissionError('Actor profile unavailable')
    return allowed


def _build_user_profile(conn, user, *, strict=False):
    roles = conn.execute(
        text(
            """
            SELECT r.RoleName
            FROM Roles r
            JOIN UserRoles ur ON r.RoleID = ur.RoleID
            WHERE ur.UserID = :uid
            """
        ),
        {"uid": user[0]},
    ).fetchall()

    role_list = [r[0] for r in roles]
    if strict and any(not isinstance(role, str) or not role.strip() for role in role_list):
        raise PermissionError('Actor profile unavailable')

    try:
        dept_rows = conn.execute(
            text("SELECT Department FROM UserDepartments WHERE UserID = :uid"),
            {"uid": user[0]}
        ).fetchall()
        allowed_departments = [r[0] for r in dept_rows]
    except Exception:
        if strict:
            raise
        allowed_departments = []

    # P0#1: loai bo phong ban da disable/archive khoi allowed_departments.
    # Giu lai: sentinel CHUNG, phong khong co trong bang Departments (legacy), va phong active.
    if strict:
        allowed_departments = _strict_departments(conn, allowed_departments)
    elif allowed_departments:
        try:
            from mech_chatbot.db.repositories.catalog import list_known_departments
            _active_codes = {d["code"] for d in list_known_departments(active_only=True)}
            _all_codes = {d["code"] for d in list_known_departments(active_only=False)}
            allowed_departments = [
                d for d in allowed_departments
                if d == SHARE_ALL_DEPARTMENT or d not in _all_codes or d in _active_codes
            ]
            if not allowed_departments:
                allowed_departments = [SHARE_ALL_DEPARTMENT]
        except Exception:
            pass  # loi tra cuu -> giu nguyen (tuong thich nguoc, khong pha login)

    # LUU Y: KHONG tu dong them user[3] (department display label nhu "Technical")
    # vao allowed_departments. Department chi la nhan hien thi; quyen xem tai lieu
    # duoc kiem soat duy nhat boi bang UserDepartments.
    if not allowed_departments and not strict:
        # Fallback an toan: neu UserDepartments chua co du lieu, chi cho xem CHUNG
        allowed_departments = [SHARE_ALL_DEPARTMENT]
        logger.warning(
            f"User '{user[1]}' khong co ban ghi trong UserDepartments. "
            "Fallback cho phep xem CHUNG. Bo sung ban ghi vao dbo.UserDepartments de cap nhat."
        )

    try:
        clr = conn.execute(
            text("SELECT MaxLevel FROM UserSecurityClearance WHERE UserID = :uid"),
            {"uid": user[0]},
        ).fetchone()
        # An toan mac dinh: thieu/khong hop le -> 'public' (khong phai 'internal')
        max_security_level = resolve_clearance(clr[0] if clr else None)
    except Exception:
        if strict:
            raise
        max_security_level = DEFAULT_MAX_SECURITY_LEVEL

    # P1.2: RBAC chieu thu 3 - site. List rong = KHONG gioi han theo site.
    try:
        site_rows = conn.execute(
            text("SELECT Site FROM UserSites WHERE UserID = :uid"),
            {"uid": user[0]},
        ).fetchall()
        allowed_sites = [r[0] for r in site_rows]
    except Exception:
        if strict:
            raise
        allowed_sites = []
    if strict and any(not isinstance(site, str) or not site.strip() for site in allowed_sites):
        raise PermissionError('Actor profile unavailable')

    return {
        "user_id": user[0],
        "username": user[1],
        "display_name": user[2],
        "department": user[3],
        "roles": role_list,
        "allowed_departments": allowed_departments,
        "max_security_level": max_security_level,
        "allowed_sites": allowed_sites,
        "preferred_language": _load_user_preferred_language(conn, user[0]),
    }


def update_user_preferred_language(user_id, lang):
    """Luu ngon ngu UI uu tien cho user. Tra ve True neu update thanh cong."""
    normalized = str(lang or "").strip().lower()
    if normalized not in _VALID_PREFERRED_LANGUAGES:
        return False
    if engine is None or user_id in (None, ""):
        return False
    try:
        with engine.begin() as conn:
            result = conn.execute(
                text("UPDATE Users SET PreferredLanguage = :lang WHERE UserID = :uid"),
                {"lang": normalized, "uid": user_id},
            )
        return getattr(result, "rowcount", 1) != 0
    except Exception as e:
        logger.error(
            "Loi cap nhat ngon ngu uu tien cho user_id=%s: %s",
            user_id,
            e,
            exc_info=True,
        )
        return False


def _load_strict_user_profile(user_id, username):
    if type(user_id) is not int or user_id <= 0 or not isinstance(username, str) or not username.strip():
        raise ValueError('An explicit actor identity is required')
    try:
        with evidence_read_connection(engine) as conn:
            user = conn.execute(text(
                'SELECT UserID, Username, DisplayName, Department, IsActive FROM Users WHERE UserID = :uid'
            ), {'uid': user_id}).fetchone()
            if (not user or type(user[0]) is not int or user[0] != user_id or user[1] != username
                    or type(user[4]) not in (int, bool) or user[4] != 1):
                raise PermissionError('Actor profile unavailable')
            return _build_user_profile(conn, user, strict=True)
    except Exception:
        raise PermissionError('Actor profile unavailable') from None


def load_user_profile(user_id=None, username=None, *, strict=False):
    """Nap profile/quyen hien tai tu DB ma khong can mat khau.

    Dung cho cac service noi bo da xac thuc service-to-service. Neu truyen ca
    user_id va username thi username phai khop voi dong DB.
    Strict requires both identities, reuses the evidence snapshot and raises
    on actor/ACL failures; it never reads PasswordHash or invents permissions.
    """
    if strict:
        return _load_strict_user_profile(user_id, username)
    if engine is None:
        return None
    if user_id in (None, "") and not username:
        return None
    try:
        with engine.connect() as conn:
            if user_id not in (None, ""):
                user = conn.execute(
                    text(
                        """
                        SELECT UserID, Username, DisplayName, Department, IsActive, PasswordHash
                        FROM Users
                        WHERE UserID = :uid
                        """
                    ),
                    {"uid": user_id},
                ).fetchone()
            else:
                user = conn.execute(
                    text(
                        """
                        SELECT UserID, Username, DisplayName, Department, IsActive, PasswordHash
                        FROM Users
                        WHERE Username = :u
                        """
                    ),
                    {"u": username},
                ).fetchone()

            if not user or not user[4]:
                return None
            if username and str(user[1]) != str(username):
                logger.warning(
                    "Tu choi nap profile: user_id=%s khong khop username='%s'.",
                    user_id, username,
                )
                return None
            return _build_user_profile(conn, user)
    except Exception as e:
        logger.error(f"Loi truy van khi nap profile user '{username or user_id}': {e}", exc_info=True)
        return None


def authenticate_user(username, password, *, session_generation_reader=None):
    """Xac thuc nguoi dung. Tra ve dict thong tin user neu hop le, nguoc lai None.

    Thuan logic - khong dung Streamlit. Loi truy van DB duoc log qua logger.
    """
    # Kiem tra rate-limit TRUOC khi truy van DB (tranh lo thong tin user ton tai)
    if rate_limit.is_rate_limited(engine, username):
        logger.warning(f"[rate-limit] User '{username}' bi khoa do qua nhieu lan sai.")
        return None

    if engine is None:
        return None
    try:
        with engine.connect() as conn:
            user = conn.execute(
                text(
                    """
                    SELECT UserID, Username, DisplayName, Department, IsActive, PasswordHash
                    FROM Users
                    WHERE Username = :u
                    """
                ),
                {"u": username},
            ).fetchone()

            if not user:
                rate_limit.record_failure(engine, username)
                return None
            if not user[4]:  # IsActive = 0
                rate_limit.record_failure(engine, username)
                return None

            session_generation = None
            stored_hash = user[5]
            if session_generation_reader is not None:
                session_generation = session_generation_reader(int(user[0]))
                # Read credentials after the epoch, so a concurrent reset cannot
                # validate the old hash and mint a token in the new epoch.
                stored_hash = conn.execute(
                    text("SELECT PasswordHash FROM Users WHERE UserID = :uid AND IsActive = 1"),
                    {"uid": int(user[0])},
                ).scalar()

            # Verify bcrypt hash
            try:
                if stored_hash is None:
                    is_valid = False
                else:
                    is_valid = bcrypt.checkpw(
                        password.encode("utf-8"),
                        stored_hash.encode("utf-8"),
                    )
            except Exception:
                is_valid = False

            if not is_valid:
                rate_limit.record_failure(engine, username)
                return None

            rate_limit.clear_failures(engine, username)  # Dang nhap thanh cong -> xoa bo dem
            profile = _build_user_profile(conn, user)
            return {**profile, "session_generation": session_generation} if session_generation is not None else profile
    except Exception as e:
        logger.error(f"Loi truy van khi xac thuc user '{username}': {e}", exc_info=True)
        return None
