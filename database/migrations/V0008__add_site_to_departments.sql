-- ============================================================================
-- V0008: Them cot Site vao dbo.Departments (P1.2 / Giai doan 2 - B1)
--
-- Ly do: cot Site truoc day CHI duoc dinh nghia ben trong CREATE TABLE cua
-- schema/01_baseline.sql (bi bao boi IF NOT EXISTS(bang)) nen cac DB da tao
-- TRUOC do KHONG nhan duoc cot Site -> seed Site va RBAC theo site khong chay.
-- Migration nay them cot theo dung chuan MIGRATIONS.md: ALTER idempotent rieng.
--
-- An toan & idempotent: chi them cot neu chua co. Khong xoa du lieu.
--
-- Chay:
--   sqlcmd -S <server> -d Mech_Chatbot_DB -E -I -i V0008__add_site_to_departments.sql
--   (sau do chay lai seed/03_departments.sql de nap gia tri Site cho 14 phong)
-- ============================================================================
USE Mech_Chatbot_DB;
GO
IF EXISTS (SELECT 1 FROM dbo._SchemaVersions WHERE Version = 'V0008') RETURN;
GO

IF COL_LENGTH('dbo.Departments','Site') IS NULL
    ALTER TABLE dbo.Departments ADD Site NVARCHAR(100) NULL;
GO

INSERT INTO dbo._SchemaVersions (Version, Description)
VALUES ('V0008', 'P1.2/B1: them cot Site vao Departments (ALTER idempotent, tach khoi CREATE TABLE)');
GO

PRINT 'V0008: Hoan tat them cot Departments.Site. Hay chay lai seed/03_departments.sql de nap gia tri Site.';
GO
