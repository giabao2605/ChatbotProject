from sqlalchemy import text
import sys
import os

# Add parent dir to path so we can import db_logic
sys.path.append(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from db_logic import engine

def migrate_phase3():
    queries = [
        "ALTER TABLE IngestionJobs ADD UploadedBy NVARCHAR(255) NULL;",
        "ALTER TABLE IngestionJobs ADD RequestedAction NVARCHAR(50) NULL;",
        "ALTER TABLE IngestionJobs ADD ClassificationJson NVARCHAR(MAX) NULL;",
        "ALTER TABLE IngestionJobs ADD ClassificationConfidence FLOAT NULL;",
        "ALTER TABLE IngestionJobs ADD RetryCount INT DEFAULT 0;",
        "ALTER TABLE IngestionJobs ADD MaxRetry INT DEFAULT 3;",
        "ALTER TABLE IngestionJobs ADD LockedBy NVARCHAR(255) NULL;",
        "ALTER TABLE IngestionJobs ADD LockedAt DATETIME NULL;",
        "ALTER TABLE IngestionJobs ADD ProgressPercent INT DEFAULT 0;"
    ]
    with engine.begin() as conn:
        for q in queries:
            try:
                conn.execute(text(q))
                print(f"Executed: {q}")
            except Exception as e:
                print(f"Skipped (probably exists): {q} - Error: {e}")

if __name__ == '__main__':
    migrate_phase3()
