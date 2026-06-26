-- Optional migration để tách lịch sử chat theo từng user.
-- Chạy trong SQL Server nếu muốn tránh user thấy lịch sử chat của nhau.

IF COL_LENGTH('dbo.LichSuChat', 'Username') IS NULL
BEGIN
    ALTER TABLE dbo.LichSuChat ADD Username NVARCHAR(255) NULL;
END
GO

CREATE INDEX IX_LichSuChat_Username_Time
ON dbo.LichSuChat(Username, ThoiGian DESC);
GO
