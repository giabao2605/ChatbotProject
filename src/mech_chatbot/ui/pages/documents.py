import streamlit as st
from sqlalchemy import text
from mech_chatbot.auth import service as auth
from mech_chatbot.db.repository import engine, list_known_departments, delete_document_completely
from mech_chatbot.ui import labels as ui_labels
from mech_chatbot.ui.i18n import t

DOMAIN_OPTIONS = ["mechanical", "tabular", "generic"]
SECURITY_LEVELS = ["public", "internal", "confidential"]
EFFECTIVE_STATUS_LABELS = {
    "active": "\u0110ang hi\u1ec7u l\u1ef1c",
    "draft": "B\u1ea3n nh\u00e1p / d\u1ef1 th\u1ea3o",
    "expired": "H\u1ebft hi\u1ec7u l\u1ef1c",
    "superseded": "\u0110\u00e3 b\u1ecb thay th\u1ebf",
}

_EXPIRY_SENTINEL_TAT_CA = "T\u1ea5t c\u1ea3"
_EXPIRY_SENTINEL_CON = "C\u00f2n hi\u1ec7u l\u1ef1c"
_EXPIRY_SENTINEL_SAP = "S\u1eafp h\u1ebft h\u1ea1n"
_EXPIRY_SENTINEL_HET = "\u0110\u00e3 h\u1ebft h\u1ea1n"
_EXPIRY_SENTINELS = [_EXPIRY_SENTINEL_TAT_CA, _EXPIRY_SENTINEL_CON, _EXPIRY_SENTINEL_SAP, _EXPIRY_SENTINEL_HET]


