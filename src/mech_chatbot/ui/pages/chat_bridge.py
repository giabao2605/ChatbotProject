"""
Cau noi nhung giao dien chat Next.js vao tab "Chatbot hoi dap" cua Streamlit.

Chi kich hoat khi bien moi truong USE_NEXTJS_CHAT duoc bat (1/true/yes/on).
Streamlit tao mot token co ky (HMAC-SHA256) chua thong tin dang nhap + phan quyen
cua nguoi dung hien tai, roi truyen sang app Next.js qua URL cua iframe. App Next.js
se xac thuc token nay bang cung CHAT_BRIDGE_SECRET truoc khi goi RAG server.
"""
import os
import json
import time
import hmac
import base64
import hashlib
import uuid
from datetime import date, timedelta
from urllib.parse import urlencode

import streamlit as st
import streamlit.components.v1 as components

from mech_chatbot.auth import service as auth
from mech_chatbot.services import clear_chat_history, get_all_sessions
from mech_chatbot.ui.i18n import t

try:
    from mech_chatbot.ui.i18n import get_lang
except Exception:  # pragma: no cover - fallback khi chay doc lap
    def get_lang():
        return "vi"


def _b64url(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode("ascii")


def build_context_token(ttl_seconds: int = 8 * 3600) -> str:
    """Tao token co ky chua ngu canh RBAC cua nguoi dung dang dang nhap."""
    secret = os.getenv("CHAT_BRIDGE_SECRET", "").encode("utf-8")
    if not secret:
        raise RuntimeError(
            "CHAT_BRIDGE_SECRET chua duoc cau hinh trong .env (can mot chuoi bi mat dai)."
        )

    user = auth.get_current_user() or {}
    payload = {
        "user_id": user.get("user_id"),
        "username": user.get("username"),
        "user_department": user.get("department"),
        "user_roles": user.get("roles", []),
        "allowed_departments": user.get("allowed_departments", []),
        "max_security_level": user.get("max_security_level", "internal"),
        "allowed_sites": user.get("allowed_sites", []),
        "response_language": get_lang() or "vi",
        "exp": int(time.time()) + ttl_seconds,
    }
    body = _b64url(
        json.dumps(payload, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
    )
    sig = _b64url(hmac.new(secret, body.encode("ascii"), hashlib.sha256).digest())
    return f"{body}.{sig}"


def render_nextjs_chat_sidebar() -> None:
    """Gop dieu khien chat vao sidebar Streamlit, tranh sidebar kep trong iframe."""
    user = auth.get_current_user() or {}
    is_admin = auth.has_role("admin")
    username = user.get("username")

    if "nextjs_chat_session_id" not in st.session_state:
        st.session_state["nextjs_chat_session_id"] = str(uuid.uuid4())

    st.markdown("---")
    st.markdown(
        """
        <style>
        .next-chat-sidebar-head {
            border: 1px solid var(--app-border, #273031);
            border-radius: 8px;
            background: rgba(255,255,255,0.025);
            padding: 10px;
            margin: 0 0 0.75rem;
        }
        .next-chat-sidebar-title {
            color: var(--app-text, #f2f5f4);
            font-size: 13px;
            font-weight: 760;
            line-height: 1.3;
        }
        .next-chat-sidebar-subtitle {
            color: var(--app-faint, #75817f);
            font-size: 12px;
            line-height: 1.35;
            margin-top: 2px;
        }
        .next-chat-sidebar-count {
            display: inline-flex;
            border: 1px solid var(--app-border, #273031);
            border-radius: 999px;
            color: var(--app-muted, #aab6b4);
            background: rgba(255,255,255,0.03);
            font-size: 11px;
            font-weight: 700;
            padding: 5px 9px;
            margin: 0.25rem 0 0.6rem;
        }
        div[data-testid="stSidebar"] div[data-testid="stHorizontalBlock"]:has(div[data-testid="column"] button) {
            align-items: center !important;
            gap: 0.35rem !important;
            border: 1px solid transparent !important;
            border-radius: 8px !important;
            min-height: 2.45rem !important;
            padding: 0.12rem !important;
            transition: background 0.12s ease, border-color 0.12s ease !important;
        }
        div[data-testid="stSidebar"] div[data-testid="stHorizontalBlock"]:has(div[data-testid="column"] button):hover {
            border-color: var(--app-border, #273031) !important;
            background: rgba(255,255,255,0.025) !important;
        }
        div[data-testid="stSidebar"] div[data-testid="stHorizontalBlock"]:has(div[data-testid="column"] button) div[data-testid="column"]:first-child {
            min-width: 0 !important;
        }
        div[data-testid="stSidebar"] div[data-testid="stHorizontalBlock"]:has(div[data-testid="column"] button) div[data-testid="column"]:first-child button {
            border: 0 !important;
            background: transparent !important;
            box-shadow: none !important;
            min-height: 2.12rem !important;
            overflow: hidden !important;
            padding: 0.32rem 0.45rem !important;
            justify-content: flex-start !important;
            text-align: left !important;
            color: var(--app-text, #f2f5f4) !important;
            line-height: 1.35 !important;
        }
        div[data-testid="stSidebar"] div[data-testid="stHorizontalBlock"]:has(div[data-testid="column"] button) div[data-testid="column"]:first-child button p {
            width: 100% !important;
            overflow: hidden !important;
            text-align: left !important;
            text-overflow: ellipsis !important;
            white-space: nowrap !important;
        }
        div[data-testid="stSidebar"] div[data-testid="stHorizontalBlock"]:has(div[data-testid="column"] button) div[data-testid="column"]:first-child button[kind="primary"] {
            background: rgba(105,214,159,0.12) !important;
            color: var(--app-accent-strong, #9af0c2) !important;
        }
        div[data-testid="stSidebar"] div[data-testid="stHorizontalBlock"]:has(div[data-testid="column"] button):hover div[data-testid="column"]:first-child button {
            background: transparent !important;
        }
        div[data-testid="stSidebar"] div[data-testid="stHorizontalBlock"]:has(div[data-testid="column"] button) div[data-testid="column"]:nth-child(2) {
            display: flex !important;
            align-items: center !important;
            justify-content: flex-end !important;
        }
        div[data-testid="stSidebar"] div[data-testid="stHorizontalBlock"]:has(div[data-testid="column"] button) div[data-testid="column"]:nth-child(2) button {
            position: relative !important;
            width: 2.12rem !important;
            min-width: 2.12rem !important;
            max-width: 2.12rem !important;
            height: 2.12rem !important;
            min-height: 2.12rem !important;
            border: 1px solid var(--app-border, #273031) !important;
            border-radius: 8px !important;
            background: transparent !important;
            box-shadow: none !important;
            color: var(--app-faint, #75817f) !important;
            font-size: 0 !important;
            opacity: 0 !important;
            padding: 0 !important;
            transition: opacity 0.12s ease, background 0.12s ease !important;
        }
        div[data-testid="stSidebar"] div[data-testid="stHorizontalBlock"]:has(div[data-testid="column"] button) div[data-testid="column"]:nth-child(2) button p {
            display: none !important;
        }
        div[data-testid="stSidebar"] div[data-testid="stHorizontalBlock"]:has(div[data-testid="column"] button) div[data-testid="column"]:nth-child(2) button::before {
            content: "" !important;
            display: block !important;
            width: 0.92rem !important;
            height: 0.92rem !important;
            background: currentColor !important;
            -webkit-mask: url("data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 512 512'%3E%3Cpath d='M497.941 273.941c18.745-18.745 18.745-49.137 0-67.882l-160-160c-18.745-18.745-49.136-18.746-67.883 0l-256 256c-18.745 18.745-18.745 49.137 0 67.882l96 96A48.004 48.004 0 0 0 144 480h356c6.627 0 12-5.373 12-12v-40c0-6.627-5.373-12-12-12H355.883l142.058-142.059zm-302.627-62.627l137.373 137.373L265.373 416H150.628l-80-80 124.686-124.686z'/%3E%3C/svg%3E") center / contain no-repeat !important;
            mask: url("data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 512 512'%3E%3Cpath d='M497.941 273.941c18.745-18.745 18.745-49.137 0-67.882l-160-160c-18.745-18.745-49.136-18.746-67.883 0l-256 256c-18.745 18.745-18.745 49.137 0 67.882l96 96A48.004 48.004 0 0 0 144 480h356c6.627 0 12-5.373 12-12v-40c0-6.627-5.373-12-12-12H355.883l142.058-142.059zm-302.627-62.627l137.373 137.373L265.373 416H150.628l-80-80 124.686-124.686z'/%3E%3C/svg%3E") center / contain no-repeat !important;
        }
        div[data-testid="stSidebar"] div[data-testid="stHorizontalBlock"]:has(div[data-testid="column"] button):hover div[data-testid="column"]:nth-child(2) button {
            opacity: 1 !important;
        }
        div[data-testid="stSidebar"] div[data-testid="stHorizontalBlock"]:has(div[data-testid="column"] button) div[data-testid="column"]:nth-child(2) button:hover {
            border-color: rgba(241,132,132,0.36) !important;
            background: rgba(241,132,132,0.1) !important;
            color: var(--app-danger, #f18484) !important;
        }
        </style>
        """,
        unsafe_allow_html=True,
    )
    st.markdown(
        f"""
        <div class="next-chat-sidebar-head">
            <div class="next-chat-sidebar-title">{t("Chatbot hỏi đáp")}</div>
            <div class="next-chat-sidebar-subtitle">{t("Lịch sử hỏi đáp tài liệu")}</div>
        </div>
        """,
        unsafe_allow_html=True,
    )

    if st.button(t("Cuộc trò chuyện mới"), key="nextjs_chat_new", use_container_width=True):
        st.session_state["nextjs_chat_session_id"] = str(uuid.uuid4())
        st.rerun()

    search_query = st.text_input(
        t("Tìm kiếm lịch sử"),
        key="nextjs_chat_history_search",
        placeholder=t("Tìm kiếm lịch sử"),
    )

    sessions = get_all_sessions(username=username, is_admin=is_admin)
    if search_query:
        q = search_query.lower().strip()
        sessions = [
            s for s in sessions
            if q in (s.get("cau_hoi") or "").lower() or q in (s.get("owner") or "").lower()
        ]
    st.markdown(
        f'<div class="next-chat-sidebar-count">{len(sessions)} {t("cuộc trò chuyện")}</div>',
        unsafe_allow_html=True,
    )

    today = date.today()
    yesterday = today - timedelta(days=1)
    grouped_sessions = {"Hôm nay": [], "Hôm qua": [], "Cũ hơn": []}
    for session in sessions:
        session_time = session.get("thoi_gian")
        session_date = session_time.date() if hasattr(session_time, "date") else None
        if session_date == today:
            grouped_sessions["Hôm nay"].append(session)
        elif session_date == yesterday:
            grouped_sessions["Hôm qua"].append(session)
        else:
            grouped_sessions["Cũ hơn"].append(session)

    shown = 0
    with st.container():
        st.markdown('<div class="chat-history-flat">', unsafe_allow_html=True)
        for group_name, group_sessions in grouped_sessions.items():
            if not group_sessions:
                continue
            st.caption("**" + t(group_name) + "**")
            for session in group_sessions:
                shown += 1
                session_id = session.get("session_id")
                if not session_id:
                    continue
                is_current = session_id == st.session_state.get("nextjs_chat_session_id")
                label = session.get("cau_hoi") or t("(không rõ)")
                if is_admin and session.get("owner"):
                    label = f"[{session.get('owner')}] {label}"
                col_chat, col_delete = st.columns([0.84, 0.16])
                with col_chat:
                    if st.button(
                        label,
                        key=f"nextjs_chat_open_{session_id}",
                        type="primary" if is_current else "secondary",
                        use_container_width=True,
                    ):
                        st.session_state["nextjs_chat_session_id"] = session_id
                        st.rerun()
                with col_delete:
                    if st.button(
                        t("Xóa"),
                        key=f"nextjs_chat_delete_{session_id}",
                        help=t("Xóa cuộc trò chuyện"),
                        use_container_width=True,
                    ):
                        clear_chat_history(session_id, username=username, is_admin=is_admin)
                        if is_current:
                            st.session_state["nextjs_chat_session_id"] = str(uuid.uuid4())
                        st.rerun()
        st.markdown("</div>", unsafe_allow_html=True)

    if shown == 0:
        st.caption(t("Chưa có lịch sử phù hợp."))


def render_nextjs_chat() -> None:
    """Render iframe chua app chat Next.js, thay cho giao dien chat Streamlit cu."""
    base_url = os.getenv("CHAT_UI_BASE_URL", "http://localhost:3000").rstrip("/")
    try:
        token = build_context_token()
    except Exception as exc:  # noqa: BLE001
        st.error(str(exc))
        st.info(
            "Kiem tra lai: (1) CHAT_BRIDGE_SECRET trong .env, "
            "(2) app Next.js dang chay o " + base_url
        )
        return

    lang = get_lang() or "vi"
    session_id = st.session_state.get("nextjs_chat_session_id") or str(uuid.uuid4())
    st.session_state["nextjs_chat_session_id"] = session_id
    query = urlencode({
        "ctx": token,
        "lang": lang,
        "embed": "1",
        "session": session_id,
    })
    src = f"{base_url}/?{query}"
    st.markdown(
        """
        <style>
        .block-container {
            max-width: 100% !important;
            padding: 0 !important;
        }

        div[data-testid="stVerticalBlock"],
        div[data-testid="stElementContainer"] {
            gap: 0 !important;
        }

        div[data-testid="stIFrame"],
        div[data-testid="stIFrame"] iframe,
        iframe[title="streamlit-component-lib"] {
            width: 100% !important;
            height: calc(100vh - 3.25rem) !important;
            min-height: calc(100vh - 3.25rem) !important;
            border: 0 !important;
            display: block !important;
        }
        </style>
        """,
        unsafe_allow_html=True,
    )
    components.iframe(src, height=760, scrolling=False)
