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

import streamlit as st
import streamlit.components.v1 as components

from mech_chatbot.auth import service as auth

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
    src = f"{base_url}/?ctx={token}&lang={lang}"
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
    components.iframe(src, height=900, scrolling=False)
