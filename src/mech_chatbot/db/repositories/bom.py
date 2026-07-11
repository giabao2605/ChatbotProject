"""P1.1 auto-split tu db/repository.py (giu nguyen logic + comment goc).
Loi goi cheo module dung tham chieu _r_<module>.<ten> (tranh circular import).
KHONG sua tay truc tiep neu chua doc AGENTS; day la mot phan cua package db/repositories.
"""
import re
import os
from sqlalchemy import text
from ..engine import _ensure_engine, engine
from mech_chatbot.config.logging import logger
from mech_chatbot.config.constants import SHARE_ALL_DEPARTMENT
from ._shared import _sanitize_int, _sanitize_text

__all__ = [
    'normalize_material_name',
    'save_bom_records',
    'search_bom_by_code',
]

def normalize_material_name(raw):
    """P2: uy quyen cho material_registry (tu dien DB). Fallback logic cu neu loi."""
    if not raw:
        return None
    try:
        from mech_chatbot.db.registry_ports import normalize_material
        return normalize_material(raw)
    except Exception:
        s = str(raw).strip().lower()
        s = s.replace("inox", "stainless steel")
        s = s.replace("sus304", "sus 304")
        s = s.replace("ss304", "sus 304")
        s = re.sub(r"\s+", " ", s)
        return s

def save_bom_records(doc_id, trang_so, records):
    """Luu danh sach cac vat tu cua bang ke vao SQL"""
    if not doc_id or not records:
        return
    _ensure_engine()
    try:
        # Perf (GD1): bulk insert thay N+1 (executemany). Giu nguyen tung dong.
        _rows = [
            {
                "doc_id": doc_id,
                "trang_so": trang_so,
                "ma_hang": _sanitize_text(rec.get("ma_hang"), 255),
                "ten": _sanitize_text(rec.get("ten_vat_tu"), 500),
                "vat_lieu": _sanitize_text(rec.get("vat_lieu"), 255),
                "normalized_material": _sanitize_text(normalize_material_name(rec.get("vat_lieu")), 255),
                "sl": _sanitize_int(rec.get("so_luong"), None),
                "ghi_chu": _sanitize_text(rec.get("ghi_chu"), 4000),
                "unit": _sanitize_text(rec.get("don_vi"), 50),
                "conf": rec.get("confidence", None),
                "raw": rec.get("raw_row_json", None),
                "idx": rec.get("source_table_index", None)
            }
            for rec in records
        ]
        with engine.begin() as conn:
            if _rows:
                conn.execute(
                    text("""
                        INSERT INTO BangKeVatTu (DocID, TrangSo, MaHang, TenVatTu, VatLieu, NormalizedMaterial, SoLuong, GhiChu, Unit, Confidence, RawRowJson, SourceTableIndex)
                        VALUES (:doc_id, :trang_so, :ma_hang, :ten, :vat_lieu, :normalized_material, :sl, :ghi_chu, :unit, :conf, :raw, :idx)
                    """),
                    _rows,
                )
    except Exception as e:
        logger.error(f"Loi save_bom_records cho doc_id {doc_id}, trang {trang_so}: {e}", exc_info=True)

