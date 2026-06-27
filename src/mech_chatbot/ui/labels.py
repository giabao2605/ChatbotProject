"""Nhan hien thi dong theo domain + badge trang thai dung chung cho toan UI.

Muc tieu (A1 + B4):
- A1: Nhan truong (field label) thay doi theo linh vuc (domain) cua tai lieu.
  Truong nao khong thuoc domain -> tra ve None de trang ben ngoai AN truong do,
  tranh hien khung co khi trong cho tai lieu ke toan / hanh chinh.
- B4: status_badge() tra ve icon + nhan tieng Viet thong nhat (mau qua emoji),
  dung markdown thay cho cac <span style=...> thu cong moi trang mot kieu.

Dung cho moi domain / moi loai file. Khong phu thuoc DB nen luon import duoc.
"""

DOMAIN_LABELS = {
    "mechanical": "Co khi / Ky thuat",
    "tabular": "Bang bieu / Tai chinh",
    "generic": "Hanh chinh / Van ban",
}

# Nhan truong theo domain. Key = ten truong logic (giu nguyen ten bien code),
# value = nhan hien thi cho nguoi dung. Neu mot truong KHONG co trong map cua
# domain -> field_label() tra ve None -> trang goi se AN truong do.
DOMAIN_FIELD_LABELS = {
    "mechanical": {
        "ma_doi_tuong": "Mã đối tượng",
        "ten_san_pham": "Tên sản phẩm",
        "vat_lieu": "Vật liệu",
        "dung_sai": "Dung sai",
        "kich_thuoc": "Kích thước tổng thể",
        "loai_tai_lieu": "Loại tài liệu",
    },
    "tabular": {
        "ten_san_pham": "Tiêu đề chứng từ",
        "loai_tai_lieu": "Kỳ / loại chứng từ",
        "don_vi": "Đơn vị",
        "tong_gia_tri": "Tổng giá trị",
    },
    "generic": {
        "ten_san_pham": "Tiêu đề tài liệu",
        "loai_tai_lieu": "Loại văn bản",
        "so_van_ban": "Số văn bản",
        "ngay_ban_hanh": "Ngày ban hành",
        "nguoi_ky": "Người ký",
    },
}

# Nhan mac dinh (fallback) khi domain khong khai bao truong nhung van muon hien.
_DEFAULT_FIELD_LABELS = {
    "ten_san_pham": "Tiêu đề tài liệu",
    "loai_tai_lieu": "Loại tài liệu",
}


def normalize_domain(domain):
    """Tra ve domain hop le, mac dinh 'generic'."""
    d = (domain or "generic").strip().lower()
    return d if d in DOMAIN_FIELD_LABELS else "generic"


def domain_label(domain):
    """Nhan hien thi cho domain (vd 'Co khi / Ky thuat')."""
    return DOMAIN_LABELS.get(normalize_domain(domain), domain or "(chưa gán)")


def field_label(domain, field_key, default=None):
    """Nhan hien thi cua mot truong theo domain.

    - Tra ve chuoi nhan neu truong THUOC domain.
    - Tra ve `default` (mac dinh None) neu truong KHONG thuoc domain ->
      trang goi nen AN truong nay (vd: vat_lieu / dung_sai voi tai lieu generic).
    """
    dom = normalize_domain(domain)
    label = DOMAIN_FIELD_LABELS.get(dom, {}).get(field_key)
    if label is not None:
        return label
    return default


def is_field_visible(domain, field_key):
    """True neu truong nen hien thi cho domain nay."""
    return field_label(domain, field_key) is not None


# ----------------------------- B4: Status badges -----------------------------
# icon (emoji mang mau) + nhan tieng Viet thong nhat cho toan he thong.
STATUS_BADGES = {
    # Review / lifecycle
    "pending_review": ("🟡", "Chờ duyệt"),
    "approved": ("✅", "Đã duyệt"),
    "published": ("🟢", "Đã xuất bản"),
    "draft": ("📝", "Bản nháp"),
    "rejected": ("❌", "Từ chối"),
    "archived": ("📦", "Lưu trữ"),
    "superseded": ("🔁", "Đã thay thế"),
    # Job / ingest pipeline
    "pending": ("⏳", "Đang chờ"),
    "pending_retry": ("🔄", "Chờ thử lại"),
    "classifying": ("🔍", "Đang phân loại"),
    "extracting": ("⚙️", "Đang bóc tách"),
    "embedding": ("🧮", "Đang tạo vector"),
    "publishing": ("📤", "Đang xuất bản"),
    "failed": ("🔴", "Lỗi"),
    "waiting_quota": ("⏸️", "Chờ quota"),
    "canceled": ("🚷", "Đã hủy"),
    # Quality gate
    "blocked": ("🚫", "Bị chặn (chất lượng)"),
    "passed": ("✅", "Đạt"),
    "warning": ("⚠️", "Cảnh báo"),
}


def status_badge(status, fallback_icon="•"):
    """Tra ve chuoi markdown 'icon Nhan' thong nhat cho mot trang thai.

    Dung duoc voi st.markdown / st.write / chuoi noi. Khong dung HTML span.
    Neu trang thai chua khai bao -> hien icon mac dinh + chinh chuoi status.
    """
    if not status:
        return f"{fallback_icon} (không rõ)"
    icon, label = STATUS_BADGES.get(str(status).strip().lower(), (fallback_icon, str(status)))
    return f"{icon} {label}"


def status_icon(status):
    """Chi tra ve icon cho trang thai (de ghep vao tieu de expander...)."""
    if not status:
        return "•"
    return STATUS_BADGES.get(str(status).strip().lower(), ("•", ""))[0]
