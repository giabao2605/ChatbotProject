import streamlit as st
from mech_chatbot.services import (
    is_engine_ready,
    dashboard_by_department,
    get_dashboard_stats,
    list_recent_documents,
    list_recent_failed_jobs,
)
from mech_chatbot.auth import service as auth
from mech_chatbot.ui import labels
from mech_chatbot.ui.i18n import t


def run_dashboard():
    st.title(t("Tổng quan hệ thống"))
    st.caption(t("Theo dõi nhanh trạng thái tài liệu, ingest, chatbot và feedback."))

    if not auth.has_role("admin"):
        st.error(t("Chỉ admin được xem trang tổng quan."))
        return
    if not is_engine_ready():
        st.error(t("Không thể kết nối Database."))
        return

    stats = get_dashboard_stats()

    c1, c2, c3, c4 = st.columns(4)
    c1.metric(t("Tổng tài liệu"), stats["total_docs"])
    c2.metric(t("Chờ duyệt"), stats["pending_review"])
    c3.metric(t("Job đang xử lý"), stats["running_jobs"])
    c4.metric(t("Job lỗi"), stats["failed_jobs"])

    c5, c6, c7 = st.columns(3)
    c5.metric(t("Đã published"), stats["published_docs"])
    c6.metric(t("Chat hôm nay"), stats["today_chats"])
    c7.metric(t("Feedback cần xử lý"), stats["pending_feedback"])

    st.markdown("---")
    st.subheader(t("Thống kê theo phòng ban"))
    render_department_breakdown()

    st.markdown("---")
    left, right = st.columns(2)
    with left:
        st.subheader(t("Tài liệu mới"))
        render_recent_documents()
    with right:
        st.subheader(t("Job lỗi gần đây"))
        render_recent_failed_jobs()


def render_department_breakdown():
    """P1.6: bang suc khoe theo tung phong ban."""
    try:
        rows = dashboard_by_department()
    except Exception as e:
        st.error(t("Không tải được thống kê theo phòng: {e}", e=e))
        return
    if not rows:
        st.info(t("Chưa có dữ liệu theo phòng ban."))
        return
    table = [
        {
            t("Phòng ban"): labels.dept_label(r["department"]),
            t("Tài liệu sở hữu"): r.get("owned_total", r["total"]),
            t("Tài liệu được chia sẻ"): r.get("shared_access", 0),
            t("Tổng tài liệu"): r["total"],
            t("Đã publish"): r["published"],
            t("Chờ duyệt"): r["pending_review"],
            t("Mật (confidential)"): r["confidential"],
            t("Job đang chạy"): r["running_jobs"],
            t("Job lỗi"): r["failed_jobs"],
        }
        for r in rows
    ]
    st.dataframe(table, use_container_width=True, hide_index=True)

    # B6: bieu do so tai lieu theo phong ban
    try:
        import pandas as pd
        _col_dept = t("Phòng ban")
        _col_docs = t("Số tài liệu")
        chart_df = pd.DataFrame(
            [{_col_dept: labels.dept_label(r["department"]), _col_docs: r["total"]} for r in rows]
        ).set_index(_col_dept)
        st.bar_chart(chart_df[_col_docs])
    except Exception:
        # fallback neu thieu pandas
        st.bar_chart({labels.dept_label(r["department"]): r["total"] for r in rows})

    # B6: lam noi cac phong dang co job loi
    depts_with_failures = [r for r in rows if r["failed_jobs"]]
    total_failed = sum(r["failed_jobs"] for r in rows)
    if depts_with_failures:
        _depts = ", ".join(f"{labels.dept_label(r['department'])} ({r['failed_jobs']})" for r in depts_with_failures)
        st.warning(
            t(
                "Có {n} job ingest đang lỗi. Phòng cần chú ý: {depts} - kiểm tra tab Hàng đợi.",
                n=total_failed, depts=_depts,
            )
        )


def render_recent_documents():
    try:
        rows = list_recent_documents()
    except Exception as e:
        st.error(t("Không tải được tài liệu mới: {e}", e=e))
        return

    if not rows:
        st.info(t("Chưa có tài liệu."))
        return
    for doc_id, ten_file, thu_muc, review_status, lifecycle_status, ngay_tai_len in rows:
        st.write(f"**{ten_file}**  \n`{labels.dept_label(thu_muc)}` · {labels.status_badge(review_status)} · {labels.status_badge(lifecycle_status)} · {ngay_tai_len}")


def render_recent_failed_jobs():
    try:
        rows = list_recent_failed_jobs()
    except Exception as e:
        st.error(t("Không tải được job lỗi: {e}", e=e))
        return

    if not rows:
        st.success(t("Không có job lỗi."))
        return
    for job_id, ten_file, thu_muc, status, error_message, updated_at in rows:
        with st.expander(f"{labels.status_badge(status)} · {ten_file}"):
            st.write(f"**JobID:** {job_id}")
            st.write("**" + t("Phòng ban:") + f"** {labels.dept_label(thu_muc)}")
            st.write("**" + t("Cập nhật:") + f"** {updated_at}")
            st.error(error_message or t("Không có thông báo lỗi."))
