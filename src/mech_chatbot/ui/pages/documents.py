import os
import streamlit as st
from sqlalchemy import text
from mech_chatbot.auth import service as auth
from mech_chatbot.ingestion.doc_type_registry import canonical_label
from mech_chatbot.ui import labels
from mech_chatbot.db.repository import (
    engine, update_document_full_metadata, delete_document_completely,
    list_known_departments, list_known_sites, write_audit_log,
    get_document_metadata, update_document_common_metadata,
    get_app_setting_int,
)
from mech_chatbot.ui import metadata_forms

# GD4b: dung chung cho cac form chinh phan loai (linh hoat da phong ban)
DOMAIN_OPTIONS = ["mechanical", "tabular", "generic"]
DOMAIN_LABELS = {
    "mechanical": "Cơ khí / Kỹ thuật",
    "tabular": "Bảng biểu / Tài chính",
    "generic": "Hành chính / Văn bản",
}
SECURITY_LEVELS = ["public", "internal", "confidential"]

PAGE_SIZE = 50  # B7: so tai lieu moi trang

# P1.1: nhan trang thai hieu luc (dong bo voi metadata_forms.EFFECTIVE_STATUSES)
EFFECTIVE_STATUS_LABELS = {
    "active": "Đang hiệu lực",
    "draft": "Bản nháp / dự thảo",
    "expired": "Hết hiệu lực",
    "superseded": "Đã bị thay thế",
}
EFFECTIVE_STATUS_ICONS = {
    "active": "✅", "draft": "✏️", "expired": "⛔", "superseded": "♻️",
}


def _effective_badge(status):
    if not status:
        return ""
    icon = EFFECTIVE_STATUS_ICONS.get(status, "")
    return f"{icon} {EFFECTIVE_STATUS_LABELS.get(status, status)}".strip()


