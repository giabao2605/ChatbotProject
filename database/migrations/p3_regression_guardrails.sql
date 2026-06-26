-- ============================================================
-- P3-5: Regression testing (RegressionQuestion + RegressionRun)
-- P3-6: (khong them bang moi; guardrail nam o tang ung dung)
-- Idempotent: chay lai nhieu lan an toan.
-- Yeu cau: chay SAU p3_answer_source.sql / p3_feedback_versioning.sql / p3_quality_golden.sql
-- Luu y: chay sqlcmd voi tham so -I (QUOTED_IDENTIFIER ON)
-- ============================================================

IF NOT EXISTS (SELECT 1 FROM sys.objects WHERE object_id = OBJECT_ID(N'dbo.RegressionQuestion') AND type = 'U')
BEGIN
    CREATE TABLE RegressionQuestion (
        RegQID           INT IDENTITY(1,1) PRIMARY KEY,
        QuestionText     NVARCHAR(2000) NOT NULL,
        ExpectedDocID    INT NULL,
        ExpectedKeywords NVARCHAR(MAX) NULL,
        Department       NVARCHAR(100) NULL,
        Site             NVARCHAR(100) NULL,
        CreatedBy        NVARCHAR(256) NULL,
        IsActive         BIT NOT NULL DEFAULT 1,
        CreatedAt        DATETIME NOT NULL DEFAULT GETDATE()
    );
END
GO

IF NOT EXISTS (SELECT 1 FROM sys.objects WHERE object_id = OBJECT_ID(N'dbo.RegressionRun') AND type = 'U')
BEGIN
    CREATE TABLE RegressionRun (
        RunID         INT IDENTITY(1,1) PRIMARY KEY,
        RegQID        INT NOT NULL,
        RunBatchID    NVARCHAR(64) NOT NULL,
        AnswerText    NVARCHAR(MAX) NULL,
        MatchedDocIDs NVARCHAR(500) NULL,
        DocHit        BIT NOT NULL DEFAULT 0,
        KeywordHit    BIT NOT NULL DEFAULT 0,
        Passed        BIT NOT NULL DEFAULT 0,
        DurationMs    INT NULL,
        ErrorText     NVARCHAR(1000) NULL,
        CreatedAt     DATETIME NOT NULL DEFAULT GETDATE(),
        CONSTRAINT FK_RegressionRun_RegQID FOREIGN KEY (RegQID) REFERENCES RegressionQuestion(RegQID) ON DELETE CASCADE
    );
END
GO

IF NOT EXISTS (SELECT 1 FROM sys.indexes WHERE name = 'IX_RegressionRun_Batch' AND object_id = OBJECT_ID('dbo.RegressionRun'))
BEGIN
    CREATE INDEX IX_RegressionRun_Batch ON RegressionRun (RunBatchID, RegQID);
END
GO
