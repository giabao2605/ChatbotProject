-- Additive branch result history. Legacy rows and older runtime readers remain valid.
IF COL_LENGTH('dbo.AnswerEvidence', 'SourceVersion') IS NULL
    ALTER TABLE dbo.AnswerEvidence ADD SourceVersion INT NULL;
GO
IF COL_LENGTH('dbo.ChatEvidenceManifest', 'AdvancedRagJson') IS NULL
    ALTER TABLE dbo.ChatEvidenceManifest ADD AdvancedRagJson NVARCHAR(MAX) NULL;
GO
IF NOT EXISTS (SELECT 1 FROM sys.check_constraints WHERE name='CK_ChatEvidenceManifest_AdvancedRagJson')
    ALTER TABLE dbo.ChatEvidenceManifest ADD CONSTRAINT CK_ChatEvidenceManifest_AdvancedRagJson
        CHECK (AdvancedRagJson IS NULL OR ISJSON(AdvancedRagJson)=1);
GO
