"""Nhan hien thi dong theo domain + badge trang thai dung chung cho toan UI.

Muc tieu (A1 + B4):
- A1: Nhan truong (field label) thay doi theo linh vuc (domain) cua tai lieu.
- B4: status_badge() tra ve icon + nhan thong nhat, dung markdown.

Dung cho moi domain / moi loai file. Khong phu thuoc DB nen luon import duoc.
"""

from mech_chatbot.ui.i18n import t

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