def run_documents():
    st.title(t("Kho T\u00e0i Li\u1ec7u"))
    st.caption(t("Danh s\u00e1ch t\u00e0i li\u1ec7u \u0111\u00e3 \u0111\u01b0\u1ee3c n\u1ea1p v\u00e0o h\u1ec7 th\u1ed1ng."))
    if engine is None:
        st.error(t("Kh\u00f4ng th\u1ec3 k\u1ebft n\u1ed1i Database."))
        return

    current_user = auth.get_current_user()
    is_admin = auth.has_role("admin")

    render_expiry_panel(is_admin)
    st.divider()

    allowed_departments = current_user.get("allowed_departments") or [current_user.get("department")]
    allowed_departments = [d for d in allowed_departments if d]

    dept_options = [
        d["code"]
        for d in list_known_departments(active_only=True)
        if is_admin or d["code"] in allowed_departments
    ]

    fc1, fc2, fc3, fc4, fc5 = st.columns([2, 2, 2, 2, 2])
    with fc1:
        dept_filter = st.selectbox(
            t("Ph\u00f2ng ban"),
            [t("T\u1ea5t c\u1ea3")] + sorted(set(dept_options)),
            format_func=ui_labels.dept_label,
            key="docs_dept",
        )
    with fc2:
        domain_filter = st.selectbox(
            ui_labels.gloss("Domain"),
            [t("T\u1ea5t c\u1ea3")] + DOMAIN_OPTIONS,
            key="docs_domain",
        )
    with fc3:
        sec_filter = st.selectbox(
            t("M\u1ee9c m\u1eadt"),
            [t("T\u1ea5t c\u1ea3")] + SECURITY_LEVELS,
            key="docs_sec",
        )
    with fc4:
        eff_filter = st.selectbox(
            t("Hi\u1ec7u l\u1ef1c"),
            _EXPIRY_SENTINELS,
            format_func=lambda x: t(EFFECTIVE_STATUS_LABELS.get(x, x)) if x in EFFECTIVE_STATUS_LABELS else t(x),
            key="docs_eff",
        )
    with fc5:
        search_kw = st.text_input(t("T\u00ecm ki\u1ebfm"), key="docs_search")

    _tat_ca = t("T\u1ea5t c\u1ea3")
    params = {}
    filters = []

    filters.append("d.LifecycleStatus IN ('published', 'archived', 'superseded')")
    filters.append("d.ReviewStatus = 'approved'")

    if not is_admin:
        if allowed_departments:
            _dept_clauses = []
            for _idx, _dept in enumerate(sorted(set(allowed_departments))):
                _k = f"allowed_dept_{_idx}"
                _dept_clauses.append(f"d.ThuMuc = :{_k}")
                params[_k] = _dept
            filters.append("(" + " OR ".join(_dept_clauses) + ")")
        else:
            filters.append("1 = 0")

    if dept_filter != _tat_ca:
        filters.append("d.ThuMuc = :dept")
        params["dept"] = dept_filter

    if domain_filter != _tat_ca:
        filters.append("d.Domain = :domain")
        params["domain"] = domain_filter

    if sec_filter != _tat_ca:
        filters.append("d.SecurityLevel = :sec")
        params["sec"] = sec_filter

    if eff_filter == _EXPIRY_SENTINEL_CON:
        filters.append("(d.ExpiryDate IS NULL OR d.ExpiryDate > GETDATE())")
        filters.append("(d.EffectiveStatus IS NULL OR d.EffectiveStatus = 'active')")
    elif eff_filter == _EXPIRY_SENTINEL_SAP:
        filters.append("d.ExpiryDate IS NOT NULL")
        filters.append("d.ExpiryDate BETWEEN GETDATE() AND DATEADD(day, 30, GETDATE())")
    elif eff_filter == _EXPIRY_SENTINEL_HET:
        filters.append("d.ExpiryDate IS NOT NULL AND d.ExpiryDate < GETDATE()")

    if search_kw:
        filters.append("(d.TenFile LIKE :kw OR d.Title LIKE :kw OR d.Tags LIKE :kw OR d.Summary LIKE :kw)")
        params["kw"] = f"%{search_kw}%"

    where_clause = ("WHERE " + " AND ".join(filters)) if filters else ""

    q = f"""
        SELECT d.DocID, d.TenFile AS OriginalFileName, d.ThuMuc AS Department, d.Domain, d.SecurityLevel,
               d.Title, d.Tags, d.Summary, d.VersionNo, d.IsCurrent AS IsCurrentVersion,
               d.UploadedBy, d.NgayTaiLen AS CreatedAt, d.ExpiryDate, d.EffectiveStatus,
               d.EffectiveDate AS EffectiveDateStart, d.ReviewDate, d.OwnerSigner, d.DocLanguage AS Language,
               d.DocNumber, d.Site, d.VariantGroup, d.VariantCode AS BranchLabel, d.LifecycleStatus, d.ReviewStatus
        FROM TaiLieu d
        {where_clause}
        ORDER BY d.NgayTaiLen DESC
    """
    try:
        with engine.connect() as conn:
            rows = conn.execute(text(q), params).fetchall()
    except Exception as e:
        st.error(t("L\u1ed7i truy xu\u1ea5t: {e}", e=e))
        return

    st.subheader(t("T\u1ed5ng s\u1ed1: {n} t\u00e0i li\u1ec7u", n=len(rows)))
    if not rows:
        st.info(t("Kh\u00f4ng t\u00ecm th\u1ea5y t\u00e0i li\u1ec7u n\u00e0o."))
        return

    selected_doc_ids = []
    select_all_docs = False
    if is_admin:
        select_all_docs = st.checkbox(t("Chọn tất cả tài liệu đang hiển thị"), key="docs_select_all")

    for row in rows:
        (
            doc_id, original_file_name, department, domain, security_level,
            title, tags, summary, version_no, is_current,
            uploaded_by, created_at, expiry_date, effective_status,
            effective_date_start, review_date, owner_signer, language,
            doc_number, site, variant_group, branch_label, lifecycle_status, review_status
        ) = row
        if is_admin:
            if st.checkbox(t("Chọn DocID {doc_id} · {name}", doc_id=doc_id, name=original_file_name), value=select_all_docs, key=f"docs_pick_{doc_id}"):
                selected_doc_ids.append(doc_id)
        render_document_row(
            doc_id=doc_id, original_file_name=original_file_name, department=department,
            domain=domain, security_level=security_level, title=title, tags=tags,
            summary=summary, version_no=version_no, is_current=is_current,
            uploaded_by=uploaded_by, created_at=created_at, expiry_date=expiry_date,
            effective_status=effective_status, effective_date_start=effective_date_start,
            review_date=review_date, owner_signer=owner_signer, language=language,
            doc_number=doc_number, site=site, variant_group=variant_group,
            branch_label=branch_label, is_admin=is_admin,
        )

    if is_admin and selected_doc_ids:
        st.markdown("---")
        st.warning(t("Đã chọn {n} tài liệu.", n=len(selected_doc_ids)))
        if st.button("🗑️ " + t("Xóa tất cả tài liệu đã chọn"), type="secondary", key="docs_bulk_delete_btn"):
            st.session_state["docs_confirm_bulk_delete"] = selected_doc_ids

    if is_admin and st.session_state.get("docs_confirm_bulk_delete"):
        ids = st.session_state["docs_confirm_bulk_delete"]
        st.error(t("Xác nhận xóa vĩnh viễn {n} tài liệu?", n=len(ids)))
        c_ok, c_cancel = st.columns(2)
        with c_ok:
            if st.button("✅ " + t("Xác nhận xóa"), key="docs_confirm_bulk_delete_btn", type="primary"):
                actor = (auth.get_current_user() or {}).get("username") or "System"
                ok, fail = 0, 0
                for did in ids:
                    try:
                        if delete_document_completely(did, reviewer=actor):
                            ok += 1
                        else:
                            fail += 1
                    except Exception:
                        fail += 1
                st.session_state.pop("docs_confirm_bulk_delete", None)
                st.success(t("Đã xóa: {ok} thành công, {fail} thất bại.", ok=ok, fail=fail))
                st.rerun()
        with c_cancel:
            if st.button(t("Hủy"), key="docs_cancel_bulk_delete"):
                st.session_state.pop("docs_confirm_bulk_delete", None)
                st.rerun()


