-- Add governed Jina metadata for the bounded reranker evaluation.
-- The secret stays in JINA_API_KEY and provider selection flags stay unchanged.

DECLARE @jina_profile_created BIT = 0;

IF NOT EXISTS (SELECT 1 FROM dbo.ExternalAIProviderProfile WHERE Provider = 'jina')
BEGIN
    INSERT INTO dbo.ExternalAIProviderProfile (
        Provider, Endpoint, DefaultModel, SecretReference, AllowedSurfacesJson,
        RetentionMode, PolicyVersion, ApprovedBy, RiskAcceptanceRef,
        ReviewExpiresAt, IsActive, UpdatedBy
    )
    VALUES (
        'jina', 'https://api.jina.ai/v1', 'jina-reranker-v3', 'env:JINA_API_KEY',
        N'["reranking"]',
        'provider_default_no_training', 'evaluation-only-v1',
        'technical-evaluation-only',
        'codex-thread:019fab5f-2aa9-71d2-9bc1-0ecaf3b6d931',
        DATEADD(day, 30, GETDATE()), 0, 'V0040 migration'
    );

    SET @jina_profile_created = 1;
END

IF @jina_profile_created = 1
   AND OBJECT_ID('dbo.AuditLog', 'U') IS NOT NULL
   AND NOT EXISTS (
       SELECT 1
       FROM dbo.AuditLog
       WHERE Action = 'external_ai_jina_evaluation_profile_created'
         AND EntityType = 'ExternalAIProviderProfile'
   )
BEGIN
    INSERT INTO dbo.AuditLog (Username, Action, EntityType, Details)
    VALUES (
        'System',
        'external_ai_jina_evaluation_profile_created',
        'ExternalAIProviderProfile',
        N'{"provider":"jina","surface":"reranking","policy_version":"evaluation-only-v1","is_active":false,"scope":"bounded A/B evaluation only; explicit activation required","risk_acceptance_ref":"codex-thread:019fab5f-2aa9-71d2-9bc1-0ecaf3b6d931"}'
    );
END
GO

IF NOT EXISTS (SELECT 1 FROM dbo._SchemaVersions WHERE Version = 'V0040')
    INSERT INTO dbo._SchemaVersions (Version, Description, AppliedAt)
    VALUES ('V0040', 'Managed Jina reranker evaluation profile', GETDATE());
GO
