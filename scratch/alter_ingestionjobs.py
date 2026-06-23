import os
import sys
sys.path.append(os.path.dirname(os.path.abspath(__file__)))
from db_logic import engine
from sqlalchemy import text

with engine.begin() as conn:
    try:
        conn.execute(text("ALTER TABLE dbo.IngestionJobs ADD FailureType NVARCHAR(50) NULL;"))
        print("Added FailureType column")
    except Exception as e:
        print("FailureType error:", e)
        
    try:
        conn.execute(text("ALTER TABLE dbo.IngestionJobs ADD NextRetryAt DATETIME NULL;"))
        print("Added NextRetryAt column")
    except Exception as e:
        print("NextRetryAt error:", e)
