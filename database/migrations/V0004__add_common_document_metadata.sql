-- ============================================================================
-- V0004: Metadata tong quat cho tai lieu da phong ban (P0)
--
-- Muc tieu: tai lieu KHONG phai co khi (ke toan / hanh chinh / ISO / HSE...)
-- can cac truong dung chung (tieu de, tom tat, tags, so van ban, ngay ban hanh,
-- ngay hieu luc, ngay het hieu luc / ngay soat xet, nguoi ky/owner, ngon ngu,
-- trang thai hieu luc). Truoc day cac truong nay khong co cot DB nen khong the
-- nhap/sua/hien. Migration nay them cot len TaiLieu + 1 cot UploadMetaJson tren
-- IngestionJobs de mang metadata nhap luc upload xuong worker.
--
-- An toan & idempotent: chi them cot neu chua co. Khong xoa du lieu.
--
-- Chay:
--   sqlcmd -S <server> -d Mech_Chatbot_DB -E -I -i V0004__add_common_document_metadata.sql
-- ============================================================================
USE Mech_Chatbot_DB;
GO
IF EXISTS (SELECT 1 FROM dbo._SchemaVersions WHERE Version = 'V0004') RETURN;
GO

-- 1) Cot metadata tong quat tren TaiLieu (tat ca NULL duoc -> tuong thich nguoc)
IF COL_LENGTH('dbo.TaiLieu','Title')           IS NULL ALTER TABLE dbo.TaiLieu ADD Title NVARCHAR(500) NULL;            -- Tieu de tai lieu (con nguoi doc)
GO
IF COL_LENGTH('dbo.TaiLieu','Summary')         IS NULL ALTER TABLE dbo.TaiLieu ADD Summary NVARCHAR(MAX) NULL;         -- Tom tat ngan
GO
IF COL_LENGTH('dbo.TaiLieu','Tags')            IS NULL ALTER TABLE dbo.TaiLieu ADD Tags NVARCHAR(1000) NULL;           -- Tu khoa, phan tach bang dau phay
GO
IF COL_LENGTH('dbo.TaiLieu','DocNumber')       IS NULL ALTER TABLE dbo.TaiLieu ADD DocNumber NVARCHAR(150) NULL;       -- So van ban / so chung tu / so hop dong
GO
IF COL_LENGTH('dbo.TaiLieu','IssuedDate')      IS NULL ALTER TABLE dbo.TaiLieu ADD IssuedDate DATE NULL;              -- Ngay ban hanh
GO
IF COL_LENGTH('dbo.TaiLieu','EffectiveDate')   IS NULL ALTER TABLE dbo.TaiLieu ADD EffectiveDate DATE NULL;           -- Ngay hieu luc
GO
IF COL_LENGTH('dbo.TaiLieu','ExpiryDate')      IS NULL ALTER TABLE dbo.TaiLieu ADD ExpiryDate DATE NULL;              -- Ngay het hieu luc
GO
IF COL_LENGTH('dbo.TaiLieu','ReviewDate')      IS NULL ALTER TABLE dbo.TaiLieu ADD ReviewDate DATE NULL;              -- Ngay soat xet ke tiep
GO
IF COL_LENGTH('dbo.TaiLieu','OwnerSigner')     IS NULL ALTER TABLE dbo.TaiLieu ADD OwnerSigner NVARCHAR(255) NULL;     -- Nguoi ky / chu so huu tai lieu
GO
IF COL_LENGTH('dbo.TaiLieu','DocLanguage')     IS NULL ALTER TABLE dbo.TaiLieu ADD DocLanguage NVARCHAR(20) NULL;      -- vi | en | ...
GO
IF COL_LENGTH('dbo.TaiLieu','EffectiveStatus') IS NULL ALTER TABLE dbo.TaiLieu ADD EffectiveStatus NVARCHAR(20) NOT NULL CONSTRAINT DF_TaiLieu_EffectiveStatus DEFAULT 'active';  -- active | expired | superseded | draft
GO

IF NOT EXISTS (SELECT 1 FROM sys.indexes WHERE name = 'IX_TaiLieu_DocNumber' AND object_id = OBJECT_ID('dbo.TaiLieu'))
    CREATE INDEX IX_TaiLieu_DocNumber ON dbo.TaiLieu(DocNumber);
GO
IF NOT EXISTS (SELECT 1 FROM sys.indexes WHERE name = 'IX_TaiLieu_EffectiveStatus' AND object_id = OBJECT_ID('dbo.TaiLieu'))
    CREATE INDEX IX_TaiLieu_EffectiveStatus ON dbo.TaiLieu(EffectiveStatus, ExpiryDate);
GO

-- 2) Mang metadata nhap luc upload xuong worker (JSON: common fields + domain attrs)
IF COL_LENGTH('dbo.IngestionJobs','UploadMetaJson') IS NULL ALTER TABLE dbo.IngestionJobs ADD UploadMetaJson NVARCHAR(MAX) NULL;
GO

INSERT INTO dbo._SchemaVersions (Version, Description)
VALUES ('V0004', 'P0: metadata tong quat da phong ban (TaiLieu.Title/Summary/Tags/DocNumber/dates/OwnerSigner/EffectiveStatus + IngestionJobs.UploadMetaJson)');
GO

PRINT 'V0004: Hoan tat metadata tong quat da phong ban.';
GO
