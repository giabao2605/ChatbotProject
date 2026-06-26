-- =============================================================================
-- P0 FIX: Them cot moi vao bang DA TON TAI (TaiLieu) ma init script bo qua.
-- Ly do: cac cot Domain/SecurityLevel/Site nam trong khoi CREATE TABLE TaiLieu,
--        bi bao boi IF NOT EXISTS(bang) nen KHONG duoc them vao DB cu.
-- File nay dung ALTER TABLE, idempotent (chay lai nhieu lan deu an toan).
-- CHAY TRUOC p0_backfill_domain_security.sql
-- =============================================================================
SET NOCOUNT ON;
GO -- TaiLieu.Domain
    IF NOT EXISTS (
        SELECT 1
        FROM sys.columns
        WHERE object_id = OBJECT_ID('dbo.TaiLieu')
            AND name = 'Domain'
    ) BEGIN
ALTER TABLE dbo.TaiLieu
ADD Domain NVARCHAR(50) NULL;
PRINT 'Da them cot TaiLieu.Domain';
END
ELSE PRINT 'TaiLieu.Domain da ton tai, bo qua.';
GO -- TaiLieu.SecurityLevel (NOT NULL + DEFAULT -> SQL Server tu dien 'internal' cho moi dong cu)
    IF NOT EXISTS (
        SELECT 1
        FROM sys.columns
        WHERE object_id = OBJECT_ID('dbo.TaiLieu')
            AND name = 'SecurityLevel'
    ) BEGIN
ALTER TABLE dbo.TaiLieu
ADD SecurityLevel NVARCHAR(20) NOT NULL CONSTRAINT DF_TaiLieu_SecurityLevel DEFAULT 'internal';
PRINT 'Da them cot TaiLieu.SecurityLevel';
END
ELSE PRINT 'TaiLieu.SecurityLevel da ton tai, bo qua.';
GO -- TaiLieu.Site
    IF NOT EXISTS (
        SELECT 1
        FROM sys.columns
        WHERE object_id = OBJECT_ID('dbo.TaiLieu')
            AND name = 'Site'
    ) BEGIN
ALTER TABLE dbo.TaiLieu
ADD Site NVARCHAR(100) NULL;
PRINT 'Da them cot TaiLieu.Site';
END
ELSE PRINT 'TaiLieu.Site da ton tai, bo qua.';
GO PRINT 'P0 add-columns hoan tat. Bay gio moi chay p0_backfill_domain_security.sql';
GO