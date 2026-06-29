import os
import streamlit as st
from sqlalchemy import text
from mech_chatbot.auth import service as auth
from mech_chatbot.db.repository import (
    engine,
    get_all_app_settings,
    set_app_setting,
    count_docs_by_department,
)


def _check_database():
    if engine is None:
        return False, "Engine chưa khởi tạo."
    try:
        with engine.connect() as conn:
            conn.execute(text("SELECT 1"))
        return True, "Kết nối Database OK."
    except Exception as e:
        return False, f"Lỗi: {e}"


def _check_qdrant():
    url = os.getenv("QDRANT_URL", "")
    if not url:
        return False, "Chưa cấu hình QDRANT_URL."
    try:
        import urllib.request
        req = urllib.request.Request(url.rstrip("/") + "/collections")
        api_key = os.getenv("QDRANT_API_KEY", "")
        if api_key:
            req.add_header("api-key", api_key)
        with urllib.request.urlopen(req, timeout=5) as resp:
            code = resp.getcode()
        if code == 200:
            return True, f"Qdrant OK ({url})."
        return False, f"Qdrant trả về HTTP {code}."
    except Exception as e:
        return False, f"Lỗi kết nối Qdrant: {e}"


def _check_embedding():
    model = os.getenv("EMBEDDING_MODEL", "BAAI/bge-m3")
    dim = os.getenv("EMBEDDING_DIM", "1024")
    if model:
        return True, f"Embedding: {model} (dim={dim})."
    return False, "Chưa cấu hình EMBEDDING_MODEL."


def _check_llm():
    keys = ["OPENAI_API_KEY", "GOOGLE_API_KEY", "GEMINI_API_KEY", "ANTHROPIC_API_KEY", "LLM_API_KEY"]
    present = [k for k in keys if os.getenv(k)]
    if present:
        return True, "Đã cấu hình API key cho LLM: " + ", ".join(present)
    return False, "Chưa thấy API key LLM trong môi trường (OPENAI/GOOGLE/GEMINI/ANTHROPIC/LLM_API_KEY)."


def _status_line(label, ok, msg):
    icon = "✅" if ok else "❌"
    if ok:
        st.success(f"{icon} **{label}:** {msg}")
    else:
        st.error(f"{icon} **{label}:** {msg}")


def run_settings():
    st.title("Cấu hình hệ thống")
    if not auth.has_role("admin"):
        st.error("Chỉ admin được truy cập cấu hình.")
        return

    # ---------------- Kiem tra suc khoe he thong ----------------
    st.subheader("Kiểm tra sức khỏe hệ thống")
    st.caption("Kiểm tra nhanh kết nối tới Database, Qdrant, Embedding và LLM.")
    if st.button("Chạy kiểm tra ngay", type="primary"):
        db_ok, db_msg = _check_database()
        _status_line("Database", db_ok, db_msg)
        q_ok, q_msg = _check_qdrant()
        _status_line("Qdrant (vector DB)", q_ok, q_msg)
        e_ok, e_msg = _check_embedding()
        _status_line("Embedding model", e_ok, e_msg)
        l_ok, l_msg = _check_llm()
        _status_line("LLM", l_ok, l_msg)

    # ---------------- Thong ke he thong ----------------
    st.markdown("---")
    st.subheader("Thống kê hệ thống")
    try:
        doc_counts = count_docs_by_department() or {}
        total_docs = sum(doc_counts.values())
        c1, c2 = st.columns(2)
        c1.metric("Tổng số tài liệu", total_docs)
        c2.metric("Số phòng ban có tài liệu", len([k for k, v in doc_counts.items() if v]))
        if doc_counts:
            st.markdown("**Tài liệu theo phòng ban**")
            st.dataframe(
                [{"Phòng ban": k, "Số tài liệu": v} for k, v in sorted(doc_counts.items(), key=lambda x: -x[1])],
                use_container_width=True, hide_index=True,
            )
    except Exception as e:
        st.warning(f"Không lấy được thống kê: {e}")

    # ---------------- Cau hinh ung dung (AppSettings) ----------------
    st.markdown("---")
    st.subheader("Tham số cấu hình ứng dụng")
    try:
        settings = get_all_app_settings(use_cache=False)
    except Exception as e:
        settings = {}
        st.warning(f"Không đọc được cấu hình: {e}")
    with st.form("app_settings_form"):
        warn_days = st.number_input(
            "Số ngày cảnh báo trước khi tài liệu hết hiệu lực",
            min_value=0, max_value=3650,
            value=int(settings.get("expiry_warning_days", 30) or 30), step=1,
            help="Tài liệu có ExpiryDate trong khoảng này sẽ được cảnh báo 'sắp hết hạn'.",
        )
        top_k = st.number_input(
            "Số đoạn tài liệu tối đa khi tìm kiếm chung (RAG general top_k)",
            min_value=1, max_value=200,
            value=int(settings.get("rag_general_top_k", 30) or 30), step=1,
            help="Số chunk tối đa lấy khi câu hỏi không gắn mã tài liệu cụ thể.",
        )
        if st.form_submit_button("Lưu cấu hình", type="primary"):
            try:
                cu = auth.get_current_user() or {}
                by = cu.get("username", "admin")
                set_app_setting("expiry_warning_days", int(warn_days), updated_by=by)
                set_app_setting("rag_general_top_k", int(top_k), updated_by=by)
                st.success("Đã lưu cấu hình ứng dụng.")
                st.rerun()
            except Exception as e:
                st.error(f"Lỗi lưu cấu hình: {e}")

    # ---------------- Thong tin RAG / Database ----------------
    st.markdown("---")
    st.subheader("RAG")
    rag_server_url = os.getenv("RAG_SERVER_URL", "")
    if rag_server_url:
        st.success("Đang dùng RAG Server API")
        st.code(rag_server_url)
    else:
        st.warning("Chưa có RAG_SERVER_URL. Hệ thống sẽ dùng subprocess worker.")
    st.write("**MAX_CONCURRENT_RAG:**", os.getenv("MAX_CONCURRENT_RAG", "2"))
    st.write("**RAG_WORKER_TIMEOUT:**", os.getenv("RAG_WORKER_TIMEOUT", "240"))

    st.subheader("Bảo mật")
    st.info("Trang này không hiển thị API key, password database hoặc token.")
