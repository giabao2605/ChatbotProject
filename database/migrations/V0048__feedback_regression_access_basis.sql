-- Additive only. Existing NULL bases remain unknown and are not backfilled.
IF COL_LENGTH('dbo.RegressionQuestion', 'AccessBasisJson') IS NULL
    ALTER TABLE dbo.RegressionQuestion ADD AccessBasisJson NVARCHAR(MAX) NULL;
GO
IF COL_LENGTH('dbo.RegressionRun', 'ResultManifestJson') IS NULL
    ALTER TABLE dbo.RegressionRun ADD ResultManifestJson NVARCHAR(MAX) NULL;
GO
IF COL_LENGTH('dbo.GoldenAnswer', 'AccessBasisJson') IS NULL
    ALTER TABLE dbo.GoldenAnswer ADD AccessBasisJson NVARCHAR(MAX) NULL;
GO
IF NOT EXISTS (SELECT 1 FROM sys.check_constraints WHERE name='CK_RegressionQuestion_AccessBasisJson')
    ALTER TABLE dbo.RegressionQuestion ADD CONSTRAINT CK_RegressionQuestion_AccessBasisJson
        CHECK (AccessBasisJson IS NULL OR ISJSON(AccessBasisJson)=1);
GO
IF NOT EXISTS (SELECT 1 FROM sys.check_constraints WHERE name='CK_RegressionRun_ResultManifestJson')
    ALTER TABLE dbo.RegressionRun ADD CONSTRAINT CK_RegressionRun_ResultManifestJson
        CHECK (ResultManifestJson IS NULL OR ISJSON(ResultManifestJson)=1);
GO
IF NOT EXISTS (SELECT 1 FROM sys.check_constraints WHERE name='CK_GoldenAnswer_AccessBasisJson')
    ALTER TABLE dbo.GoldenAnswer ADD CONSTRAINT CK_GoldenAnswer_AccessBasisJson
        CHECK (AccessBasisJson IS NULL OR ISJSON(AccessBasisJson)=1);
GO
