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
from mech_chatbot.config.constants import SHARE_ALL_DEPARTMENT
from mech_chatbot.config.logging import logger
from mech_chatbot.auth import rate_limit
from mech_chatbot.auth.security_policy import resolve_clearance, DEFAULT_MAX_SECURITY_LEVEL


def _build_user_profile(conn, user):
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

    try:
        dept_rows = conn.execute(
            text("SELECT Department FROM UserDepartments WHERE UserID = :uid"),
            {"uid": user[0]}
        ).fetchall()
        allowed_departments = [r[0] for r in dept_rows]
    except Exception:
        allowed_departments = []

    # P0#1: loai bo phong ban da disable/archive khoi allowed_departments.
    # Giu lai: sentinel CHUNG, phong khong co trong bang Departments (legacy), va phong active.
    if allowed_departments:
        try:
            from mech_chatbot.db.repository import list_known_departments
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
    if not allowed_departments:
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
        max_security_level = DEFAULT_MAX_SECURITY_LEVEL

    # P1.2: RBAC chieu thu 3 - site. List rong = KHONG gioi han theo site.
    try:
        site_rows = conn.execute(
            text("SELECT Site FROM UserSites WHERE UserID = :uid"),
            {"uid": user[0]},
        ).fetchall()
        allowed_sites = [r[0] for r in site_rows]
    except Exception:
        allowed_sites = []

    return {
        "user_id": user[0],
        "username": user[1],
        "display_name": user[2],
        "department": user[3],
        "roles": role_list,
        "allowed_departments": allowed_departments,
        "max_security_level": max_security_level,
        "allowed_sites": allowed_sites,
    }


def load_user_profile(user_id=None, username=None):
    """Nap profile/quyen hien tai tu DB ma khong can mat khau.

    Dung cho cac service noi bo da xac thuc service-to-service. Neu truyen ca
    user_id va username thi username phai khop voi dong DB.
    """
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


def authenticate_user(username, password):
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

            stored_hash = user[5]

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
            return _build_user_profile(conn, user)
    except Exception as e:
        logger.error(f"Loi truy van khi xac thuc user '{username}': {e}", exc_info=True)
        return None
