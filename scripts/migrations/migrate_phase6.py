from sqlalchemy import text
import sys
import os

# Add parent dir to path so we can import db_logic
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from db_logic import engine

def migrate_phase6():
    queries = [
        """
        CREATE TABLE FeedbackReview (
            FeedbackID INT IDENTITY(1,1) PRIMARY KEY,
            ChatID INT,
            Question NVARCHAR(MAX),
            BotAnswer NVARCHAR(MAX),
            FailureType NVARCHAR(100),
            CorrectAnswer NVARCHAR(MAX),
            CorrectSourceDocID INT NULL,
            ReviewerNote NVARCHAR(MAX),
            AddedToGoldenSet BIT DEFAULT 0,
            CreatedAt DATETIME DEFAULT GETDATE()
        );
        """,
        """
        ALTER TABLE BangKeVatTu ADD
            Unit NVARCHAR(50) NULL,
            Confidence FLOAT NULL,
            RawRowJson NVARCHAR(MAX) NULL,
            SourceTableIndex INT NULL;
        """
    ]
    with engine.begin() as conn:
        for q in queries:
            try:
                conn.execute(text(q))
                print(f"Executed OK.")
            except Exception as e:
                print(f"Skipped (probably exists/already altered): Error: {e}")

if __name__ == '__main__':
    migrate_phase6()
