-- V0022: Department knowledge governance, domain profiles, and publication
-- serving epochs. Governance records are intentionally unassigned by default:
-- business owners/approvers must be selected explicitly by an administrator.

IF COL_LENGTH('dbo.TaiLieu', 'KnowledgeOwnerUserID') IS NULL
    ALTER TABLE dbo.TaiLieu ADD KnowledgeOwnerUserID INT NULL;
GO
IF COL_LENGTH('dbo.TaiLieu', 'KnowledgeApproverUserID') IS NULL
    ALTER TABLE dbo.TaiLieu ADD KnowledgeApproverUserID INT NULL;
GO
IF COL_LENGTH('dbo.TaiLieu', 'ParentApplicable') IS NULL
    ALTER TABLE dbo.TaiLieu ADD ParentApplicable BIT NOT NULL
        CONSTRAINT DF_TaiLieu_ParentApplicable DEFAULT 0;
GO
IF COL_LENGTH('dbo.TaiLieu', 'ServingEpoch') IS NULL
    ALTER TABLE dbo.TaiLieu ADD ServingEpoch BIGINT NOT NULL
        CONSTRAINT DF_TaiLieu_ServingEpoch DEFAULT 0;
GO
IF COL_LENGTH('dbo.TaiLieu', 'TaxonomyVersion') IS NULL
    ALTER TABLE dbo.TaiLieu ADD TaxonomyVersion NVARCHAR(100) NULL;
GO

UPDATE dbo.TaiLieu
SET ParentApplicable = CASE
        WHEN NULLIF(LTRIM(RTRIM(ParentSection)), '') IS NOT NULL OR ParentPage IS NOT NULL THEN 1
        ELSE 0
    END,
    ServingEpoch = CASE WHEN ISNULL(ServingEpoch, 0) = 0 THEN ISNULL(PublicationVersion, 0) ELSE ServingEpoch END;
GO

IF NOT EXISTS (
    SELECT 1 FROM sys.foreign_keys
    WHERE name = 'FK_TaiLieu_KnowledgeOwner_Users'
)
    ALTER TABLE dbo.TaiLieu ADD CONSTRAINT FK_TaiLieu_KnowledgeOwner_Users
        FOREIGN KEY (KnowledgeOwnerUserID) REFERENCES dbo.Users(UserID);
GO
IF NOT EXISTS (
    SELECT 1 FROM sys.foreign_keys
    WHERE name = 'FK_TaiLieu_KnowledgeApprover_Users'
)
    ALTER TABLE dbo.TaiLieu ADD CONSTRAINT FK_TaiLieu_KnowledgeApprover_Users
        FOREIGN KEY (KnowledgeApproverUserID) REFERENCES dbo.Users(UserID);
GO

IF OBJECT_ID('dbo.DepartmentKnowledgeGovernance', 'U') IS NULL
BEGIN
    CREATE TABLE dbo.DepartmentKnowledgeGovernance (
        DeptCode                NVARCHAR(255) NOT NULL PRIMARY KEY,
        KnowledgeOwnerUserID    INT NULL,
        KnowledgeApproverUserID INT NULL,
        TaxonomyVersion         NVARCHAR(100) NOT NULL CONSTRAINT DF_DepartmentKnowledgeGovernance_TaxonomyVersion DEFAULT 'v1',
        ExternalProcessingPolicy NVARCHAR(50) NOT NULL CONSTRAINT DF_DepartmentKnowledgeGovernance_ExternalPolicy DEFAULT 'all_external',
        IsActive                BIT NOT NULL CONSTRAINT DF_DepartmentKnowledgeGovernance_Active DEFAULT 1,
        CreatedAt               DATETIME NOT NULL CONSTRAINT DF_DepartmentKnowledgeGovernance_Created DEFAULT GETDATE(),
        UpdatedAt               DATETIME NOT NULL CONSTRAINT DF_DepartmentKnowledgeGovernance_Updated DEFAULT GETDATE(),
        UpdatedBy               NVARCHAR(100) NOT NULL CONSTRAINT DF_DepartmentKnowledgeGovernance_UpdatedBy DEFAULT 'System',
        CONSTRAINT FK_DepartmentKnowledgeGovernance_Department
            FOREIGN KEY (DeptCode) REFERENCES dbo.Departments(DeptCode),
        CONSTRAINT FK_DepartmentKnowledgeGovernance_Owner
            FOREIGN KEY (KnowledgeOwnerUserID) REFERENCES dbo.Users(UserID),
        CONSTRAINT FK_DepartmentKnowledgeGovernance_Approver
            FOREIGN KEY (KnowledgeApproverUserID) REFERENCES dbo.Users(UserID),
        CONSTRAINT CHK_DepartmentKnowledgeGovernance_Policy
            CHECK (ExternalProcessingPolicy IN ('all_external', 'internal_only'))
    );
END
GO

