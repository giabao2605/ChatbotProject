-- Additive provenance for structured sources. V1 rows and runtime rollback remain intact.
IF COL_LENGTH('dbo.ChatEvidenceManifest', 'IsStructuredComplete') IS NULL
    ALTER TABLE dbo.ChatEvidenceManifest ADD IsStructuredComplete BIT NOT NULL
        CONSTRAINT DF_ChatEvidenceManifest_StructuredComplete DEFAULT 0;
GO
IF COL_LENGTH('dbo.AnswerSource', 'SourceLocatorJson') IS NULL
    ALTER TABLE dbo.AnswerSource ADD SourceLocatorJson NVARCHAR(MAX) NULL;
GO
IF COL_LENGTH('dbo.AnswerEvidence', 'SourceLocatorJson') IS NULL
    ALTER TABLE dbo.AnswerEvidence ADD SourceLocatorJson NVARCHAR(MAX) NULL;
GO
IF NOT EXISTS (SELECT 1 FROM sys.check_constraints WHERE name='CK_AnswerSource_SourceLocatorJson')
    ALTER TABLE dbo.AnswerSource ADD CONSTRAINT CK_AnswerSource_SourceLocatorJson
        CHECK (SourceLocatorJson IS NULL OR ISJSON(SourceLocatorJson)=1);
GO
IF NOT EXISTS (SELECT 1 FROM sys.check_constraints WHERE name='CK_AnswerEvidence_SourceLocatorJson')
    ALTER TABLE dbo.AnswerEvidence ADD CONSTRAINT CK_AnswerEvidence_SourceLocatorJson
        CHECK (SourceLocatorJson IS NULL OR ISJSON(SourceLocatorJson)=1);
GO
