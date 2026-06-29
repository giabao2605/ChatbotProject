"""P0: Form metadata tong quat DONG theo domain (dung chung cho Upload / Kho tai lieu / Duyet).

Muc tieu da phong ban:
- Moi tai lieu (ke toan, hanh chinh, ISO, HSE, ky thuat...) deu nhap duoc cac
  truong dung chung: Tieu de, Tom tat, Tu khoa, So van ban, cac moc ngay, Nguoi
  ky/owner, Ngon ngu, Trang thai hieu luc.
- Cac truong DAC THU theo domain (vd ke toan: don vi/ky/tong gia tri; hanh chinh:
  co quan ban hanh/pham vi) duoc render rieng va luu vao DocumentAttributes.

Module chi phu thuoc streamlit nen import duoc o moi trang. Cac ham render tra ve
dict gia tri da chuan hoa; KHONG tu ghi DB (trang goi quyet dinh luu the nao).
"""
import re
import streamlit as st

DOMAINS = ["mechanical", "tabular", "generic"]
DOMAIN_LABELS = {
    "mechanical": "Cơ khí / Kỹ thuật",
    "tabular": "Bảng biểu / Tài chính",
    "generic": "Hành chính / Văn bản",
}

LANGUAGES = ["", "vi", "en", "vi+en", "other"]
EFFECTIVE_STATUSES = ["active", "draft", "expired", "superseded"]
EFFECTIVE_STATUS_LABELS = {
    "active": "Đang hiệu lực",
    "draft": "Bản nháp / dự thảo",
    "expired": "Hết hiệu lực",
    "superseded": "Đã bị thay thế",
}

# Truong dac thu theo domain -> luu vao DocumentAttributes (key = AttributeKey).
DOMAIN_ATTR_FIELDS = {
    "tabular": [
        ("don_vi", "Đơn vị tính / tiền tệ", "text"),
        ("ky_chung_tu", "Kỳ / loại chứng từ", "text"),
        ("tong_gia_tri", "Tổng giá trị", "text"),
        ("doi_tac", "Đối tác / Nhà cung cấp", "text"),
    ],
    "generic": [
        ("co_quan_ban_hanh", "Cơ quan / Phòng ban ban hành", "text"),
        ("pham_vi_ap_dung", "Phạm vi áp dụng", "text"),
        ("linh_vuc", "Lĩnh vực / chủ đề", "text"),
    ],
    "mechanical": [],  # da co bang TaiLieuKyThuat / BangKeVatTu rieng
}

_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")


def normalize_domain(domain):
    d = (domain or "generic").strip().lower()
    return d if d in DOMAINS else "generic"


def _s(v):
    """strip -> chuoi rong giu nguyen '' (de phan biet xoa khi edit)."""
    if v is None:
        return ""
    return str(v).strip()


def _date_value(label, value, key):
    """O nhap ngay dang text (YYYY-MM-DD) de cho phep BO TRONG. Tra ve chuoi."""
    raw = st.text_input(label, value=_fmt_date(value), key=key, placeholder="YYYY-MM-DD")
    raw = _s(raw)
    if raw and not _DATE_RE.match(raw):
        st.caption(f"⚠️ `{label}` nên theo định dạng YYYY-MM-DD (vd 2026-06-29). Giá trị hiện tại sẽ không được lưu nếu sai định dạng.")
    return raw


def _fmt_date(value):
    if value is None:
        return ""
    s = str(value)
    # date/datetime -> lay phan YYYY-MM-DD
    return s[:10] if _DATE_RE.match(s[:10]) else s


