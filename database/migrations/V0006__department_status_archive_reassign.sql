-- V0006: Bo sung vong doi phong ban (active/disabled/archived)
USE Mech_Chatbot_DB;
GO
IF EXISTS (SELECT 1 FROM dbo._SchemaVersions WHERE Version = 'V0006') RETURN;
GO

IF COL_LENGTH('dbo.Departments','Status') IS NULL
    ALTER TABLE dbo.Departments ADD Status NVARCHAR(20) NOT NULL CONSTRAINT DF_Departments_Status DEFAULT 'active';
GO
IF COL_LENGTH('dbo.Departments','DisabledAt') IS NULL
    ALTER TABLE dbo.Departments ADD DisabledAt DATETIME NULL;
GO
IF COL_LENGTH('dbo.Departments','ArchivedAt') IS NULL
    ALTER TABLE dbo.Departments ADD ArchivedAt DATETIME NULL;
GO

UPDATE dbo.Departments
SET Status = CASE WHEN ISNULL(IsActive, 1) = 1 THEN 'active' ELSE 'disabled' END
WHERE Status IS NULL OR LTRIM(RTRIM(Status)) = '';
GO

UPDATE dbo.Departments
SET DisabledAt = GETDATE()
WHERE Status = 'disabled' AND DisabledAt IS NULL;
GO

IF NOT EXISTS (
    SELECT 1 FROM sys.check_constraints
    WHERE name = 'CHK_Departments_Status'
      AND parent_object_id = OBJECT_ID('dbo.Departments')
)
BEGIN
    ALTER TABLE dbo.Departments WITH NOCHECK
    ADD CONSTRAINT CHK_Departments_Status CHECK (Status IN ('active','disabled','archived'));
END
GO

IF NOT EXISTS (SELECT 1 FROM dbo._SchemaVersions WHERE Version = 'V0006')
BEGIN
    INSERT INTO dbo._SchemaVersions (Version, Description, AppliedAt)
    VALUES ('V0006', 'P2: vong doi phong ban active/disabled/archived + timestamps', GETDATE());
END
GO

PRINT 'V0006: Hoan tat them Status/DisabledAt/ArchivedAt cho Departments.';
GO
