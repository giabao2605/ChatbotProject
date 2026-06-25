from pathlib import Path
import threading
import time
from collections import defaultdict

import bcrypt
import streamlit as st
import streamlit.components.v1 as components
from sqlalchemy import text
from mech_chatbot.db.repository import engine

# ---------------------------------------------------------------------------
# Rate-limit / Account lockout (in-process, resets khi restart server)
# ---------------------------------------------------------------------------
_MAX_FAILURES   = 5        # So lan sai toi da
_LOCKOUT_SECONDS = 300     # Khoa 5 phut sau khi vuot nguong
_WINDOW_SECONDS  = 600     # Cua so dem: reset neu khong sai them trong 10 phut

_lock   = threading.Lock()
_fails  = defaultdict(list)  # username -> [timestamp, ...]

def _is_rate_limited(username: str) -> bool:
    """Kiem tra xem username co dang bi khoa do nhieu lan dang nhap sai khong."""
    now = time.monotonic()
    with _lock:
        attempts = _fails[username]
        # Xoa cac lan cu hon cua so
        _fails[username] = [t for t in attempts if now - t < _WINDOW_SECONDS]
        return len(_fails[username]) >= _MAX_FAILURES

def _record_failure(username: str):
    """Ghi nhan mot lan dang nhap sai."""
    now = time.monotonic()
    with _lock:
        _fails[username].append(now)

def _clear_failures(username: str):
    """Xoa lich su sai sau khi dang nhap thanh cong."""
    with _lock:
        _fails.pop(username, None)

def authenticate_user(username, password):
    # Kiem tra rate-limit TRUOC khi truy van DB (tranh lo thong tin user ton tai)
    if _is_rate_limited(username):
        from mech_chatbot.config.logging import logger
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
                _record_failure(username)
                return None
            if not user[4]:  # IsActive = 0
                _record_failure(username)
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
                _record_failure(username)
                return None
                
            _clear_failures(username)  # Dang nhap thanh cong -> xoa bộ dem
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

            # LUU Y: KHONG tu dong them user[3] (department display label nhu "Ky_Thuat")
            # vao allowed_departments. Department chi la nhan hien thi; quyen xem tai lieu
            # duoc kiem soat duy nhat boi bang UserDepartments (chua ten to san xuat thuc te:
            # To_Han, To_Dap, CHUNG...). Them "Ky_Thuat" vao day se khien filter Qdrant
            # tim gia tri khong ton tai trong metadata.phong_ban_quyen.
            if not allowed_departments:
                # Fallback an toan: neu UserDepartments chua co du lieu, chi cho xem CHUNG
                allowed_departments = ["CHUNG"]
                from mech_chatbot.config.logging import logger
                logger.warning(
                    f"User '{user[1]}' khong co ban ghi trong UserDepartments. "
                    "Fallback cho phep xem CHUNG. Chay migration fix_rbac_seed.sql de cap nhat."
                )
            
            try:
                clr = conn.execute(
                    text("SELECT MaxLevel FROM UserSecurityClearance WHERE UserID = :uid"),
                    {"uid": user[0]},
                ).fetchone()
                max_security_level = clr[0] if clr else "internal"
            except Exception:
                max_security_level = "internal"

            # P1.2: RBAC chieu thu 3 — site. List rong = KHONG gioi han theo site.
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
    except Exception as e:
        st.error(f"Lỗi truy vấn: {e}")
        return None

_PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent.parent
_LIQUID_LOGIN_COMPONENT = components.declare_component(
    "liquid_login",
    path=str(_PROJECT_ROOT / "components" / "liquid_login"),
)


def _inject_login_page_css():
    """Chỉ reset layout Streamlit; hiệu ứng login nằm nguyên trong custom component."""
    st.markdown(
        """
        <style>
        /* Giữ nền mặc định của Streamlit, chỉ reset layout cho trang login */
        [data-testid="stAppViewContainer"] .block-container {
            max-width: 100% !important;
            padding: 0 !important;
            margin: 0 !important;
        }
        [data-testid="stVerticalBlock"],
        [data-testid="element-container"],
        .stCustomComponentV1 {
            height: 100vh !important;
            min-height: 100vh !important;
            padding: 0 !important;
            margin: 0 !important;
        }
        iframe {
            display: block !important;
            width: 100vw !important;
            height: 100vh !important;
            min-height: 100vh !important;
            border: none !important;
            background: transparent !important;
        }
        header[data-testid="stHeader"] { background: transparent; }
        #MainMenu, footer { visibility: hidden; }
        </style>
        """,
        unsafe_allow_html=True,
    )

def login_screen():
    _inject_login_page_css()

    error_message = st.session_state.pop("login_error", "")
    result = _LIQUID_LOGIN_COMPONENT(error=error_message, default=None, key="liquid_login")

    if result and result.get("submittedAt"):
        username = (result.get("username") or "").strip()
        password = result.get("password") or ""
        user_data = authenticate_user(username, password)
        if user_data:
            st.session_state["user"] = user_data
            st.rerun()

        # Thong bao khac nhau: bi khoa vs sai mat khau thuong
        if _is_rate_limited(username):
            st.session_state["login_error"] = (
                f"Tài khoản tạm thời bị khóa do đăng nhập sai quá {_MAX_FAILURES} lần. "
                f"Vui lòng thử lại sau {_LOCKOUT_SECONDS // 60} phút."
            )
        else:
            st.session_state["login_error"] = "Sai tên đăng nhập hoặc mật khẩu."
        st.rerun()

    return False

def check_auth():
    if "user" not in st.session_state:
        logged_in = login_screen()
        if not logged_in:
            st.stop()

def get_current_user():
    return st.session_state.get("user")

def has_role(role_name):
    user = get_current_user()
    if not user:
        return False
    return role_name in user["roles"] or "admin" in user["roles"]

def logout():
    if "user" in st.session_state:
        del st.session_state["user"]
    st.rerun()



def is_admin():
    return has_role("admin")


def get_allowed_departments():
    user = get_current_user()
    if not user:
        return []
    allowed = list(user.get("allowed_departments") or [])
    dept = user.get("department")
    if dept and dept not in allowed:
        allowed.append(dept)
    return allowed