def _expiry_note(expiry_date, warn_days):
    """Tra ve (level, msg) canh bao han hieu luc, hoac None."""
    if not expiry_date:
        return None
    from datetime import datetime, date
    s = str(expiry_date)[:10]
    try:
        exp = datetime.strptime(s, "%Y-%m-%d").date()
    except Exception:
        return None
    delta = (exp - date.today()).days
    if delta < 0:
        return ("error", f"⛔ Đã hết hiệu lực từ {s} (quá {abs(delta)} ngày).")
    if delta <= int(warn_days or 0):
        return ("warning", f"⚠️ Sắp hết hiệu lực: còn {delta} ngày (hết hạn {s}).")
    return None


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

    # B5: bo loc co key on dinh -> Streamlit tu nho lua chon qua moi rerun trong phien
    search = st.text_input("Tìm kiếm", placeholder="Tên file, Base Code, mã đối tượng...", key="docs_search")

    fc1, fc2, fc3 = st.columns(3)
    with fc1:
        status_filter = st.selectbox(
            "Trạng thái",
            ["Tất cả", "published", "draft", "rejected", "archived", "superseded"],
            key="docs_status",
        )

    # Danh sach phong ban kha dung cho user (admin thay tat ca)
    if is_admin:
        dept_options = [d["code"] for d in list_known_departments(active_only=True)]
    else:
        dept_options = [d for d in (current_user.get("allowed_departments") or [current_user.get("department")]) if d]
    with fc2:
        dept_filter = st.selectbox("Phòng ban", ["Tất cả"] + sorted(set(dept_options)), key="docs_dept")

    # Danh sach khu/site (admin: tat ca; user: theo allowed_sites neu co)
    if is_admin:
        site_options = [s["code"] for s in list_known_sites(active_only=False)]
    else:
        site_options = [s for s in (current_user.get("allowed_sites") or []) if s]
    with fc3:
        site_filter = st.selectbox("Khu / Site", ["Tất cả"] + sorted(set(site_options)), key="docs_site")

    # P1.1: bo loc theo trang thai hieu luc + han hieu luc (metadata tong quat P0)
    fc4, fc5 = st.columns(2)
    with fc4:
        eff_filter = st.selectbox(
            "Trạng thái hiệu lực",
            ["Tất cả", "active", "draft", "expired", "superseded"],
            format_func=lambda x: EFFECTIVE_STATUS_LABELS.get(x, x),
            key="docs_eff",
        )
    with fc5:
        expiry_filter = st.selectbox(
            "Hiệu lực / Hết hạn",
            ["Tất cả", "Còn hiệu lực", "Sắp hết hạn", "Đã hết hạn"],
            key="docs_expiry",
        )

    # B7: phan trang — reset ve trang 1 khi bo loc thay doi
    filter_signature = (search, status_filter, dept_filter, site_filter, eff_filter, expiry_filter)
    if st.session_state.get("docs_filter_sig") != filter_signature:
        st.session_state["docs_page"] = 1
        st.session_state["docs_filter_sig"] = filter_signature
    page = st.session_state.get("docs_page", 1)

    total = count_documents(current_user, search, status_filter, dept_filter, site_filter, eff_filter, expiry_filter)
    if not total:
        st.info("Không tìm thấy tài liệu.")
        return

    total_pages = max(1, (total + PAGE_SIZE - 1) // PAGE_SIZE)
    page = min(max(1, page), total_pages)
    st.session_state["docs_page"] = page
    offset = (page - 1) * PAGE_SIZE

    docs = load_documents(
        current_user, search, status_filter, dept_filter, site_filter,
        eff_filter=eff_filter, expiry_filter=expiry_filter,
        offset=offset, limit=PAGE_SIZE,
    )

    st.write(f"Tìm thấy **{total}** tài liệu · Trang **{page}/{total_pages}** (mỗi trang {PAGE_SIZE}).")
    for doc in docs:
        render_document_item(doc, current_user)

    _render_pagination(page, total_pages)


def _render_pagination(page, total_pages):
    if total_pages <= 1:
        return
    st.markdown("---")
    pc1, pc2, pc3 = st.columns([1, 2, 1])
    with pc1:
        if st.button("⬅️ Trang trước", disabled=(page <= 1), use_container_width=True, key="docs_prev"):
            st.session_state["docs_page"] = max(1, page - 1)
            st.rerun()
    with pc2:
        st.markdown(
            f"<div style='text-align:center;padding-top:6px'>Trang <b>{page}</b> / {total_pages}</div>",
            unsafe_allow_html=True,
        )
    with pc3:
        if st.button("Trang sau ➡️", disabled=(page >= total_pages), use_container_width=True, key="docs_next"):
            st.session_state["docs_page"] = min(total_pages, page + 1)
            st.rerun()


def _build_document_filters(current_user, search, status_filter, dept_filter, site_filter, eff_filter="Tất cả", expiry_filter="Tất cả"):
    """Dung chung cho count_documents + load_documents: tra ve (where_sql, params)."""
    is_admin = auth.has_role("admin")
    where = " WHERE 1 = 1"
    params = {}
    if status_filter and status_filter != "Tất cả":
        where += " AND t.LifecycleStatus = :status"
        params["status"] = status_filter
    if search:
        where += """
            AND (t.TenFile LIKE :search OR t.BaseCode LIKE :search
                 OR tk.MaDoiTuong LIKE :search OR tk.TenSanPham LIKE :search
                 OR t.Title LIKE :search OR t.Tags LIKE :search OR t.DocNumber LIKE :search)
        """
        params["search"] = f"%{search}%"

    # RBAC phong ban: non-admin chi thay phong duoc phep
    allowed = [d for d in (current_user.get("allowed_departments") or [current_user.get("department")]) if d]
    if not is_admin and allowed:
        keys = []
        for i, dept in enumerate(allowed):
            key = f"dept_{i}"
            params[key] = dept
            keys.append(f":{key}")
        where += f" AND t.ThuMuc IN ({', '.join(keys)})"

    # loc theo phong ban duoc chon
    if dept_filter and dept_filter != "Tất cả":
        where += " AND t.ThuMuc = :dept_pick"
        params["dept_pick"] = dept_filter

    # RBAC site: non-admin gioi han theo allowed_sites (cho phep Site NULL de khong an du lieu cu)
    user_sites = [s for s in (current_user.get("allowed_sites") or []) if s]
    if not is_admin and user_sites:
        keys = []
        for i, s in enumerate(user_sites):
            key = f"usite_{i}"
            params[key] = s
            keys.append(f":{key}")
        where += f" AND (t.Site IS NULL OR t.Site IN ({', '.join(keys)}))"

    # loc theo khu/site duoc chon
    if site_filter and site_filter != "Tất cả":
        where += " AND t.Site = :site_pick"
        params["site_pick"] = site_filter

    # P1.1: loc theo trang thai hieu luc (EffectiveStatus)
    if eff_filter and eff_filter != "Tất cả":
        where += " AND t.EffectiveStatus = :eff"
        params["eff"] = eff_filter

    # P1.1: loc theo han hieu luc (ExpiryDate) — nguong canh bao lay tu AppSettings
    if expiry_filter and expiry_filter != "Tất cả":
        warn_days = get_app_setting_int("expiry_warning_days", 30)
        params["warn_days"] = warn_days
        if expiry_filter == "Đã hết hạn":
            where += " AND t.ExpiryDate IS NOT NULL AND t.ExpiryDate < CAST(GETDATE() AS DATE)"
        elif expiry_filter == "Sắp hết hạn":
            where += (" AND t.ExpiryDate IS NOT NULL"
                      " AND t.ExpiryDate >= CAST(GETDATE() AS DATE)"
                      " AND t.ExpiryDate <= DATEADD(DAY, :warn_days, CAST(GETDATE() AS DATE))")
        elif expiry_filter == "Còn hiệu lực":
            where += (" AND (t.ExpiryDate IS NULL"
                      " OR t.ExpiryDate > DATEADD(DAY, :warn_days, CAST(GETDATE() AS DATE)))")
    return where, params


def count_documents(current_user, search, status_filter, dept_filter="Tất cả", site_filter="Tất cả", eff_filter="Tất cả", expiry_filter="Tất cả"):
    """B7: dem tong so tai lieu khop bo loc (de tinh so trang)."""
    where, params = _build_document_filters(current_user, search, status_filter, dept_filter, site_filter, eff_filter, expiry_filter)
    query = """
        SELECT COUNT(*)
        FROM TaiLieu t
        LEFT JOIN TaiLieuKyThuat tk ON t.DocID = tk.DocID AND tk.TrangSo = 1
    """ + where
    with engine.connect() as conn:
        return conn.execute(text(query), params).scalar() or 0


def load_documents(current_user, search, status_filter, dept_filter="Tất cả", site_filter="Tất cả", eff_filter="Tất cả", expiry_filter="Tất cả", offset=0, limit=PAGE_SIZE):
    where, params = _build_document_filters(current_user, search, status_filter, dept_filter, site_filter, eff_filter, expiry_filter)
    query = """
        SELECT
            t.DocID, t.TenFile, t.ThuMuc, t.BaseCode, t.VersionNo, t.VersionLabel,
            t.VariantCode, t.VariantGroup, t.LifecycleStatus, t.ReviewStatus,
            t.IsCurrent, t.NgayTaiLen, t.Site, t.Domain, t.SecurityLevel,
            tk.MaDoiTuong, tk.LoaiTaiLieu, tk.TenSanPham, t.FilePath,
            t.Title, t.Summary, t.DocNumber, t.EffectiveStatus,
            t.IssuedDate, t.EffectiveDate, t.ExpiryDate, t.Tags
        FROM TaiLieu t
        LEFT JOIN TaiLieuKyThuat tk ON t.DocID = tk.DocID AND tk.TrangSo = 1
    """ + where
    # B7: phan trang bang OFFSET/FETCH (yeu cau ORDER BY)
    query += " ORDER BY t.NgayTaiLen DESC OFFSET :offset ROWS FETCH NEXT :limit ROWS ONLY"
    params["offset"] = int(offset)
    params["limit"] = int(limit)
    with engine.connect() as conn:
        return conn.execute(text(query), params).fetchall()


# --------------------------- C8: tai lai file goc an toan ---------------------------
def _project_root():
    # documents.py: src/mech_chatbot/ui/pages/documents.py -> len 5 cap toi root project
    return os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))))


