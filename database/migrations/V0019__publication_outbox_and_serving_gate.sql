-- V0019: Publication contract, transactional outbox, and fail-closed serving gate.
-- Idempotent; safe to run more than once.

IF COL_LENGTH('dbo.TaiLieu', 'OwnerDepartment') IS NULL
    ALTER TABLE dbo.TaiLieu ADD OwnerDepartment NVARCHAR(50) NULL;
GO
IF COL_LENGTH('dbo.TaiLieu', 'SourceSystem') IS NULL
    ALTER TABLE dbo.TaiLieu ADD SourceSystem NVARCHAR(100) NULL;
GO
IF COL_LENGTH('dbo.TaiLieu', 'ParentSection') IS NULL
    ALTER TABLE dbo.TaiLieu ADD ParentSection NVARCHAR(500) NULL;
GO
IF COL_LENGTH('dbo.TaiLieu', 'ParentPage') IS NULL
    ALTER TABLE dbo.TaiLieu ADD ParentPage INT NULL;
GO
IF COL_LENGTH('dbo.TaiLieu', 'ExternalProcessingPolicy') IS NULL
    ALTER TABLE dbo.TaiLieu ADD ExternalProcessingPolicy NVARCHAR(50) NULL;
GO
IF COL_LENGTH('dbo.TaiLieu', 'ClassificationRationale') IS NULL
    ALTER TABLE dbo.TaiLieu ADD ClassificationRationale NVARCHAR(1000) NULL;
GO
IF COL_LENGTH('dbo.TaiLieu', 'ClassificationModel') IS NULL
    ALTER TABLE dbo.TaiLieu ADD ClassificationModel NVARCHAR(100) NULL;
GO
IF COL_LENGTH('dbo.TaiLieu', 'PublicationState') IS NULL
    ALTER TABLE dbo.TaiLieu ADD PublicationState NVARCHAR(30) NULL;
GO
IF COL_LENGTH('dbo.TaiLieu', 'PublicationVersion') IS NULL
    ALTER TABLE dbo.TaiLieu ADD PublicationVersion INT NULL;
GO
IF COL_LENGTH('dbo.TaiLieu', 'PublicationError') IS NULL
    ALTER TABLE dbo.TaiLieu ADD PublicationError NVARCHAR(2000) NULL;
GO
IF COL_LENGTH('dbo.TaiLieu', 'PublicationRetryCount') IS NULL
    ALTER TABLE dbo.TaiLieu ADD PublicationRetryCount INT NULL;
GO
IF COL_LENGTH('dbo.TaiLieu', 'PublicationUpdatedAt') IS NULL
    ALTER TABLE dbo.TaiLieu ADD PublicationUpdatedAt DATETIME NULL;
GO
IF COL_LENGTH('dbo.TaiLieu', 'Servable') IS NULL
    ALTER TABLE dbo.TaiLieu ADD Servable BIT NULL;
GO

-- Backfill legacy documents without changing lifecycle/version semantics.
UPDATE t
SET OwnerDepartment = COALESCE(
        NULLIF(LTRIM(RTRIM(t.OwnerDepartment)), ''),
        NULLIF(LTRIM(RTRIM(t.ThuMuc)), ''),
        (SELECT TOP 1 p.DeptCode
         FROM dbo.PhongBanChiaSe p
         WHERE p.DocID = t.DocID AND p.DeptCode <> 'CHUNG'
         ORDER BY p.DeptCode),
        'CHUNG'
    ),
    SourceSystem = COALESCE(NULLIF(LTRIM(RTRIM(t.SourceSystem)), ''), 'legacy_upload'),
    ExternalProcessingPolicy = COALESCE(
        NULLIF(LTRIM(RTRIM(t.ExternalProcessingPolicy)), ''),
        'all_external'
    ),
    ClassificationRationale = COALESCE(
        NULLIF(LTRIM(RTRIM(t.ClassificationRationale)), ''),
        'legacy_backfill'
    ),
    ClassificationModel = COALESCE(
        NULLIF(LTRIM(RTRIM(t.ClassificationModel)), ''),
        'legacy'
    ),
    PublicationState = COALESCE(
        NULLIF(LTRIM(RTRIM(t.PublicationState)), ''),
        CASE
            WHEN t.LifecycleStatus = 'published'
             AND t.ReviewStatus = 'approved'
             AND t.IsCurrent = 1 THEN 'published'
            WHEN t.ReviewStatus = 'rejected' THEN 'failed'
            ELSE 'draft'
        END
    ),
    PublicationVersion = ISNULL(t.PublicationVersion, 1),
    PublicationRetryCount = ISNULL(t.PublicationRetryCount, 0),
    PublicationUpdatedAt = ISNULL(t.PublicationUpdatedAt, GETDATE()),
    Servable = ISNULL(
        t.Servable,
        CASE
            WHEN t.LifecycleStatus = 'published'
             AND t.ReviewStatus = 'approved'
             AND t.IsCurrent = 1 THEN 1
            ELSE 0
        END
    )
