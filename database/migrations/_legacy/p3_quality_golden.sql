-- =============================================================
-- P3-3: DocQualityScore (diem chat luong tai lieu)
-- P3-4: GoldenAnswer (cau tra loi da duyet -> RAG uu tien dung lai)
-- Idempotent. Chay SAU p3_answer_source.sql va p3_feedback_versioning.sql
-- Chay: sqlcmd -S localhost\SQLEXPRESS -d Mech_Chatbot_DB -I -i database\migrations\p3_quality_golden.sql
-- =============================================================
SET NOCOUNT ON;
GO

IF OBJECT_ID('dbo.DocQualityScore','U') IS NULL
BEGIN
    CREATE TABLE dbo.DocQualityScore (
        DocID            INT NOT NULL PRIMARY KEY,
        LikeCount        INT NOT NULL DEFAULT 0,
        DislikeCount     INT NOT NULL DEFAULT 0,
        WeightedLike     FLOAT NOT NULL DEFAULT 0,
        WeightedDislike  FLOAT NOT NULL DEFAULT 0,
        QualityScore     FLOAT NULL,
        NetScore         FLOAT NULL,
        SampleSize       INT NOT NULL DEFAULT 0,
        LastComputedAt   DATETIME NULL,
        CONSTRAINT FK_DocQualityScore_DocID FOREIGN KEY (DocID) REFERENCES dbo.TaiLieu(DocID) ON DELETE CASCADE
    );
END
GO

IF OBJECT_ID('dbo.GoldenAnswer','U') IS NULL
BEGIN
    CREATE TABLE dbo.GoldenAnswer (
        GoldenID      INT IDENTITY(1,1) PRIMARY KEY,
        FeedbackID    INT NULL,
        QuestionHash  NVARCHAR(64) NOT NULL,
        QuestionText  NVARCHAR(4000) NULL,
        GoldenAnswer  NVARCHAR(MAX) NULL,
        SourceDocID   INT NULL,
        Department    NVARCHAR(100) NULL,
        Site          NVARCHAR(100) NULL,
        CreatedBy     NVARCHAR(256) NULL,
        IsActive      BIT NOT NULL DEFAULT 1,
        CreatedAt     DATETIME NOT NULL DEFAULT GETDATE()
    );
END
GO

IF NOT EXISTS (SELECT 1 FROM sys.indexes WHERE name = 'IX_GoldenAnswer_Hash_Active' AND object_id = OBJECT_ID('dbo.GoldenAnswer'))
    CREATE INDEX IX_GoldenAnswer_Hash_Active ON dbo.GoldenAnswer(QuestionHash, IsActive);
GO

IF NOT EXISTS (SELECT 1 FROM sys.indexes WHERE name = 'IX_DocQualityScore_Quality' AND object_id = OBJECT_ID('dbo.DocQualityScore'))
    CREATE INDEX IX_DocQualityScore_Quality ON dbo.DocQualityScore(QualityScore);
GO

PRINT 'P3-3/P3-4 migration hoan tat.';
GO
