-- V0023: Configuration and evidence records for the 3 -> 4 -> 4 -> 4
-- department rollout. No department is auto-enabled by this migration.

IF OBJECT_ID('dbo.DepartmentRolloutPlan', 'U') IS NULL
BEGIN
    CREATE TABLE dbo.DepartmentRolloutPlan (
        DeptCode                NVARCHAR(255) NOT NULL PRIMARY KEY,
        WaveNumber              TINYINT NOT NULL,
        RolloutStatus           NVARCHAR(30) NOT NULL
            CONSTRAINT DF_DepartmentRolloutPlan_Status DEFAULT 'planned',
        EvaluationQuestionTarget INT NOT NULL
            CONSTRAINT DF_DepartmentRolloutPlan_EvalTarget DEFAULT 75,
        DarkLaunchStartedAt     DATETIME NULL,
        ActivatedAt             DATETIME NULL,
        UpdatedAt               DATETIME NOT NULL
            CONSTRAINT DF_DepartmentRolloutPlan_Updated DEFAULT GETDATE(),
        UpdatedBy               NVARCHAR(100) NOT NULL
            CONSTRAINT DF_DepartmentRolloutPlan_UpdatedBy DEFAULT 'System',
        CONSTRAINT FK_DepartmentRolloutPlan_Department
            FOREIGN KEY (DeptCode) REFERENCES dbo.Departments(DeptCode),
        CONSTRAINT CHK_DepartmentRolloutPlan_Wave
            CHECK (WaveNumber BETWEEN 1 AND 4),
        CONSTRAINT CHK_DepartmentRolloutPlan_Status
            CHECK (RolloutStatus IN ('planned', 'pilot', 'dark_launch', 'active', 'blocked')),
        CONSTRAINT CHK_DepartmentRolloutPlan_EvalTarget
            CHECK (EvaluationQuestionTarget >= 75)
    );
END
GO

IF OBJECT_ID('dbo.DepartmentEvaluationGate', 'U') IS NULL
BEGIN
    CREATE TABLE dbo.DepartmentEvaluationGate (
        GateID                    BIGINT IDENTITY(1,1) NOT NULL PRIMARY KEY,
        DeptCode                  NVARCHAR(255) NOT NULL,
        BatchID                   NVARCHAR(100) NOT NULL,
        QuestionCount             INT NOT NULL,
        SourceTop5Rate            DECIMAL(6,5) NOT NULL,
        CitationOrRefusalRate     DECIMAL(6,5) NOT NULL,
        EvidenceSupportRate       DECIMAL(6,5) NOT NULL,
        RbacSitePublicationLeaks  INT NOT NULL CONSTRAINT DF_DepartmentEvaluationGate_Leaks DEFAULT 0,
        Passed                    BIT NOT NULL,
        Notes                     NVARCHAR(2000) NULL,
        EvaluatedAt               DATETIME NOT NULL CONSTRAINT DF_DepartmentEvaluationGate_Evaluated DEFAULT GETDATE(),
        EvaluatedBy               NVARCHAR(100) NOT NULL CONSTRAINT DF_DepartmentEvaluationGate_EvaluatedBy DEFAULT 'System',
        CONSTRAINT FK_DepartmentEvaluationGate_Department
            FOREIGN KEY (DeptCode) REFERENCES dbo.Departments(DeptCode),
        CONSTRAINT CHK_DepartmentEvaluationGate_Rates
            CHECK (
                SourceTop5Rate BETWEEN 0 AND 1
                AND CitationOrRefusalRate BETWEEN 0 AND 1
                AND EvidenceSupportRate BETWEEN 0 AND 1
                AND RbacSitePublicationLeaks >= 0
            )
    );
END
GO

IF NOT EXISTS (
    SELECT 1 FROM sys.indexes
    WHERE object_id = OBJECT_ID('dbo.DepartmentEvaluationGate')
      AND name = 'IX_DepartmentEvaluationGate_Latest'
)
    CREATE INDEX IX_DepartmentEvaluationGate_Latest
        ON dbo.DepartmentEvaluationGate (DeptCode, EvaluatedAt DESC, GateID DESC);
GO

IF NOT EXISTS (SELECT 1 FROM dbo._SchemaVersions WHERE Version = 'V0023')
    INSERT INTO dbo._SchemaVersions (Version, Description, AppliedAt)
    VALUES ('V0023', 'Department rollout plan and evaluation gates', GETDATE());
GO
