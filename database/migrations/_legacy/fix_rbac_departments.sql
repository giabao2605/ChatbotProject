-- ============================================================
-- MIGRATION: Fix RBAC seed data - UserDepartments
-- File: database/migrations/fix_rbac_departments.sql
--
-- MUC DICH: Cap nhat bang UserDepartments de dung ten to san xuat
--           thuc te (khop voi ThuMuc trong data/raw/) thay vi
--           ten phong ban chung ("Ky_Thuat" khong khop voi To_Han, To_Dap...).
--
-- CHAY KHI: Sau khi nang cap auth/service.py (bo logic tu dong them department).
-- AN TOAN:  Script nay KHONG xoa hay thay doi bang khac. Chi cap nhat UserDepartments.
-- ============================================================

USE Mech_Chatbot_DB;
GO

-- -------------------------------------------------------
-- Buoc 1: Xoa seed data cu (sai namespace)
-- -------------------------------------------------------
DELETE FROM UserDepartments WHERE UserID IN (
    SELECT UserID FROM Users WHERE Username IN ('viewer1', 'uploader1', 'reviewer1')
);
GO

-- -------------------------------------------------------
-- Buoc 2: Seed lai dung ten to san xuat
-- Cac gia tri hop le phai khop chinh xac voi ten thu muc trong data/raw/:
--   Bang_Ke, Gia_Cong_Ngoai, IT, To_Ban_Le, To_Dap, To_Dong_Goi,
--   To_Han, To_Nham, To_Phoi, To_Son, To_Tien_Phay, Tu_Hoc, CHUNG
-- -------------------------------------------------------

-- viewer1: Chi xem tai lieu public (Tu_Hoc) va tai lieu chung
INSERT INTO UserDepartments (UserID, Department)
SELECT u.UserID, d.Department
FROM Users u
CROSS JOIN (VALUES ('Tu_Hoc'), ('CHUNG')) AS d(Department)
WHERE u.Username = 'viewer1';

-- uploader1: Nap tai lieu cho cac to san xuat chinh
INSERT INTO UserDepartments (UserID, Department)
SELECT u.UserID, d.Department
FROM Users u
CROSS JOIN (VALUES
    ('To_Han'), ('To_Dap'), ('To_Son'), ('To_Nham'),
    ('To_Phoi'), ('To_Tien_Phay'), ('To_Dong_Goi'),
    ('To_Ban_Le'), ('Bang_Ke'), ('Gia_Cong_Ngoai'), ('CHUNG')
) AS d(Department)
WHERE u.Username = 'uploader1';

-- reviewer1: Duyet tai lieu cua tat ca cac to (toan quyen doc)
INSERT INTO UserDepartments (UserID, Department)
SELECT u.UserID, d.Department
FROM Users u
CROSS JOIN (VALUES
    ('To_Han'), ('To_Dap'), ('To_Son'), ('To_Nham'),
    ('To_Phoi'), ('To_Tien_Phay'), ('To_Dong_Goi'),
    ('To_Ban_Le'), ('Bang_Ke'), ('Gia_Cong_Ngoai'), ('IT'), ('Tu_Hoc'), ('CHUNG')
) AS d(Department)
WHERE u.Username = 'reviewer1';
GO

-- -------------------------------------------------------
-- Buoc 3: Kiem tra ket qua
-- -------------------------------------------------------
SELECT
    u.Username,
    u.Department      AS [Display Dept],
    d.Department      AS [Folder Access]
FROM Users u
LEFT JOIN UserDepartments d ON u.UserID = d.UserID
ORDER BY u.Username, d.Department;
GO
