import json
import os
from pathlib import Path

import streamlit as st
from sqlalchemy import text

from mech_chatbot.auth import service as auth
from mech_chatbot.db.repository import (
    engine, update_document_common_metadata,
    publish_as_new_version, publish_as_new_variant, publish_as_standalone,
    delete_document_completely,
)
from mech_chatbot.ui import metadata_forms
from mech_chatbot.ui.i18n import t
from mech_chatbot.ui.labels import dept_label, gloss

DOMAIN_OPTIONS = ["mechanical", "tabular", "generic"]
SECURITY_LEVELS = ["public", "internal", "confidential"]


def run_admin():
    st.title("\U0001f6e0\ufe0f " + t("Qu\u1ea3n tr\u1ecb h\u1ec7 th\u1ed1ng"))
    st.markdown(t(
        "Duy\u1ec7t t\u00e0i li\u1ec7u pending_review, s\u1eeda metadata, v\u00e0 v\u1eadn h\u00e0nh h\u1ec7 th\u1ed1ng."
    ))
    if not (auth.has_role("reviewer") or auth.has_role("admin")):
        st.error(t("B\u1ea1n kh\u00f4ng c\u00f3 quy\u1ec1n truy c\u1eadp trang n\u00e0y."))
        return
    if engine is None:
        st.error(t("Kh\u00f4ng th\u1ec3 k\u1ebft n\u1ed1i Database."))
        return

    tab_review, tab_bulk, tab_meta = st.tabs([
        "\U0001f4cb " + t("Duy\u1ec7t t\u00e0i li\u1ec7u"),
        "\U0001f4e6 " + t("Bulk action"),
        "\U0001f527 " + t("S\u1eeda metadata h\u00e0ng lo\u1ea1t"),
    ])

    with tab_review:
        render_doc_list()
    with tab_bulk:
        _render_bulk_panel()
    with tab_meta:
        render_bulk_meta_panel()


# ---------------------------------------------------------------------------
# Tab 1: danh sach can duyet
# ---------------------------------------------------------------------------

def render_doc_list():
    st.subheader(t("T\u00e0i li\u1ec7u ch\u1edd duy\u1ec7t"))
    st.caption(t(
        "Hi\u1ec3n c\u00e1c file \u0111\u00e3 x\u1eed l\u00fd xong (status = pending_review), "
        "c\u1ea7n Reviewer x\u00e1c nh\u1eadn tr\u01b0\u1edbc khi push l\u00ean Qdrant."
    ))

    with engine.connect() as conn:
        rows = conn.execute(text("""
            SELECT j.JobID, j.TenFile, j.ThuMuc, j.UploadedBy, j.UpdatedAt, j.ExtractionReport,
                   j.Domain, j.SecurityLevel, j.Site, j.UploadMetaJson,
                   d.DocID, d.Title, d.Summary, d.Tags, d.DocNumber, d.DocLanguage AS Language,
                   d.IssuedDate, d.EffectiveDate AS EffectiveDateStart, d.ExpiryDate, d.ReviewDate,
                   d.OwnerSigner, d.EffectiveStatus, d.VersionNo, d.IsCurrent AS IsCurrentVersion,
                   d.VariantGroup, d.VariantCode AS BranchLabel
            FROM IngestionJobs j
            LEFT JOIN TaiLieu d
              ON d.TenFile = j.TenFile
             AND d.ThuMuc = j.ThuMuc
             AND d.ReviewStatus = 'pending_review'
             AND d.LifecycleStatus <> 'deleting'
            WHERE j.Status = 'pending_review'
            ORDER BY j.UpdatedAt ASC
        """)).fetchall()

    if not rows:
        st.info(t("Kh\u00f4ng c\u00f3 t\u00e0i li\u1ec7u n\u00e0o c\u1ea7n duy\u1ec7t hi\u1ec7n t\u1ea1i."))
        return

    st.success(t("C\u00f3 {n} t\u00e0i li\u1ec7u ch\u1edd duy\u1ec7t.", n=len(rows)))

    for row in rows:
        (
            job_id, ten_file, thu_muc, uploaded_by, updated_at, extraction_report,
            job_domain, job_sec, job_site, upload_meta_json,
            doc_id, title, summary, tags, doc_number, language,
            issued_date, effective_date, expiry_date, review_date,
            owner_signer, effective_status, version_no, is_current,
            variant_group, branch_label
        ) = row
        _render_review_item(
            job_id=job_id, ten_file=ten_file, thu_muc=thu_muc,
            uploaded_by=uploaded_by, updated_at=updated_at,
            extraction_report=extraction_report,
            job_domain=job_domain, job_sec=job_sec, job_site=job_site,
            upload_meta_json=upload_meta_json,
            doc_id=doc_id, title=title, summary=summary, tags=tags,
            doc_number=doc_number, language=language,
            issued_date=issued_date, effective_date=effective_date,
            expiry_date=expiry_date, review_date=review_date,
            owner_signer=owner_signer, effective_status=effective_status,
            version_no=version_no, is_current=is_current,
            variant_group=variant_group, branch_label=branch_label,
        )


