import streamlit as st
from sqlalchemy import text
from mech_chatbot.db.repository import (
    engine, queue_eta_seconds, set_job_priority, cancel_job, requeue_job,
    list_known_departments, list_known_sites,
)
from mech_chatbot.ui import labels
from mech_chatbot.ui.i18n import t


def _fmt_eta(seconds):
    seconds = int(seconds or 0)
    if seconds <= 0:
        return "~0 " + t("ph\u00fat")
    h, rem = divmod(seconds, 3600)
    m, s = divmod(rem, 60)
    if h:
        return f"~{h}h{m:02d}m"
    if m:
        return f"~{m} " + t("ph\u00fat") + f" {s:02d}s"
    return f"~{s}s"


def run_queue():
    st.title(t("Qu\u1ea3n L\u00fd Ti\u1ebfn Tr\u00ecnh N\u1ea1p D\u1eef Li\u1ec7u"))
    st.markdown(t("Xem danh s\u00e1ch c\u00e1c file \u0111ang \u0111\u01b0\u1ee3c \u0111\u01b0a v\u00e0o x\u1eed l\u00fd b\u00f3c t\u00e1ch (Worker Queue)."))

    if engine is None:
        st.error(t("Kh\u00f4ng th\u1ec3 k\u1ebft n\u1ed1i \u0111\u1ebfn Database."))
        return

    from mech_chatbot.auth import service as auth
    current_user = auth.get_current_user()
    is_admin = auth.has_role("admin")

    non_admin_allowed_departments = []
    if not is_admin:
        _active_codes_q = {d["code"] for d in list_known_departments(active_only=True)}
        non_admin_allowed_departments = [
            d for d in (current_user.get("allowed_departments") or [current_user.get("department")])
            if d and d in _active_codes_q
        ]

    # --- P1.5: Bang dieu khien ETA ---
    eta = queue_eta_seconds()
    m1, m2, m3 = st.columns(3)
    m1.metric(t("\u0110ang ch\u1edd x\u1eed l\u00fd"), eta.get("pending", 0))
    m2.metric(t("TB m\u1ed7i job"), f"{eta.get('avg_seconds', 0):.0f}s")
    m3.metric(t("D\u1ef1 ki\u1ebfn x\u1eed xong"), _fmt_eta(eta.get("eta_seconds", 0)))

    # --- Bo loc ---
    fc1, fc2, fc3 = st.columns(3)
    with fc1:
        status_filter = st.selectbox(
            t("Tr\u1ea1ng th\u00e1i"),
            [t("T\u1ea5t c\u1ea3"), "pending", "pending_retry", "classifying", "extracting", "embedding", "failed", "waiting_quota", "publishing"],
            key="queue_status",
        )
    with fc2:
        if is_admin:
            dept_options = [d["code"] for d in list_known_departments(active_only=True)]
        else:
            # P0.4: chi hien phong ban dang active + user duoc phep
            _active_codes_q = {d["code"] for d in list_known_departments(active_only=True)}
            dept_options = [
                d for d in (current_user.get("allowed_departments") or [current_user.get("department")])
                if d and d in _active_codes_q
            ]
        dept_filter = st.selectbox(t("Ph\u00f2ng ban"), [t("T\u1ea5t c\u1ea3")] + sorted(set(dept_options)), format_func=labels.dept_label, key="queue_dept")
    with fc3:
        search = st.text_input(t("T\u00ecm file"), key="queue_search")

    _tat_ca = t("T\u1ea5t c\u1ea3")
    try:
        with engine.connect() as conn:
            query_str = """
            SELECT 
                JobID, TenFile, ThuMuc, Status, ErrorMessage, 
                CreatedAt, UploadedBy, RetryCount, MaxRetry, 
                LockedBy, LockedAt, ProgressPercent, UpdatedAt,
                FailureType, NextRetryAt, QualityScore, QualityStatus, ExtractionReport,
                ISNULL(Priority, 100) AS Priority,
                Domain, SecurityLevel, CongDoan, Site
            FROM dbo.IngestionJobs
            WHERE Status NOT IN ('pending_review', 'published', 'archived', 'superseded')
            """
            params = {}
            if status_filter != _tat_ca:
                query_str += " AND Status = :status"
                params["status"] = status_filter
            if dept_filter and dept_filter != _tat_ca:
                query_str += " AND ThuMuc = :dept_pick"
                params["dept_pick"] = dept_filter
            if search:
                query_str += " AND TenFile LIKE :search"
                params["search"] = f"%{search}%"
            if not is_admin:
                params["uname"] = current_user["username"]
                if non_admin_allowed_departments:
                    _dept_clauses = []
                    for _idx, _dept in enumerate(sorted(set(non_admin_allowed_departments))):
                        _k = f"allowed_dept_{_idx}"
                        _dept_clauses.append(f"ThuMuc = :{_k}")
                        params[_k] = _dept
                    query_str += " AND ((" + " OR ".join(_dept_clauses) + ") OR UploadedBy = :uname)"
                else:
                    query_str += " AND UploadedBy = :uname"

            query_str += " ORDER BY ISNULL(Priority, 100) ASC, CreatedAt DESC"

            result = conn.execute(text(query_str), params)
            jobs = result.fetchall()

        if not jobs:
            st.info(t("Hi\u1ec7n kh\u00f4ng c\u00f3 file n\u00e0o trong h\u00e0ng \u0111\u1ee3i."))
            return

        st.subheader(t("T\u1ed5ng s\u1ed1: {n} jobs", n=len(jobs)))

        selected_job_ids = []
        select_all_jobs = False
        if is_admin:
            select_all_jobs = st.checkbox(t("Chọn tất cả jobs đang hiển thị"), key="queue_select_all_jobs")

        for job in jobs:
            (
                job_id, ten_file, thu_muc, status, error_message,
                created_at, uploaded_by, retry_count, max_retry,
                locked_by, locked_at, progress_percent, updated_at,
                failure_type, next_retry_at, quality_score, quality_status, extraction_report,
                priority,
                domain_val, security_val, cong_doan_val, site_val
            ) = job

            if is_admin:
                if st.checkbox(t("Chọn Job {job_id} · {name}", job_id=job_id, name=ten_file), value=select_all_jobs, key=f"queue_pick_{job_id}"):
                    selected_job_ids.append(job_id)

            prio_badge = ("\U0001f525 GAP" if (priority or 100) < 50
                          else ("\u2b07\ufe0f " + t("th\u1ea5p") if (priority or 100) > 150
                                else t("th\u01b0\u1eddng")))
            with st.expander(
                f"{labels.status_badge(status)} \u00b7 {ten_file} (Job: {job_id}) \u00b7 "
                + t("\u01afu ti\u00ean:") + f" {prio_badge} - {created_at.strftime('%Y-%m-%d %H:%M:%S')}"
            ):
                st.write(f"**" + t("Ph\u00f2ng ban:") + f"** {labels.dept_label(thu_muc)} | **" + t("Ng\u01b0\u1eddi t\u1ea3i l\u00ean:") + f"** {uploaded_by or 'Unknown'}")
                st.write(
                    f"**Domain:** {domain_val or t('theo th\u01b0 m\u1ee5c')} | "
                    f"**" + t("M\u1ee9c m\u1eadt:") + f"** {security_val or t('theo th\u01b0 m\u1ee5c')}"
                    + (f" | **Site:** {site_val}" if site_val else "")
                )
                st.write(f"**" + t("Tr\u1ea1ng th\u00e1i:") + f"** {labels.status_badge(status)} (" + t("Ti\u1ebfn \u0111\u1ed9:") + f" {progress_percent}%)")
                st.progress((progress_percent or 0) / 100)
                st.write(f"**" + t("\u01afu ti\u00ean (Priority):") + f"** {priority} (" + t("nh\u1ecf h\u01a1n = \u01b0u ti\u00ean h\u01a1n") + ")")
                st.write(f"**Debug Info:** Retry: {retry_count}/{max_retry} | LockedBy: {locked_by} at {locked_at}")
                st.write(f"**Failure Type:** {failure_type or 'N/A'}")
                st.write(f"**Next Retry At:** {next_retry_at or 'N/A'}")
                st.write(f"**Quality:** {quality_status or 'N/A'} ({quality_score or 0}/100)")
                if error_message:
                    st.write(f"**" + t("Th\u00f4ng b\u00e1o:") + f"** {error_message}")

                import json
                if extraction_report:
                    try:
                        report_obj = json.loads(extraction_report)
                        st.json(report_obj)
                    except Exception:
                        st.text(extraction_report)

                # --- P1.5: Hanh dong (admin) ---
                if is_admin:
                    st.markdown("---")
                    ac1, ac2, ac3 = st.columns([2, 1, 1])
                    with ac1:
                        new_prio = st.number_input(
                            t("\u0110\u1eb7t \u01b0u ti\u00ean"),
                            value=int(priority or 100), step=10, min_value=0, max_value=1000,
                            key=f"prio_{job_id}",
                        )
                        if st.button(t("L\u01b0u \u01b0u ti\u00ean"), key=f"saveprio_{job_id}"):
                            if set_job_priority(job_id, int(new_prio)):
                                st.success(t("\u0110\u00e3 c\u1eadp nh\u1eadt \u01b0u ti\u00ean."))
                                st.rerun()
                            else:
                                st.error(t("Kh\u00f4ng c\u1eadp nh\u1eadt \u0111\u01b0\u1ee3c \u01b0u ti\u00ean."))
                    with ac2:
                        if status in ["failed", "waiting_quota", "pending_retry"]:
                            if st.button(t("Th\u1eed l\u1ea1i"), key=f"retry_{job_id}"):
                                if requeue_job(job_id):
                                    st.success(t("\u0110\u00e3 \u0111\u01b0a l\u1ea1i v\u00e0o h\u00e0ng \u0111\u1ee3i!"))
                                    st.rerun()
                                else:
                                    st.error(t("Th\u1eed l\u1ea1i th\u1ea5t b\u1ea1i."))
                    with ac3:
                        if st.button(t("H\u1ee7y job"), key=f"cancel_{job_id}", type="secondary"):
                            if cancel_job(job_id, canceled_by=current_user["username"]):
                                st.success(t("\u0110\u00e3 h\u1ee7y job."))
                                st.rerun()
                            else:
                                st.warning(t("Kh\u00f4ng th\u1ec3 h\u1ee7y (job c\u00f3 th\u1ec3 \u0111\u00e3 ho\u00e0n t\u1ea5t ho\u1eb7c \u0111ang publish)."))
                else:
                    if status in ["failed", "waiting_quota"]:
                        if st.button(t("Th\u1eed l\u1ea1i Job {jid}", jid=job_id), key=f"retry_{job_id}"):
                            if requeue_job(job_id):
                                st.success(t("\u0110\u00e3 \u0111\u01b0a l\u1ea1i v\u00e0o h\u00e0ng \u0111\u1ee3i!"))
                                st.rerun()
                            else:
                                st.error(t("Th\u1eed l\u1ea1i th\u1ea5t b\u1ea1i."))

        if is_admin and selected_job_ids:
            st.markdown("---")
            st.warning(t("Đã chọn {n} job.", n=len(selected_job_ids)))
            if st.button("🗑️ " + t("Xóa tất cả jobs đã chọn"), key="queue_bulk_delete_btn", type="secondary"):
                st.session_state["queue_confirm_bulk_delete"] = selected_job_ids

        if is_admin and st.session_state.get("queue_confirm_bulk_delete"):
            ids = st.session_state["queue_confirm_bulk_delete"]
            st.error(t("Xác nhận xóa {n} job?", n=len(ids)))
            c_ok, c_cancel = st.columns(2)
            with c_ok:
                if st.button("✅ " + t("Xác nhận xóa"), key="queue_confirm_bulk_delete_btn", type="primary"):
                    ok, fail = 0, 0
                    with engine.begin() as conn:
                        for jid in ids:
                            try:
                                conn.execute(text("DELETE FROM dbo.IngestionJobs WHERE JobID = :jid"), {"jid": jid})
                                ok += 1
                            except Exception:
                                fail += 1
                    st.session_state.pop("queue_confirm_bulk_delete", None)
                    st.success(t("Đã xóa: {ok} thành công, {fail} thất bại.", ok=ok, fail=fail))
                    st.rerun()
            with c_cancel:
                if st.button(t("Hủy"), key="queue_cancel_bulk_delete"):
                    st.session_state.pop("queue_confirm_bulk_delete", None)
                    st.rerun()

    except Exception as e:
        st.error(t("L\u1ed7i truy xu\u1ea5t d\u1eef li\u1ec7u: {e}", e=e))