def _resolve_original_path(file_path):
    """Tra ve (realpath_hop_le|None, message).

    Chong path traversal: chi cho phep file nam trong thu muc data/raw.
    """
    if not file_path:
        return None, "Tài liệu chưa lưu đường dẫn file gốc."
    root = _project_root()
    raw_root = os.path.realpath(os.path.join(root, "data", "raw"))
    candidate = file_path if os.path.isabs(file_path) else os.path.join(root, file_path)
    real = os.path.realpath(candidate)
    try:
        within = os.path.commonpath([real, raw_root]) == raw_root
    except ValueError:
        within = False
    if not within:
        return None, "Đường dẫn file gốc không hợp lệ (ngoài vùng cho phép data/raw)."
    if not os.path.isfile(real):
        return None, "File gốc không còn trên server."
    return real, None


def render_download_original(doc_id, ten_file, file_path, security_level, current_user, key_prefix="docs"):
    """C8: nut tai file goc. Doc bytes lazily (chi khi user yeu cau) de tranh I/O hang loat."""
    real, msg = _resolve_original_path(file_path)
    if real is None:
        st.caption(f"📎 {msg}")
        return
    prep = st.checkbox("📎 Chuẩn bị tải file gốc", key=f"{key_prefix}_prep_{doc_id}")
    if not prep:
        return
    try:
        with open(real, "rb") as f:
            data = f.read()
    except Exception as e:
        st.warning(f"Không đọc được file gốc: {e}")
        return
    clicked = st.download_button(
        "⬇️ Bấm để tải xuống",
        data=data,
        file_name=os.path.basename(real) or (ten_file or "file"),
        key=f"{key_prefix}_dl_{doc_id}",
    )
    if clicked:
        try:
            write_audit_log(
                current_user.get("username"), "download_original", "TaiLieu", doc_id,
                {"file": ten_file, "security_level": security_level},
            )
        except Exception:
            pass


