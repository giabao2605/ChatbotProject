-- =============================================================
-- P3-2: Feedback gan version + co 'stale'
--   Them cot vao FeedbackReview de gan moi feedback voi tai lieu/version/ngu canh
--   va co IsStale (tu danh dau khi tai lieu duoc cap nhat/superseded).
-- Idempotent. Chay SAU p3_answer_source.sql.
-- Chay: sqlcmd -S localhost\SQLEXPRESS -d Mech_Chatbot_DB -I -i database\migrations\p3_feedback_versioning.sql
-- =============================================================
SET NOCOUNT ON;
GO

IF COL_LENGTH('dbo.FeedbackReview','SourceDocID') IS NULL
    ALTER TABLE dbo.FeedbackReview ADD SourceDocID INT NULL;
GO
IF COL_LENGTH('dbo.FeedbackReview','DocVersionNo') IS NULL
    ALTER TABLE dbo.FeedbackReview ADD DocVersionNo INT NULL;
GO
IF COL_LENGTH('dbo.FeedbackReview','ContextHash') IS NULL
    ALTER TABLE dbo.FeedbackReview ADD ContextHash NVARCHAR(64) NULL;
GO
IF COL_LENGTH('dbo.FeedbackReview','Department') IS NULL
    ALTER TABLE dbo.FeedbackReview ADD Department NVARCHAR(100) NULL;
GO
IF COL_LENGTH('dbo.FeedbackReview','Site') IS NULL
    ALTER TABLE dbo.FeedbackReview ADD Site NVARCHAR(100) NULL;
GO
IF COL_LENGTH('dbo.FeedbackReview','IsStale') IS NULL
    ALTER TABLE dbo.FeedbackReview ADD IsStale BIT NOT NULL CONSTRAINT DF_FeedbackReview_IsStale DEFAULT 0;
GO
IF COL_LENGTH('dbo.FeedbackReview','ResolvedByDocID') IS NULL
    ALTER TABLE dbo.FeedbackReview ADD ResolvedByDocID INT NULL;
GO
IF COL_LENGTH('dbo.FeedbackReview','ResolvedAt') IS NULL
    ALTER TABLE dbo.FeedbackReview ADD ResolvedAt DATETIME NULL;
GO

IF NOT EXISTS (SELECT 1 FROM sys.indexes WHERE name = 'IX_FeedbackReview_SourceDocID' AND object_id = OBJECT_ID('dbo.FeedbackReview'))
    CREATE INDEX IX_FeedbackReview_SourceDocID ON dbo.FeedbackReview(SourceDocID);
GO
IF NOT EXISTS (SELECT 1 FROM sys.indexes WHERE name = 'IX_FeedbackReview_IsStale' AND object_id = OBJECT_ID('dbo.FeedbackReview'))
    CREATE INDEX IX_FeedbackReview_IsStale ON dbo.FeedbackReview(IsStale);
GO

PRINT 'P3-2 migration hoan tat.';
GO