def render_document_row(
    doc_id, original_file_name, department, domain, security_level,
    title, tags, summary, version_no, is_current, uploaded_by, created_at,
    expiry_date, effective_status, effective_date_start, review_date,
    owner_signer, language, doc_number, site, variant_group, branch_label,
    is_admin=False,
):
    eff_label = t(EFFECTIVE_STATUS_LABELS.get(effective_status or "active", effective_status or ""))
    current_badge = "\u2705 " + t("Hi\u1ec7n h\u00e0nh") if is_current else "\U0001f4c4 " + t("C\u0169")
    display_title = title or original_file_name or f"DocID {doc_id}"
    with st.expander(
        f"{current_badge} \u00b7 {display_title} \u00b7 v{version_no or 1} \u00b7 {ui_labels.dept_label(department)} \u00b7 {eff_label}"
    ):
        c1, c2, c3 = st.columns(3)
        with c1:
            st.write(f"**DocID:** {doc_id}")
            st.write(f"**" + t("File:") + f"** {original_file_name or ''}")
            st.write(f"**Domain:** {domain or t('(ch\u01b0a x\u00e1c \u0111\u1ecbnh)')}")
            st.write(f"**" + t("M\u1ee9c m\u1eadt:") + f"** {security_level or ''}")
            if site:
                st.write(f"**Site:** {site}")
        with c2:
            st.write(f"**" + t("Ti\u00eau \u0111\u1ec1:") + f"** {title or ''}")
            st.write(f"**" + t("S\u1ed1 v\u0103n b\u1ea3n:") + f"** {doc_number or ''}")
            st.write(f"**" + t("Ng\u01b0\u1eddi k\u00fd:") + f"** {owner_signer or ''}")
            st.write(f"**" + t("Ng\u00f4n ng\u1eef:") + f"** {language or ''}")
        with c3:
            st.write(f"**" + t("Ng\u00e0y hi\u1ec7u l\u1ef1c:") + f"** {effective_date_start or ''}")
            st.write(f"**" + t("Ng\u00e0y h\u1ebft h\u1ea1n:") + f"** {expiry_date or ''}")
            st.write(f"**" + t("So\u00e1t x\u00e9t:") + f"** {review_date or ''}")
            st.write(f"**" + t("Tr\u1ea1ng th\u00e1i hi\u1ec7u l\u1ef1c:") + f"** {eff_label}")
        if tags:
            st.write(f"**Tags:** {tags}")
        if summary:
            st.write(f"**" + t("T\u00f3m t\u1eaft:") + f"** {summary}")
        if variant_group:
            st.write(f"**Variant Group:** {variant_group}" + (f" \u00b7 Branch: `{branch_label}`" if branch_label else ""))

        if is_admin:
            render_doc_admin_actions(doc_id, is_current, original_file_name or "")


