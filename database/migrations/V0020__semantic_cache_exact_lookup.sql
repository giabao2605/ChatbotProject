-- V0020: Indexed exact semantic-cache lookup before router/embedding work.

IF COL_LENGTH('dbo.SemanticCache', 'QuestionHash') IS NULL
    ALTER TABLE dbo.SemanticCache ADD QuestionHash CHAR(64) NULL;
GO

IF NOT EXISTS (
    SELECT 1 FROM sys.indexes
    WHERE object_id = OBJECT_ID('dbo.SemanticCache')
      AND name = 'IX_SemanticCache_Exact'
)
    CREATE INDEX IX_SemanticCache_Exact
        ON dbo.SemanticCache (ScopeSig, QuestionHash, CreatedAt DESC)
        INCLUDE (CacheID, QuestionText, Answer, RefText, RefImages, SourceDocIDs, EstCost);
GO

IF NOT EXISTS (SELECT 1 FROM dbo._SchemaVersions WHERE Version = 'V0020')
    INSERT INTO dbo._SchemaVersions (Version, Description, AppliedAt)
    VALUES ('V0020', 'Indexed exact semantic cache lookup before router', GETDATE());
GO
