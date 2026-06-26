-- ============================================================================
-- DATA MIGRATION 0001 — Chuan hoa gia tri Domain cu -> 3 kieu doc moi
--   co_khi, ky_thuat            -> mechanical
--   ke_toan, mua_hang, kho, ...  -> tabular   (xu ly o GD2 theo phong ban)
--   nhan_su, chung, ...          -> generic
-- DUNG KHI: ban da co du lieu cu mang Domain = co_khi/ky_thuat/ke_toan/nhan_su/chung.
--   (Backfill P0 cu da ghi cac gia tri nay vao DB.)
-- KHONG can chay tren DB moi/sach.
-- Idempotent: chay lai an toan.
-- ============================================================================
USE Mech_Chatbot_DB;
GO
SET NOCOUNT ON;
GO

-- 1) TaiLieu.Domain
UPDATE dbo.TaiLieu SET Domain = 'mechanical' WHERE Domain IN ('co_khi','ky_thuat');
UPDATE dbo.TaiLieu SET Domain = 'generic'    WHERE Domain IN ('nhan_su','chung');
-- Luu y: ke_toan/mua_hang/kho/sales -> tabular se duoc gan lai theo TaiLieu.PhongBan o GD2
-- (vi mot so 'chung' cu thuc te la tabular). Tam coi cac gia tri con lai la generic.
UPDATE dbo.TaiLieu SET Domain = 'tabular'    WHERE Domain IN ('ke_toan');
GO

-- 2) Departments.Domain (bang seed moi da chuan, day chi xu ly hang cu phat sinh)
UPDATE dbo.Departments SET Domain = 'mechanical' WHERE Domain IN ('co_khi','ky_thuat');
UPDATE dbo.Departments SET Domain = 'tabular'    WHERE Domain IN ('ke_toan');
UPDATE dbo.Departments SET Domain = 'generic'    WHERE Domain IN ('nhan_su','chung');
GO

-- 3) DocumentAttributes.Domain
UPDATE dbo.DocumentAttributes SET Domain = 'mechanical' WHERE Domain IN ('co_khi','ky_thuat');
UPDATE dbo.DocumentAttributes SET Domain = 'tabular'    WHERE Domain IN ('ke_toan');
UPDATE dbo.DocumentAttributes SET Domain = 'generic'    WHERE Domain IN ('nhan_su','chung');
GO

IF NOT EXISTS (SELECT 1 FROM dbo._SchemaVersions WHERE Version = 'DM0001')
    INSERT INTO dbo._SchemaVersions (Version, Description) VALUES ('DM0001','Normalize Domain values -> mechanical/tabular/generic');
GO
PRINT 'Data migration 0001 hoan tat.';
GO