def render_doc_admin_actions(doc_id, is_current, file_name):
    st.markdown("---")
    col1, col2, col3 = st.columns(3)
    with col1:
        if st.button(t("\u0110\u00e1nh d\u1ea5u hi\u1ec7n h\u00e0nh"), key=f"set_current_{doc_id}", disabled=bool(is_current)):
            try:
                with engine.begin() as conn:
                    # P4.3: Chi reset cac ban trong cung VariantGroup neu VariantGroup khong NULL.
                    # Neu NULL, subquery tra ve NULL -> WHERE VariantGroup = NULL khong match gi
                    # -> se co nhieu ban cung IsCurrent=1. Fix: them AND VariantGroup IS NOT NULL.
                    conn.execute(text("""
                        UPDATE TaiLieu SET IsCurrent = 0
                        WHERE VariantGroup IS NOT NULL
                          AND VariantGroup = (SELECT VariantGroup FROM TaiLieu WHERE DocID = :id)
                    """), {"id": doc_id})
                    conn.execute(
                        text("UPDATE TaiLieu SET IsCurrent = 1 WHERE DocID = :id"),
                        {"id": doc_id},
                    )
                st.success(t("\u0110\u00e3 \u0111\u00e1nh d\u1ea5u hi\u1ec7n h\u00e0nh."))
                st.rerun()
            except Exception as e:
                st.error(t("L\u1ed7i: {e}", e=e))
    with col2:
        if st.button(t("\u0110\u00e1nh d\u1ea5u h\u1ebft hi\u1ec7u l\u1ef1c"), key=f"expire_{doc_id}"):
            try:
                with engine.begin() as conn:
                    conn.execute(
                        text("UPDATE TaiLieu SET EffectiveStatus = 'expired' WHERE DocID = :id"),
                        {"id": doc_id},
                    )
                st.success(t("\u0110\u00e3 c\u1eadp nh\u1eadt tr\u1ea1ng th\u00e1i."))
                st.rerun()
            except Exception as e:
                st.error(t("L\u1ed7i: {e}", e=e))
    with col3:
        if st.button("\U0001f5d1\ufe0f " + t("X\u00f3a t\u00e0i li\u1ec7u"), key=f"del_doc_{doc_id}", type="secondary"):
            st.session_state[f"confirm_del_{doc_id}"] = True
    if st.session_state.get(f"confirm_del_{doc_id}"):
        st.warning(t("X\u00e1c nh\u1eadn x\u00f3a v\u0129nh vi\u1ec5n? Kh\u00f4ng th\u1ec3 ho\u00e0n t\u00e1c."))
        dc1, dc2 = st.columns(2)
        with dc1:
            if st.button("\u2705 " + t("X\u00e1c nh\u1eadn"), key=f"confirm_del_btn_{doc_id}", type="primary"):
                try:
                    actor = (auth.get_current_user() or {}).get("username") or "System"
                    if delete_document_completely(doc_id, reviewer=actor):
                        st.session_state.pop(f"confirm_del_{doc_id}", None)
                        st.success(t("\u0110\u00e3 x\u00f3a t\u00e0i li\u1ec7u."))
                        st.rerun()
                    else:
                        st.error(t("Xóa tài liệu thất bại."))
                except Exception as e:
                    st.error(t("L\u1ed7i x\u00f3a: {e}", e=e))
        with dc2:
            if st.button(t("H\u1ee7y"), key=f"cancel_del_{doc_id}"):
                st.session_state.pop(f"confirm_del_{doc_id}", None)
                st.rerun()


def render_expiry_panel(is_admin):
    with st.expander("\u23f0 " + t("T\u00e0i li\u1ec7u s\u1eafp h\u1ebft hi\u1ec7u l\u1ef1c / c\u1ea7n so\u00e1t x\u00e9t"), expanded=False):
        st.caption(
            t(
                "Hi\u1ec3n c\u00e1c t\u00e0i li\u1ec7u c\u00f3 ExpiryDate ho\u1eb7c ReviewDate trong v\u00f2ng 60 ng\u00e0y t\u1edbi, "
                "ho\u1eb7c \u0111\u00e3 qu\u00e1 h\u1ea1n m\u00e0 v\u1eabn \u0111ang \u1edf tr\u1ea1ng th\u00e1i active."
            )
        )
        try:
            with engine.connect() as conn:
                q = text("""
                    SELECT DocID, TenFile AS OriginalFileName, ThuMuc AS Department, EffectiveStatus,
                           ExpiryDate, ReviewDate, IsCurrent AS IsCurrentVersion
                    FROM TaiLieu
                    WHERE (
                        (ExpiryDate IS NOT NULL AND ExpiryDate <= DATEADD(day, 60, GETDATE()))
                        OR (ReviewDate IS NOT NULL AND ReviewDate <= DATEADD(day, 60, GETDATE()))
                    )
                    AND IsCurrent = 1
                    AND LifecycleStatus <> 'deleting'
                    ORDER BY ExpiryDate ASC
                """)
                rows = conn.execute(q).fetchall()
            if not rows:
                st.info(t("Kh\u00f4ng c\u00f3 t\u00e0i li\u1ec7u n\u00e0o s\u1eafp h\u1ebft h\u1ea1n trong 60 ng\u00e0y t\u1edbi."))
            else:
                st.dataframe(
                    [{
                        "DocID": r[0],
                        t("T\u00ean file"): r[1],
                        t("Ph\u00f2ng"): r[2],
                        t("Tr\u1ea1ng th\u00e1i"): t(EFFECTIVE_STATUS_LABELS.get(r[3] or "active", r[3] or "")),
                        t("H\u1ebft h\u1ea1n"): str(r[4]) if r[4] else "",
                        t("So\u00e1t x\u00e9t"): str(r[5]) if r[5] else "",
                    } for r in rows],
                    use_container_width=True, hide_index=True,
                )
        except Exception as e:
            st.error(t("L\u1ed7i t\u1ea3i d\u1eef li\u1ec7u: {e}", e=e))