IF OBJECT_ID('dbo.DepartmentDomainProfile', 'U') IS NULL
BEGIN
    CREATE TABLE dbo.DepartmentDomainProfile (
        DeptCode               NVARCHAR(255) NOT NULL PRIMARY KEY,
        DocumentTypesJson      NVARCHAR(MAX) NOT NULL,
        RequiredMetadataJson   NVARCHAR(MAX) NOT NULL,
        RouterPatternsJson     NVARCHAR(MAX) NOT NULL,
        ParentContextEnabled   BIT NOT NULL CONSTRAINT DF_DepartmentDomainProfile_ParentContext DEFAULT 1,
        IsActive               BIT NOT NULL CONSTRAINT DF_DepartmentDomainProfile_Active DEFAULT 1,
        CreatedAt              DATETIME NOT NULL CONSTRAINT DF_DepartmentDomainProfile_Created DEFAULT GETDATE(),
        UpdatedAt              DATETIME NOT NULL CONSTRAINT DF_DepartmentDomainProfile_Updated DEFAULT GETDATE(),
        UpdatedBy              NVARCHAR(100) NOT NULL CONSTRAINT DF_DepartmentDomainProfile_UpdatedBy DEFAULT 'System',
        CONSTRAINT FK_DepartmentDomainProfile_Department
            FOREIGN KEY (DeptCode) REFERENCES dbo.Departments(DeptCode)
    );
END
GO

-- Bootstrap a profile for every known department. These are domain defaults,
-- not an approval assignment; administrators can tailor them per department.
INSERT INTO dbo.DepartmentDomainProfile (
    DeptCode, DocumentTypesJson, RequiredMetadataJson, RouterPatternsJson,
    ParentContextEnabled, IsActive, UpdatedBy
)
SELECT
    d.DeptCode,
    CASE LOWER(ISNULL(d.Domain, 'generic'))
        WHEN 'mechanical' THEN N'["technical_drawing","drawing","bom","technical_instruction","maintenance","quality_record","other"]'
        WHEN 'tabular' THEN N'["policy","procedure","decision","form","contract","purchase_order","quotation","spreadsheet","invoice","payroll","report","generic"]'
        ELSE N'["policy","procedure","decision","form","contract","purchase_order","quotation","guide","record","invoice","payroll","report","generic"]'
    END,
    N'["owner_department","shared_departments","domain","document_type","source_system","site","security_level","classification_rationale","classification_model","external_processing_policy"]',
    CASE LOWER(ISNULL(d.Domain, 'generic'))
        WHEN 'mechanical' THEN N'["drawing_code","version","bom","material","dimension"]'
        WHEN 'tabular' THEN N'["policy","procedure","contract","purchase_order","form"]'
        ELSE N'["policy","procedure","contract","purchase_order","form"]'
    END,
    1, 1, 'V0022 migration'
FROM dbo.Departments d
WHERE NOT EXISTS (
    SELECT 1 FROM dbo.DepartmentDomainProfile p WHERE p.DeptCode = d.DeptCode
);
GO

INSERT INTO dbo.DepartmentKnowledgeGovernance (
    DeptCode, TaxonomyVersion, ExternalProcessingPolicy, IsActive, UpdatedBy
)
SELECT d.DeptCode, 'v1', 'all_external', 1, 'V0022 migration'
FROM dbo.Departments d
WHERE NOT EXISTS (
    SELECT 1 FROM dbo.DepartmentKnowledgeGovernance g WHERE g.DeptCode = d.DeptCode
);
GO

-- A document inherits the active departmental taxonomy version until an
-- administrator intentionally overrides it during metadata review.
UPDATE t
SET TaxonomyVersion = g.TaxonomyVersion
FROM dbo.TaiLieu t
JOIN dbo.DepartmentKnowledgeGovernance g ON g.DeptCode = t.OwnerDepartment
WHERE NULLIF(LTRIM(RTRIM(t.TaxonomyVersion)), '') IS NULL;
GO

IF NOT EXISTS (
    SELECT 1 FROM sys.indexes
    WHERE object_id = OBJECT_ID('dbo.TaiLieu')
      AND name = 'IX_TaiLieu_KnowledgeGovernance'
)
    CREATE INDEX IX_TaiLieu_KnowledgeGovernance
        ON dbo.TaiLieu (OwnerDepartment, KnowledgeApproverUserID, Servable, PublicationState);
GO

IF OBJECT_ID('dbo.vwMissingSiteDocuments', 'V') IS NOT NULL
    DROP VIEW dbo.vwMissingSiteDocuments;
GO
CREATE VIEW dbo.vwMissingSiteDocuments
AS
SELECT t.DocID, t.TenFile, t.ThuMuc, t.OwnerDepartment, t.Domain,
       t.LifecycleStatus, t.ReviewStatus, t.PublicationState, t.NgayTaiLen
FROM dbo.TaiLieu t
WHERE NULLIF(LTRIM(RTRIM(t.Site)), '') IS NULL;
GO

IF NOT EXISTS (SELECT 1 FROM dbo._SchemaVersions WHERE Version = 'V0022')
    INSERT INTO dbo._SchemaVersions (Version, Description, AppliedAt)
    VALUES ('V0022', 'Knowledge governance, domain profiles, and serving epoch', GETDATE());
GO
