"""Nhan hien thi dong theo domain + badge trang thai dung chung cho toan UI.

Muc tieu (A1 + B4):
- A1: Nhan truong (field label) thay doi theo linh vuc (domain) cua tai lieu.
- B4: status_badge() tra ve nhan thong nhat, dung markdown.

Dung cho moi domain / moi loai file. Khong phu thuoc DB nen luon import duoc.
"""

from mech_chatbot.ui.i18n import t, get_lang

DOMAIN_LABELS = {
    "mechanical": "Co khi / Ky thuat",
    "tabular": "Bang bieu / Tai chinh",
    "generic": "Hanh chinh / Van ban",
}

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
    "pending_review": ("", "Ch\u1edd duy\u1ec7t"),
    "approved": ("", "\u0110\u00e3 duy\u1ec7t"),
    "published": ("", "\u0110\u00e3 xu\u1ea5t b\u1ea3n"),
    "draft": ("", "B\u1ea3n nh\u00e1p"),
    "rejected": ("", "T\u1eeb ch\u1ed1i"),
    "archived": ("", "L\u01b0u tr\u1eef"),
    "superseded": ("", "\u0110\u00e3 thay th\u1ebf"),
    # Job / ingest pipeline
    "pending": ("", "\u0110ang ch\u1edd"),
    "pending_retry": ("", "Ch\u1edd th\u1eed l\u1ea1i"),
    "classifying": ("", "\u0110ang ph\u00e2n lo\u1ea1i"),
    "extracting": ("", "\u0110ang b\u00f3c t\u00e1ch"),
    "embedding": ("", "\u0110ang t\u1ea1o vector"),
    "publishing": ("", "\u0110ang xu\u1ea5t b\u1ea3n"),
    "failed": ("", "L\u1ed7i"),
    "waiting_quota": ("", "Ch\u1edd quota"),
    "canceled": ("", "\u0110\u00e3 h\u1ee7y"),
    # Quality gate
    "blocked": ("", "B\u1ecb ch\u1eb7n (ch\u1ea5t l\u01b0\u1ee3ng)"),
    "passed": ("", "\u0110\u1ea1t"),
    "warning": ("", "C\u1ea3nh b\u00e1o"),
}


def status_badge(status, fallback_label=""):
    """Tra ve nhan trang thai thong nhat."""
    if not status:
        return t("(không rõ)")
    _, label = STATUS_BADGES.get(str(status).strip().lower(), (fallback_label, str(status)))
    return t(label)


# ---------------------------------------------------------------------------
# Nhan song ngu cho PHONG BAN + thuat ngu chuyen nganh (i18n nhat quan)
# ---------------------------------------------------------------------------
DEPARTMENT_LABELS = {
    "Accountant": {"vi": "Kế toán", "en": "Accounting"},
    "CHUNG": {"vi": "Dùng chung (mọi phòng ban)", "en": "Shared (all departments)"},
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
}

# Hau to trang thai admin co the gan vao ma phong ban khi hien thi.
_DEPT_DISPLAY_SUFFIXES = {
    " (disabled)": {"vi": "tạm tắt", "en": "disabled"},
    " (archived)": {"vi": "lưu trữ", "en": "archived"},
}


def dept_label(code):
    """Hien thi phong ban dang 'MA - Nghia'. Giu MA goc, fallback ve MA neu la.

    - VI: 'HR - Nhân sự'  | EN: 'HR - Human Resources'
    - Ten trung ma -> chi hien mot lan (vd ISO).
    - Giu hau to ' (disabled)' / ' (archived)'.
    - code rong/None -> ''.
    """
    if not code:
        return ""
    raw = str(code)
    core = raw
    suffix = ""
    for suf, suffix_names in _DEPT_DISPLAY_SUFFIXES.items():
        if core.endswith(suf):
            lang = get_lang()
            suffix = f" ({suffix_names.get(lang) or suffix_names.get('vi') or suf.strip()})"
            core = core[: -len(suf)]
            break
    names = DEPARTMENT_LABELS.get(core)
    if names:
        name = names.get(get_lang()) or names.get("vi") or core
    else:
        name = core
    if name == core:
        return f"{core}{suffix}"
    return f"{core} - {name}{suffix}"


def dept_labels_str(codes, sep=", "):
    """Noi nhieu ma phong ban thanh chuoi hien thi song ngu."""
    if not codes:
        return ""
    return sep.join(dept_label(c) for c in codes if c)


# Thuat ngu chuyen nganh: giu tu goc, them nghia theo ngon ngu dang chon.
GLOSSARY = {
    "Domain": {"vi": "lĩnh vực", "en": "domain"},
    "RAG": {"vi": "truy hồi tăng cường", "en": "retrieval augmented generation"},
    "Qdrant": {"vi": "cơ sở dữ liệu vector", "en": "vector database"},
    "metadata": {"vi": "siêu dữ liệu", "en": "metadata"},
    "variant": {"vi": "biến thể", "en": "variant"},
    "payload": {"vi": "dữ liệu đính kèm", "en": "payload data"},
    "embedding": {"vi": "vector ngữ nghĩa", "en": "semantic vector"},
    "worker": {"vi": "tiến trình xử lý nền", "en": "background worker"},
}


def gloss(term):
    """Giu nguyen thuat ngu va them nghia theo ngon ngu hien tai."""
    if not term:
        return ""
    meanings = GLOSSARY.get(term)
    if meanings:
        meaning = meanings.get(get_lang()) or meanings.get("vi")
        if meaning and meaning.strip().lower() != str(term).strip().lower():
            return f"{term} - {meaning}"
    return term
