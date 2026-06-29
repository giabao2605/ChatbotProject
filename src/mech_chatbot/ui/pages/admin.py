import streamlit as st
import json
from sqlalchemy import text
from mech_chatbot.db.repository import (
    engine, 
    publish_as_new_version, 
    publish_as_new_variant, 
    publish_as_standalone, 
    reject_document,
    write_audit_log,
    update_document_full_metadata,
    delete_document_completely,
    update_qdrant_metadata,
    get_document_metadata,
    update_document_common_metadata,
)
from mech_chatbot.ui import metadata_forms
from mech_chatbot.ui import labels

# GD4b: dung chung cho form duyet (linh hoat da phong ban)
DOMAIN_OPTIONS = ["mechanical", "tabular", "generic"]
DOMAIN_LABELS = {
    "mechanical": "Cơ khí / Kỹ thuật",
    "tabular": "Bảng biểu / Tài chính",
    "generic": "Hành chính / Văn bản",
}
SECURITY_LEVELS = ["public", "internal", "confidential"]

def run_admin():
    st.title("Duyệt Tài Liệu Đầu Vào (Phase 3 Workflow)")
    st.markdown("Kiểm tra AI Classification và quyết định chiến lược publish (New Version / Variant / Standalone).")

    col_new, col_reset, _col_sp = st.columns([1, 1, 4])
    with col_new:
        if st.button("Thêm file mới", help="Chuyển sang trang upload để nạp bản vẽ mới", use_container_width=True):
            st.session_state["_nav_request"] = "upload"
            st.rerun()
    with col_reset:
        if st.button("Làm mới (Reset)", help="Tải lại dữ liệu mới nhất từ database", use_container_width=True):
            st.rerun()

    if engine is None:
        st.error("Không thể kết nối đến Database.")
        return

    from mech_chatbot.auth import service as auth
    current_user = auth.get_current_user()
    is_admin = auth.has_role("admin")
    user_dept = current_user["department"]

    tabs = st.tabs([
        "Chờ Duyệt (Pending)", 
        "Đã Duyệt (Published)", 
        "Bị Từ Chối (Rejected)"
    ])
    
    with engine.connect() as conn:
        query_str = """
            SELECT t.DocID, t.TenFile, t.ThuMuc, t.ReviewStatus, t.NgayTaiLen,
                   tk.MaDoiTuong, tk.LoaiTaiLieu, tk.TenSanPham, tk.VatLieu, tk.DungSaiDay, tk.KichThuocTongThe,
                   j.ClassificationJson, j.RequestedAction, t.LifecycleStatus, j.ClassificationConfidence,
                   t.BaseCode, t.VersionNo, t.VersionLabel, t.VariantCode, t.VariantGroup,
                   t.Domain, t.SecurityLevel, t.Site,
                   j.QualityStatus, j.QualityScore
            FROM TaiLieu t
            LEFT JOIN TaiLieuKyThuat tk ON t.DocID = tk.DocID AND tk.TrangSo = 1
            OUTER APPLY (
                SELECT TOP 1 ClassificationJson, RequestedAction, ClassificationConfidence, QualityStatus, QualityScore 
                FROM dbo.IngestionJobs j2 
                WHERE j2.TenFile = t.TenFile AND j2.ThuMuc = t.ThuMuc 
                ORDER BY j2.CreatedAt DESC
            ) j
        """
        if not is_admin:
            query_str += " WHERE t.ThuMuc = :dept"
            
        query_str += " ORDER BY t.NgayTaiLen DESC"
        
        query = text(query_str)
        if not is_admin:
            result = conn.execute(query, {"dept": user_dept})
        else:
            result = conn.execute(query)
            
        all_docs = result.fetchall()

    pending_docs = [d for d in all_docs if d[3] == "pending_review"]
    published_docs = [d for d in all_docs if d[3] == "approved"]
    rejected_docs = [d for d in all_docs if d[3] == "rejected"]

    def render_doc_list(docs, show_actions=False, allow_manage=False):
        if not docs:
            st.info("Không có tài liệu nào trong danh sách này.")
            return

        if show_actions:
            _render_bulk_panel(docs, current_user)

        for d in docs:
            doc_id, ten_file, thu_muc, review_status, ngay_tai_len, ma_dt, loai_tl, ten_sp, vat_lieu, dung_sai, kich_thuoc, class_json, req_action, life_status, class_conf, t_bc, t_vn, t_vl, t_vc, t_vg, t_dom, t_sec, t_site, t_qstatus, t_qscore = d
            
            with st.expander(f"{ten_file} - Tải lên: {ngay_tai_len.strftime('%Y-%m-%d')} | {labels.status_badge(life_status)}"):
                st.write(f"**Phòng ban:** {thu_muc}")
                if show_actions:
                    st.checkbox("☑️ Chọn để thao tác hàng loạt", key=f"bulk_sel_{doc_id}")
                
                # Hien thi AI Classification
                cls_data = {}
                if class_json:
                    try:
                        cls_data = json.loads(class_json)
                        st.markdown("### AI Classification Đề Xuất:")
                        st.info(f"**Action:** `{cls_data.get('detected_action')}` | **Base Code:** `{cls_data.get('base_code')}` | **Confidence:** {class_conf*100 if class_conf else 0:.1f}%")
                        st.write(f"**Lý do AI:** {cls_data.get('reason')}")
                    except Exception as e:
                        st.warning(f"ClassificationJson không hợp lệ: {e}")

                else:
                    st.warning("Chưa có kết quả AI Classification cho file này.")

                st.markdown("### Dữ Liệu Bóc Tách Metadata:")
                col1, col2 = st.columns(2)
                with col1:
                    st.write(f"- **Phòng ban:** {thu_muc}")
                    st.write(f"- **Lĩnh vực (domain):** {labels.domain_label(t_dom)}")
                    st.write(f"- **Mức mật:** {t_sec or '(chưa gán)'}")
                    st.write(f"- **Khu / Site:** {t_site or '(chưa gán)'}")
                    _lbl_loai = labels.field_label(t_dom, "loai_tai_lieu")
                    if _lbl_loai:
                        st.write(f"- **{_lbl_loai}:** {loai_tl}")
                with col2:
                    # A1: nhan dong theo domain, truong khong thuoc domain se bi an
                    _lbl_ma = labels.field_label(t_dom, "ma_doi_tuong")
                    _lbl_ten = labels.field_label(t_dom, "ten_san_pham")
                    _lbl_vl = labels.field_label(t_dom, "vat_lieu")
                    _lbl_kt = labels.field_label(t_dom, "kich_thuoc")
                    _lbl_ds = labels.field_label(t_dom, "dung_sai")
                    _shown_any = False
                    if _lbl_ma and ma_dt:
                        st.write(f"- **{_lbl_ma}:** `{ma_dt}`"); _shown_any = True
                    if _lbl_ten and ten_sp:
                        st.write(f"- **{_lbl_ten}:** {ten_sp}"); _shown_any = True
                    if _lbl_vl and vat_lieu:
                        st.write(f"- **{_lbl_vl}:** {vat_lieu}"); _shown_any = True
                    if _lbl_kt and kich_thuoc:
                        st.write(f"- **{_lbl_kt}:** {kich_thuoc}"); _shown_any = True
                    if _lbl_ds and dung_sai:
                        st.write(f"- **{_lbl_ds}:** {dung_sai}"); _shown_any = True
                    if not _shown_any:
                        st.caption("(Không có trường chi tiết áp dụng cho loại tài liệu này.)")

                if show_actions:
                    st.markdown("### Cập nhật Metadata trước khi Duyệt (Bắt buộc kiểm tra):")
                    col_a, col_b = st.columns(2)
                    with col_a:
                        edit_base_code = st.text_input("Base Code", value=t_bc or "", key=f"bc_{doc_id}")
                        edit_version_no = st.number_input("Version No", value=int(t_vn) if t_vn else 1, step=1, key=f"vn_{doc_id}")
                        edit_version_label = st.text_input("Version Label", value=t_vl or "", key=f"vl_{doc_id}")
                    with col_b:
                        edit_variant_code = st.text_input("Variant Code", value=t_vc or "default", key=f"vc_{doc_id}")
                        edit_variant_group = st.text_input("Variant Group", value=t_vg or "", key=f"vg_{doc_id}")
                        edit_loai_tl = st.text_input("Document Type / Loại tài liệu", value=loai_tl or "", key=f"dt_{doc_id}")

                    st.markdown("**Phân loại & quyền truy cập (đa phòng ban):**")
                    colc, cold, cole = st.columns(3)
                    with colc:
                        _dom_idx = DOMAIN_OPTIONS.index(t_dom) if t_dom in DOMAIN_OPTIONS else DOMAIN_OPTIONS.index("generic")
                        edit_domain = st.selectbox("Lĩnh vực (domain)", DOMAIN_OPTIONS, index=_dom_idx, format_func=lambda dv: DOMAIN_LABELS.get(dv, dv), key=f"dom_{doc_id}")
                    with cold:
                        _sec_idx = SECURITY_LEVELS.index(t_sec) if t_sec in SECURITY_LEVELS else SECURITY_LEVELS.index("internal")
                        edit_security = st.selectbox("Mức mật", SECURITY_LEVELS, index=_sec_idx, key=f"sec_{doc_id}")
                    with cole:
                        edit_site = st.text_input("Khu / Site", value=t_site or "", key=f"site_{doc_id}")

                    _meta = get_document_metadata(doc_id)
                    with st.expander("Metadata tổng quát (đa phòng ban) — kiểm tra/bổ sung trước khi duyệt", expanded=False):
                        edit_common = metadata_forms.render_common_metadata(prefix=f"admeta_{doc_id}", defaults=_meta, show_header=False)
                        edit_attrs = metadata_forms.render_domain_attributes(edit_domain, prefix=f"admeta_{doc_id}", defaults=_meta.get("attributes"))

                    # GD5: gate chat luong — cho phep reviewer/admin override khi tai lieu bi blocked
                    _is_blocked = (t_qstatus == "blocked")
                    override_quality = False
                    if _is_blocked:
                        st.warning(f"⚠️ Tài liệu đang bị chặn bởi gate chất lượng (quality_status=blocked, điểm {t_qscore or 0}/100). Hãy kiểm tra kỹ nội dung trước khi publish.")
                        override_quality = st.checkbox("Tôi đã kiểm tra và vẫn muốn publish (override gate chất lượng)", key=f"ovr_{doc_id}")

                    action_choice = st.radio(
                        "Chọn hành động Publish (AI Đề xuất: " + cls_data.get('detected_action', 'new_document') + "):",
                        options=[
                            "Publish làm version mới (Archive bản cũ cùng variant)",
                            "Publish song song như variant mới (Giữ nguyên bản cũ)",
                            "Publish như tài liệu độc lập (Standalone)",
                            "Lưu nháp / Cần sửa metadata",
                            "Từ chối (Reject)"
                        ],
                        key=f"radio_{doc_id}"
                    )
                    
                    if st.button("Lưu Metadata & Thực hiện", key=f"btn_{doc_id}", type="primary"):
                        from mech_chatbot.db.repository import normalize_base_code
                        edit_base_code_norm = normalize_base_code(edit_base_code)
                        
                        # Lưu metadata vào DB trước
                        with engine.begin() as save_conn:
                            save_conn.execute(text("""
                                UPDATE TaiLieu 
                                SET BaseCode = :bc, VersionNo = :vn, VersionLabel = :vl, VariantCode = :vc, VariantGroup = :vg,
                                    Domain = :dom, SecurityLevel = :sec, Site = :site
                                WHERE DocID = :did
                            """), {
                                "bc": edit_base_code_norm, "vn": edit_version_no, "vl": edit_version_label,
                                "vc": edit_variant_code, "vg": edit_variant_group, "did": doc_id,
                                "dom": edit_domain, "sec": edit_security, "site": (edit_site or None)
                            })
                            save_conn.execute(text("UPDATE TaiLieuKyThuat SET LoaiTaiLieu = :ltl WHERE DocID = :did"), {"ltl": edit_loai_tl, "did": doc_id})
                            
                            if edit_base_code_norm:
                                f_row = save_conn.execute(text("SELECT FamilyID FROM DocumentFamily WHERE BaseCode = :b"), {"b": edit_base_code_norm}).fetchone()
                                if f_row:
                                    save_conn.execute(text("UPDATE TaiLieu SET FamilyID = :fid WHERE DocID = :did"), {"fid": f_row[0], "did": doc_id})
                                else:
                                    save_conn.execute(text("INSERT INTO DocumentFamily (BaseCode, FamilyName) VALUES (:b, :n)"), {"b": edit_base_code_norm, "n": f"Family {edit_base_code_norm}"})
                                    f_row2 = save_conn.execute(text("SELECT FamilyID FROM DocumentFamily WHERE BaseCode = :b"), {"b": edit_base_code_norm}).fetchone()
                                    save_conn.execute(text("UPDATE TaiLieu SET FamilyID = :fid WHERE DocID = :did"), {"fid": f_row2[0], "did": doc_id})

                        try:
                            update_document_common_metadata(
                                doc_id, reviewer=current_user["username"], domain=edit_domain,
                                attributes=edit_attrs, **edit_common,
                            )
                        except Exception as _me:
                            st.warning(f"Lưu metadata tổng quát lỗi: {_me}")

                        write_audit_log(current_user["username"], "edit_metadata", "TaiLieu", doc_id, {
                            "old_base_code": t_bc, "new_base_code": edit_base_code_norm,
                            "old_version": t_vn, "new_version": edit_version_no,
                            "old_variant": t_vc, "new_variant": edit_variant_code,
                            "old_security": t_sec, "new_security": edit_security,
                            "old_domain": t_dom, "new_domain": edit_domain
                        })

                        # GD4b: dong bo phan loai/quyen xuong Qdrant payload
                        try:
                            update_qdrant_metadata(doc_id, {"domain": edit_domain, "security_level": edit_security, "site": (edit_site or None), "loai_tai_lieu": edit_loai_tl})
                        except Exception as _qe:
                            st.warning(f"Đã lưu SQL nhưng đồng bộ Qdrant lỗi: {_qe}")

                        if "sửa metadata" in action_choice:
                            with engine.begin() as conn:
                                conn.execute(text("""
                                    UPDATE TaiLieu
                                    SET LifecycleStatus = 'draft',
                                        ReviewStatus = 'pending_review'
                                    WHERE DocID = :did
                                """), {"did": doc_id})

                                conn.execute(text("""
                                    UPDATE dbo.IngestionJobs
                                    SET Status = 'pending_review',
                                        ErrorMessage = N'Cần sửa metadata trước khi publish',
                                        UpdatedAt = GETDATE()
                                    WHERE TenFile = :f AND ThuMuc = :t
                                """), {"f": ten_file, "t": thu_muc})
                            st.success("Đã cập nhật metadata và đưa về trạng thái chờ review!")
                            st.rerun()
                        else:
                            success = False
                            is_publish_action = "Từ chối" not in action_choice

                            # GD5: chan publish neu tai lieu blocked ma chua tick override
                            if is_publish_action and _is_blocked and not override_quality:
                                st.error("Tài liệu đang bị chặn bởi gate chất lượng. Hãy tích ô override để publish, hoặc chọn phương án từ chối / lưu nháp.")
                                st.stop()
                            if is_publish_action and _is_blocked and override_quality:
                                write_audit_log(current_user["username"], "override_quality_gate", "TaiLieu", doc_id, {"quality_status": t_qstatus, "quality_score": t_qscore, "action": action_choice})
                            
                            if is_publish_action:
                                with engine.begin() as conn:
                                    conn.execute(text("""
                                        UPDATE dbo.IngestionJobs
                                        SET Status = 'publishing',
                                            UpdatedAt = GETDATE()
                                        WHERE TenFile = :f AND ThuMuc = :t
                                    """), {"f": ten_file, "t": thu_muc})

                            try:
                                if "version mới" in action_choice:
                                    success = publish_as_new_version(doc_id, reviewer=current_user["username"])
                                elif "variant mới" in action_choice:
                                    success = publish_as_new_variant(doc_id, reviewer=current_user["username"])
                                elif "độc lập" in action_choice:
                                    success = publish_as_standalone(doc_id, reviewer=current_user["username"])
                                elif "Từ chối" in action_choice:
                                    success = reject_document(doc_id, reviewer=current_user["username"])
                            except Exception as e:
                                success = False
                                st.error(f"Lỗi khi xử lý '{action_choice}': {e}")
                                
                            if success:
                                with engine.begin() as conn:
                                    if is_publish_action:
                                        conn.execute(text("""
                                            UPDATE dbo.IngestionJobs
                                            SET Status = 'published',
                                                ProgressPercent = 100,
                                                UpdatedAt = GETDATE()
                                            WHERE TenFile = :f AND ThuMuc = :t
                                        """), {"f": ten_file, "t": thu_muc})
                                    else:
                                        conn.execute(text("""
                                            UPDATE dbo.IngestionJobs
                                            SET Status = 'rejected',
                                                UpdatedAt = GETDATE()
                                            WHERE TenFile = :f AND ThuMuc = :t
                                        """), {"f": ten_file, "t": thu_muc})
                                        
                                st.success(f"Đã xử lý thành công: {action_choice}")
                                st.info("Tài liệu đã được duyệt. Vui lòng refresh trang nếu danh sách chưa cập nhật.")
                            else:
                                st.error("Xử lý thất bại. Vui lòng kiểm tra log.")

                if allow_manage:
                    st.markdown("---")
                    st.markdown("### Cập nhật bản vẽ (Update)")
                    with st.form(key=f"update_form_{doc_id}"):
                        uc1, uc2 = st.columns(2)
                        with uc1:
                            u_bc = st.text_input("Base Code (Mã bản vẽ)", value=t_bc or "", key=f"u_bc_{doc_id}")
                            u_vn = st.number_input("Version No", value=int(t_vn) if t_vn else 1, step=1, key=f"u_vn_{doc_id}")
                            u_vl = st.text_input("Version Label", value=t_vl or "", key=f"u_vl_{doc_id}")
                        with uc2:
                            u_vc = st.text_input("Variant Code", value=t_vc or "default", key=f"u_vc_{doc_id}")
                            u_vg = st.text_input("Variant Group", value=t_vg or "", key=f"u_vg_{doc_id}")
                            u_dt = st.text_input("Document Type", value=loai_tl or "", key=f"u_dt_{doc_id}")
                        if st.form_submit_button("Lưu cập nhật bản vẽ", type="primary"):
                            try:
                                ok_upd = update_document_full_metadata(
                                    doc_id, base_code=u_bc, version_no=u_vn, version_label=u_vl,
                                    variant_code=u_vc, variant_group=u_vg, loai_tai_lieu=u_dt,
                                    reviewer=current_user["username"]
                                )
                                if ok_upd:
                                    st.success("Đã cập nhật bản vẽ (đồng bộ SQL + Qdrant).")
                                    st.rerun()
                                else:
                                    st.error("Cập nhật Qdrant thất bại. Vui lòng kiểm tra log.")
                            except Exception as e:
                                st.error(f"Lỗi khi cập nhật: {e}")

                    st.markdown("### Xóa bản vẽ (Delete)")
                    confirm_del = st.checkbox(
                        "Tôi hiểu thao tác này sẽ xóa VĨNH VIỄN toàn bộ dữ liệu (SQL + vector Qdrant) và không thể khôi phục.",
                        key=f"confirm_del_{doc_id}"
                    )
                    if st.button("Xóa toàn bộ dữ liệu bản vẽ này", key=f"del_{doc_id}", type="secondary", disabled=not confirm_del):
                        try:
                            ok_del = delete_document_completely(doc_id, reviewer=current_user["username"])
                            if ok_del:
                                st.success(f"Đã xóa toàn bộ dữ liệu của '{ten_file}'.")
                                st.rerun()
                            else:
                                st.error("Xóa thất bại. Vui lòng kiểm tra log.")
                        except Exception as e:
                            st.error(f"Lỗi khi xóa: {e}")

    with tabs[0]:
        render_doc_list(pending_docs, show_actions=True)
    with tabs[1]:
        render_doc_list(published_docs, show_actions=False, allow_manage=True)
    with tabs[2]:
        render_doc_list(rejected_docs, show_actions=False, allow_manage=True)


