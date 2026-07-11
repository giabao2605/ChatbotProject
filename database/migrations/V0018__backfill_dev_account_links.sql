USE Mech_Chatbot_DB;
GO

-- V0018: backfill quan he RBAC cho cac dev account da ton tai tu truoc.
-- Loi cu: seed/02_dev_accounts.sql chi gan roles/departments/clearance ben trong
-- IF NOT EXISTS Users, nen neu user co san thi UserDepartments co the bi rong.

INSERT INTO dbo.UserRoles (UserID, RoleID)
SELECT u.UserID, r.RoleID
FROM dbo.Users u
JOIN (VALUES
    ('admin', 'admin'),
    ('viewer1', 'viewer'),
    ('uploader1', 'uploader'),
    ('reviewer1', 'reviewer')
) map_user(Username, RoleName) ON map_user.Username = u.Username
JOIN dbo.Roles r ON r.RoleName = map_user.RoleName
WHERE NOT EXISTS (
    SELECT 1 FROM dbo.UserRoles ur WHERE ur.UserID = u.UserID AND ur.RoleID = r.RoleID
);
GO

INSERT INTO dbo.UserSecurityClearance (UserID, MaxLevel)
SELECT u.UserID, map_user.MaxLevel
FROM dbo.Users u
JOIN (VALUES
    ('admin', 'confidential'),
    ('viewer1', 'internal'),
    ('uploader1', 'internal'),
    ('reviewer1', 'confidential')
) map_user(Username, MaxLevel) ON map_user.Username = u.Username
WHERE NOT EXISTS (
    SELECT 1 FROM dbo.UserSecurityClearance usc WHERE usc.UserID = u.UserID
);
GO

UPDATE usc
SET MaxLevel = map_user.MaxLevel
FROM dbo.UserSecurityClearance usc
JOIN dbo.Users u ON u.UserID = usc.UserID
JOIN (VALUES
    ('admin', 'confidential'),
    ('viewer1', 'internal'),
    ('uploader1', 'internal'),
    ('reviewer1', 'confidential')
) map_user(Username, MaxLevel) ON map_user.Username = u.Username
WHERE ISNULL(usc.MaxLevel, '') <> map_user.MaxLevel;
GO

INSERT INTO dbo.UserDepartments (UserID, Department)
SELECT u.UserID, d.DeptCode
FROM dbo.Users u
CROSS JOIN dbo.Departments d
WHERE u.Username IN ('admin', 'reviewer1')
  AND NOT EXISTS (
      SELECT 1 FROM dbo.UserDepartments ud
      WHERE ud.UserID = u.UserID AND ud.Department = d.DeptCode
  );
GO

INSERT INTO dbo.UserDepartments (UserID, Department)
SELECT u.UserID, 'IT'
FROM dbo.Users u
WHERE u.Username = 'viewer1'
  AND NOT EXISTS (
      SELECT 1 FROM dbo.UserDepartments ud WHERE ud.UserID = u.UserID AND ud.Department = 'IT'
  );
GO

INSERT INTO dbo.UserDepartments (UserID, Department)
SELECT u.UserID, d.DeptCode
FROM dbo.Users u
JOIN dbo.Departments d ON d.Domain = 'mechanical'
WHERE u.Username = 'uploader1'
  AND NOT EXISTS (
      SELECT 1 FROM dbo.UserDepartments ud
      WHERE ud.UserID = u.UserID AND ud.Department = d.DeptCode
  );
GO

IF NOT EXISTS (SELECT 1 FROM dbo._SchemaVersions WHERE Version = 'V0018')
    INSERT INTO dbo._SchemaVersions(Version, Description)
    VALUES ('V0018', 'Backfill dev account RBAC links when users already existed before seed.');
GO

PRINT 'V0018: Hoan tat backfill quan he dev account.';
GO
