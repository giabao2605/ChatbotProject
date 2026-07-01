# Ban va Giai doan 2 & 3 + loi an (audit)

Tat ca thay doi giu tuong thich nguoc, khong xoa du lieu.

## P0 - Nhat quan & bao mat
- **B3 + B2** `ingestion/site_registry.py`: `resolve_site_by_department()` gio tu doc `dbo.Departments.Site`
  (ham `_lookup_site`, lazy import tranh circular). Nho vay `metadata.site` (Qdrant) trong `pdf_processor`
  va `TaiLieu.Site` (SQL) cung lay 1 nguon -> het lech. Mo rong map fallback `DEPARTMENT_SITE`
  cho 14 DeptCode moi.
- **B1a** `database/schema/01_baseline.sql`: bo `Site` khoi CREATE TABLE, them `ALTER ... ADD Site` idempotent
  (dung chuan MIGRATIONS.md) de DB cu cung nhan cot.
- **B1a (DB cu)** `database/migrations/V0008__add_site_to_departments.sql`: migration moi them cot Site.
- **B1b** `database/seed/03_departments.sql`: seed gia tri `Site` cho 14 phong.

## P1 - Chong hoi quy & dung du lieu
- **C1** `rag/service.py`: XOA ban trung `create_rbac_filter/_security_filter/_site_filter/_allowed_levels/LEVEL_ORDER`,
  import tu `rag/rbac.py` (1 nguon su that; test va production dung chung).
- **C2** `auth/service.py`: sua comment DeptCode that (Technical/Production/HR...), bo tham chieu file
  `fix_rbac_seed.sql` khong ton tai.
- **Bug#5** `db/repository.py`: chuan hoa so khop CSV `PhongBan` bang `REPLACE(...,' ','')` -> khong loi khi co khoang trang.
- **Bug#4** `ingestion/pdf_processor.py`: them `metadata.thu_muc` va `_delete_vectors_for_file` khop theo
  `thu_muc` (gia tri don) + van fallback `phong_ban_quyen` de tuong thich vector cu.

## P2 - Hoan (can quyet dinh thiet ke, chua lam)
- F1 (hang so CHUNG), F2 (prompt tabular rieng), E1 (bang PhongBanChiaSe nhieu-nhieu).

## Cach ap dung DB
```
sqlcmd ... -i database/migrations/V0008__add_site_to_departments.sql
sqlcmd ... -i database/seed/03_departments.sql
```