def _collect_selected(docs):
    """C10: lay danh sach tai lieu da tick chon (theo session_state)."""
    selected = []
    for d in docs:
        doc_id = d[0]
        if st.session_state.get(f"bulk_sel_{doc_id}"):
            selected.append(d)
    return selected


def _run_bulk(selected, current_user, mode, include_blocked=False):
    """C10: duyet (standalone) hoac tu choi hang loat, bao cao tung file.

    GD5: ton trong gate chat luong — mac dinh BO QUA tai lieu blocked khi duyet,
    tru khi tick override (include_blocked=True) -> ghi audit override_quality_gate.
    """
    ok, fail, skipped, results = 0, 0, 0, []
    for d in selected:
        doc_id, ten_file, thu_muc = d[0], d[1], d[2]
        quality_status = d[23] if len(d) > 23 else None
        try:
            if mode == "approve":
                if quality_status == "blocked" and not include_blocked:
                    skipped += 1
                    results.append(f"⏭️ {ten_file} (bị chặn chất lượng — bỏ qua, hãy duyệt riêng để override)")
                    continue
                if quality_status == "blocked" and include_blocked:
                    write_audit_log(current_user["username"], "override_quality_gate", "TaiLieu", doc_id, {"quality_status": quality_status, "quality_score": (d[24] if len(d) > 24 else None), "action": "bulk_approve"})
                success = publish_as_standalone(doc_id, reviewer=current_user["username"])
            else:
                success = reject_document(doc_id, reviewer=current_user["username"])
            if success:
                ok += 1
                with engine.begin() as conn:
                    conn.execute(text("""
                        UPDATE dbo.IngestionJobs SET Status = :s, UpdatedAt = GETDATE()
                        WHERE TenFile = :f AND ThuMuc = :t
                    """), {"s": "published" if mode == "approve" else "rejected", "f": ten_file, "t": thu_muc})
                write_audit_log(current_user["username"], "bulk_" + mode, "TaiLieu", doc_id, {"file": ten_file})
                results.append(f"✅ {ten_file}")
            else:
                fail += 1
                results.append(f"❌ {ten_file} (thất bại)")
        except Exception as e:
            fail += 1
            results.append(f"❌ {ten_file}: {e}")
        st.session_state[f"bulk_sel_{doc_id}"] = False
    st.success(f"Hoàn tất: {ok} thành công, {fail} lỗi, {skipped} bị bỏ qua (chất lượng).")
    with st.expander("Chi tiết từng tài liệu", expanded=True):
        for r in results:
            st.write(r)
    st.info("Vui lòng bấm **Làm mới (Reset)** hoặc chuyển tab để cập nhật danh sách.")


