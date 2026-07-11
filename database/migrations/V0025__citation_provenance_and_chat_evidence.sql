-- V0025: Preserve a complete, metadata-only evidence basis for cached and
-- persisted answers.  Display citations remain a small final-attributed set;
-- authorization rechecks use the full evidence manifest.

IF COL_LENGTH('dbo.SemanticCache', 'CitationSnapshotJson') IS NULL
    ALTER TABLE dbo.SemanticCache ADD CitationSnapshotJson NVARCHAR(MAX) NULL;
GO

IF COL_LENGTH('dbo.SemanticCache', 'EvidenceSnapshotJson') IS NULL
    ALTER TABLE dbo.SemanticCache ADD EvidenceSnapshotJson NVARCHAR(MAX) NULL;
GO

IF OBJECT_ID('dbo.ChatEvidenceManifest', 'U') IS NULL
BEGIN
    CREATE TABLE dbo.ChatEvidenceManifest (
        ChatID                 INT NOT NULL PRIMARY KEY,
        RequiresAuthorization  BIT NOT NULL,
        IsComplete             BIT NOT NULL,
        EvidenceCount          INT NOT NULL CONSTRAINT DF_ChatEvidenceManifest_Count DEFAULT 0,
        SchemaVersion          NVARCHAR(30) NOT NULL CONSTRAINT DF_ChatEvidenceManifest_Version DEFAULT 'v1',
        CreatedAt              DATETIME NOT NULL CONSTRAINT DF_ChatEvidenceManifest_Created DEFAULT GETDATE(),
        UpdatedAt              DATETIME NOT NULL CONSTRAINT DF_ChatEvidenceManifest_Updated DEFAULT GETDATE(),
        CONSTRAINT FK_ChatEvidenceManifest_ChatID
            FOREIGN KEY (ChatID) REFERENCES dbo.LichSuChat(ChatID) ON DELETE CASCADE,
        CONSTRAINT CHK_ChatEvidenceManifest_Count
            CHECK (EvidenceCount >= 0)
    );
END
GO

IF OBJECT_ID('dbo.AnswerEvidence', 'U') IS NULL
BEGIN
    CREATE TABLE dbo.AnswerEvidence (
        EvidenceID       BIGINT IDENTITY(1,1) NOT NULL PRIMARY KEY,
        ChatID           INT NOT NULL,
        DocID            INT NOT NULL,
        PageNo           INT NULL,
        SourceRef        NVARCHAR(80) NULL,
        SecurityLevel    NVARCHAR(30) NULL,
        RankNo           INT NULL,
        CreatedAt        DATETIME NOT NULL CONSTRAINT DF_AnswerEvidence_Created DEFAULT GETDATE(),
        CONSTRAINT FK_AnswerEvidence_ChatID
            FOREIGN KEY (ChatID) REFERENCES dbo.LichSuChat(ChatID) ON DELETE CASCADE,
        CONSTRAINT FK_AnswerEvidence_DocID
            FOREIGN KEY (DocID) REFERENCES dbo.TaiLieu(DocID),
        CONSTRAINT CHK_AnswerEvidence_PageNo
            CHECK (PageNo IS NULL OR PageNo > 0)
    );
END
GO

IF NOT EXISTS (
    SELECT 1 FROM sys.indexes
    WHERE object_id = OBJECT_ID('dbo.AnswerEvidence')
      AND name = 'IX_AnswerEvidence_Chat_Doc_Page'
)
    CREATE INDEX IX_AnswerEvidence_Chat_Doc_Page
        ON dbo.AnswerEvidence (ChatID, DocID, PageNo, EvidenceID);
GO

IF NOT EXISTS (SELECT 1 FROM dbo._SchemaVersions WHERE Version = 'V0025')
    INSERT INTO dbo._SchemaVersions (Version, Description, AppliedAt)
    VALUES ('V0025', 'Citation provenance snapshots and complete chat evidence manifests', GETDATE());
GO