def _render_review_item(
    job_id, ten_file, thu_muc, uploaded_by, updated_at,
    extraction_report, job_domain, job_sec, job_site, upload_meta_json,
    doc_id, title, summary, tags, doc_number, language,
    issued_date, effective_date, expiry_date, review_date,
    owner_signer, effective_status, version_no, is_current,
    variant_group, branch_label,
):
    with st.expander(
        f"[Job {job_id}] {ten_file} \u00b7 {thu_muc} "
        + t("(c\u1eadp nh\u1eadt:") + f" {updated_at})"
    ):
        st.write(f"**" + t("Ng\u01b0\u1eddi upload:") + f"** {uploaded_by or ''}")
        st.write(f"**" + t("Ph\u00f2ng ban:") + f"** {thu_muc}")
        st.write(
            f"**Domain:** {job_domain or t('(kh\u00f4ng r\u00f5)')} | "
            f"**" + t("M\u1ee9c m\u1eadt:") + f"** {job_sec or t('(kh\u00f4ng r\u00f5)')}"
            + (f" | **Site:** {job_site}" if job_site else "")
        )
        if extraction_report:
            show_report = st.checkbox(
                t("Xem kết quả trích xuất"),
                value=False,
                key=f"show_extraction_report_{job_id}",
            )
            if show_report:
                try:
                    st.json(json.loads(extraction_report))
                except Exception:
                    st.text(extraction_report)

        # --- Action radio ---
        action_options = [
            "Publish l\u00e0m version m\u1edbi (Archive b\u1ea3n c\u0169 c\u00f9ng variant)",
            "Publish song song nh\u01b0 variant m\u1edbi (Gi\u1eef nguy\u00ean b\u1ea3n c\u0169)",
            "Publish nh\u01b0 t\u00e0i li\u1ec7u \u0111\u1ed9c l\u1eadp (Standalone)",
            "L\u01b0u nh\u00e1p / C\u1ea7n s\u1eeda metadata",
            "T\u1eeb ch\u1ed1i (Reject)",
        ]
        action_choice = st.radio(
            t("H\u00e0nh \u0111\u1ed9ng"),
            action_options,
            format_func=lambda x: t(x),
            key=f"action_{job_id}",
        )

        # --- Metadata form ---
        st.markdown("---")
        st.markdown("**" + t("X\u00e1c nh\u1eadn / ch\u1ec9nh s\u1eeda metadata") + "**")
        current_domain = job_domain or "generic"
        _meta_common, _meta_attrs = metadata_forms.render_metadata_section(
            current_domain, prefix=f"adm_{job_id}",
            common_defaults={
                "title": title, "summary": summary, "tags": tags,
                "doc_number": doc_number, "language": language,
                "issued_date": issued_date, "effective_date": effective_date,
                "expiry_date": expiry_date, "review_date": review_date,
                "owner_signer": owner_signer, "effective_status": effective_status,
            },
        )
        meta = metadata_forms.build_upload_meta(_meta_common, _meta_attrs)

        # --- Reject reason ---
        reject_reason = ""
        if "T\u1eeb ch\u1ed1i" in action_choice:
            reject_reason = st.text_area(
                t("L\u00fd do t\u1eeb ch\u1ed1i"), key=f"reject_reason_{job_id}"
            )

        # --- Submit ---
        if st.button(t("X\u00e1c nh\u1eadn"), type="primary", key=f"submit_review_{job_id}"):
            _process_review_action(
                job_id=job_id, action_choice=action_choice,
                meta=meta, doc_id=doc_id, reject_reason=reject_reason,
                variant_group=variant_group,
            )


