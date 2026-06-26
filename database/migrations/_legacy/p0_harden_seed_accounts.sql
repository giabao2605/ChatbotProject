-- =============================================================================
-- P0 HARDENING: Vo hieu hoa / doi tai khoan seed mac dinh truoc go-live
-- CHAY SAU KHI da tao tai khoan admin THAT cua ban.
-- Idempotent.
-- =============================================================================
SET NOCOUNT ON;
GO

-- !!! BUOC 1: Tao admin that TRUOC khi chay file nay (qua UI hoac script rieng).
-- !!! Neu chua co admin that, KHONG chay phan duoi (se mat quyen admin).

IF EXISTS (
    SELECT 1 FROM Users u
    JOIN UserRoles ur ON ur.UserID = u.UserID
    JOIN Roles r ON r.RoleID = ur.RoleID
    WHERE r.RoleName = 'admin' AND u.Username <> 'admin' AND u.IsActive = 1
)
BEGIN
    -- Da co it nhat 1 admin that khac 'admin' seed -> an toan de khoa cac seed account
    UPDATE Users SET IsActive = 0
     WHERE Username IN ('admin', 'viewer1', 'uploader1', 'reviewer1');
    PRINT 'Da vo hieu hoa cac tai khoan seed mac dinh.';
END
ELSE
BEGIN
    PRINT 'BO QUA: Chua co admin that nao khac tai khoan seed. Hay tao admin that truoc.';
END
GO
