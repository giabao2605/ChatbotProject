import streamlit as st
from sqlalchemy import text
from db_logic import engine

def run_queue():
    st.title("Quản Lý Tiến Trình Nạp Dữ Liệu")
    st.markdown("Xem danh sách các file đang được đưa vào xử lý bóc tách (Worker Queue).")

    if engine is None:
        st.error("Không thể kết nối đến Database.")
        return

    import auth
    current_user = auth.get_current_user()
    is_admin = auth.has_role("admin")

    try:
        with engine.connect() as conn:
            query_str = """
            SELECT 
                JobID, TenFile, ThuMuc, Status, ErrorMessage, 
                CreatedAt, UploadedBy, RetryCount, MaxRetry, 
                LockedBy, LockedAt, ProgressPercent, UpdatedAt,
                FailureType, NextRetryAt, QualityScore, QualityStatus, ExtractionReport
            FROM dbo.IngestionJobs
            WHERE Status NOT IN ('pending_review', 'published', 'rejected', 'archived', 'superseded')
            """
            params = {}
            if not is_admin:
                query_str += " AND (ThuMuc = :dept OR UploadedBy = :uname)"
                params["dept"] = current_user["department"]
                params["uname"] = current_user["username"]
                
            query_str += " ORDER BY CreatedAt DESC"
            
            result = conn.execute(text(query_str), params)
            jobs = result.fetchall()

        if not jobs:
            st.info("Hiện không có file nào trong hàng đợi.")
            return

        st.subheader(f"Tổng số: {len(jobs)} jobs")
        
        # Tạo bảng hiển thị
        for job in jobs:
            (
                job_id, ten_file, thu_muc, status, error_message,
                created_at, uploaded_by, retry_count, max_retry,
                locked_by, locked_at, progress_percent, updated_at,
                failure_type, next_retry_at, quality_score, quality_status, extraction_report
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

            with st.expander(f"[{status.upper()}] {ten_file} (Job: {job_id}) - {created_at.strftime('%Y-%m-%d %H:%M:%S')}"):
                st.write(f"**Thư mục:** {thu_muc} | **Người tải lên:** {uploaded_by or 'Unknown'}")
                st.write(f"**Trạng thái:** <span style='color:{color}'>{status}</span> (Tiến độ: {progress_percent}%)", unsafe_allow_html=True)
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
                
                if status in ["failed", "waiting_quota"]:
                    if st.button(f"Retry Job {job_id}", key=f"retry_{job_id}"):
                        with engine.begin() as conn:
                            conn.execute(
                                text("""
                                    UPDATE dbo.IngestionJobs
                                    SET Status = 'pending',
                                        ErrorMessage = NULL,
                                        FailureType = NULL,
                                        NextRetryAt = NULL,
                                        RetryCount = 0,
                                        LockedBy = NULL,
                                        LockedAt = NULL,
                                        ProgressPercent = 0,
                                        UpdatedAt = GETDATE()
                                    WHERE JobID = :id
                                """),
                                {"id": job_id}
                            )
                        st.success("Đã đưa lại vào hàng đợi!")
                        st.rerun()

    except Exception as e:
        st.error(f"Lỗi truy xuất dữ liệu: {e}")