def _process_review_action(job_id, action_choice, meta, doc_id, reject_reason, variant_group):
    try:
        if "T\u1eeb ch\u1ed1i" in action_choice:
            with engine.begin() as conn:
                conn.execute(text("""
                    UPDATE IngestionJobs
                    SET Status = 'rejected', RejectReason = :reason, UpdatedAt = GETDATE()
                    WHERE JobID = :jid
                """), {"reason": reject_reason, "jid": job_id})
            st.success(t("\u0110\u00e3 t\u1eeb ch\u1ed1i t\u00e0i li\u1ec7u."))
            st.rerun()
            return

        if "s\u1eeda metadata" in action_choice:
            _save_meta_to_doc(doc_id, meta)
            with engine.begin() as conn:
                conn.execute(text("""
                    UPDATE IngestionJobs SET Status = 'pending_review', UpdatedAt = GETDATE() WHERE JobID = :jid
                """), {"jid": job_id})
            st.success(t("\u0110\u00e3 l\u01b0u metadata, gi\u1eef tr\u1ea1ng th\u00e1i ch\u1edd duy\u1ec7t."))
            st.rerun()
            return

        if "\u0111\u1ed9c l\u1eadp" in action_choice:
            publish_mode = "standalone"
        elif "variant m\u1edbi" in action_choice:
            publish_mode = "new_variant"
        else:
            publish_mode = "new_version"

        _save_meta_to_doc(doc_id, meta)
        _publish_doc_and_mark_job(job_id, doc_id, publish_mode)

        st.success(t("Đã xuất bản tài liệu. Chatbot có thể dùng sau khi payload Qdrant cập nhật."))
        st.rerun()

    except Exception as e:
        st.error(t("L\u1ed7i x\u1eed l\u00fd: {e}", e=e))


def _current_reviewer():
    try:
        user = auth.get_current_user() or {}
        return user.get("username") or user.get("display_name") or "System"
    except Exception:
        return "System"


def _publish_doc_and_mark_job(job_id, doc_id, publish_mode="standalone"):
    """Publish DONG BO ngay trong Admin UI.

    Flow cu set IngestionJobs.Status='publishing' roi cho worker, nhung worker
    khong pick status 'publishing' -> job bi ket, Kho tai lieu hien sai.
    Flow moi:
      pending_review -> publish_as_*() update SQL + Qdrant payload
      -> IngestionJobs.Status='published'
    """
    if not doc_id:
        raise RuntimeError("Không tìm thấy DocID tương ứng với job này.")

    reviewer = _current_reviewer()
    if publish_mode == "new_version":
        ok = publish_as_new_version(doc_id, reviewer=reviewer)
    elif publish_mode == "new_variant":
        ok = publish_as_new_variant(doc_id, reviewer=reviewer)
    else:
        ok = publish_as_standalone(doc_id, reviewer=reviewer)

    if not ok:
        raise RuntimeError("Publish thất bại: không tìm thấy tài liệu hoặc không update được Qdrant.")

    with engine.begin() as conn:
        conn.execute(text("""
            UPDATE IngestionJobs
            SET Status = 'published', UpdatedAt = GETDATE()
            WHERE JobID = :jid
        """), {"jid": job_id})


def _delete_review_job_and_doc(job_id, doc_id=None):
    reviewer = _current_reviewer()
    if doc_id:
        try:
            delete_document_completely(doc_id, reviewer=reviewer)
        except Exception:
            # Neu xoa doc loi, van tiep tuc xoa job de UI khong ket; loi se hien trong log.
            pass
    with engine.begin() as conn:
        conn.execute(text("DELETE FROM IngestionJobs WHERE JobID = :jid"), {"jid": job_id})


def _save_meta_to_doc(doc_id, meta):
    if not doc_id or not meta:
        return
    update_document_common_metadata(
        doc_id,
        title=meta.get("title"),
        summary=meta.get("summary"),
        tags=meta.get("tags"),
        doc_number=meta.get("doc_number"),
        language=meta.get("language"),
        owner_signer=meta.get("owner_signer"),
        effective_status=meta.get("effective_status"),
        issued_date=meta.get("issued_date") or None,
        effective_date=meta.get("effective_date") or None,
        expiry_date=meta.get("expiry_date") or None,
        review_date=meta.get("review_date") or None,
        domain=meta.get("domain"),
        attributes=meta.get("attributes"),
    )


# ---------------------------------------------------------------------------
# Tab 2: Bulk action
# ---------------------------------------------------------------------------

