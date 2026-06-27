-- ============================================================================
-- V0003: Them cot FilePath vao bang TaiLieu (sua loi 'Invalid column name FilePath')
--
-- Nguyen nhan: ban va C8 (tai lai file goc) co SELECT t.FilePath tu TaiLieu va
-- luong publish ghi FilePath, nhung schema baseline KHONG tao cot nay tren TaiLieu
-- (chi co tren IngestionJobs). DB cu thieu cot -> trang Kho tai lieu / Duyet bi loi.
--
-- An toan & idempotent: chi them cot neu chua co, roi backfill tu IngestionJobs.
--
-- Chay:
--   sqlcmd -S WS-IT-04\SQLEXPRESS -d Mech_Chatbot_DB -E -I -i V0003__add_filepath_to_tailieu.sql
-- ============================================================================
USE Mech_Chatbot_DB;
GO

-- 1) Them cot FilePath (NULL duoc, vi tai lieu cu co the khong co duong dan)
IF COL_LENGTH('dbo.TaiLieu', 'FilePath') IS NULL
BEGIN
    ALTER TABLE dbo.TaiLieu ADD FilePath NVARCHAR(500) NULL;
    PRINT 'V0003: Da them cot dbo.TaiLieu.FilePath.';
END
ELSE
    PRINT 'V0003: Cot dbo.TaiLieu.FilePath da ton tai, bo qua.';
GO

-- 2) Backfill FilePath cho tai lieu cu tu IngestionJobs (match TenFile + ThuMuc,
--    lay job moi nhat co FilePath). Khong dung khi khong tim thay job tuong ung.
UPDATE t
SET t.FilePath = j.FilePath
FROM dbo.TaiLieu t
CROSS APPLY (
    SELECT TOP 1 ij.FilePath
    FROM dbo.IngestionJobs ij
    WHERE ij.TenFile = t.TenFile
      AND ij.ThuMuc  = t.ThuMuc
      AND ij.FilePath IS NOT NULL
    ORDER BY ij.JobID DESC
) j
WHERE t.FilePath IS NULL;
GO

PRINT 'V0003: Hoan tat. So tai lieu da co FilePath:';
SELECT COUNT(*) AS DocsWithFilePath FROM dbo.TaiLieu WHERE FilePath IS NOT NULL;
GO
