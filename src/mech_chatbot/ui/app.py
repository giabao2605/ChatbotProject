import sys
import os

# Đảm bảo src/ luôn trong sys.path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

import streamlit as st
from mech_chatbot.auth import service as auth
from mech_chatbot.config import theme as ui_theme

st.set_page_config(page_title="Trợ Lý Tài Liệu Nội Bộ", layout="wide", initial_sidebar_state="expanded")
ui_theme.inject_global_css()

auth.check_auth()
user = auth.get_current_user()
if user is None:
    st.stop()


def can_access_page(allowed_roles):
    if not allowed_roles:
        return True
    roles = user.get("roles", [])
    if "admin" in roles:
        return True
    return any(role in roles for role in allowed_roles)


PAGES = [
    {"key": "dashboard", "label": "Tổng quan", "roles": ["admin"]},
    {"key": "chatbot", "label": "Chatbot hỏi đáp", "roles": ["viewer", "uploader", "reviewer", "admin"]},
    {"key": "help", "label": "Hướng dẫn", "roles": ["viewer", "uploader", "reviewer", "admin"]},
    {"key": "upload", "label": "Tải tài liệu", "roles": ["uploader", "admin"]},
    {"key": "queue", "label": "Tiến trình ingest", "roles": ["uploader", "admin"]},
    {"key": "review", "label": "Duyệt tài liệu", "roles": ["reviewer", "admin"]},
    {"key": "documents", "label": "Kho tài liệu", "roles": ["reviewer", "admin"]},
    {"key": "feedback", "label": "Feedback Loop", "roles": ["reviewer", "admin"]},
    {"key": "users", "label": "Người dùng", "roles": ["admin"]},
    {"key": "materials", "label": "Từ điển vật tư", "roles": ["admin"]},
    {"key": "analytics", "label": "Báo cáo sử dụng", "roles": ["admin"]},
    {"key": "audit", "label": "Audit Log", "roles": ["admin"]},
    {"key": "settings", "label": "Cấu hình", "roles": ["admin"]},
]

available_pages = [page for page in PAGES if can_access_page(page["roles"])]
if not available_pages:
    st.error("Tài khoản chưa được gán quyền truy cập trang nào.")
    st.stop()

if "nav_page" not in st.session_state or st.session_state["nav_page"] not in [p["key"] for p in available_pages]:
    st.session_state["nav_page"] = available_pages[0]["key"]

# Cho phep cac trang yeu cau dieu huong (vd nut 'Them file moi') — xu ly TRUOC khi tao radio
if "_nav_request" in st.session_state:
    _req = st.session_state.pop("_nav_request")
    if _req in [p["key"] for p in available_pages]:
        st.session_state["nav_page"] = _req

with st.sidebar:
    st.markdown("### Trợ Lý Tài Liệu Nội Bộ")
    st.caption("Quản trị dữ liệu kỹ thuật & hỏi đáp RAG")
    st.markdown("---")
    st.markdown(f"**Xin chào, {user['display_name']}!**")
    st.caption(f"Phòng ban: {user.get('department')}")
    st.caption("Role: " + ", ".join(user.get("roles", [])))

    # C12: hien thi ro quyen cua nguoi dung (phong ban / khu / muc mat)
    with st.expander("🔑 Quyền của tôi"):
        _allowed_depts = user.get("allowed_departments") or ([user.get("department")] if user.get("department") else [])
        _allowed_sites = user.get("allowed_sites") or []
        st.markdown("**Phòng ban được xem:**")
        st.write(", ".join([d for d in _allowed_depts if d]) or "(chưa gán)")
        st.markdown("**Khu / Site được xem:**")
        st.write(", ".join([s for s in _allowed_sites if s]) or "(không giới hạn)")
        st.markdown("**Mức mật tối đa:**")
        st.write(user.get("max_security_level", "internal"))
        st.caption("Nếu không thấy tài liệu mong đợi, hãy liên hệ admin để được cấp thêm quyền.")
    st.markdown("---")

    page_labels = {page["key"]: f"{page['label']}" for page in available_pages}
    st.radio(
        "Điều hướng",
        options=list(page_labels.keys()),
        format_func=lambda key: page_labels[key],
        key="nav_page",
        label_visibility="collapsed",
    )

    st.markdown("---")
    if st.button("Đăng xuất", use_container_width=True):
        auth.logout()

page = st.session_state["nav_page"]

if page == "dashboard":
    from mech_chatbot.ui.pages import dashboard as app_dashboard
    app_dashboard.run_dashboard()
elif page == "chatbot":
    from mech_chatbot.ui.pages import chatbot as app_chatbot
    app_chatbot.run_chat()
elif page == "help":
    from mech_chatbot.ui.pages import help as app_help
    app_help.run_help()
elif page == "upload":
    from mech_chatbot.ui.pages import upload as app_upload
    app_upload.run_upload()
elif page == "queue":
    from mech_chatbot.ui.pages import queue as app_queue
    app_queue.run_queue()
elif page == "review":
    from mech_chatbot.ui.pages import admin as app_admin
    app_admin.run_admin()
elif page == "documents":
    from mech_chatbot.ui.pages import documents as app_documents
    app_documents.run_documents()
elif page == "feedback":
    from mech_chatbot.ui.pages import feedback as app_feedback
    app_feedback.run_feedback()
elif page == "users":
    from mech_chatbot.ui.pages import users as app_users
    app_users.run_users()
elif page == "materials":
    from mech_chatbot.ui.pages import materials as app_materials
    app_materials.run_materials()
elif page == "analytics":
    from mech_chatbot.ui.pages import analytics as app_analytics
    app_analytics.run_analytics()
elif page == "audit":
    from mech_chatbot.ui.pages import audit as app_audit
    app_audit.run_audit()
elif page == "settings":
    from mech_chatbot.ui.pages import settings as app_settings
    app_settings.run_settings()