def _render_bulk_panel():
    st.subheader(t("Bulk action trên jobs"))
    st.caption(t(
        "Chọn nhiều job cùng lúc để publish, reject hoặc xóa. "
        "Publish sẽ chạy trực tiếp, không còn kẹt ở trạng thái publishing."
    ))
    with engine.connect() as conn:
        rows = conn.execute(text("""
            SELECT j.JobID, j.TenFile, j.ThuMuc, j.Status, j.UpdatedAt, d.DocID
            FROM IngestionJobs j
            LEFT JOIN TaiLieu d
              ON d.TenFile = j.TenFile
             AND d.ThuMuc = j.ThuMuc
             AND d.LifecycleStatus <> 'deleting'
            WHERE j.Status IN ('pending_review', 'failed', 'rejected', 'publishing')
            ORDER BY j.UpdatedAt ASC
        """)).fetchall()

    if not rows:
        st.info(t("Không có job nào đủ điều kiện."))
        return

    select_all = st.checkbox(t("Chọn tất cả jobs đang hiển thị"), key="bulk_select_all_jobs")
    selected = []
    for job_id, ten_file, thu_muc, status, updated_at, doc_id in rows:
        checked = st.checkbox(
            f"[{status}] {ten_file} ({thu_muc}) · {updated_at}",
            value=select_all,
            key=f"bulk_chk_{job_id}",
        )
        if checked:
            selected.append({"job_id": job_id, "doc_id": doc_id, "status": status})

    st.markdown(f"**" + t("Đã chọn: {n} job", n=len(selected)) + "**")
    publish_mode_label = st.selectbox(
        t("Kiểu publish hàng loạt"),
        [
            ("standalone", t("Publish như tài liệu độc lập")),
            ("new_variant", t("Publish song song như variant mới")),
            ("new_version", t("Publish làm version mới")),
        ],
        format_func=lambda x: x[1],
        key="bulk_publish_mode",
    )
    publish_mode = publish_mode_label[0]

    c1, c2, c3 = st.columns(3)
    with c1:
        if st.button(t("Publish tất cả đã chọn"), type="primary", disabled=not selected):
            ok, fail = _run_bulk_review(selected, action="publish", publish_mode=publish_mode)
            st.success(t("Publish: {ok} thành công, {fail} thất bại.", ok=ok, fail=fail))
            st.rerun()
    with c2:
        if st.button(t("Reject tất cả đã chọn"), disabled=not selected):
            ok, fail = _run_bulk_review(selected, action="reject")
            st.success(t("Reject: {ok} thành công, {fail} thất bại.", ok=ok, fail=fail))
            st.rerun()
    with c3:
        if st.button(t("Xóa tất cả đã chọn"), disabled=not selected, type="secondary"):
            st.session_state["confirm_bulk_del"] = selected

    if st.session_state.get("confirm_bulk_del"):
        st.warning(t("Xác nhận xóa {n} job/tài liệu? Không thể hoàn tác.", n=len(st.session_state["confirm_bulk_del"])))
        cc1, cc2 = st.columns(2)
        with cc1:
            if st.button(t("Xác nhận"), key="confirm_bulk_del_btn", type="primary"):
                ok, fail = _run_bulk_review(st.session_state["confirm_bulk_del"], action="delete")
                st.session_state.pop("confirm_bulk_del", None)
                st.success(t("Đã xóa: {ok} thành công, {fail} thất bại.", ok=ok, fail=fail))
                st.rerun()
        with cc2:
            if st.button(t("Hủy"), key="cancel_bulk_del"):
                st.session_state.pop("confirm_bulk_del", None)
                st.rerun()


def _run_bulk_review(items, action, publish_mode="standalone"):
    ok, fail = 0, 0
    for item in items:
        try:
            jid = item["job_id"] if isinstance(item, dict) else item
            did = item.get("doc_id") if isinstance(item, dict) else None
            if action == "publish":
                if not did:
                    raise RuntimeError("Thiếu DocID để publish")
                _publish_doc_and_mark_job(jid, did, publish_mode=publish_mode)
            elif action == "reject":
                with engine.begin() as conn:
                    conn.execute(text("""
                        UPDATE IngestionJobs SET Status = 'rejected', UpdatedAt = GETDATE()
                        WHERE JobID = :jid
                    """), {"jid": jid})
            elif action == "delete":
                _delete_review_job_and_doc(jid, did)
            ok += 1
        except Exception:
            fail += 1
    return ok, fail


# ---------------------------------------------------------------------------
# Tab 3: Sua metadata hang loat
# ---------------------------------------------------------------------------