FROM dbo.TaiLieu t;
GO

ALTER TABLE dbo.TaiLieu ALTER COLUMN OwnerDepartment NVARCHAR(50) NOT NULL;
GO
ALTER TABLE dbo.TaiLieu ALTER COLUMN SourceSystem NVARCHAR(100) NOT NULL;
GO
ALTER TABLE dbo.TaiLieu ALTER COLUMN ExternalProcessingPolicy NVARCHAR(50) NOT NULL;
GO
ALTER TABLE dbo.TaiLieu ALTER COLUMN ClassificationRationale NVARCHAR(1000) NOT NULL;
GO
ALTER TABLE dbo.TaiLieu ALTER COLUMN ClassificationModel NVARCHAR(100) NOT NULL;
GO
ALTER TABLE dbo.TaiLieu ALTER COLUMN PublicationState NVARCHAR(30) NOT NULL;
GO
ALTER TABLE dbo.TaiLieu ALTER COLUMN PublicationVersion INT NOT NULL;
GO
ALTER TABLE dbo.TaiLieu ALTER COLUMN PublicationRetryCount INT NOT NULL;
GO
ALTER TABLE dbo.TaiLieu ALTER COLUMN PublicationUpdatedAt DATETIME NOT NULL;
GO
ALTER TABLE dbo.TaiLieu ALTER COLUMN Servable BIT NOT NULL;
GO

IF NOT EXISTS (
    SELECT 1 FROM sys.default_constraints
    WHERE parent_object_id = OBJECT_ID('dbo.TaiLieu')
      AND name = 'DF_TaiLieu_OwnerDepartment'
)
    ALTER TABLE dbo.TaiLieu ADD CONSTRAINT DF_TaiLieu_OwnerDepartment DEFAULT 'CHUNG' FOR OwnerDepartment;
GO
IF NOT EXISTS (
    SELECT 1 FROM sys.default_constraints
    WHERE parent_object_id = OBJECT_ID('dbo.TaiLieu')
      AND name = 'DF_TaiLieu_SourceSystem'
)
    ALTER TABLE dbo.TaiLieu ADD CONSTRAINT DF_TaiLieu_SourceSystem DEFAULT 'upload' FOR SourceSystem;
GO
IF NOT EXISTS (
    SELECT 1 FROM sys.default_constraints
    WHERE parent_object_id = OBJECT_ID('dbo.TaiLieu')
      AND name = 'DF_TaiLieu_ExternalProcessingPolicy'
)
    ALTER TABLE dbo.TaiLieu ADD CONSTRAINT DF_TaiLieu_ExternalProcessingPolicy DEFAULT 'all_external' FOR ExternalProcessingPolicy;
GO
IF NOT EXISTS (
    SELECT 1 FROM sys.default_constraints
    WHERE parent_object_id = OBJECT_ID('dbo.TaiLieu')
      AND name = 'DF_TaiLieu_ClassificationRationale'
)
    ALTER TABLE dbo.TaiLieu ADD CONSTRAINT DF_TaiLieu_ClassificationRationale DEFAULT 'pending_classification' FOR ClassificationRationale;
