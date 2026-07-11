-- ============================================================================
-- V0009: Chuan hoa chia se phong ban (E1)
--   TaiLieu.PhongBan (CSV) -> bang nhieu-nhieu dbo.PhongBanChiaSe(DocID, DeptCode)
--
-- Phuong an B (da chot): XOA HAN cot TaiLieu.PhongBan sau khi tao bang moi.
--   1) Tao bang dbo.PhongBanChiaSe (nguon su that moi) + index.
--   2) Backfill tu CSV cu (neu con cot): them ThuMuc (phong chu) + cac phong chia se.
--   3) Xoa index IX_TaiLieu_PhongBan va cot TaiLieu.PhongBan.
--
-- An toan & idempotent: guard theo _SchemaVersions; moi buoc deu IF EXISTS/NOT EXISTS.
-- DeptCode co the la sentinel SHARE_ALL ('CHUNG') nen KHONG rang buoc FK sang Departments.
--
-- Chay:
--   sqlcmd -S <server> -d Mech_Chatbot_DB -E -I -i V0009__normalize_phongban_sharing.sql
-- ============================================================================
USE Mech_Chatbot_DB;
GO
IF EXISTS (SELECT 1 FROM dbo._SchemaVersions WHERE Version = 'V0009') RETURN;
GO

-- 1) Bang nhieu-nhieu: 1 tai lieu <-> nhieu phong ban duoc chia se
IF OBJECT_ID('dbo.PhongBanChiaSe', 'U') IS NULL
BEGIN
    CREATE TABLE dbo.PhongBanChiaSe (
        DocID    INT           NOT NULL,
        DeptCode NVARCHAR(50)  NOT NULL,
        CONSTRAINT PK_PhongBanChiaSe PRIMARY KEY (DocID, DeptCode),
        CONSTRAINT FK_PhongBanChiaSe_TaiLieu
            FOREIGN KEY (DocID) REFERENCES dbo.TaiLieu(DocID) ON DELETE CASCADE
    );
END
GO
IF NOT EXISTS (SELECT 1 FROM sys.indexes WHERE name = 'IX_PhongBanChiaSe_Dept' AND object_id = OBJECT_ID('dbo.PhongBanChiaSe'))
    CREATE INDEX IX_PhongBanChiaSe_Dept ON dbo.PhongBanChiaSe(DeptCode, DocID);
GO

-- 2a) Phong chu (ThuMuc) luon co quyen doc, ca tren baseline moi khong con cot CSV.
INSERT INTO dbo.PhongBanChiaSe (DocID, DeptCode)
SELECT DISTINCT t.DocID, LTRIM(RTRIM(t.ThuMuc))
FROM dbo.TaiLieu t
WHERE t.ThuMuc IS NOT NULL AND LTRIM(RTRIM(t.ThuMuc)) <> ''
  AND NOT EXISTS (SELECT 1 FROM dbo.PhongBanChiaSe p
                  WHERE p.DocID = t.DocID AND p.DeptCode = LTRIM(RTRIM(t.ThuMuc)));

-- 2b) Cac phong chia se them tu CSV cu. SQL Server bind cot o compile time,
-- nen phai dung dynamic SQL: IF COL_LENGTH ben ngoai khong du de bao ve DB moi
-- da khong con TaiLieu.PhongBan.
IF COL_LENGTH('dbo.TaiLieu', 'PhongBan') IS NOT NULL
BEGIN
    BEGIN TRY
        EXEC sys.sp_executesql N'
            INSERT INTO dbo.PhongBanChiaSe (DocID, DeptCode)
            SELECT DISTINCT t.DocID, LTRIM(RTRIM(s.value))
            FROM dbo.TaiLieu t
            CROSS APPLY STRING_SPLIT(t.PhongBan, '','') s
            WHERE t.PhongBan IS NOT NULL
              AND LTRIM(RTRIM(s.value)) <> ''''
              AND NOT EXISTS (SELECT 1 FROM dbo.PhongBanChiaSe p
                              WHERE p.DocID = t.DocID AND p.DeptCode = LTRIM(RTRIM(s.value)));';
    END TRY
    BEGIN CATCH
        PRINT 'V0009 WARNING: STRING_SPLIT khong kha dung, bo qua backfill CSV chia se. Loi: ' + ERROR_MESSAGE();
    END CATCH
END
GO

-- 3) Xoa index + cot PhongBan cu (phuong an B: xoa han)
IF EXISTS (SELECT 1 FROM sys.indexes WHERE name = 'IX_TaiLieu_PhongBan' AND object_id = OBJECT_ID('dbo.TaiLieu'))
    DROP INDEX IX_TaiLieu_PhongBan ON dbo.TaiLieu;
GO
IF COL_LENGTH('dbo.TaiLieu', 'PhongBan') IS NOT NULL
    ALTER TABLE dbo.TaiLieu DROP COLUMN PhongBan;
GO

INSERT INTO dbo._SchemaVersions (Version, Description)
VALUES ('V0009', 'E1: chuan hoa TaiLieu.PhongBan CSV -> bang nhieu-nhieu PhongBanChiaSe; xoa cot PhongBan');
GO

PRINT 'V0009: Hoan tat chuan hoa PhongBanChiaSe + xoa cot TaiLieu.PhongBan.';
GO