def render_document_item(doc, current_user):
    (doc_id, ten_file, thu_muc, base_code, version_no, version_label, variant_code,
     variant_group, lifecycle_status, review_status, is_current, ngay_tai_len,
     site, domain, security_level,
     ma_doi_tuong, loai_tai_lieu, ten_san_pham, file_path,
     title, summary, doc_number, effective_status,
     issued_date, effective_date, expiry_date, tags) = doc

    current_badge = " · current" if is_current else ""
    sec_badge = f" · 🔒 {security_level}" if security_level else ""
    eff_badge = f" · {_effective_badge(effective_status)}" if effective_status else ""
    # P1.1: uu tien hien Tieu de (de doc) neu co, van giu ten file
    _head = f"{title} ({ten_file})" if title else ten_file
    _warn_days = get_app_setting_int("expiry_warning_days", 30)
    _exp = _expiry_note(expiry_date, _warn_days)
    _exp_head = " · ⛔ Hết hiệu lực" if (_exp and _exp[0] == "error") else (" · ⚠️ Sắp hết hạn" if _exp else "")
    # B4: badge trang thai thong nhat
    with st.expander(f"{_head} · {labels.status_badge(lifecycle_status)}{current_badge}{sec_badge}{eff_badge}{_exp_head}"):
        if _exp:
            (st.error if _exp[0] == "error" else st.warning)(_exp[1])
        if summary:
            st.caption(f"📝 {summary}")
        c1, c2 = st.columns(2)
        with c1:
            st.write(f"**DocID:** {doc_id}")
            st.write(f"**Phòng ban:** {thu_muc}")
            st.write(f"**Khu / Site:** {site or '(chưa gán)'}")
            st.write(f"**Lĩnh vực (domain):** {labels.domain_label(domain)}")
            st.write(f"**Mức mật:** {security_level or '(chưa gán)'}")
            st.write(f"**Base Code:** `{base_code}`")
            st.write(f"**Version:** {version_no} - {version_label}")
            st.write(f"**Variant:** {variant_code}")
            if doc_number:
                st.write(f"**Số văn bản:** {doc_number}")
            if effective_status:
                st.write(f"**Trạng thái hiệu lực:** {_effective_badge(effective_status)}")
        with c2:
            st.write(f"**Review:** {labels.status_badge(review_status)}")
            st.write(f"**Lifecycle:** {labels.status_badge(lifecycle_status)}")
            st.write(f"**Ngày tải:** {ngay_tai_len}")
            if issued_date:
                st.write(f"**Ngày ban hành:** {str(issued_date)[:10]}")
            if effective_date:
                st.write(f"**Ngày hiệu lực:** {str(effective_date)[:10]}")
            if expiry_date:
                st.write(f"**Ngày hết hiệu lực:** {str(expiry_date)[:10]}")
            if tags:
                st.write(f"**Từ khóa:** {tags}")
            # A1: hien thi nhan dong theo domain — truong khong thuoc domain se bi an
            _lbl_ma = labels.field_label(domain, "ma_doi_tuong")
            if _lbl_ma and ma_doi_tuong:
                st.write(f"**{_lbl_ma}:** {ma_doi_tuong}")
            _lbl_ten = labels.field_label(domain, "ten_san_pham")
            if _lbl_ten and ten_san_pham:
                st.write(f"**{_lbl_ten}:** {ten_san_pham}")
            _lbl_loai = labels.field_label(domain, "loai_tai_lieu")
            if _lbl_loai:
                st.write(f"**{_lbl_loai}:** {canonical_label(loai_tai_lieu)}")

        # C8: tai lai file goc tu kho tai lieu
        render_download_original(doc_id, ten_file, file_path, security_level, current_user)

        if auth.has_role("admin"):
            render_admin_actions(doc_id, base_code, version_no, version_label, variant_code, variant_group, loai_tai_lieu, domain, security_level, site, thu_muc, current_user)


def render_admin_actions(doc_id, base_code, version_no, version_label, variant_code, variant_group, loai_tai_lieu, domain, security_level, site, thu_muc, current_user):
    st.markdown("---")
    st.subheader("Quản trị tài liệu")
    _meta = get_document_metadata(doc_id)
    with st.form(f"edit_doc_{doc_id}"):
        # --- Phan loai & quyen truy cap (linh hoat da phong ban) ---
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
        st.markdown("---")
        st.markdown("**Metadata tổng quát (đa phòng ban)**")
        _ed_common = metadata_forms.render_common_metadata(prefix=f"docmeta_{doc_id}", defaults=_meta, show_header=False)
        _ed_attrs = metadata_forms.render_domain_attributes(new_domain, prefix=f"docmeta_{doc_id}", defaults=_meta.get("attributes"))
        submitted = st.form_submit_button("Lưu metadata", type="primary")
    if submitted:
        try:
            update_document_common_metadata(
                doc_id, reviewer=current_user["username"], domain=new_domain,
                attributes=_ed_attrs, **_ed_common,
            )
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