def _render_bulk_panel(docs, current_user):
    """C10: panel thao tac hang loat o dau tab Cho Duyet."""
    with st.container(border=True):
        st.markdown("#### ⚡ Thao tác hàng loạt")
        selected = _collect_selected(docs)
        n_blocked = sum(1 for d in selected if (len(d) > 23 and d[23] == "blocked"))
        st.caption(f"Đã chọn **{len(selected)}** / {len(docs)} tài liệu. Tích chọn ở từng tài liệu bên dưới.")
        if n_blocked:
            st.warning(f"⚠️ Trong số đã chọn có **{n_blocked}** tài liệu bị chặn chất lượng. Mặc định chúng sẽ bị BỎ QUA khi duyệt.")
        include_blocked = st.checkbox("Vẫn duyệt cả tài liệu bị chặn chất lượng (override gate)", key="bulk_include_blocked", disabled=not n_blocked)
        confirm_bulk = st.checkbox("Tôi xác nhận thao tác hàng loạt trên các tài liệu đã chọn.", key="bulk_confirm_pending")
        bc1, bc2 = st.columns(2)
        with bc1:
            if st.button("Duyệt đã chọn (Standalone)", disabled=not (selected and confirm_bulk), key="bulk_approve_btn", use_container_width=True, type="primary"):
                _run_bulk(selected, current_user, mode="approve", include_blocked=include_blocked)
        with bc2:
            if st.button("Từ chối đã chọn", disabled=not (selected and confirm_bulk), key="bulk_reject_btn", use_container_width=True, type="secondary"):
                _run_bulk(selected, current_user, mode="reject")
