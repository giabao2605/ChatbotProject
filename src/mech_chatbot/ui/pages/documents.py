import streamlit as st
from sqlalchemy import text
from mech_chatbot.auth import service as auth
from mech_chatbot.ingestion.doc_type_registry import canonical_label
from mech_chatbot.db.repository import (
    engine, update_document_full_metadata, delete_document_completely,
    list_known_departments, list_known_sites,
)

# GD4b: dung chung cho cac form chinh phan loai (linh hoat da phong ban)
DOMAIN_OPTIONS = ["mechanical", "tabular", "generic"]
DOMAIN_LABELS = {
    "mechanical": "Cơ khí / Kỹ thuật",
    "tabular": "Bảng biểu / Tài chính",
    "generic": "Hành chính / Văn bản",
}
SECURITY_LEVELS = ["public", "internal", "confidential"]


def run_documents():
    st.title("Kho tài liệu")
    st.caption("Tra cứu, lọc và quản lý tài liệu đã ingest.")

    if not (auth.has_role("reviewer") or auth.has_role("admin")):
        st.error("Bạn không có quyền xem kho tài liệu.")
        return
    if engine is None:
        st.error("Không thể kết nối Database.")
        return

    current_user = auth.get_current_user()
    is_admin = auth.has_role("admin")

    search = st.text_input("Tìm kiếm", placeholder="Tên file, Base Code, mã đối tượng...")

    # P1.4: bộ lọc theo phòng ban + khu/site
    fc1, fc2, fc3 = st.columns(3)
    with fc1:
        status_filter = st.selectbox("Trạng thái", ["Tất cả", "published", "draft", "rejected", "archived", "superseded"])

    # Danh sách phòng ban khả dụng cho user (admin thấy tất cả)
    if is_admin:
        dept_options = [d["code"] for d in list_known_departments(active_only=False)]
    else:
        dept_options = [d for d in (current_user.get("allowed_departments") or [current_user.get("department")]) if d]
    with fc2:
        dept_filter = st.selectbox("Phòng ban", ["Tất cả"] + sorted(set(dept_options)))

    # Danh sách khu/site (admin: tất cả; user: theo allowed_sites nếu có)
    if is_admin:
        site_options = [s["code"] for s in list_known_sites(active_only=False)]
    else:
        site_options = [s for s in (current_user.get("allowed_sites") or []) if s]
    with fc3:
        site_filter = st.selectbox("Khu / Site", ["Tất cả"] + sorted(set(site_options)))

    docs = load_documents(current_user, search, status_filter, dept_filter, site_filter)

    if not docs:
        st.info("Không tìm thấy tài liệu.")
        return

    st.write(f"Tìm thấy **{len(docs)}** tài liệu.")
    for doc in docs:
        render_document_item(doc, current_user)


def load_documents(current_user, search, status_filter, dept_filter="Tất cả", site_filter="Tất cả"):
    is_admin = auth.has_role("admin")
    query = """
        SELECT TOP 200
            t.DocID, t.TenFile, t.ThuMuc, t.BaseCode, t.VersionNo, t.VersionLabel,
            t.VariantCode, t.VariantGroup, t.LifecycleStatus, t.ReviewStatus,
            t.IsCurrent, t.NgayTaiLen, t.Site, t.Domain, t.SecurityLevel,
            tk.MaDoiTuong, tk.LoaiTaiLieu, tk.TenSanPham
        FROM TaiLieu t
        LEFT JOIN TaiLieuKyThuat tk ON t.DocID = tk.DocID AND tk.TrangSo = 1
        WHERE 1 = 1
    """
    params = {}
    if status_filter != "Tất cả":
        query += " AND t.LifecycleStatus = :status"
        params["status"] = status_filter
    if search:
        query += """
            AND (t.TenFile LIKE :search OR t.BaseCode LIKE :search
                 OR tk.MaDoiTuong LIKE :search OR tk.TenSanPham LIKE :search)
        """
        params["search"] = f"%{search}%"

    # RBAC phòng ban: non-admin chỉ thấy phòng được phép
    allowed = [d for d in (current_user.get("allowed_departments") or [current_user.get("department")]) if d]
    if not is_admin and allowed:
        keys = []
        for i, dept in enumerate(allowed):
            key = f"dept_{i}"
            params[key] = dept
            keys.append(f":{key}")
        query += f" AND t.ThuMuc IN ({', '.join(keys)})"

    # P1.4: lọc theo phòng ban được chọn
    if dept_filter and dept_filter != "Tất cả":
        query += " AND t.ThuMuc = :dept_pick"
        params["dept_pick"] = dept_filter

    # P1.4 + RBAC site: non-admin giới hạn theo allowed_sites (cho phép Site NULL để không ẩn dữ liệu cũ)
    user_sites = [s for s in (current_user.get("allowed_sites") or []) if s]
    if not is_admin and user_sites:
        keys = []
        for i, s in enumerate(user_sites):
            key = f"usite_{i}"
            params[key] = s
            keys.append(f":{key}")
        query += f" AND (t.Site IS NULL OR t.Site IN ({', '.join(keys)}))"

    # P1.4: lọc theo khu/site được chọn
    if site_filter and site_filter != "Tất cả":
        query += " AND t.Site = :site_pick"
        params["site_pick"] = site_filter

    query += " ORDER BY t.NgayTaiLen DESC"
    with engine.connect() as conn:
        return conn.execute(text(query), params).fetchall()


