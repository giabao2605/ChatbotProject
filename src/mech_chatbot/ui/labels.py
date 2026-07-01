"""Nhan hien thi dong theo domain + badge trang thai dung chung cho toan UI.

Muc tieu (A1 + B4):
- A1: Nhan truong (field label) thay doi theo linh vuc (domain) cua tai lieu.
- B4: status_badge() tra ve icon + nhan thong nhat, dung markdown.

Dung cho moi domain / moi loai file. Khong phu thuoc DB nen luon import duoc.
"""

from mech_chatbot.ui.i18n import t, get_lang

DOMAIN_LABELS = {
    "mechanical": "Co khi / Ky thuat",
    "tabular": "Bang bieu / Tai chinh",
    "generic": "Hanh chinh / Van ban",
}

# ----------------------------- Departments (song ngu) -----------------------
# Nhan hien thi song ngu cho ma phong ban (DeptCode).
# QUAN TRONG: gia tri luu trong DB / dung cho RBAC / query VAN LA MA GOC.
# Day chi la lop HIEN THI. Dinh dang: "Ten (MA)".
# Phong ban chua khai bao o day -> fallback ve chinh ma goc.
DEPARTMENT_LABELS = {
    "Accountant": {"vi": "Kế toán", "en": "Accounting"},
    "HR": {"vi": "Nhân sự", "en": "Human Resources"},
    "HSE_5S": {"vi": "HSE & 5S", "en": "HSE & 5S"},
    "ISO": {"vi": "ISO", "en": "ISO"},
    "IT": {"vi": "Công nghệ thông tin", "en": "Information Technology"},
    "Maintenance": {"vi": "Bảo trì", "en": "Maintenance"},
    "Molding": {"vi": "Khuôn đúc", "en": "Molding"},
    "Planning": {"vi": "Kế hoạch", "en": "Planning"},
    "Production": {"vi": "Sản xuất", "en": "Production"},
    "Purchasing": {"vi": "Mua hàng", "en": "Purchasing"},
    "QualityControl": {"vi": "Quản lý chất lượng", "en": "Quality Control"},
    "Sales": {"vi": "Kinh doanh", "en": "Sales"},
    "Technical": {"vi": "Kỹ thuật", "en": "Technical"},
    "Warehouse": {"vi": "Kho", "en": "Warehouse"},
    "CHUNG": {"vi": "Dùng chung (mọi phòng ban)", "en": "Shared (all departments)"},
}

_DEPT_DISPLAY_SUFFIXES = (" (disabled)", " (archived)")


def dept_label(code):
    """Nhan hien thi song ngu cho 1 ma phong ban: 'Ten (MA)'.

    - Gia tri goc (code) KHONG doi; chi doi cach hien thi.
    - Giu hau to ' (disabled)' / ' (archived)' neu admin dropdown them vao.
    - Ten trung ma (vd 'ISO') -> chi hien 1 lan, tranh 'ISO (ISO)'.
    - Phong chua khai bao -> fallback ve ma goc.
    """
    if code is None:
        return ""
    code_str = str(code).strip()
    if not code_str:
        return code_str
    suffix = ""
    for _sfx in _DEPT_DISPLAY_SUFFIXES:
        if code_str.endswith(_sfx):
            suffix = _sfx
            code_str = code_str[: -len(_sfx)].strip()
            break
    entry = DEPARTMENT_LABELS.get(code_str)
    if not entry:
        return code_str + suffix
    name = entry.get(get_lang()) or entry.get("en") or code_str
    if name.strip().lower() == code_str.lower():
        return name + suffix
    return f"{name} ({code_str}){suffix}"


def dept_labels_str(codes, sep=", ", empty=""):
    """Noi nhieu ma phong ban thanh chuoi hien thi song ngu."""
    if not codes:
        return empty
    if isinstance(codes, str):
        codes = [codes]
    parts = [dept_label(c) for c in codes if c not in (None, "")]
    return sep.join(parts) if parts else empty


# ----------------------------- Glossary (thuat ngu) -------------------------
# Giu nguyen thuat ngu goc + kem nghia (theo lua chon nguoi dung).
# Ap dung cho cac NHAN dung mot minh (vi du selectbox "Domain").
GLOSSARY = {
    "Domain": {"vi": "Domain (lĩnh vực)", "en": "Domain"},
    "RAG": {"vi": "RAG (truy hồi tăng cường)", "en": "RAG"},
    "Qdrant": {"vi": "Qdrant (cơ sở dữ liệu vector)", "en": "Qdrant"},
    "metadata": {"vi": "metadata (siêu dữ liệu)", "en": "metadata"},
    "variant": {"vi": "variant (biến thể)", "en": "variant"},
    "payload": {"vi": "payload (dữ liệu đính kèm)", "en": "payload"},
    "embedding": {"vi": "embedding (vector nhúng)", "en": "embedding"},
    "worker": {"vi": "worker (tiến trình xử lý nền)", "en": "worker"},
}


def gloss(term):
    """Tra ve thuat ngu kem nghia theo ngon ngu hien tai."""
    entry = GLOSSARY.get(term)
    if not entry:
        return term
    return entry.get(get_lang()) or entry.get("en") or term

