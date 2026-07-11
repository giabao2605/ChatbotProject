-- V0024: Separate platform administration from security, approval, and
-- knowledge-consumption duties. This migration does not silently change a
-- user's existing department/site/clearance grants; those remain explicit
-- entitlements managed by security administrators.

DECLARE @roles TABLE (RoleName NVARCHAR(100) NOT NULL PRIMARY KEY);
INSERT INTO @roles (RoleName)
VALUES
    (N'platform_admin'),
    (N'security_admin'),
    (N'knowledge_approver'),
    (N'knowledge_consumer');

INSERT INTO dbo.Roles (RoleName)
SELECT source.RoleName
FROM @roles source
WHERE NOT EXISTS (
    SELECT 1 FROM dbo.Roles target WHERE target.RoleName = source.RoleName
);
GO

IF OBJECT_ID('dbo.AuditLog', 'U') IS NOT NULL
   AND NOT EXISTS (
       SELECT 1
       FROM dbo.AuditLog
       WHERE Action = 'knowledge_role_separation_v1'
         AND EntityType = 'Roles'
   )
BEGIN
    INSERT INTO dbo.AuditLog (Username, Action, EntityType, Details)
    VALUES (
        'System',
        'knowledge_role_separation_v1',
        'Roles',
        N'{"roles":["platform_admin","security_admin","knowledge_approver","knowledge_consumer"],"retrieval":"department_site_clearance_required"}'
    );
END
GO

IF NOT EXISTS (SELECT 1 FROM dbo._SchemaVersions WHERE Version = 'V0024')
    INSERT INTO dbo._SchemaVersions (Version, Description, AppliedAt)
    VALUES ('V0024', 'Separate platform, security, knowledge approver and consumer roles', GETDATE());
GO
