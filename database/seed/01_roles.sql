-- Seed Roles (idempotent). Chay sau baseline.
USE Mech_Chatbot_DB;
GO
DECLARE @roles TABLE (RoleName NVARCHAR(100) NOT NULL PRIMARY KEY);
INSERT INTO @roles (RoleName)
VALUES
    ('admin'),
    ('reviewer'),
    ('uploader'),
    ('viewer'),
    ('platform_admin'),
    ('security_admin'),
    ('knowledge_approver'),
    ('knowledge_consumer');

INSERT INTO dbo.Roles (RoleName)
SELECT source.RoleName
FROM @roles source
WHERE NOT EXISTS (
    SELECT 1 FROM dbo.Roles target WHERE target.RoleName = source.RoleName
);
GO
PRINT 'Seed roles hoan tat.';
GO
