-- V0030: Enforce rollout invariants at the database boundary and reconcile
-- migration-managed profiles for inactive legacy departments.

IF COL_LENGTH('dbo.RegressionQuestion', 'DemoBatchID') IS NULL
    ALTER TABLE dbo.RegressionQuestion ADD DemoBatchID NVARCHAR(64) NULL;
IF COL_LENGTH('dbo.RegressionQuestion', 'CaseID') IS NULL
    ALTER TABLE dbo.RegressionQuestion ADD CaseID NVARCHAR(150) NULL;
IF COL_LENGTH('dbo.RegressionQuestion', 'Scenario') IS NULL
    ALTER TABLE dbo.RegressionQuestion ADD Scenario NVARCHAR(50) NULL;
IF COL_LENGTH('dbo.RegressionQuestion', 'ExpectedBehavior') IS NULL
    ALTER TABLE dbo.RegressionQuestion ADD ExpectedBehavior NVARCHAR(30) NULL;
IF COL_LENGTH('dbo.RegressionQuestion', 'ExpectedReference') IS NULL
    ALTER TABLE dbo.RegressionQuestion ADD ExpectedReference NVARCHAR(255) NULL;
IF COL_LENGTH('dbo.RegressionQuestion', 'CaseJson') IS NULL
    ALTER TABLE dbo.RegressionQuestion ADD CaseJson NVARCHAR(MAX) NULL;
GO

IF NOT EXISTS (
    SELECT 1 FROM sys.indexes
    WHERE object_id = OBJECT_ID('dbo.RegressionQuestion')
      AND name = 'UX_RegressionQuestion_DemoBatch_Case'
)
    CREATE UNIQUE INDEX UX_RegressionQuestion_DemoBatch_Case
        ON dbo.RegressionQuestion (DemoBatchID, CaseID)
        WHERE DemoBatchID IS NOT NULL AND CaseID IS NOT NULL;
GO

IF COL_LENGTH('dbo.RegressionRun', 'CitationOrRefusalHit') IS NULL
    ALTER TABLE dbo.RegressionRun ADD CitationOrRefusalHit BIT NULL;
IF COL_LENGTH('dbo.RegressionRun', 'EvidenceSupported') IS NULL
    ALTER TABLE dbo.RegressionRun ADD EvidenceSupported BIT NULL;
IF COL_LENGTH('dbo.RegressionRun', 'LeakageDetected') IS NULL
    ALTER TABLE dbo.RegressionRun ADD LeakageDetected BIT NULL;
GO

IF NOT EXISTS (
    SELECT 1 FROM sys.check_constraints
    WHERE name = 'CHK_DepartmentDomainProfile_DocumentTypesJson'
)
    ALTER TABLE dbo.DepartmentDomainProfile WITH CHECK ADD CONSTRAINT
        CHK_DepartmentDomainProfile_DocumentTypesJson CHECK (ISJSON(DocumentTypesJson) = 1);
GO

IF NOT EXISTS (
    SELECT 1 FROM sys.check_constraints
    WHERE name = 'CHK_DepartmentDomainProfile_RequiredMetadataJson'
)
    ALTER TABLE dbo.DepartmentDomainProfile WITH CHECK ADD CONSTRAINT
        CHK_DepartmentDomainProfile_RequiredMetadataJson CHECK (ISJSON(RequiredMetadataJson) = 1);
GO

IF NOT EXISTS (
    SELECT 1 FROM sys.check_constraints
    WHERE name = 'CHK_DepartmentDomainProfile_RouterPatternsJson'
)
    ALTER TABLE dbo.DepartmentDomainProfile WITH CHECK ADD CONSTRAINT
        CHK_DepartmentDomainProfile_RouterPatternsJson CHECK (ISJSON(RouterPatternsJson) = 1);
GO

CREATE OR ALTER TRIGGER dbo.TR_DepartmentRolloutPlan_Invariants
ON dbo.DepartmentRolloutPlan
AFTER INSERT, UPDATE
AS
BEGIN
    SET NOCOUNT ON;

    IF EXISTS (
        SELECT 1
        FROM dbo.DepartmentRolloutPlan
        GROUP BY WaveNumber
        HAVING COUNT(*) > CASE WaveNumber WHEN 1 THEN 3 ELSE 4 END
    )
        THROW 51030, 'Wave capacity exceeds the 3 -> 4 -> 4 -> 4 rollout plan.', 1;

    IF EXISTS (
        SELECT 1
        FROM inserted i
        JOIN deleted d ON d.DeptCode = i.DeptCode
        WHERE i.WaveNumber <> d.WaveNumber
          AND d.RolloutStatus NOT IN ('planned', 'blocked')
    )
        THROW 51031, 'An operated department cannot be reassigned to another wave.', 1;

    IF EXISTS (
        SELECT 1
        FROM inserted i
        JOIN deleted d ON d.DeptCode = i.DeptCode
        WHERE NOT (
            (d.RolloutStatus = 'planned' AND i.RolloutStatus IN ('planned', 'pilot', 'dark_launch', 'blocked'))
            OR (d.RolloutStatus = 'pilot' AND i.RolloutStatus IN ('pilot', 'dark_launch', 'active', 'blocked'))
            OR (d.RolloutStatus = 'dark_launch' AND i.RolloutStatus IN ('dark_launch', 'active', 'blocked'))
            OR (d.RolloutStatus = 'blocked' AND i.RolloutStatus IN ('blocked', 'planned'))
            OR (d.RolloutStatus = 'active' AND i.RolloutStatus = 'active')
        )
    )
        THROW 51032, 'Invalid department rollout status transition.', 1;
END;
GO

UPDATE p
SET IsActive = 0, UpdatedAt = GETDATE(), UpdatedBy = N'V0030 migration'
FROM dbo.DepartmentDomainProfile p
JOIN dbo.Departments d ON d.DeptCode = p.DeptCode
WHERE d.IsActive = 0
  AND p.IsActive = 1
  AND p.UpdatedBy LIKE N'V00% migration';
GO

UPDATE g
SET IsActive = 0, UpdatedAt = GETDATE(), UpdatedBy = N'V0030 migration'
FROM dbo.DepartmentKnowledgeGovernance g
JOIN dbo.Departments d ON d.DeptCode = g.DeptCode
WHERE d.IsActive = 0
  AND g.IsActive = 1
  AND g.UpdatedBy LIKE N'V00% migration';
GO

IF NOT EXISTS (SELECT 1 FROM dbo._SchemaVersions WHERE Version = 'V0030')
    INSERT INTO dbo._SchemaVersions (Version, Description, AppliedAt)
    VALUES ('V0030', 'Rollout invariants and inactive profile hygiene', GETDATE());
GO
