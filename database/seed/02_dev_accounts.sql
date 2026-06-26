-- ============================================================================
-- TAI KHOAN DEV / TEST — CHI DUNG O MOI TRUONG LOCAL
-- !!! TRUOC GO-LIVE: vo hieu hoa cac tai khoan nay (xem data_migrations/
--     hoac task bao mat "hoan den truoc go-live" trong plan).
-- Mat khau mac dinh: Admin@123  (bcrypt hash giu nguyen tu schema cu)
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
