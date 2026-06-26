-- Seed Roles (idempotent). Chay sau baseline.
USE Mech_Chatbot_DB;
GO
IF NOT EXISTS (SELECT 1 FROM dbo.Roles)
    INSERT INTO dbo.Roles (RoleName) VALUES ('admin'), ('reviewer'), ('uploader'), ('viewer');
GO
PRINT 'Seed roles hoan tat.';
GO
