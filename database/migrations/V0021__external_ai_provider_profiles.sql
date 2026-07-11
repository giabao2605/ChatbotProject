-- V0021: External provider profiles, risk-acceptance record, and richer
-- metadata-only auditing. No secret values or raw prompts are stored here.

IF OBJECT_ID('dbo.ExternalAIProviderProfile', 'U') IS NULL
BEGIN
    CREATE TABLE dbo.ExternalAIProviderProfile (
        Provider             NVARCHAR(100) NOT NULL PRIMARY KEY,
        Endpoint             NVARCHAR(500) NOT NULL,
        DefaultModel         NVARCHAR(150) NOT NULL,
        SecretReference      NVARCHAR(200) NOT NULL,
        AllowedSurfacesJson  NVARCHAR(MAX) NOT NULL,
        RetentionMode        NVARCHAR(100) NOT NULL,
        PolicyVersion        NVARCHAR(100) NOT NULL,
        ApprovedBy           NVARCHAR(200) NOT NULL,
        RiskAcceptanceRef    NVARCHAR(500) NOT NULL,
        ReviewExpiresAt      DATETIME NOT NULL,
        IsActive             BIT NOT NULL CONSTRAINT DF_ExternalAIProviderProfile_Active DEFAULT 1,
        CreatedAt            DATETIME NOT NULL CONSTRAINT DF_ExternalAIProviderProfile_Created DEFAULT GETDATE(),
        UpdatedAt            DATETIME NOT NULL CONSTRAINT DF_ExternalAIProviderProfile_Updated DEFAULT GETDATE(),
        UpdatedBy            NVARCHAR(100) NOT NULL CONSTRAINT DF_ExternalAIProviderProfile_UpdatedBy DEFAULT 'System'
    );
END
GO

IF NOT EXISTS (
    SELECT 1 FROM sys.indexes
    WHERE object_id = OBJECT_ID('dbo.ExternalAIProviderProfile')
      AND name = 'IX_ExternalAIProviderProfile_Review'
)
    CREATE INDEX IX_ExternalAIProviderProfile_Review
        ON dbo.ExternalAIProviderProfile (IsActive, ReviewExpiresAt);
GO

IF COL_LENGTH('dbo.ExternalAICallAudit', 'ActorUsername') IS NULL
    ALTER TABLE dbo.ExternalAICallAudit ADD ActorUsername NVARCHAR(100) NULL;
GO
IF COL_LENGTH('dbo.ExternalAICallAudit', 'ActorIsAdmin') IS NULL
    ALTER TABLE dbo.ExternalAICallAudit ADD ActorIsAdmin BIT NULL;
GO
IF COL_LENGTH('dbo.ExternalAICallAudit', 'Endpoint') IS NULL
    ALTER TABLE dbo.ExternalAICallAudit ADD Endpoint NVARCHAR(500) NULL;
GO
IF COL_LENGTH('dbo.ExternalAICallAudit', 'PolicyVersion') IS NULL
    ALTER TABLE dbo.ExternalAICallAudit ADD PolicyVersion NVARCHAR(100) NULL;
GO
IF COL_LENGTH('dbo.ExternalAICallAudit', 'RetentionMode') IS NULL
    ALTER TABLE dbo.ExternalAICallAudit ADD RetentionMode NVARCHAR(100) NULL;
GO
IF COL_LENGTH('dbo.ExternalAICallAudit', 'RiskAcceptanceRef') IS NULL
    ALTER TABLE dbo.ExternalAICallAudit ADD RiskAcceptanceRef NVARCHAR(500) NULL;
GO
IF COL_LENGTH('dbo.ExternalAICallAudit', 'InputBytes') IS NULL
    ALTER TABLE dbo.ExternalAICallAudit ADD InputBytes BIGINT NULL;
GO
IF COL_LENGTH('dbo.ExternalAICallAudit', 'InputTokenEstimate') IS NULL
    ALTER TABLE dbo.ExternalAICallAudit ADD InputTokenEstimate INT NULL;
GO

DECLARE @risk_ref NVARCHAR(500) = N'notion:92459b78-3e54-4c47-8322-d44ab2b65664';
DECLARE @policy_version NVARCHAR(100) = N'risk-accepted-v3';

IF NOT EXISTS (SELECT 1 FROM dbo.ExternalAIProviderProfile WHERE Provider = 'proxyllm')
BEGIN
    INSERT INTO dbo.ExternalAIProviderProfile (
        Provider, Endpoint, DefaultModel, SecretReference, AllowedSurfacesJson,
        RetentionMode, PolicyVersion, ApprovedBy, RiskAcceptanceRef,
        ReviewExpiresAt, IsActive, UpdatedBy
    )
    VALUES (
        'proxyllm', 'https://api.proxyllm.eu/v1', 'gpt-5.4', 'env:PROXYLLM_API_KEY',
        N'["document_classification","intent_routing","query_disambiguation","chat_history_summary","hyde","interaction_routing","evidence_verification","generation","vision_ocr"]',
        'provider_default_no_opt_out', @policy_version, 'documented-risk-acceptance', @risk_ref,
        DATEADD(day, 90, GETDATE()), 1, 'V0021 migration'
    );
END

IF NOT EXISTS (SELECT 1 FROM dbo.ExternalAIProviderProfile WHERE Provider = 'voyage')
BEGIN
    INSERT INTO dbo.ExternalAIProviderProfile (
        Provider, Endpoint, DefaultModel, SecretReference, AllowedSurfacesJson,
        RetentionMode, PolicyVersion, ApprovedBy, RiskAcceptanceRef,
        ReviewExpiresAt, IsActive, UpdatedBy
    )
    VALUES (
        'voyage', 'https://api.voyageai.com/v1', 'rerank-2.5-lite', 'env:VOYAGE_API_KEY',
        N'["reranking"]',
        'provider_default_no_opt_out', @policy_version, 'documented-risk-acceptance', @risk_ref,
        DATEADD(day, 90, GETDATE()), 1, 'V0021 migration'
    );
END
GO

IF OBJECT_ID('dbo.AuditLog', 'U') IS NOT NULL
   AND NOT EXISTS (
       SELECT 1
       FROM dbo.AuditLog
       WHERE Action = 'external_ai_risk_acceptance_v3'
         AND EntityType = 'ExternalAIProviderProfile'
   )
BEGIN
    INSERT INTO dbo.AuditLog (Username, Action, EntityType, Details)
    VALUES (
        'System',
        'external_ai_risk_acceptance_v3',
        'ExternalAIProviderProfile',
        N'{"policy_version":"risk-accepted-v3","risk_acceptance_ref":"notion:92459b78-3e54-4c47-8322-d44ab2b65664","scope":"confidential data may be processed by configured external providers"}'
    );
END
GO

IF NOT EXISTS (SELECT 1 FROM dbo._SchemaVersions WHERE Version = 'V0021')
    INSERT INTO dbo._SchemaVersions (Version, Description, AppliedAt)
    VALUES ('V0021', 'External AI provider profiles and audit metadata', GETDATE());
GO
