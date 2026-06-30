-- P0 #5 — Bang luu lan dang nhap sai DUNG CHUNG giua cac tien trinh/worker.
-- Thay cho bo dem in-process (defaultdict) chi song trong 1 process.
-- Idempotent: chay nhieu lan khong loi.

IF NOT EXISTS (
    SELECT 1 FROM sys.tables WHERE name = 'LoginAttempts'
)
BEGIN
    CREATE TABLE dbo.LoginAttempts (
        Id        BIGINT IDENTITY(1,1) NOT NULL PRIMARY KEY,
        Username  NVARCHAR(256)        NOT NULL,
        AttemptAt DATETIME2(0)         NOT NULL CONSTRAINT DF_LoginAttempts_AttemptAt DEFAULT SYSUTCDATETIME()
    );

    -- Index phuc vu dem theo (Username, thoi gian)
    CREATE INDEX IX_LoginAttempts_User_Time
        ON dbo.LoginAttempts (Username, AttemptAt);
END
GO

-- (Tuy chon) Don rac dinh ky: xoa cac lan sai cu hon 1 ngay de bang khong phinh.
-- Co the dat trong SQL Agent Job chay hang ngay:
-- DELETE FROM dbo.LoginAttempts WHERE AttemptAt < DATEADD(DAY, -1, SYSUTCDATETIME());
