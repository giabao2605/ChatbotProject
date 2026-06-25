-- Migration: Them cot/bang cho he thong da phong ban (P0)
-- Chay tren DB hien tai (idempotent, an toan chay lai nhieu lan)
-- Ngay: 2026-06-25

USE Mech_Chatbot_DB;
GO

-- 1. Them cot Domain, SecurityLevel, Site vao TaiLieu
IF COL_LENGTH('dbo.TaiLieu', 'Domain') IS NULL
    ALTER TABLE dbo.TaiLieu ADD Domain NVARCHAR(50) NULL;
GO

IF COL_LENGTH('dbo.TaiLieu', 'SecurityLevel') IS NULL
    ALTER TABLE dbo.TaiLieu ADD SecurityLevel NVARCHAR(20) NOT NULL DEFAULT 'internal';
GO

IF COL_LENGTH('dbo.TaiLieu', 'Site') IS NULL
    ALTER TABLE dbo.TaiLieu ADD Site NVARCHAR(100) NULL;
GO

-- 2. Backfill Domain cho tai lieu cu dua tren ThuMuc
UPDATE TaiLieu SET Domain = 'co_khi'
WHERE Domain IS NULL AND ThuMuc IN (
    'To_Han','To_Dap','To_Son','To_Nham','To_Phoi','To_Tien_Phay',
    'To_Dong_Goi','To_Ban_Le','Bang_Ke','Gia_Cong_Ngoai'
);

UPDATE TaiLieu SET Domain = 'ky_thuat'
WHERE Domain IS NULL AND ThuMuc IN ('Ky_Thuat');

UPDATE TaiLieu SET Domain = 'chung'
WHERE Domain IS NULL;
GO

-- 3. Bang UserSecurityClearance
IF NOT EXISTS (SELECT 1 FROM sys.objects WHERE object_id = OBJECT_ID(N'dbo.UserSecurityClearance') AND type = 'U')
BEGIN
    CREATE TABLE UserSecurityClearance (
        UserID   INT NOT NULL PRIMARY KEY,
        MaxLevel NVARCHAR(20) NOT NULL DEFAULT 'internal',
        CONSTRAINT FK_USC_Users FOREIGN KEY (UserID) REFERENCES Users(UserID)
    );
END
GO

-- Seed clearance cho user hien co (idempotent)
INSERT INTO UserSecurityClearance (UserID, MaxLevel)
SELECT u.UserID, 'confidential'
FROM Users u
WHERE u.Username IN ('admin', 'reviewer1')
  AND NOT EXISTS (SELECT 1 FROM UserSecurityClearance usc WHERE usc.UserID = u.UserID);

INSERT INTO UserSecurityClearance (UserID, MaxLevel)
SELECT u.UserID, 'internal'
FROM Users u
WHERE u.Username IN ('viewer1', 'uploader1')
  AND NOT EXISTS (SELECT 1 FROM UserSecurityClearance usc WHERE usc.UserID = u.UserID);
GO

-- 4. Bang DocumentAttributes (metadata tong quat cho domain phi co khi)
IF NOT EXISTS (SELECT 1 FROM sys.objects WHERE object_id = OBJECT_ID(N'dbo.DocumentAttributes') AND type = 'U')
BEGIN
    CREATE TABLE DocumentAttributes (
        AttrID         INT IDENTITY(1,1) PRIMARY KEY,
        DocID          INT NOT NULL,
        Domain         NVARCHAR(50)  NOT NULL,
        AttributeKey   NVARCHAR(150) NOT NULL,
        AttributeValue NVARCHAR(MAX) NULL,
        Confidence     FLOAT NULL,
        ExtractedBy    NVARCHAR(50) NULL,
        CreatedAt      DATETIME DEFAULT GETDATE(),
        CONSTRAINT FK_DocAttr_TaiLieu FOREIGN KEY (DocID) REFERENCES TaiLieu(DocID) ON DELETE CASCADE
    );
    CREATE INDEX IX_DocAttr_Doc_Domain ON DocumentAttributes(DocID, Domain);
END
GO

-- 5. Kiem tra ket qua
SELECT 'TaiLieu columns' AS [Check], COUNT(*) AS [Count] FROM INFORMATION_SCHEMA.COLUMNS WHERE TABLE_NAME = 'TaiLieu';
SELECT 'DocumentAttributes' AS [Table], COUNT(*) AS [Rows] FROM DocumentAttributes;
SELECT 'UserSecurityClearance' AS [Table], COUNT(*) AS [Rows] FROM UserSecurityClearance;
GO

PRINT 'Migration P0 multi-domain hoan tat.';
GO
