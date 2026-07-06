"""Bien gioi Streamlit cho auth (session, login screen, role helpers).

P0 refactor:
- Logic xac thuc thuan da chuyen sang `auth/core.py` (khong phu thuoc Streamlit/i18n).
- File nay chi con phan dung cham Streamlit: login screen, session_state, dieu huong.
- `authenticate_user` duoc re-export tu core de tuong thich nguoc
  (moi noi goi `auth.authenticate_user` van chay).

i18n van duoc dung o day vi day la tang UI (khong phai tang core).
"""
from pathlib import Path

import streamlit as st
import streamlit.components.v1 as components

from mech_chatbot.db.engine import engine
from mech_chatbot.auth import rate_limit
from mech_chatbot.auth.core import authenticate_user  # re-export (backward compat)

try:
    from mech_chatbot.ui.i18n import t, get_lang
except ImportError:
    def t(s, **kw): return s.format(**kw) if kw else s  # noqa: E731
    def get_lang(): return "vi"  # noqa: E731


_PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent.parent
_LIQUID_LOGIN_COMPONENT = components.declare_component(
    "liquid_login",
    path=str(_PROJECT_ROOT / "components" / "liquid_login"),
)


def _inject_login_page_css():
    """Chi reset layout Streamlit; hieu ung login nam nguyen trong custom component."""
    st.markdown(
        """
        <style>
        /* Giu nen mac dinh cua Streamlit, chi reset layout cho trang login */
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

    # Chi xu ly MOI lan submit DUY NHAT 1 lan. Custom component giu lai gia tri
    # cu (ke ca submittedAt) qua moi rerun -> neu khong chot lai se bi lap vo han
    # (sai mat khau -> set login_error -> st.rerun() -> xu ly lai -> spam restart).
    submitted_at = result.get("submittedAt") if result else None
    if submitted_at and st.session_state.get("_last_login_submit") != submitted_at:
        st.session_state["_last_login_submit"] = submitted_at
        username = (result.get("username") or "").strip()
        password = result.get("password") or ""
        user_data = authenticate_user(username, password)
        if user_data:
            st.session_state["user"] = user_data
            st.rerun()

        # Thong bao khac nhau: bi khoa vs sai mat khau thuong
        if rate_limit.is_rate_limited(engine, username):
            st.session_state["login_error"] = t(
                "Tai khoan tam thoi bi khoa do dang nhap sai qua {n} lan. "
                "Vui long thu lai sau {m} phut.",
                n=rate_limit.MAX_FAILURES, m=rate_limit.LOCKOUT_SECONDS // 60,
            )
        else:
            st.session_state["login_error"] = t("Sai ten dang nhap hoac mat khau.")
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
    # GD5 fix (lo ri du lieu): xoa SACH session khi dang xuat. Truoc day chi xoa "user"
    # nen chat_history/session_id van con trong session_state -> account dang nhap sau
    # van thay lai cuoc tro chuyen cu (vd admin hoi ve luong -> viewer login bi lo).
    st.session_state.clear()
    st.rerun()


def is_admin():
    return has_role("admin")


def get_allowed_departments():
    # GD5 fix nhat quan RBAC: chi tra ve allowed_departments tu UserDepartments (nguon su that).
    user = get_current_user()
    if not user:
        return []
    return list(user.get("allowed_departments") or [])
