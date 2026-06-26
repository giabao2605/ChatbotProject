-- =============================================================================
-- P2 FIX: Doi cot LichSuChat.DanhGia tu TINYINT -> SMALLINT
-- Ly do: TINYINT trong SQL Server la KHONG dau (0..255) nen KHONG luu duoc -1
--        (dislike) -> loi 'Arithmetic overflow error for data type tinyint, value = -1'.
-- Quy uoc giu nguyen: 1 = Like, -1 = Dislike, NULL = Chua danh gia.
-- File idempotent: chi ALTER khi cot dang con la tinyint.
-- =============================================================================
SET NOCOUNT ON;
GO

IF EXISTS (
    SELECT 1
    FROM sys.columns c
    JOIN sys.types t ON c.user_type_id = t.user_type_id
    WHERE c.object_id = OBJECT_ID('dbo.LichSuChat')
        AND c.name = 'DanhGia'
        AND t.name = 'tinyint'
)
BEGIN
    ALTER TABLE dbo.LichSuChat ALTER COLUMN DanhGia SMALLINT NULL;
    PRINT 'Da doi LichSuChat.DanhGia -> SMALLINT (luu duoc -1).';
END
ELSE
    PRINT 'LichSuChat.DanhGia khong con la tinyint (da sua hoac kieu khac), bo qua.';
GO

PRINT 'p2_fix_danhgia_smallint hoan tat.';
GO