# Nhan truong theo domain.
DOMAIN_FIELD_LABELS = {
    "mechanical": {
        "ma_doi_tuong": "M\u00e3 \u0111\u1ed1i t\u01b0\u1ee3ng",
        "ten_san_pham": "T\u00ean s\u1ea3n ph\u1ea9m",
        "vat_lieu": "V\u1eadt li\u1ec7u",
        "dung_sai": "Dung sai",
        "kich_thuoc": "K\u00edch th\u01b0\u1edbc t\u1ed5ng th\u1ec3",
        "loai_tai_lieu": "Lo\u1ea1i t\u00e0i li\u1ec7u",
    },
    "tabular": {
        "ten_san_pham": "Ti\u00eau \u0111\u1ec1 ch\u1ee9ng t\u1eeb",
        "loai_tai_lieu": "K\u1ef3 / lo\u1ea1i ch\u1ee9ng t\u1eeb",
        "don_vi": "\u0110\u01a1n v\u1ecb",
        "tong_gia_tri": "T\u1ed5ng gi\u00e1 tr\u1ecb",
    },
    "generic": {
        "ten_san_pham": "Ti\u00eau \u0111\u1ec1 t\u00e0i li\u1ec7u",
        "loai_tai_lieu": "Lo\u1ea1i v\u0103n b\u1ea3n",
        "so_van_ban": "S\u1ed1 v\u0103n b\u1ea3n",
        "ngay_ban_hanh": "Ng\u00e0y ban h\u00e0nh",
        "nguoi_ky": "Ng\u01b0\u1eddi k\u00fd",
    },
}

_DEFAULT_FIELD_LABELS = {
    "ten_san_pham": "Ti\u00eau \u0111\u1ec1 t\u00e0i li\u1ec7u",
    "loai_tai_lieu": "Lo\u1ea1i t\u00e0i li\u1ec7u",
}


def normalize_domain(domain):
    """Tra ve domain hop le, mac dinh 'generic'."""
    d = (domain or "generic").strip().lower()
    return d if d in DOMAIN_FIELD_LABELS else "generic"


def domain_label(domain):
    """Nhan hien thi cho domain."""
    _raw = DOMAIN_LABELS.get(normalize_domain(domain), domain or "(ch\u01b0a g\u00e1n)")
    return t(_raw)


def field_label(domain, field_key, default=None):
    """Nhan hien thi cua mot truong theo domain.

    - Tra ve chuoi nhan (da dich) neu truong THUOC domain.
    - Tra ve `default` (mac dinh None) neu KHONG thuoc domain.
    """
    dom = normalize_domain(domain)
    label = DOMAIN_FIELD_LABELS.get(dom, {}).get(field_key)
    if label is not None:
        return t(label)
    return default


def is_field_visible(domain, field_key):
    """True neu truong nen hien thi cho domain nay."""
    return field_label(domain, field_key) is not None


# ----------------------------- B4: Status badges -----------------------------
STATUS_BADGES = {
    # Review / lifecycle
    "pending_review": ("\U0001f7e1", "Ch\u1edd duy\u1ec7t"),
    "approved": ("\u2705", "\u0110\u00e3 duy\u1ec7t"),
    "published": ("\U0001f7e2", "\u0110\u00e3 xu\u1ea5t b\u1ea3n"),
    "draft": ("\U0001f4dd", "B\u1ea3n nh\u00e1p"),
    "rejected": ("\u274c", "T\u1eeb ch\u1ed1i"),
    "archived": ("\U0001f4e6", "L\u01b0u tr\u1eef"),
    "superseded": ("\U0001f501", "\u0110\u00e3 thay th\u1ebf"),
    # Job / ingest pipeline
    "pending": ("\u23f3", "\u0110ang ch\u1edd"),
    "pending_retry": ("\U0001f504", "Ch\u1edd th\u1eed l\u1ea1i"),
    "classifying": ("\U0001f50d", "\u0110ang ph\u00e2n lo\u1ea1i"),
    "extracting": ("\u2699\ufe0f", "\u0110ang b\u00f3c t\u00e1ch"),
    "embedding": ("\U0001f9ee", "\u0110ang t\u1ea1o vector"),
    "publishing": ("\U0001f4e4", "\u0110ang xu\u1ea5t b\u1ea3n"),
    "failed": ("\U0001f534", "L\u1ed7i"),
    "waiting_quota": ("\u23f8\ufe0f", "Ch\u1edd quota"),
    "canceled": ("\U0001f6b7", "\u0110\u00e3 h\u1ee7y"),
    # Quality gate
    "blocked": ("\U0001f6ab", "B\u1ecb ch\u1eb7n (ch\u1ea5t l\u01b0\u1ee3ng)"),
    "passed": ("\u2705", "\u0110\u1ea1t"),
    "warning": ("\u26a0\ufe0f", "C\u1ea3nh b\u00e1o"),
}


def status_badge(status, fallback_icon="\u2022"):
    """Tra ve chuoi markdown 'icon Nhan' thong nhat cho mot trang thai."""
    if not status:
        return f"{fallback_icon} {t('(kh\u00f4ng r\u00f5)')}"
    icon, label = STATUS_BADGES.get(str(status).strip().lower(), (fallback_icon, str(status)))
    return f"{icon} {t(label)}"


def status_icon(status):
    """Chi tra ve icon cho trang thai."""
    if not status:
        return "\u2022"
    return STATUS_BADGES.get(str(status).strip().lower(), ("\u2022", ""))[0]