def render_document_item(doc, current_user):
    (doc_id, ten_file, thu_muc, base_code, version_no, version_label, variant_code,
     variant_group, lifecycle_status, review_status, is_current, ngay_tai_len,
     site, domain, security_level,
     ma_doi_tuong, loai_tai_lieu, ten_san_pham) = doc

    current_badge = " · current" if is_current else ""
    sec_badge = f" · 🔒 {security_level}" if security_level else ""
    with st.expander(f"{ten_file} · {lifecycle_status}{current_badge}{sec_badge}"):
        c1, c2 = st.columns(2)
        with c1:
            st.write(f"**DocID:** {doc_id}")
            st.write(f"**Phòng ban:** {thu_muc}")
            st.write(f"**Khu / Site:** {site or '(chưa gán)'}")
            st.write(f"**Lĩnh vực (domain):** {domain or '(chưa gán)'}")
            st.write(f"**Mức mật:** {security_level or '(chưa gán)'}")
            st.write(f"**Base Code:** `{base_code}`")
            st.write(f"**Version:** {version_no} - {version_label}")
            st.write(f"**Variant:** {variant_code}")
        with c2:
            st.write(f"**Review:** {review_status}")
            st.write(f"**Lifecycle:** {lifecycle_status}")
            st.write(f"**Ngày tải:** {ngay_tai_len}")
            # GD4b: hien thi linh hoat theo domain — chi tai lieu co khi moi show ma doi tuong
            if (domain or "generic") == "mechanical":
                st.write(f"**Mã đối tượng:** {ma_doi_tuong}")
                st.write(f"**Tên sản phẩm:** {ten_san_pham}")
            elif ten_san_pham:
                st.write(f"**Tiêu đề tài liệu:** {ten_san_pham}")
            st.write(f"**Loại tài liệu:** {canonical_label(loai_tai_lieu)}")

        if auth.has_role("admin"):
            render_admin_actions(doc_id, base_code, version_no, version_label, variant_code, variant_group, loai_tai_lieu, domain, security_level, site, thu_muc, current_user)


def render_admin_actions(doc_id, base_code, version_no, version_label, variant_code, variant_group, loai_tai_lieu, domain, security_level, site, thu_muc, current_user):
    st.markdown("---")
    st.subheader("Quản trị tài liệu")
    with st.form(f"edit_doc_{doc_id}"):
        # --- Phân loại & quyền truy cập (linh hoạt đa phòng ban) ---
        st.markdown("**Phân loại & quyền truy cập**")
        cc1, cc2, cc3 = st.columns(3)
        with cc1:
            _dom_idx = DOMAIN_OPTIONS.index(domain) if domain in DOMAIN_OPTIONS else DOMAIN_OPTIONS.index("generic")
            new_domain = st.selectbox("Lĩnh vực (domain)", DOMAIN_OPTIONS, index=_dom_idx,
                                      format_func=lambda d: DOMAIN_LABELS.get(d, d), key=f"dom_{doc_id}")
        with cc2:
            _sec_idx = SECURITY_LEVELS.index(security_level) if security_level in SECURITY_LEVELS else SECURITY_LEVELS.index("internal")
            new_security = st.selectbox("Mức mật", SECURITY_LEVELS, index=_sec_idx, key=f"sec_{doc_id}")
        with cc3:
            _site_opts = [""] + sorted({s["code"] for s in list_known_sites(active_only=False)} | ({site} if site else set()))
            _site_idx = _site_opts.index(site) if site in _site_opts else 0
            new_site = st.selectbox("Khu / Site", _site_opts, index=_site_idx,
                                    format_func=lambda s: s or "(chưa gán)", key=f"site_{doc_id}")
        st.markdown("**Thông tin phiên bản / mã tài liệu**")
        c1, c2 = st.columns(2)
        with c1:
            new_base_code = st.text_input("Base Code / Mã tài liệu", value=base_code or "")
            new_version_no = st.number_input("Version No", value=int(version_no) if version_no else 1, step=1)
            new_version_label = st.text_input("Version Label", value=version_label or "")
        with c2:
            new_variant_code = st.text_input("Variant Code", value=variant_code or "default")
            new_variant_group = st.text_input("Variant Group", value=variant_group or "")
            new_doc_type = st.text_input("Document Type / Loại tài liệu", value=loai_tai_lieu or "")
        submitted = st.form_submit_button("Lưu metadata", type="primary")
    if submitted:
        try:
            ok = update_document_full_metadata(
                doc_id, base_code=new_base_code, version_no=new_version_no,
                version_label=new_version_label, variant_code=new_variant_code,
                variant_group=new_variant_group, loai_tai_lieu=new_doc_type,
                domain=new_domain, security_level=new_security, site=new_site,
                reviewer=current_user["username"],
            )
            if ok:
                st.success("Đã cập nhật metadata.")
                st.rerun()
            else:
                st.error("Cập nhật thất bại.")
        except Exception as e:
            st.error(f"Lỗi cập nhật: {e}")

    confirm = st.checkbox("Tôi hiểu thao tác này sẽ xóa vĩnh viễn dữ liệu SQL + Qdrant.", key=f"confirm_delete_{doc_id}")
    if st.button("Xóa tài liệu", key=f"delete_doc_{doc_id}", disabled=not confirm, type="secondary"):
        try:
            ok = delete_document_completely(doc_id, reviewer=current_user["username"])
            if ok:
                st.success("Đã xóa tài liệu.")
                st.rerun()
            else:
                st.error("Xóa thất bại.")
        except Exception as e:
            st.error(f"Lỗi xóa: {e}")
