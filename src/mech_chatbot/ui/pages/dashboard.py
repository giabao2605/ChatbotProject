import streamlit as st
from sqlalchemy import text
from mech_chatbot.db.repository import engine, dashboard_by_department
from mech_chatbot.auth import service as auth
def _scalar(conn, sql, params=None):
    try:
        return conn.execute(text(sql), params or {}).scalar() or 0
    except Exception:
        return 0


def run_dashboard():
    st.title("Tổng quan hệ thống")
    st.caption("Theo dõi nhanh trạng thái tài liệu, ingest, chatbot và feedback.")

    if not auth.has_role("admin"):
        st.error("Chỉ admin được xem trang tổng quan.")
        return
    if engine is None:
        st.error("Không thể kết nối Database.")
        return

    with engine.connect() as conn:
        stats = {
            "total_docs": _scalar(conn, "SELECT COUNT(*) FROM TaiLieu"),
            "pending_review": _scalar(conn, "SELECT COUNT(*) FROM TaiLieu WHERE ReviewStatus = 'pending_review'"),
            "published_docs": _scalar(conn, "SELECT COUNT(*) FROM TaiLieu WHERE ReviewStatus = 'approved' AND LifecycleStatus = 'published'"),
            "running_jobs": _scalar(conn, "SELECT COUNT(*) FROM dbo.IngestionJobs WHERE Status IN ('pending','pending_retry','classifying','extracting','embedding','publishing')"),
            "failed_jobs": _scalar(conn, "SELECT COUNT(*) FROM dbo.IngestionJobs WHERE Status IN ('failed','waiting_quota')"),
            "today_chats": _scalar(conn, "SELECT COUNT(*) FROM LichSuChat WHERE CAST(ThoiGian AS DATE) = CAST(GETDATE() AS DATE)"),
            "pending_feedback": _scalar(conn, "SELECT COUNT(*) FROM FeedbackReview WHERE ISNULL(AddedToGoldenSet, 0) = 0"),
        }

    c1, c2, c3, c4 = st.columns(4)
    c1.metric("Tổng tài liệu", stats["total_docs"])
    c2.metric("Chờ duyệt", stats["pending_review"])
    c3.metric("Job đang xử lý", stats["running_jobs"])
    c4.metric("Job lỗi", stats["failed_jobs"])

    c5, c6, c7 = st.columns(3)
    c5.metric("Đã published", stats["published_docs"])
    c6.metric("Chat hôm nay", stats["today_chats"])
    c7.metric("Feedback cần xử lý", stats["pending_feedback"])

    st.markdown("---")
    st.subheader("Thong ke theo phong ban")
    render_department_breakdown()

    st.markdown("---")
    left, right = st.columns(2)
    with left:
        st.subheader("Tài liệu mới")
        render_recent_documents()
    with right:
        st.subheader("Job lỗi gần đây")
        render_recent_failed_jobs()


def render_department_breakdown():
    """P1.6: bang suc khoe theo tung phong ban."""
    try:
        rows = dashboard_by_department()
    except Exception as e:
        st.error(f"Khong tai duoc thong ke theo phong: {e}")
        return
    if not rows:
        st.info("Chua co du lieu theo phong ban.")
        return
    table = [
        {
            "Phong ban": r["department"],
            "Tong tai lieu": r["total"],
            "Da publish": r["published"],
            "Cho duyet": r["pending_review"],
            "Mat (confidential)": r["confidential"],
            "Job dang chay": r["running_jobs"],
            "Job loi": r["failed_jobs"],
        }
        for r in rows
    ]
    st.dataframe(table, use_container_width=True, hide_index=True)
    total_failed = sum(r["failed_jobs"] for r in rows)
    if total_failed:
        st.warning(f"Co {total_failed} job ingest dang loi tren toan he thong - kiem tra tab Hang doi.")


def render_recent_documents():
    try:
        with engine.connect() as conn:
            rows = conn.execute(text("""
                SELECT TOP 10 DocID, TenFile, ThuMuc, ReviewStatus, LifecycleStatus, NgayTaiLen
                FROM TaiLieu
                ORDER BY NgayTaiLen DESC
            """)).fetchall()
    except Exception as e:
        st.error(f"Không tải được tài liệu mới: {e}")
        return

    if not rows:
        st.info("Chưa có tài liệu.")
        return
    for doc_id, ten_file, thu_muc, review_status, lifecycle_status, ngay_tai_len in rows:
        st.write(f"**{ten_file}**  \n`{thu_muc}` · `{review_status}` · `{lifecycle_status}` · {ngay_tai_len}")


def render_recent_failed_jobs():
    try:
        with engine.connect() as conn:
            rows = conn.execute(text("""
                SELECT TOP 10 JobID, TenFile, ThuMuc, Status, ErrorMessage, UpdatedAt
                FROM dbo.IngestionJobs
                WHERE Status IN ('failed','waiting_quota')
                ORDER BY UpdatedAt DESC
            """)).fetchall()
    except Exception as e:
        st.error(f"Không tải được job lỗi: {e}")
        return

    if not rows:
        st.success("Không có job lỗi.")
        return
    for job_id, ten_file, thu_muc, status, error_message, updated_at in rows:
        with st.expander(f"[{status}] {ten_file}"):
            st.write(f"**JobID:** {job_id}")
            st.write(f"**Thư mục:** {thu_muc}")
            st.write(f"**Cập nhật:** {updated_at}")
            st.error(error_message or "Không có thông báo lỗi.")
