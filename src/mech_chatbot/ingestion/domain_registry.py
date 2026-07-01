"""
Domain Registry — cau hinh trung tam cho pipeline da phong ban (GD2: data-driven).

"Domain" o day = KIEU DOC tai lieu (reading strategy), gom 3 loai:
  - mechanical : ban ve / BOM co khi      -> mechanical_extractors
  - tabular    : tai lieu bang bieu        (ke toan, mua hang, kho, sales)
  - generic    : tai lieu hanh chinh/van ban (HR, ISO, QC, ke hoach, HSE, IT)

Phong ban -> domain & muc mat duoc tra cuu DONG tu bang dbo.Departments
(DeptCode / FolderGoc -> Domain, DefaultSecurity). Neu DB chua san sang thi
fallback ve map tinh ben duoi, roi ve 'generic' / 'internal'. KHONG raise loi:
moi tra cuu deu co fallback an toan de pipeline ingest khong vo.
"""

from dataclasses import dataclass

from mech_chatbot.config.constants import SHARE_ALL_DEPARTMENT


@dataclass
class DomainConfig:
    key: str            # 'mechanical' | 'tabular' | 'generic'
    label: str
    extractor: str      # 'mechanical' | 'tabular' | 'generic'
    quality_fn: str     # 'quality_mechanical' | 'quality_generic'
    default_security: str = 'internal'


# 3 kieu doc (reading strategy) — nguon cho extractor + quality_fn.
DOMAINS = {
    'mechanical': DomainConfig(
        'mechanical', 'Co khi / Ky thuat',
        extractor='mechanical', quality_fn='quality_mechanical',
        default_security='internal',
    ),
    'tabular': DomainConfig(
        'tabular', 'Bang bieu / Tai chinh',
        extractor='tabular', quality_fn='quality_generic',
        default_security='internal',
    ),
    'generic': DomainConfig(
        'generic', 'Hanh chinh / Van ban',
        extractor='generic', quality_fn='quality_generic',
        default_security='internal',
    ),
}

DEFAULT_DOMAIN = 'generic'

# Map TINH (fallback khi DB chua san sang). Gom ca DeptCode moi lan ten thu muc cu.
_FALLBACK_DOMAIN = {
    # --- DeptCode moi (data-driven Departments) ---
    'Technical': 'mechanical', 'Production': 'mechanical',
    'Maintenance': 'mechanical', 'Molding': 'mechanical',
    'Accountant': 'tabular', 'Purchasing': 'tabular',
    'Warehouse': 'tabular', 'Sales': 'tabular',
    'HR': 'generic', 'Planning': 'generic', 'QualityControl': 'generic',
    'ISO': 'generic', 'HSE_5S': 'generic', 'IT': 'generic',
    # --- ten thu muc CU (du lieu lich su) ---
    'Ky_Thuat': 'mechanical',
    'To_Han': 'mechanical', 'To_Dap': 'mechanical', 'To_Son': 'mechanical',
    'To_Nham': 'mechanical', 'To_Phoi': 'mechanical', 'To_Tien_Phay': 'mechanical',
    'To_Dong_Goi': 'mechanical', 'To_Ban_Le': 'mechanical',
    'Bang_Ke': 'mechanical', 'Gia_Cong_Ngoai': 'mechanical',
    'Ke_Toan': 'tabular',
    'Nhan_Su': 'generic', SHARE_ALL_DEPARTMENT: 'generic', 'Tu_Hoc': 'generic',
}

# Muc mat fallback theo ten phong/thu muc (khi DB chua co cot DefaultSecurity).
_FALLBACK_SECURITY = {
    'Accountant': 'confidential', 'Ke_Toan': 'confidential',
    'HR': 'confidential', 'Nhan_Su': 'confidential',
}


def _normalize_domain_value(value):
    """Chuan hoa gia tri domain (gom ca key cu) ve mechanical|tabular|generic."""
    if not value:
        return None
    v = str(value).strip().lower()
    if v in ('co_khi', 'ky_thuat', 'mechanical'):
        return 'mechanical'
    if v in ('ke_toan', 'tabular'):
        return 'tabular'
    if v in ('nhan_su', 'chung', 'generic'):
        return 'generic'
    return v if v in DOMAINS else None


def _lookup_department(thu_muc):
    """Tra cuu (domain, default_security) tu bang Departments.
    Tra ve None neu khong tim thay hoac DB loi (de caller fallback).
    """
    if not thu_muc:
        return None
    try:
        from mech_chatbot.db.repository import engine
        from sqlalchemy import text
        with engine.connect() as conn:
            try:
                # Khop theo DeptCode (uu tien) hoac FolderGoc (ten thu muc goc).
                row = conn.execute(
                    text("SELECT Domain, DefaultSecurity FROM dbo.Departments "
                         "WHERE DeptCode = :t OR FolderGoc = :t"),
                    {"t": thu_muc},
                ).fetchone()
            except Exception:
                # DB cu chua co cot DefaultSecurity/FolderGoc -> thu ban toi gian.
                row = conn.execute(
                    text("SELECT Domain, NULL FROM dbo.Departments WHERE DeptCode = :t"),
                    {"t": thu_muc},
                ).fetchone()
        if row:
            domain = _normalize_domain_value(row[0])
            security = str(row[1]).strip().lower() if row[1] else None
            return (domain, security)
    except Exception:
        return None
    return None


def resolve_domain_by_department(thu_muc):
    """Phong ban / thu muc -> domain (mechanical|tabular|generic). Mac dinh 'generic'."""
    found = _lookup_department(thu_muc)
    if found and found[0]:
        return found[0]
    if thu_muc and thu_muc in _FALLBACK_DOMAIN:
        return _FALLBACK_DOMAIN[thu_muc]
    return DEFAULT_DOMAIN


def resolve_security_by_department(thu_muc):
    """Phong ban / thu muc -> muc mat mac dinh.
    Uu tien Departments.DefaultSecurity, roi map tinh, roi mac dinh theo domain.
    """
    found = _lookup_department(thu_muc)
    if found and found[1]:
        return found[1]
    if thu_muc and thu_muc in _FALLBACK_SECURITY:
        return _FALLBACK_SECURITY[thu_muc]
    return get_default_security(resolve_domain_by_department(thu_muc))


def get_default_security(domain):
    """Muc mat mac dinh theo domain (backward compat / fallback)."""
    cfg = DOMAINS.get(_normalize_domain_value(domain) or DEFAULT_DOMAIN, DOMAINS[DEFAULT_DOMAIN])
    return cfg.default_security


def get_domain_config(domain):
    """Cau hinh cua domain (extractor, quality_fn...). Fallback 'generic'."""
    return DOMAINS.get(_normalize_domain_value(domain) or DEFAULT_DOMAIN, DOMAINS[DEFAULT_DOMAIN])
