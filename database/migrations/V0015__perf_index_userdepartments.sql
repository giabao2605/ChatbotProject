-- V0015: Perf index cho tra cuu theo Department tren UserDepartments.
-- PK hien tai la (UserID, Department) -> loc theo Department don le KHONG dung index.
-- Cac cho dung: count user theo phong, revoke_user_department, reassign_department_data.
-- Idempotent + tuong thich nguoc (chi them index, khong doi du lieu/logic).
SET NOCOUNT ON;
GO

IF NOT EXISTS (SELECT 1 FROM sys.indexes WHERE name = 'IX_UserDepartments_Department' AND object_id = OBJECT_ID(N'dbo.UserDepartments'))
    CREATE INDEX IX_UserDepartments_Department ON dbo.UserDepartments (Department, UserID);
GO

IF NOT EXISTS (SELECT 1 FROM dbo._SchemaVersions WHERE Version = 'V0015')
    INSERT INTO dbo._SchemaVersions (Version, Description)
    VALUES ('V0015', 'Perf: index UserDepartments(Department) cho tra cuu/revoke/reassign theo phong ban.');
GO
