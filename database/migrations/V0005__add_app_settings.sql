-- V0005: Them bang AppSettings (cau hinh ung dung dong)
-- Idempotent: an toan khi chay lai nhieu lan.

IF NOT EXISTS (SELECT 1 FROM sys.tables WHERE name = 'AppSettings' AND schema_id = SCHEMA_ID('dbo'))
BEGIN
    CREATE TABLE dbo.AppSettings (
        SettingKey   NVARCHAR(100) NOT NULL PRIMARY KEY,
        SettingValue NVARCHAR(MAX) NULL,
        UpdatedAt    DATETIME NOT NULL CONSTRAINT DF_AppSettings_UpdatedAt DEFAULT GETDATE(),
        UpdatedBy    NVARCHAR(255) NULL
    );
    PRINT 'Da tao bang dbo.AppSettings.';
END
ELSE
    PRINT 'Bang dbo.AppSettings da ton tai - bo qua tao moi.';
GO

-- Seed gia tri mac dinh (chi them khi chua co)
MERGE dbo.AppSettings AS tgt
USING (VALUES
    ('expiry_warning_days', '30'),
    ('rag_general_top_k', '30')
) AS src (SettingKey, SettingValue)
ON tgt.SettingKey = src.SettingKey
WHEN NOT MATCHED BY TARGET THEN
    INSERT (SettingKey, SettingValue) VALUES (src.SettingKey, src.SettingValue);
GO

-- Ghi nhan phien ban
IF NOT EXISTS (SELECT 1 FROM dbo._SchemaVersions WHERE Version = 'V0005')
    INSERT INTO dbo._SchemaVersions (Version, Description, AppliedAt)
    VALUES ('V0005', 'Them bang AppSettings (cau hinh ung dung)', GETDATE());
GO
