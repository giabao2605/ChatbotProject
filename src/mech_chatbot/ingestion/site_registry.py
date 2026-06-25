"""Site registry (P1.2) — ban do phong ban -> khu/site.

Site la mot CAP PHIA TREN phong ban (Site -> Department -> ThuMuc), phuc vu
nhieu dia diem (duoi xuong co khi, van phong ke toan, phong ky thuat...).

Gia tri site duoc gan vao payload Qdrant (metadata.site) khi ingest, va dung
cho RBAC chieu thu 3 (site x department x security_level) khi truy van.

Thu tu uu tien khi xac dinh site cua mot tai lieu:
  1) Site cau hinh tay tren tung phong (bang dbo.Departments.Site)  -- neu co
  2) Map mac dinh trong file nay (DEPARTMENT_SITE)
  3) Fallback: SITE_DEFAULT ('HQ')

KHONG raise loi: moi ham deu co fallback an toan de pipeline ingest khong vo.
"""
from __future__ import annotations

SITE_DEFAULT = "HQ"

# Danh muc khu/site goi y (co the mo rong / quan ly dong qua bang dbo.Sites)
SITES = {
    "XUONG_CO_KHI": "Xuong co khi",
    "VP_KE_TOAN": "Van phong ke toan",
    "VP_NHAN_SU": "Van phong nhan su",
    "PHONG_KY_THUAT": "Phong ky thuat",
    "HQ": "Tru so chinh",
}

# Ban do mac dinh phong ban (ThuMuc) -> site
DEPARTMENT_SITE = {
    # Xuong co khi
    "To_Han": "XUONG_CO_KHI",
    "To_Dap": "XUONG_CO_KHI",
    "To_Son": "XUONG_CO_KHI",
    "To_Nham": "XUONG_CO_KHI",
    "To_Phoi": "XUONG_CO_KHI",
    "To_Tien_Phay": "XUONG_CO_KHI",
    "To_Dong_Goi": "XUONG_CO_KHI",
    "To_Ban_Le": "XUONG_CO_KHI",
    "Bang_Ke": "XUONG_CO_KHI",
    "Gia_Cong_Ngoai": "XUONG_CO_KHI",
    # Phong ky thuat
    "Ky_Thuat": "PHONG_KY_THUAT",
    # Ke toan / nhan su
    "Ke_Toan": "VP_KE_TOAN",
    "Nhan_Su": "VP_NHAN_SU",
}


def resolve_site_by_department(thu_muc, db_site=None):
    """Tra ve site code cho mot phong ban.

    Args:
        thu_muc: ten phong ban / thu muc upload.
        db_site: site cau hinh tay tren bang Departments (uu tien cao nhat).
    """
    if db_site:
        return str(db_site)
    if thu_muc and thu_muc in DEPARTMENT_SITE:
        return DEPARTMENT_SITE[thu_muc]
    return SITE_DEFAULT


def site_label(site_code):
    """Ten hien thi cho mot site code (fallback ve chinh code)."""
    if not site_code:
        return SITE_DEFAULT
    return SITES.get(site_code, str(site_code))