GO
IF NOT EXISTS (
    SELECT 1 FROM sys.default_constraints
    WHERE parent_object_id = OBJECT_ID('dbo.TaiLieu')
      AND name = 'DF_TaiLieu_ClassificationModel'
)
    ALTER TABLE dbo.TaiLieu ADD CONSTRAINT DF_TaiLieu_ClassificationModel DEFAULT 'unknown' FOR ClassificationModel;
GO
IF NOT EXISTS (
    SELECT 1 FROM sys.default_constraints
    WHERE parent_object_id = OBJECT_ID('dbo.TaiLieu')
      AND name = 'DF_TaiLieu_PublicationState'
)
    ALTER TABLE dbo.TaiLieu ADD CONSTRAINT DF_TaiLieu_PublicationState DEFAULT 'draft' FOR PublicationState;
GO
IF NOT EXISTS (
    SELECT 1 FROM sys.default_constraints
    WHERE parent_object_id = OBJECT_ID('dbo.TaiLieu')
      AND name = 'DF_TaiLieu_PublicationVersion'
)
    ALTER TABLE dbo.TaiLieu ADD CONSTRAINT DF_TaiLieu_PublicationVersion DEFAULT 1 FOR PublicationVersion;
GO
IF NOT EXISTS (
    SELECT 1 FROM sys.default_constraints
    WHERE parent_object_id = OBJECT_ID('dbo.TaiLieu')
      AND name = 'DF_TaiLieu_PublicationRetryCount'
)
    ALTER TABLE dbo.TaiLieu ADD CONSTRAINT DF_TaiLieu_PublicationRetryCount DEFAULT 0 FOR PublicationRetryCount;
GO
IF NOT EXISTS (
    SELECT 1 FROM sys.default_constraints
    WHERE parent_object_id = OBJECT_ID('dbo.TaiLieu')
      AND name = 'DF_TaiLieu_PublicationUpdatedAt'
)
    ALTER TABLE dbo.TaiLieu ADD CONSTRAINT DF_TaiLieu_PublicationUpdatedAt DEFAULT GETDATE() FOR PublicationUpdatedAt;
GO
IF NOT EXISTS (
    SELECT 1 FROM sys.default_constraints
    WHERE parent_object_id = OBJECT_ID('dbo.TaiLieu')
      AND name = 'DF_TaiLieu_Servable'
)
    ALTER TABLE dbo.TaiLieu ADD CONSTRAINT DF_TaiLieu_Servable DEFAULT 0 FOR Servable;
GO

IF NOT EXISTS (
    SELECT 1 FROM sys.check_constraints
    WHERE parent_object_id = OBJECT_ID('dbo.TaiLieu')
      AND name = 'CHK_TaiLieu_PublicationState'
)
    ALTER TABLE dbo.TaiLieu ADD CONSTRAINT CHK_TaiLieu_PublicationState
        CHECK (PublicationState IN ('draft','validated','publishing','qdrant_synced','published','failed'));
GO

IF NOT EXISTS (
    SELECT 1 FROM sys.indexes
    WHERE object_id = OBJECT_ID('dbo.TaiLieu')
      AND name = 'IX_TaiLieu_Serving'
)
    CREATE INDEX IX_TaiLieu_Serving
        ON dbo.TaiLieu (Servable, PublicationState, LifecycleStatus, ReviewStatus, IsCurrent);
GO

-- Backfill user-site grants from authoritative department membership before
-- switching site filtering to fail-closed.
INSERT INTO dbo.UserSites (UserID, Site)
SELECT DISTINCT ud.UserID, d.Site
FROM dbo.UserDepartments ud
JOIN dbo.Departments d ON d.DeptCode = ud.Department
WHERE d.Site IS NOT NULL AND LTRIM(RTRIM(d.Site)) <> ''
  AND NOT EXISTS (
      SELECT 1 FROM dbo.UserSites us
      WHERE us.UserID = ud.UserID AND us.Site = d.Site
  );
