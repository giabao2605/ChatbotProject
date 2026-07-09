-- ============================================================================
-- TAI KHOAN DEV / TEST — CHI DUNG O MOI TRUONG LOCAL
-- !!! TRUOC GO-LIVE: vo hieu hoa cac tai khoan nay (xem data_migrations/
--     hoac task bao mat "hoan den truoc go-live" trong plan).
-- Mat khau mac dinh theo bcrypt hash hien tai: admin123
-- Idempotent: chi tao khi chua co.
-- ============================================================================
USE Mech_Chatbot_DB;
GO

-- admin: toan quyen, clearance cao nhat
IF NOT EXISTS (SELECT 1 FROM dbo.Users WHERE Username = 'admin')
BEGIN
    INSERT INTO dbo.Users (Username, PasswordHash, DisplayName, Department)
    VALUES ('admin', '$2b$12$GjF79FWNuuNfl4VWOA28iOk4ubZWWd5OltSsAiZ5TgaWPz5UtAZpu', 'Administrator', 'IT');
    INSERT INTO dbo.UserRoles (UserID, RoleID)
        SELECT u.UserID, r.RoleID FROM dbo.Users u, dbo.Roles r WHERE u.Username='admin' AND r.RoleName='admin';
    INSERT INTO dbo.UserSecurityClearance (UserID, MaxLevel)
        SELECT UserID, 'confidential' FROM dbo.Users WHERE Username='admin';
    -- admin xem tat ca phong ban
    INSERT INTO dbo.UserDepartments (UserID, Department)
        SELECT u.UserID, d.DeptCode FROM dbo.Users u CROSS JOIN dbo.Departments d WHERE u.Username='admin';
END
GO

-- viewer1: chi xem generic + internal
IF NOT EXISTS (SELECT 1 FROM dbo.Users WHERE Username = 'viewer1')
BEGIN
    INSERT INTO dbo.Users (Username, PasswordHash, DisplayName, Department)
    VALUES ('viewer1', '$2b$12$12Y5ru30M7ai9YuW3Ip7ZOiXXYiuyv/.Yn4YH2mX749joCzzEvhI2', 'Nhan Vien A', 'IT');
    INSERT INTO dbo.UserRoles (UserID, RoleID)
        SELECT u.UserID, r.RoleID FROM dbo.Users u, dbo.Roles r WHERE u.Username='viewer1' AND r.RoleName='viewer';
    INSERT INTO dbo.UserSecurityClearance (UserID, MaxLevel)
        SELECT UserID, 'internal' FROM dbo.Users WHERE Username='viewer1';
    INSERT INTO dbo.UserDepartments (UserID, Department)
        SELECT u.UserID, 'IT' FROM dbo.Users u WHERE u.Username='viewer1';
END
GO

-- uploader1: nap tai lieu cho cac phong mechanical
IF NOT EXISTS (SELECT 1 FROM dbo.Users WHERE Username = 'uploader1')
BEGIN
    INSERT INTO dbo.Users (Username, PasswordHash, DisplayName, Department)
    VALUES ('uploader1', '$2b$12$12Y5ru30M7ai9YuW3Ip7ZOiXXYiuyv/.Yn4YH2mX749joCzzEvhI2', 'Uploader', 'Production');
    INSERT INTO dbo.UserRoles (UserID, RoleID)
        SELECT u.UserID, r.RoleID FROM dbo.Users u, dbo.Roles r WHERE u.Username='uploader1' AND r.RoleName='uploader';
    INSERT INTO dbo.UserSecurityClearance (UserID, MaxLevel)
        SELECT UserID, 'internal' FROM dbo.Users WHERE Username='uploader1';
    INSERT INTO dbo.UserDepartments (UserID, Department)
        SELECT u.UserID, d.DeptCode FROM dbo.Users u JOIN dbo.Departments d ON d.Domain='mechanical' WHERE u.Username='uploader1';
END
GO

-- reviewer1: duyet toan bo, xem confidential
IF NOT EXISTS (SELECT 1 FROM dbo.Users WHERE Username = 'reviewer1')
BEGIN
    INSERT INTO dbo.Users (Username, PasswordHash, DisplayName, Department)
    VALUES ('reviewer1', '$2b$12$12Y5ru30M7ai9YuW3Ip7ZOiXXYiuyv/.Yn4YH2mX749joCzzEvhI2', 'Truong Phong', 'QualityControl');
    INSERT INTO dbo.UserRoles (UserID, RoleID)
        SELECT u.UserID, r.RoleID FROM dbo.Users u, dbo.Roles r WHERE u.Username='reviewer1' AND r.RoleName='reviewer';
    INSERT INTO dbo.UserSecurityClearance (UserID, MaxLevel)
        SELECT UserID, 'confidential' FROM dbo.Users WHERE Username='reviewer1';
    INSERT INTO dbo.UserDepartments (UserID, Department)
        SELECT u.UserID, d.DeptCode FROM dbo.Users u CROSS JOIN dbo.Departments d WHERE u.Username='reviewer1';
END
GO

PRINT 'Seed tai khoan dev hoan tat (NHO vo hieu hoa truoc go-live).';
GO

-- Backfill quan he cho truong hop user da ton tai tu truoc nen cac khoi
-- IF NOT EXISTS o tren khong chay nua.
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
