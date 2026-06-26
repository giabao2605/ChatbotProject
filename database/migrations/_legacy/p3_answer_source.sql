-- =============================================================
-- P3-1: AnswerSource - truy vet nguon (tai lieu/version/chunk)
--        da dung de sinh moi cau tra loi RAG.
-- Idempotent: chay lai nhieu lan an toan.
-- Chay: sqlcmd -S localhost\SQLEXPRESS -d Mech_Chatbot_DB -I -i database\migrations\p3_answer_source.sql
-- =============================================================
SET NOCOUNT ON;
GO

IF NOT EXISTS (SELECT 1 FROM sys.objects WHERE object_id = OBJECT_ID(N'dbo.AnswerSource') AND type = 'U')
BEGIN
    CREATE TABLE dbo.AnswerSource (
        SourceID    INT IDENTITY(1,1) PRIMARY KEY,
        ChatID      INT NOT NULL,
        DocID       INT NULL,
        FileName    NVARCHAR(500) NULL,
        VersionNo   INT NULL,
        VariantCode NVARCHAR(100) NULL,
        ChunkRef    NVARCHAR(200) NULL,
        Score       FLOAT NULL,
        RankNo      INT NULL,
        IsCurrent   BIT NULL,
        CreatedAt   DATETIME DEFAULT GETDATE(),
        CONSTRAINT FK_AnswerSource_ChatID
            FOREIGN KEY (ChatID) REFERENCES dbo.LichSuChat(ChatID) ON DELETE CASCADE
    );
    PRINT 'Da tao bang AnswerSource.';
END
ELSE
    PRINT 'Bang AnswerSource da ton tai, bo qua.';
GO

IF NOT EXISTS (SELECT 1 FROM sys.indexes WHERE name = 'IX_AnswerSource_ChatID' AND object_id = OBJECT_ID('dbo.AnswerSource'))
    CREATE INDEX IX_AnswerSource_ChatID ON dbo.AnswerSource(ChatID);
GO

IF NOT EXISTS (SELECT 1 FROM sys.indexes WHERE name = 'IX_AnswerSource_DocID' AND object_id = OBJECT_ID('dbo.AnswerSource'))
    CREATE INDEX IX_AnswerSource_DocID ON dbo.AnswerSource(DocID);
GO

PRINT 'P3-1 migration hoan tat.';
GO
