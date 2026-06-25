import streamlit as st
from sqlalchemy import text
from mech_chatbot.db.repository import (
    engine, queue_eta_seconds, set_job_priority, cancel_job, requeue_job,
    list_known_departments, list_known_sites,
)


def _fmt_eta(seconds):
    seconds = int(seconds or 0)
    if seconds <= 0:
        return "~0 phút"
    h, rem = divmod(seconds, 3600)
    m, s = divmod(rem, 60)
    if h:
        return f"~{h}h{m:02d}m"
    if m:
        return f"~{m} phút {s:02d}s"
    return f"~{s}s"


def run_queue():
    st.title("Quản Lý Tiến Trình Nạp Dữ Liệu")
    st.markdown("Xem danh sách các file đang được đưa vào xử lý bóc tách (Worker Queue).")

    if engine is None:
        st.error("Không thể kết nối đến Database.")
        return

    from mech_chatbot.auth import service as auth
    current_user = auth.get_current_user()
    is_admin = auth.has_role("admin")

    # --- P1.5: Bảng điều khiển ETA ---
    eta = queue_eta_seconds()
    m1, m2, m3 = st.columns(3)
    m1.metric("Đang chờ xử lý", eta.get("pending", 0))
    m2.metric("TB mỗi job", f"{eta.get('avg_seconds', 0):.0f}s")
    m3.metric("Dự kiến xử xong", _fmt_eta(eta.get("eta_seconds", 0)))

    # --- Bộ lọc ---
    fc1, fc2, fc3 = st.columns(3)
    with fc1:
        status_filter = st.selectbox(
            "Trạng thái",
            ["Tất cả", "pending", "pending_retry", "classifying", "extracting", "embedding", "failed", "waiting_quota"],
        )
    with fc2:
        if is_admin:
            dept_options = [d["code"] for d in list_known_departments(active_only=False)]
        else:
            dept_options = [d for d in (current_user.get("allowed_departments") or [current_user.get("department")]) if d]
        dept_filter = st.selectbox("Phòng ban", ["Tất cả"] + sorted(set(dept_options)))
    with fc3:
        search = st.text_input("Tìm file")

    try:
        with engine.connect() as conn:
            query_str = """
            SELECT 
                JobID, TenFile, ThuMuc, Status, ErrorMessage, 
                CreatedAt, UploadedBy, RetryCount, MaxRetry, 
                LockedBy, LockedAt, ProgressPercent, UpdatedAt,
                FailureType, NextRetryAt, QualityScore, QualityStatus, ExtractionReport,
                ISNULL(Priority, 100) AS Priority
            FROM dbo.IngestionJobs
            WHERE Status NOT IN ('pending_review', 'published', 'archived', 'superseded')
            """
            params = {}
            if status_filter != "Tất cả":
                query_str += " AND Status = :status"
                params["status"] = status_filter
            if dept_filter and dept_filter != "Tất cả":
                query_str += " AND ThuMuc = :dept_pick"
                params["dept_pick"] = dept_filter
            if search:
                query_str += " AND TenFile LIKE :search"
                params["search"] = f"%{search}%"
            if not is_admin:
                query_str += " AND (ThuMuc = :dept OR UploadedBy = :uname)"
                params["dept"] = current_user["department"]
                params["uname"] = current_user["username"]

            # Ưu tiên hiển thị giống thứ tự worker lấy job
            query_str += " ORDER BY ISNULL(Priority, 100) ASC, CreatedAt DESC"

            result = conn.execute(text(query_str), params)
            jobs = result.fetchall()

        if not jobs:
            st.info("Hiện không có file nào trong hàng đợi.")
            return

        st.subheader(f"Tổng số: {len(jobs)} jobs")

        for job in jobs:
            (
                job_id, ten_file, thu_muc, status, error_message,
                created_at, uploaded_by, retry_count, max_retry,
                locked_by, locked_at, progress_percent, updated_at,
                failure_type, next_retry_at, quality_score, quality_status, extraction_report,
                priority
            ) = job

            if status == "published":
                color = "green"
            elif status == "failed":
                color = "red"
            elif status == "pending_review":
                color = "orange"
            elif status == "classifying":
                color = "purple"
            else:
                color = "blue"

            prio_badge = "🔥 GAP" if (priority or 100) < 50 else ("⬇️ thấp" if (priority or 100) > 150 else "thường")
            with st.expander(f"[{status.upper()}] {ten_file} (Job: {job_id}) · Ưu tiên: {prio_badge} - {created_at.strftime('%Y-%m-%d %H:%M:%S')}"):
                st.write(f"**Thư mục:** {thu_muc} | **Người tải lên:** {uploaded_by or 'Unknown'}")
                st.write(f"**Trạng thái:** <span style='color:{color}'>{status}</span> (Tiến độ: {progress_percent}%)", unsafe_allow_html=True)
                st.progress((progress_percent or 0) / 100)
                st.write(f"**Ưu tiên (Priority):** {priority} (nhỏ hơn = ưu tiên hơn)")
                st.write(f"**Debug Info:** Retry: {retry_count}/{max_retry} | LockedBy: {locked_by} at {locked_at}")
                st.write(f"**Failure Type:** {failure_type or 'N/A'}")
                st.write(f"**Next Retry At:** {next_retry_at or 'N/A'}")
                st.write(f"**Quality:** {quality_status or 'N/A'} ({quality_score or 0}/100)")
                if error_message:
                    st.write(f"**Thông báo:** {error_message}")

                import json
                if extraction_report:
                    try:
                        report_obj = json.loads(extraction_report)
                        st.json(report_obj)
                    except Exception:
                        st.text(extraction_report)

                # --- P1.5: Hành động (admin) ---
                if is_admin:
                    st.markdown("---")
                    ac1, ac2, ac3 = st.columns([2, 1, 1])
                    with ac1:
                        new_prio = st.number_input(
                            "Đặt ưu tiên", value=int(priority or 100), step=10, min_value=0, max_value=1000,
                            key=f"prio_{job_id}",
                        )
                        if st.button("Lưu ưu tiên", key=f"saveprio_{job_id}"):
                            if set_job_priority(job_id, int(new_prio)):
                                st.success("Đã cập nhật ưu tiên.")
                                st.rerun()
                            else:
                                st.error("Không cập nhật được ưu tiên.")
                    with ac2:
                        if status in ["failed", "waiting_quota", "pending_retry"]:
                            if st.button("Thử lại", key=f"retry_{job_id}"):
                                if requeue_job(job_id):
                                    st.success("Đã đưa lại vào hàng đợi!")
                                    st.rerun()
                                else:
                                    st.error("Thử lại thất bại.")
                    with ac3:
                        if st.button("Hủy job", key=f"cancel_{job_id}", type="secondary"):
                            if cancel_job(job_id, canceled_by=current_user["username"]):
                                st.success("Đã hủy job.")
                                st.rerun()
                            else:
                                st.warning("Không thể hủy (job có thể đã hoàn tất hoặc đang publish).")
                else:
                    # Non-admin: chỉ retry job của mình khi lỗi
                    if status in ["failed", "waiting_quota"]:
                        if st.button(f"Thử lại Job {job_id}", key=f"retry_{job_id}"):
                            if requeue_job(job_id):
                                st.success("Đã đưa lại vào hàng đợi!")
                                st.rerun()
                            else:
                                st.error("Thử lại thất bại.")

    except Exception as e:
        st.error(f"Lỗi truy xuất dữ liệu: {e}")
