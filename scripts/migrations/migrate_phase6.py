from sqlalchemy import text
import sys
import os

# Add parent dir to path so we can import db_logic
sys.path.append(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from db_logic import engine

def migrate_phase6():
    queries = [
        """
        UPDATE dbo.TaiLieu
        SET LifecycleStatus = 'draft'
        WHERE LifecycleStatus IS NULL
           OR LifecycleStatus NOT IN ('draft', 'published', 'archived', 'superseded', 'retired', 'rejected');
        """,
        """
        UPDATE dbo.TaiLieu
        SET ReviewStatus = 'pending_review'
        WHERE ReviewStatus IS NULL
           OR ReviewStatus NOT IN ('pending_review', 'approved', 'rejected');
        """,
        """
        IF OBJECT_ID('dbo.FeedbackReview', 'U') IS NULL
        BEGIN
            CREATE TABLE FeedbackReview (
                FeedbackID INT IDENTITY(1,1) PRIMARY KEY,
                ChatID INT NOT NULL,
                Question NVARCHAR(MAX),
                BotAnswer NVARCHAR(MAX),
                FailureType NVARCHAR(100),
                CorrectAnswer NVARCHAR(MAX),
                CorrectSourceDocID INT NULL,
                ReviewerNote NVARCHAR(MAX),
                AddedToGoldenSet BIT DEFAULT 0,
                CreatedAt DATETIME DEFAULT GETDATE(),
                CONSTRAINT FK_FeedbackReview_ChatID FOREIGN KEY (ChatID) REFERENCES LichSuChat(ChatID) ON DELETE CASCADE
            );
        END
        """,
        """
        IF COL_LENGTH('dbo.BangKeVatTu', 'Unit') IS NULL
            ALTER TABLE dbo.BangKeVatTu ADD Unit NVARCHAR(50) NULL;

        IF COL_LENGTH('dbo.BangKeVatTu', 'Confidence') IS NULL
            ALTER TABLE dbo.BangKeVatTu ADD Confidence FLOAT NULL;

        IF COL_LENGTH('dbo.BangKeVatTu', 'RawRowJson') IS NULL
            ALTER TABLE dbo.BangKeVatTu ADD RawRowJson NVARCHAR(MAX) NULL;

        IF COL_LENGTH('dbo.BangKeVatTu', 'SourceTableIndex') IS NULL
            ALTER TABLE dbo.BangKeVatTu ADD SourceTableIndex INT NULL;

        IF COL_LENGTH('dbo.BangKeVatTu', 'NormalizedMaterial') IS NULL
            ALTER TABLE dbo.BangKeVatTu ADD NormalizedMaterial NVARCHAR(255) NULL;
        """,
        """
        IF NOT EXISTS (
            SELECT 1 FROM sys.check_constraints
            WHERE name = 'CHK_LifecycleStatus'
        )
        BEGIN
            ALTER TABLE dbo.TaiLieu
            ADD CONSTRAINT CHK_LifecycleStatus
            CHECK (LifecycleStatus IN ('draft', 'published', 'archived', 'superseded', 'retired', 'rejected'));
        END
        """,
        """
        IF NOT EXISTS (
            SELECT 1 FROM sys.check_constraints
            WHERE name = 'CHK_ReviewStatus'
        )
        BEGIN
            ALTER TABLE dbo.TaiLieu
            ADD CONSTRAINT CHK_ReviewStatus
            CHECK (ReviewStatus IN ('pending_review', 'approved', 'rejected'));
        END
        """
    ]
    with engine.begin() as conn:
        for q in queries:
            try:
                conn.execute(text(q))
                print(f"Executed OK.")
            except Exception as e:
                print(f"Failed query:\n{q}\nError: {e}")

if __name__ == '__main__':
    migrate_phase6()
