-- Activate only the exact migration-owned or prior thread-closed Jina profile.
-- Production remains blocked by the evaluation-only runtime policy.

DECLARE @jina_profile_activated BIT = 0;

IF (SELECT COUNT(*) FROM dbo.ExternalAIProviderProfile WHERE Provider = 'jina') <> 1
    THROW 51041, 'Expected exactly one Jina profile; refusing evaluation activation', 1;

IF NOT EXISTS (
    SELECT 1
    FROM dbo.ExternalAIProviderProfile
    WHERE Provider = 'jina'
      AND Endpoint = 'https://api.jina.ai/v1'
      AND DefaultModel = 'jina-reranker-v3'
      AND SecretReference = 'env:JINA_API_KEY'
      AND AllowedSurfacesJson = N'["reranking"]'
      AND RetentionMode = 'provider_default_no_training'
      AND PolicyVersion = 'evaluation-only-v1'
      AND ApprovedBy = 'technical-evaluation-only'
      AND RiskAcceptanceRef = 'codex-thread:019fab5f-2aa9-71d2-9bc1-0ecaf3b6d931'
      AND ReviewExpiresAt > GETDATE()
      AND ReviewExpiresAt <= DATEADD(day, 30, GETDATE())
      AND (
          (IsActive = 0 AND UpdatedBy = 'V0040 migration')
          OR (IsActive = 0 AND UpdatedBy = 'codex-eval-closeout')
          OR (IsActive = 1 AND UpdatedBy = 'V0041 migration')
      )
)
    THROW 51041, 'Jina profile does not match the V0040 evaluation profile; refusing activation', 1;

UPDATE dbo.ExternalAIProviderProfile
SET IsActive = 1,
    UpdatedAt = GETDATE(),
    UpdatedBy = 'V0041 migration'
WHERE Provider = 'jina'
  AND Endpoint = 'https://api.jina.ai/v1'
  AND DefaultModel = 'jina-reranker-v3'
  AND SecretReference = 'env:JINA_API_KEY'
  AND AllowedSurfacesJson = N'["reranking"]'
  AND RetentionMode = 'provider_default_no_training'
  AND PolicyVersion = 'evaluation-only-v1'
  AND ApprovedBy = 'technical-evaluation-only'
  AND RiskAcceptanceRef = 'codex-thread:019fab5f-2aa9-71d2-9bc1-0ecaf3b6d931'
  AND ReviewExpiresAt > GETDATE()
  AND ReviewExpiresAt <= DATEADD(day, 30, GETDATE())
  AND IsActive = 0
  AND UpdatedBy IN ('V0040 migration', 'codex-eval-closeout');

IF @@ROWCOUNT = 1
    SET @jina_profile_activated = 1;

IF @jina_profile_activated = 1
   AND OBJECT_ID('dbo.AuditLog', 'U') IS NOT NULL
   AND NOT EXISTS (
       SELECT 1
       FROM dbo.AuditLog
       WHERE Action = 'external_ai_jina_evaluation_profile_activated'
         AND EntityType = 'ExternalAIProviderProfile'
   )
BEGIN
    INSERT INTO dbo.AuditLog (Username, Action, EntityType, Details)
    VALUES (
        'System',
        'external_ai_jina_evaluation_profile_activated',
        'ExternalAIProviderProfile',
        N'{"provider":"jina","surface":"reranking","policy_version":"evaluation-only-v1","is_active":true,"scope":"bounded A/B evaluation only","risk_acceptance_ref":"codex-thread:019fab5f-2aa9-71d2-9bc1-0ecaf3b6d931"}'
    );
END
GO

IF NOT EXISTS (SELECT 1 FROM dbo._SchemaVersions WHERE Version = 'V0041')
    INSERT INTO dbo._SchemaVersions (Version, Description, AppliedAt)
    VALUES ('V0041', 'Activate evaluation-only Jina reranker profile', GETDATE());
GO
