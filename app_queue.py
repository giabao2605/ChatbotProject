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
            query_str = "SELECT JobID, TenFile, ThuMuc, Status, ErrorMessage, CreatedAt, UploadedBy FROM IngestionJobs"
            params = {}
            if not is_admin:
                query_str += " WHERE ThuMuc = :dept OR UploadedBy = :uname"
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
            job_id, ten_file, thu_muc, status, error_message, created_at, uploaded_by = job
            
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
                st.write(f"**Trạng thái:** <span style='color:{color}'>{status}</span>", unsafe_allow_html=True)
                if error_message:
                    st.write(f"**Thông báo:** {error_message}")
                
                if status == "failed":
                    if st.button(f"Retry Job {job_id}", key=f"retry_{job_id}"):
                        with engine.begin() as conn:
                            conn.execute(
                                text("UPDATE IngestionJobs SET Status = 'pending', ErrorMessage = NULL WHERE JobID = :id"),
                                {"id": job_id}
                            )
                        st.success("Đã đưa lại vào hàng đợi!")
                        st.rerun()

    except Exception as e:
        st.error(f"Lỗi truy xuất dữ liệu: {e}")