def render_common_metadata(prefix, defaults=None, show_header=True):
    """Render cac truong metadata DUNG CHUNG. Tra ve dict theo key cua repository
    (_COMMON_META_COLS): title, summary, tags, doc_number, issued_date,
    effective_date, expiry_date, review_date, owner_signer, language, effective_status.
    Gia tri ngay sai dinh dang se bi loai (set '').
    """
    d = defaults or {}
    if show_header:
        st.markdown("**Thông tin tài liệu (dùng chung)**")
    title = st.text_input("Tiêu đề tài liệu", value=_s(d.get("title")), key=f"{prefix}_title",
                          help="Tên gọi dễ đọc của tài liệu (khác với tên file).")
    summary = st.text_area("Tóm tắt nội dung", value=_s(d.get("summary")), key=f"{prefix}_summary",
                           help="Vài câu mô tả để người dùng & chatbot hiểu nhanh tài liệu nói về gì.")
    tags = st.text_input("Từ khóa (phân tách bằng dấu phẩy)", value=_s(d.get("tags")), key=f"{prefix}_tags",
                         help="VD: an toàn, 5S, bảo trì máy CNC")
    c1, c2 = st.columns(2)
    with c1:
        doc_number = st.text_input("Số văn bản / chứng từ", value=_s(d.get("doc_number")), key=f"{prefix}_doc_number")
        issued_date = _date_value("Ngày ban hành", d.get("issued_date"), key=f"{prefix}_issued_date")
        effective_date = _date_value("Ngày hiệu lực", d.get("effective_date"), key=f"{prefix}_effective_date")
    with c2:
        owner_signer = st.text_input("Người ký / phụ trách", value=_s(d.get("owner_signer")), key=f"{prefix}_owner_signer")
        expiry_date = _date_value("Ngày hết hiệu lực", d.get("expiry_date"), key=f"{prefix}_expiry_date")
        review_date = _date_value("Ngày soát xét kế tiếp", d.get("review_date"), key=f"{prefix}_review_date")
    c3, c4 = st.columns(2)
    with c3:
        _lang = _s(d.get("language"))
        language = st.selectbox("Ngôn ngữ", LANGUAGES,
                                index=LANGUAGES.index(_lang) if _lang in LANGUAGES else 0,
                                format_func=lambda x: x or "(không rõ)", key=f"{prefix}_language")
    with c4:
        _st = _s(d.get("effective_status")) or "active"
        effective_status = st.selectbox("Trạng thái hiệu lực", EFFECTIVE_STATUSES,
                                        index=EFFECTIVE_STATUSES.index(_st) if _st in EFFECTIVE_STATUSES else 0,
                                        format_func=lambda x: EFFECTIVE_STATUS_LABELS.get(x, x),
                                        key=f"{prefix}_effective_status")

    def _vd(x):  # validate date: sai dinh dang -> '' (khong luu)
        return x if (x == "" or _DATE_RE.match(x)) else ""

    return {
        "title": title, "summary": summary, "tags": tags, "doc_number": doc_number,
        "issued_date": _vd(issued_date), "effective_date": _vd(effective_date),
        "expiry_date": _vd(expiry_date), "review_date": _vd(review_date),
        "owner_signer": owner_signer, "language": language, "effective_status": effective_status,
    }


def render_domain_attributes(domain, prefix, defaults=None, show_header=True):
    """Render cac truong DAC THU theo domain. Tra ve dict {AttributeKey: value}."""
    dom = normalize_domain(domain)
    fields = DOMAIN_ATTR_FIELDS.get(dom, [])
    if not fields:
        return {}
    d = (defaults or {})
    if show_header:
        st.markdown(f"**Trường riêng cho lĩnh vực: {DOMAIN_LABELS.get(dom, dom)}**")
    out = {}
    cols = st.columns(2)
    for i, (key, label, _typ) in enumerate(fields):
        with cols[i % 2]:
            out[key] = st.text_input(label, value=_s(d.get(key)), key=f"{prefix}_attr_{key}")
    return out


def render_metadata_section(domain, prefix, common_defaults=None, attr_defaults=None):
    """Render ca common + domain attrs. Tra ve (common_dict, attrs_dict)."""
    common = render_common_metadata(prefix, defaults=common_defaults)
    attrs = render_domain_attributes(domain, prefix, defaults=attr_defaults)
    return common, attrs


def compact(d):
    """Bo cac gia tri rong/None (dung khi nhap luc upload, chi giu field co data)."""
    out = {}
    for k, v in (d or {}).items():
        if v is None:
            continue
        if isinstance(v, str) and v.strip() == "":
            continue
        out[k] = v.strip() if isinstance(v, str) else v
    return out


def build_upload_meta(common, attrs):
    """Gop common + attrs thanh dict luu IngestionJobs.UploadMetaJson (chi field co data)."""
    meta = compact(common)
    a = compact(attrs)
    if a:
        meta["attributes"] = a
    return meta
