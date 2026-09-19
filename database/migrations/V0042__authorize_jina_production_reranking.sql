-- Promote the exact evaluation-owned Jina profile to owner-approved production
-- reranking. Voyage remains the runtime fallback; no secret is stored here.

IF (SELECT COUNT(*) FROM dbo.ExternalAIProviderProfile WHERE Provider = 'jina') <> 1
    THROW 51042, 'Expected exactly one Jina profile; refusing production authorization', 1;
GO

IF NOT EXISTS (
    SELECT 1
    FROM dbo.ExternalAIProviderProfile
    WHERE Provider = 'jina'
      AND Endpoint = 'https://api.jina.ai/v1'
      AND DefaultModel = 'jina-reranker-v3'
      AND SecretReference = 'env:JINA_API_KEY'
      AND AllowedSurfacesJson = N'["reranking"]'
      AND RetentionMode = 'provider_default_no_training'
      AND IsActive = 1
      AND (
          (
              PolicyVersion = 'evaluation-only-v1'
              AND ApprovedBy = 'technical-evaluation-only'
              AND RiskAcceptanceRef = 'codex-thread:019fab5f-2aa9-71d2-9bc1-0ecaf3b6d931'
              AND UpdatedBy = 'V0041 migration'
          )
          OR (
              PolicyVersion = 'risk-accepted-v1-jina-production'
              AND ApprovedBy = 'owner-production-approval'
              AND RiskAcceptanceRef = 'owner-decision:2026-07-31:jina-primary-voyage-fallback'
              AND ReviewExpiresAt > GETDATE()
              AND UpdatedBy = 'V0042 migration'
          )
      )
)
    THROW 51042, 'Jina profile does not match the governed evaluation profile; refusing production authorization', 1;
GO

UPDATE dbo.ExternalAIProviderProfile
SET PolicyVersion = 'risk-accepted-v1-jina-production',
    ApprovedBy = 'owner-production-approval',
    RiskAcceptanceRef = 'owner-decision:2026-07-31:jina-primary-voyage-fallback',
    ReviewExpiresAt = DATEADD(day, 90, GETDATE()),
    UpdatedAt = GETDATE(),
    UpdatedBy = 'V0042 migration'
WHERE Provider = 'jina'
  AND Endpoint = 'https://api.jina.ai/v1'
  AND DefaultModel = 'jina-reranker-v3'
  AND SecretReference = 'env:JINA_API_KEY'
  AND AllowedSurfacesJson = N'["reranking"]'
  AND RetentionMode = 'provider_default_no_training'
  AND PolicyVersion = 'evaluation-only-v1'
  AND ApprovedBy = 'technical-evaluation-only'
  AND RiskAcceptanceRef = 'codex-thread:019fab5f-2aa9-71d2-9bc1-0ecaf3b6d931'
  AND IsActive = 1
  AND UpdatedBy = 'V0041 migration';
GO

IF NOT EXISTS (
    SELECT 1
    FROM dbo.ExternalAIProviderProfile
    WHERE Provider = 'jina'
      AND Endpoint = 'https://api.jina.ai/v1'
      AND DefaultModel = 'jina-reranker-v3'
      AND SecretReference = 'env:JINA_API_KEY'
      AND AllowedSurfacesJson = N'["reranking"]'
      AND RetentionMode = 'provider_default_no_training'
      AND PolicyVersion = 'risk-accepted-v1-jina-production'
      AND ApprovedBy = 'owner-production-approval'
      AND RiskAcceptanceRef = 'owner-decision:2026-07-31:jina-primary-voyage-fallback'
      AND ReviewExpiresAt > GETDATE()
      AND ReviewExpiresAt <= DATEADD(day, 90, GETDATE())
      AND IsActive = 1
      AND UpdatedBy = 'V0042 migration'
)
    THROW 51042, 'Jina production authorization could not be verified', 1;
GO

IF OBJECT_ID('dbo.AuditLog', 'U') IS NOT NULL
   AND NOT EXISTS (
       SELECT 1
       FROM dbo.AuditLog
       WHERE Action = 'external_ai_jina_production_authorized'
         AND EntityType = 'ExternalAIProviderProfile'
   )
BEGIN
    INSERT INTO dbo.AuditLog (Username, Action, EntityType, Details)
    VALUES (
        'System',
        'external_ai_jina_production_authorized',
        'ExternalAIProviderProfile',
        N'{"provider":"jina","surface":"reranking","policy_version":"risk-accepted-v1-jina-production","is_active":true,"scope":"production primary reranker with Voyage fallback","risk_acceptance_ref":"owner-decision:2026-07-31:jina-primary-voyage-fallback"}'
    );
END
GO

IF NOT EXISTS (SELECT 1 FROM dbo._SchemaVersions WHERE Version = 'V0042')
    INSERT INTO dbo._SchemaVersions (Version, Description, AppliedAt)
    VALUES ('V0042', 'Authorize Jina production reranking', GETDATE());
GO
