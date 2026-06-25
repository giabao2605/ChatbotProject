"""
Domain Registry — cau hinh trung tam cho pipeline da phong ban.

Moi domain (co_khi, ke_toan, nhan_su, ...) co:
  - departments: danh sach thu muc/phong ban thuoc domain nay
  - extractor: ten bo trich xuat ('mechanical' | 'tabular' | 'generic')
  - quality_fn: ten ham tinh diem chat luong
  - default_security: muc mat mac dinh khi ingest
"""

from dataclasses import dataclass, field
from typing import List


@dataclass
class DomainConfig:
    key: str                       # 'co_khi'
    label: str                     # 'Cơ khí'
    departments: List[str]         # cac thu muc/phong thuoc domain nay
    extractor: str                 # 'mechanical' | 'tabular' | 'generic'
    quality_fn: str                # 'quality_mechanical' | 'quality_generic'
    default_security: str = 'internal'


DOMAINS = {
    'co_khi': DomainConfig(
        'co_khi', 'Cơ khí',
        ['To_Han', 'To_Dap', 'To_Son', 'To_Nham', 'To_Phoi', 'To_Tien_Phay',
         'To_Dong_Goi', 'To_Ban_Le', 'Bang_Ke', 'Gia_Cong_Ngoai'],
        extractor='mechanical',
        quality_fn='quality_mechanical',
    ),
    'ky_thuat': DomainConfig(
        'ky_thuat', 'Kỹ thuật',
        ['Ky_Thuat'],
        extractor='mechanical',
        quality_fn='quality_mechanical',
    ),
    'ke_toan': DomainConfig(
        'ke_toan', 'Kế toán',
        ['Ke_Toan'],
        extractor='tabular',
        quality_fn='quality_generic',
        default_security='confidential',
    ),
    'nhan_su': DomainConfig(
        'nhan_su', 'Nhân sự',
        ['Nhan_Su'],
        extractor='generic',
        quality_fn='quality_generic',
        default_security='confidential',
    ),
    'chung': DomainConfig(
        'chung', 'Chung',
        ['CHUNG', 'Tu_Hoc', 'IT'],
        extractor='generic',
        quality_fn='quality_generic',
        default_security='public',
    ),
}


def resolve_domain_by_department(thu_muc: str) -> str:
    """Xac dinh domain dua tren thu muc upload.

    Returns:
        Domain key (str). Mac dinh 'chung' neu khong tim thay.
    """
    if not thu_muc:
        return 'chung'
    for cfg in DOMAINS.values():
        if thu_muc in cfg.departments:
            return cfg.key
    return 'chung'


def get_default_security(domain: str) -> str:
    """Lay muc bao mat mac dinh cua domain."""
    cfg = DOMAINS.get(domain, DOMAINS['chung'])
    return cfg.default_security


def get_domain_config(domain: str) -> DomainConfig:
    """Lay cau hinh cua domain, fallback ve 'chung'."""
    return DOMAINS.get(domain, DOMAINS['chung'])
