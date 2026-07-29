-- Allow the bounded, post-generation claim-repair pass through the same
-- metadata-only External AI audit boundary as ordinary generation.

IF (SELECT COUNT(*) FROM dbo.ExternalAIProviderProfile WHERE Provider = 'proxyllm') <> 1
    THROW 51035, 'Expected exactly one proxyllm profile; refusing surface update', 1;
GO

IF EXISTS (
    SELECT 1
    FROM dbo.ExternalAIProviderProfile
    WHERE Provider = 'proxyllm'
      AND (
          ISJSON(AllowedSurfacesJson) <> 1
          OR LEFT(LTRIM(AllowedSurfacesJson), 1) <> '['
          OR RIGHT(RTRIM(AllowedSurfacesJson), 1) <> ']'
      )
)
    THROW 51035, 'proxyllm AllowedSurfacesJson must be a JSON array; refusing surface update', 1;
GO

IF EXISTS (
    SELECT 1
    FROM dbo.ExternalAIProviderProfile
    WHERE Provider = 'proxyllm'
      AND NOT EXISTS (
          SELECT 1
          FROM OPENJSON(AllowedSurfacesJson)
          WHERE [value] = 'claim_repair'
      )
)
BEGIN
    UPDATE dbo.ExternalAIProviderProfile
    SET AllowedSurfacesJson = JSON_MODIFY(
            AllowedSurfacesJson,
            'append $',
            'claim_repair'
        ),
        PolicyVersion = 'risk-accepted-v4-claim-repair',
        UpdatedAt = GETDATE(),
        UpdatedBy = 'V0035 migration'
    WHERE Provider = 'proxyllm';
END
GO

IF NOT EXISTS (
    SELECT 1
    FROM dbo.ExternalAIProviderProfile AS profile
    CROSS APPLY OPENJSON(profile.AllowedSurfacesJson) AS surface
    WHERE profile.Provider = 'proxyllm'
      AND surface.[value] = 'claim_repair'
)
    THROW 51035, 'claim_repair surface update could not be verified', 1;
GO

IF OBJECT_ID('dbo.AuditLog', 'U') IS NOT NULL
   AND NOT EXISTS (
       SELECT 1
       FROM dbo.AuditLog
       WHERE Action = 'external_ai_claim_repair_surface_enabled'
         AND EntityType = 'ExternalAIProviderProfile'
   )
BEGIN
    INSERT INTO dbo.AuditLog (Username, Action, EntityType, Details)
    VALUES (
        'System',
        'external_ai_claim_repair_surface_enabled',
        'ExternalAIProviderProfile',
        N'{"provider":"proxyllm","surface":"claim_repair","policy_version":"risk-accepted-v4-claim-repair","scope":"bounded post-generation repair"}'
    );
END
GO