GO

IF OBJECT_ID('dbo.PublicationOutbox', 'U') IS NULL
BEGIN
    CREATE TABLE dbo.PublicationOutbox (
        OutboxID       BIGINT IDENTITY(1,1) NOT NULL PRIMARY KEY,
        DocID          INT NOT NULL,
        Action         NVARCHAR(50) NOT NULL,
        PayloadJson    NVARCHAR(MAX) NULL,
        IdempotencyKey NVARCHAR(120) NOT NULL,
        Status         NVARCHAR(20) NOT NULL CONSTRAINT DF_PublicationOutbox_Status DEFAULT 'pending',
        AttemptCount   INT NOT NULL CONSTRAINT DF_PublicationOutbox_Attempt DEFAULT 0,
        LastError      NVARCHAR(2000) NULL,
        AvailableAt    DATETIME NOT NULL CONSTRAINT DF_PublicationOutbox_Available DEFAULT GETDATE(),
        LockedAt       DATETIME NULL,
        LockedBy       NVARCHAR(100) NULL,
        CreatedAt      DATETIME NOT NULL CONSTRAINT DF_PublicationOutbox_Created DEFAULT GETDATE(),
        UpdatedAt      DATETIME NOT NULL CONSTRAINT DF_PublicationOutbox_Updated DEFAULT GETDATE(),
        CompletedAt    DATETIME NULL,
        CONSTRAINT FK_PublicationOutbox_TaiLieu
            FOREIGN KEY (DocID) REFERENCES dbo.TaiLieu(DocID) ON DELETE CASCADE,
        CONSTRAINT UQ_PublicationOutbox_Idempotency UNIQUE (IdempotencyKey),
        CONSTRAINT CHK_PublicationOutbox_Status
            CHECK (Status IN ('pending','processing','done','failed'))
    );
END
GO

IF NOT EXISTS (
    SELECT 1 FROM sys.indexes
    WHERE object_id = OBJECT_ID('dbo.PublicationOutbox')
      AND name = 'IX_PublicationOutbox_Pending'
)
    CREATE INDEX IX_PublicationOutbox_Pending
        ON dbo.PublicationOutbox (Status, AvailableAt, CreatedAt)
        INCLUDE (DocID, Action, AttemptCount);
GO

IF OBJECT_ID('dbo.ExternalAICallAudit', 'U') IS NULL
BEGIN
    CREATE TABLE dbo.ExternalAICallAudit (
        ExternalCallID BIGINT IDENTITY(1,1) NOT NULL PRIMARY KEY,
        TraceID        NVARCHAR(100) NULL,
        Provider       NVARCHAR(100) NOT NULL,
        Model          NVARCHAR(150) NULL,
        Surface        NVARCHAR(50) NOT NULL,
        DocIDsJson     NVARCHAR(MAX) NULL,
        SecurityJson   NVARCHAR(500) NULL,
        InputChars     INT NULL,
        Status         NVARCHAR(30) NOT NULL,
        LatencyMs      INT NULL,
        ErrorType      NVARCHAR(100) NULL,
        CreatedAt      DATETIME NOT NULL CONSTRAINT DF_ExternalAICallAudit_Created DEFAULT GETDATE()
    );
END
GO

IF NOT EXISTS (
    SELECT 1 FROM sys.indexes
    WHERE object_id = OBJECT_ID('dbo.ExternalAICallAudit')
      AND name = 'IX_ExternalAICallAudit_Trace'
)
    CREATE INDEX IX_ExternalAICallAudit_Trace
        ON dbo.ExternalAICallAudit (TraceID, CreatedAt DESC);
GO

IF NOT EXISTS (SELECT 1 FROM dbo._SchemaVersions WHERE Version = 'V0019')
    INSERT INTO dbo._SchemaVersions (Version, Description, AppliedAt)
    VALUES ('V0019', 'Publication outbox, serving gate, and external AI audit', GETDATE());
GO
