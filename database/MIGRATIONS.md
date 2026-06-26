# Quy uoc SQL (sau khi don sach)

Truoc day `init/` va `migrations/` bi trung dinh nghia bang, 3 file P0 chong nhau, va thu tu chay chi ghi bang loi. Nay gom lai theo cau truc tuyen tinh, co theo doi phien ban.

## Cau truc thu muc

```
database/
  schema/01_baseline.sql        # NGUON SU THAT cua cau truc DB (idempotent)
  seed/                          # du lieu khoi tao
    01_roles.sql
    02_dev_accounts.sql          # CHI dung local; vo hieu hoa truoc go-live
    03_departments.sql           # 14 phong ban (domain + muc mat)
  migrations/                    # thay doi schema SAU baseline, danh so tang dan
    V0001__<mo_ta>.sql           # (chua co; mau o duoi)
  data_migrations/               # don/chuyen DU LIEU (khong doi cau truc)
    0001_normalize_domain_values.sql
  migrations/_legacy/            # cac file cu da gom vao baseline (luu lai de tra cuu)
```

## Thu tu chay (DB moi / local)

```bash
sqlcmd -S localhost\SQLEXPRESS -I -i database/schema/01_baseline.sql
sqlcmd -S localhost\SQLEXPRESS -d Mech_Chatbot_DB -I -i database/seed/01_roles.sql
sqlcmd -S localhost\SQLEXPRESS -d Mech_Chatbot_DB -I -i database/seed/03_departments.sql
sqlcmd -S localhost\SQLEXPRESS -d Mech_Chatbot_DB -I -i database/seed/02_dev_accounts.sql  # can Departments truoc
# (tuy chon) neu mang du lieu cu sang:
sqlcmd -S localhost\SQLEXPRESS -d Mech_Chatbot_DB -I -i database/data_migrations/0001_normalize_domain_values.sql
```

## Nguyen tac (tranh tai pham loi cu)

1. **1 nguon su that:** moi bang dinh nghia DUY NHAT trong `schema/01_baseline.sql`. Khong dinh nghia lai bang o cho khac.
2. **Cot moi = ALTER idempotent rieng**, KHONG nhet vao trong `CREATE TABLE` bi bao boi `IF NOT EXISTS(bang)` (loi cu khien DB cu khong nhan cot Domain/Security/Site).
3. **Theo doi phien ban** qua bang `dbo._SchemaVersions`. Moi migration/data-migration ghi 1 dong version.
4. **Thong nhat:** luon dung tien to `dbo.`; khoa phong ban duy nhat = `Departments.DeptCode` (TaiLieu.PhongBan, UserDepartments.Department deu tham chieu).
5. **Migration moi**: tao file `migrations/V0002__mo_ta.sql`, dau file kiem tra version, cuoi file ghi version.

## Mau migration moi

```sql
USE Mech_Chatbot_DB;
GO
IF EXISTS (SELECT 1 FROM dbo._SchemaVersions WHERE Version = 'V0002') RETURN;
GO
-- ... ALTER/CREATE idempotent o day ...
GO
INSERT INTO dbo._SchemaVersions (Version, Description) VALUES ('V0002', 'mo ta ngan');
GO
```

## Ghi chu ve `_legacy/`

Cac file trong `migrations/_legacy/` (init cu, migrate_p0_*, p0_*, p1_*, p2_*, p3_*, fix_rbac_*) DA duoc gom vao baseline + seed + data_migrations. Giu lai chi de tra cuu lich su; **khong chay** tren DB moi.