def search_bom_by_code(
    ma_hang_list,
    version_policy="current_only",
    detected_versions=None,
    user_department=None,
    user_roles=None,
    allowed_departments=None,
    max_security_level=None,
    allowed_sites=None,
):
    """Tim kiem bang ke vat tu tren SQL theo ma hang hoac ma doi tuong (parent assembly).

    Su dung CONTAINS() neu Full-Text Index da duoc cai dat tren BangKeVatTu,
    fallback ve LIKE '%...%' neu Full-Text Search khong kha dung.
    """
    if not ma_hang_list:
        return []
    if not user_roles:
        logger.warning("Deny SQL BOM search because user_roles is empty.")
        return []
    _ensure_engine()
    try:
        with engine.connect() as conn:
            # Kiem tra Full-Text Index co kha dung khong (1 lan, nhe)
            ft_row = conn.execute(text(
                """SELECT COUNT(1) FROM sys.fulltext_indexes fi
                   JOIN sys.objects o ON fi.object_id = o.object_id
                   WHERE o.name = 'BangKeVatTu'"""
            )).scalar()
            use_fulltext = (ft_row or 0) > 0

            # Tao dieu kien OR cho tung ma
            conditions = []
            params = {}
            for i, m in enumerate(ma_hang_list):
                if use_fulltext:
                    # CONTAINS dung double-quote de tim cum tu chinh xac hon
                    # prefix search: "ma*" khop maHang bat dau bang ma
                    params[f"m{i}"] = f'"{m}*"'
                    conditions.append(f"""
                    (
                        CONTAINS(b.MaHang, :m{i})
                        OR EXISTS (
                            SELECT 1 FROM TaiLieuKyThuat tk
                            WHERE tk.DocID = b.DocID
                            AND tk.MaDoiTuong LIKE :ml{i}
                        )
                    )
                    """)
                    params[f"ml{i}"] = f"%{m}%"   # MaDoiTuong la NVARCHAR(MAX), FT ko ho tro
                else:
                    params[f"m{i}"] = f"%{m}%"
                    conditions.append(f"""
                    (
                        b.MaHang LIKE :m{i}
                        OR EXISTS (
                            SELECT 1 FROM TaiLieuKyThuat tk
                            WHERE tk.DocID = b.DocID
                            AND tk.MaDoiTuong LIKE :m{i}
                        )
                    )
                    """)

            filter_sql = "1=1"
            if version_policy in ["current_only", "all_current_variants"]:
                filter_sql += " AND t.Servable = 1 AND t.PublicationState = 'published' AND t.LifecycleStatus = 'published' AND t.ReviewStatus = 'approved' AND t.IsCurrent = 1"
            elif version_policy == "specific_version":
                filter_sql += " AND t.Servable = 1 AND t.PublicationState = 'published' AND t.LifecycleStatus IN ('published', 'archived', 'superseded') AND t.ReviewStatus = 'approved'"
                if detected_versions:
                    filter_sql += f" AND t.VersionNo = {int(detected_versions[0])}"
            elif version_policy == "compare_versions":
                filter_sql += " AND t.Servable = 1 AND t.PublicationState = 'published' AND t.LifecycleStatus IN ('published', 'archived', 'superseded') AND t.ReviewStatus = 'approved'"
                if detected_versions:
                    vers_str = ",".join(str(int(v)) for v in detected_versions)
                    filter_sql += f" AND t.VersionNo IN ({vers_str})"
            else:
                filter_sql += " AND t.Servable = 1 AND t.PublicationState = 'published' AND t.LifecycleStatus = 'published' AND t.ReviewStatus = 'approved' AND t.IsCurrent = 1"

            # The legacy ``admin`` role has the business-approved global-read
            # capability.  Every other role must pass the same department,
            # clearance, and site predicates as the Qdrant retrieval path.
            normalized_roles = {
                str(role).strip().lower() for role in (user_roles or []) if str(role).strip()
            }
            if "admin" not in normalized_roles:
                # RBAC: chi dung allowed_departments tu UserDepartments, khong tu dong them department
                allowed = list(allowed_departments or [])
                if SHARE_ALL_DEPARTMENT not in allowed:
                    allowed.append(SHARE_ALL_DEPARTMENT)

                dept_conditions = []
                for i, dept in enumerate(allowed):
                    key = f"dept{i}"
                    # E1: chia se nhieu phong qua bang nhieu-nhieu dbo.PhongBanChiaSe.
                    # Khop chinh xac DeptCode (khong con substring match nhu CSV cu).
                    dept_conditions.append(
                        "(t.ThuMuc = :" + key + " OR EXISTS (SELECT 1 FROM dbo.PhongBanChiaSe pbc WHERE pbc.DocID = t.DocID AND pbc.DeptCode = :" + key + "))"
                    )
                    params[key] = dept

                filter_sql += " AND (" + " OR ".join(dept_conditions) + ")"

                # GD5 muc 1: RBAC chieu thu 2 — muc mat (security_level).
                # Truoc day duong SQL BOM CHI loc phong ban (ThuMuc) ma KHONG loc SecurityLevel
                # -> user clearance thap van moi duoc du lieu BOM tu tai lieu 'confidential'
                # qua nga SQL (trong khi nga Qdrant da chan). Dong bo logic voi _security_filter
                # / _allowed_levels ben rag/service.py: cho xem cac muc <= clearance, mac dinh 'internal'.
                _LEVEL_ORDER = {"public": 0, "internal": 1, "confidential": 2}
                _max_order = _LEVEL_ORDER.get((max_security_level or "public"), 0)
                _sec_levels = [lvl for lvl, o in _LEVEL_ORDER.items() if o <= _max_order]
                sec_conditions = []
                for i, lvl in enumerate(_sec_levels):
                    key = f"sec{i}"
                    sec_conditions.append(f"t.SecurityLevel = :{key}")
                    params[key] = lvl
                # GD5 muc 5: tai lieu THIEU muc mat coi nhu confidential. Chi cho NULL/rong khi
                # user co clearance confidential; nguoc lai an di (dong bo voi _security_filter Qdrant).
                _allow_empty_sec = "confidential" in _sec_levels
                if _allow_empty_sec:
                    filter_sql += " AND (t.SecurityLevel IS NULL OR LTRIM(RTRIM(t.SecurityLevel)) = '' OR " + " OR ".join(sec_conditions) + ")"
                else:
                    filter_sql += " AND (t.SecurityLevel IS NOT NULL AND LTRIM(RTRIM(t.SecurityLevel)) <> '' AND (" + " OR ".join(sec_conditions) + "))"

                # Keep the SQL BOM path fail-closed by site just like Qdrant.
                # Legacy data without a site is visible only while the explicit
                # compatibility switch is off; strict mode is the default.
                sites = sorted({str(site).strip() for site in (allowed_sites or []) if str(site).strip()})
                strict_site = str(os.getenv("RBAC_STRICT_SITE_FILTER", "true")).strip().lower() in {
                    "1", "true", "yes", "on"
                }
                if not sites:
                    filter_sql += " AND 1 = 0"
                else:
                    site_conditions = []
                    for i, site in enumerate(sites):
                        key = f"site{i}"
                        site_conditions.append(f"LTRIM(RTRIM(t.Site)) = :{key}")
                        params[key] = site
                    if strict_site:
                        filter_sql += " AND t.Site IS NOT NULL AND LTRIM(RTRIM(t.Site)) <> '' AND (" + " OR ".join(site_conditions) + ")"
                    else:
                        filter_sql += " AND (t.Site IS NULL OR LTRIM(RTRIM(t.Site)) = '' OR " + " OR ".join(site_conditions) + ")"

            query = text(f"""
                SELECT DISTINCT b.DocID, b.TrangSo, b.MaHang, b.TenVatTu, b.VatLieu,
                       b.SoLuong, b.GhiChu, t.TenFile, t.VersionNo, t.SecurityLevel,
                       t.Site, t.ExternalProcessingPolicy
                FROM BangKeVatTu b
                JOIN TaiLieu t ON b.DocID = t.DocID
                WHERE {filter_sql} AND b.TrangSo IS NOT NULL AND (
                    {" OR ".join(conditions)}
                )
            """)

            result = conn.execute(query, params).fetchall()
            return result
    except Exception as e:
        logger.error(f"Loi search_bom_by_code: {e}", exc_info=True)
        return []
