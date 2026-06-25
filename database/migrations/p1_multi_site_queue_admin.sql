-- =====================================================================
-- P1 MIGRATION — Multi-site RBAC, dynamic departments/sites, queue upgrade
-- Idempotent. Chay an toan nhieu lan. KHONG pha du lieu cu.
--   sqlcmd -S localhost\SQLEXPRESS -d Mech_Chatbot_DB -I -i p1_multi_site_queue_admin.sql
-- =====================================================================
SET QUOTED_IDENTIFIER ON;
SET ANSI_NULLS ON;
GO

-- ---------------------------------------------------------------------
-- 1) Bang tham chieu PHONG BAN (quan ly dong trong UI - P1.1)
--    Khac UserDepartments (gan user<->phong): day la danh muc phong ban.
-- ---------------------------------------------------------------------
IF NOT EXISTS (SELECT 1 FROM sys.objects WHERE object_id = OBJECT_ID(N'dbo.Departments') AND type = 'U')
BEGIN
    CREATE TABLE Departments (
        DeptCode  NVARCHAR(255) NOT NULL PRIMARY KEY,   -- vd: To_Han, Ke_Toan, Nhan_Su
        DeptName  NVARCHAR(255) NULL,                   -- ten hien thi: "To Han"
        Domain    NVARCHAR(50)  NULL,                   -- co_khi/ke_toan/nhan_su/ky_thuat/chung
        Site      NVARCHAR(100) NULL,                   -- khu/site mac dinh cua phong
        IsActive  BIT NOT NULL DEFAULT 1,
        CreatedAt DATETIME DEFAULT GETDATE()
    );
END
GO

-- Seed danh muc phong ban tu cac gia tri da co trong UserDepartments + TaiLieu (idempotent)
INSERT INTO dbo.Departments (DeptCode)
SELECT DISTINCT x.Department
FROM (
    SELECT Department FROM dbo.UserDepartments
    UNION
    SELECT ThuMuc AS Department FROM dbo.TaiLieu WHERE ThuMuc IS NOT NULL
) x
WHERE x.Department IS NOT NULL
  AND NOT EXISTS (SELECT 1 FROM dbo.Departments d WHERE d.DeptCode = x.Department);
GO

-- Gan domain goi y cho cac phong da biet (chi cap nhat khi dang NULL)
UPDATE dbo.Departments SET Domain = 'co_khi'
WHERE Domain IS NULL AND DeptCode IN
    ('To_Han','To_Dap','To_Son','To_Nham','To_Phoi','To_Tien_Phay','To_Dong_Goi','To_Ban_Le','Bang_Ke','Gia_Cong_Ngoai');
UPDATE dbo.Departments SET Domain = 'ky_thuat' WHERE Domain IS NULL AND DeptCode IN ('Ky_Thuat');
UPDATE dbo.Departments SET Domain = 'ke_toan'  WHERE Domain IS NULL AND DeptCode IN ('Ke_Toan');
UPDATE dbo.Departments SET Domain = 'nhan_su'  WHERE Domain IS NULL AND DeptCode IN ('Nhan_Su');
UPDATE dbo.Departments SET Domain = 'chung'    WHERE Domain IS NULL AND DeptCode IN ('CHUNG','Tu_Hoc');
GO

-- ---------------------------------------------------------------------
-- 2) Bang tham chieu KHU / SITE (P1.2)
-- ---------------------------------------------------------------------
IF NOT EXISTS (SELECT 1 FROM sys.objects WHERE object_id = OBJECT_ID(N'dbo.Sites') AND type = 'U')
BEGIN
    CREATE TABLE Sites (
        SiteCode  NVARCHAR(100) NOT NULL PRIMARY KEY,   -- vd: XUONG_CO_KHI, VP_KE_TOAN
        SiteName  NVARCHAR(255) NULL,                   -- "Xuong co khi", "Van phong ke toan"
        IsActive  BIT NOT NULL DEFAULT 1,
        CreatedAt DATETIME DEFAULT GETDATE()
    );
END
GO

-- ---------------------------------------------------------------------
-- 3) Gan USER <-> SITE (RBAC chieu thu 3: site x department x security)
--    De TRONG = user khong bi gioi han theo site (tuong thich nguoc).
-- ---------------------------------------------------------------------
IF NOT EXISTS (SELECT 1 FROM sys.objects WHERE object_id = OBJECT_ID(N'dbo.UserSites') AND type = 'U')
BEGIN
    CREATE TABLE UserSites (
        UserID INT NOT NULL,
        Site   NVARCHAR(100) NOT NULL,
        PRIMARY KEY (UserID, Site)
    );
END
GO

-- ---------------------------------------------------------------------
-- 4) Nang cap HANG DOI INGEST (P1.5): uu tien, huy job, gioi han trang
-- ---------------------------------------------------------------------
IF COL_LENGTH('dbo.IngestionJobs','Priority') IS NULL
    ALTER TABLE dbo.IngestionJobs ADD Priority INT NOT NULL DEFAULT 100;  -- nho hon = uu tien hon
GO
IF COL_LENGTH('dbo.IngestionJobs','MaxPages') IS NULL
    ALTER TABLE dbo.IngestionJobs ADD MaxPages INT NULL;                  -- gioi han so trang/job
GO
IF COL_LENGTH('dbo.IngestionJobs','CanceledBy') IS NULL
    ALTER TABLE dbo.IngestionJobs ADD CanceledBy NVARCHAR(255) NULL;
GO
IF COL_LENGTH('dbo.IngestionJobs','CanceledAt') IS NULL
    ALTER TABLE dbo.IngestionJobs ADD CanceledAt DATETIME NULL;
GO

-- Index moi phuc vu get_pending_job theo (Priority, CreatedAt)
IF NOT EXISTS (SELECT 1 FROM sys.indexes WHERE name = 'IX_IngestionJobs_Status_Priority' AND object_id = OBJECT_ID('dbo.IngestionJobs'))
    CREATE INDEX IX_IngestionJobs_Status_Priority ON dbo.IngestionJobs(Status, Priority, CreatedAt);
GO

-- ---------------------------------------------------------------------
-- 5) Index ho tro loc tai lieu theo Site/Domain (P1.4 + dashboard P1.6)
-- ---------------------------------------------------------------------
IF NOT EXISTS (SELECT 1 FROM sys.indexes WHERE name = 'IX_TaiLieu_Site_Domain' AND object_id = OBJECT_ID('dbo.TaiLieu'))
    CREATE INDEX IX_TaiLieu_Site_Domain ON dbo.TaiLieu(Site, Domain, ReviewStatus);
GO

PRINT 'P1 migration hoan tat: Departments, Sites, UserSites, IngestionJobs(Priority/MaxPages/Cancel), indexes.';
GO