def render_bulk_meta_panel():
    st.subheader(t("S\u1eeda metadata h\u00e0ng lo\u1ea1t"))
    st.caption(t(
        "L\u1ecdc t\u00e0i li\u1ec7u theo b\u1ed9 l\u1ecdc, sau \u0111\u00f3 ch\u1ecdn nhi\u1ec1u t\u00e0i li\u1ec7u "
        "v\u00e0 \u00e1p d\u1ee5ng c\u00f9ng 1 metadata cho t\u1ea5t c\u1ea3."
    ))

    fc1, fc2 = st.columns(2)
    with fc1:
        dept_f = st.selectbox(
            t("Ph\u00f2ng ban"), [t("T\u1ea5t c\u1ea3")] + _get_departments(),
            format_func=dept_label,
            key="bulk_meta_dept",
        )
    with fc2:
        domain_f = st.selectbox(
            gloss("Domain"), [t("T\u1ea5t c\u1ea3")] + DOMAIN_OPTIONS,
            key="bulk_meta_domain",
        )

    _tat_ca = t("T\u1ea5t c\u1ea3")
    q = "SELECT DocID, TenFile, ThuMuc, Domain FROM TaiLieu WHERE IsCurrent = 1 AND LifecycleStatus <> 'deleting'"
    params = {}
    # P4.5 fix: dropdown co the hien badge "(disabled)" / "(archived)" cho admin,
    # nhung gia tri query vao ThuMuc phai la ma phong ban goc.
    dept_value = dept_f
    for _suffix in (" (disabled)", " (archived)"):
        if isinstance(dept_value, str) and dept_value.endswith(_suffix):
            dept_value = dept_value[: -len(_suffix)]
            break
    if dept_f != _tat_ca:
        q += " AND ThuMuc = :dept"
        params["dept"] = dept_value
    if domain_f != _tat_ca:
        q += " AND Domain = :domain"
        params["domain"] = domain_f
    q += " ORDER BY ThuMuc, TenFile"

    with engine.connect() as conn:
        docs = conn.execute(text(q), params).fetchall()

    if not docs:
        st.info(t("Kh\u00f4ng c\u00f3 t\u00e0i li\u1ec7u n\u00e0o."))
        return

    selected_doc_ids = []
    for doc_id, fname, dept, dom in docs:
        if st.checkbox(
            f"[{doc_id}] {fname} ({dept_label(dept)} / {dom})",
            key=f"bmeta_chk_{doc_id}",
        ):
            selected_doc_ids.append(doc_id)

    st.markdown(f"**" + t("\u0110\u00e3 ch\u1ecdn: {n} t\u00e0i li\u1ec7u", n=len(selected_doc_ids)) + "**")

    if selected_doc_ids:
        st.markdown("---")
        st.markdown("**" + t("Nh\u1eadp metadata \u00e1p d\u1ee5ng") + "**")
        _meta_common, _meta_attrs = metadata_forms.render_metadata_section(
            domain_f if domain_f != _tat_ca else "generic",
            prefix="bulk_meta_form",
        )
        meta = metadata_forms.build_upload_meta(_meta_common, _meta_attrs)
        meta_clean = {k: v for k, v in meta.items() if v not in (None, "")}
        if st.button(
            t("\u00c1p d\u1ee5ng cho {n} t\u00e0i li\u1ec7u", n=len(selected_doc_ids)),
            type="primary", disabled=not meta_clean,
        ):
            ok, fail = 0, 0
            for did in selected_doc_ids:
                try:
                    _save_meta_to_doc(did, meta_clean)
                    ok += 1
                except Exception:
                    fail += 1
            st.success(t(
                "\u0110\u00e3 c\u1eadp nh\u1eadt metadata: {ok} th\u00e0nh c\u00f4ng, {fail} th\u1ea5t b\u1ea1i.",
                ok=ok, fail=fail,
            ))
            st.rerun()


def _get_departments():
    """P4.5: Lay danh sach phong ban tu TaiLieu.ThuMuc (bao gom ca phong da disabled/archived
    neu con tai lieu cu). The hien badge trang thai de admin nhan biet.
    Dieu nay la intentional: admin co the can sua metadata tai lieu cua phong da dong.
    """
    try:
        with engine.connect() as conn:
            # Join voi Departments de lay Status; fallback ve ThuMuc neu khong join duoc
            try:
                rows = conn.execute(text("""
                    SELECT DISTINCT t.ThuMuc,
                           ISNULL(d.Status, 'active') AS DeptStatus
                    FROM TaiLieu t
                    LEFT JOIN dbo.Departments d ON d.DeptCode = t.ThuMuc
                    WHERE t.ThuMuc IS NOT NULL
                    ORDER BY t.ThuMuc
                """)).fetchall()
                result = []
                for thu_muc, dept_status in rows:
                    if not thu_muc:
                        continue
                    if dept_status in ('disabled', 'archived'):
                        result.append(f"{thu_muc} ({dept_status})")
                    else:
                        result.append(thu_muc)
                return result
            except Exception:
                # Fallback: Departments chua co -> tra ve thuan tuy ThuMuc
                rows = conn.execute(text(
                    "SELECT DISTINCT ThuMuc FROM TaiLieu WHERE ThuMuc IS NOT NULL ORDER BY ThuMuc"
                )).fetchall()
                return [r[0] for r in rows if r[0]]
    except Exception:
        return []
