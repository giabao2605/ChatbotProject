import os
import sys
from sqlalchemy import text

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from db_logic import engine

def fix_ingestionjobs_schema():
    if engine is None:
        print("SQLAlchemy engine chưa sẵn sàng.")
        return
    
    columns = {
        "TenFile": "NVARCHAR(255) NULL",
        "FilePath": "NVARCHAR(500) NULL",
        "ThuMuc": "NVARCHAR(255) NULL",
        "UploadedBy": "NVARCHAR(255) NULL",
        "RequestedAction": "NVARCHAR(50) NULL",
        "ClassificationJson": "NVARCHAR(MAX) NULL",
        "ClassificationConfidence": "FLOAT NULL",
        "RetryCount": "INT NULL",
        "MaxRetry": "INT NULL",
        "LockedBy": "NVARCHAR(255) NULL",
        "LockedAt": "DATETIME NULL",
        "ProgressPercent": "INT NULL",
        "UpdatedAt": "DATETIME NULL",
    }
    
    with engine.begin() as conn:
        for col, col_type in columns.items():
            sql = f"""
            IF COL_LENGTH('dbo.IngestionJobs', '{col}') IS NULL
            BEGIN
                ALTER TABLE dbo.IngestionJobs ADD {col} {col_type};
                PRINT 'Added column {col}';
            END
            ELSE
            BEGIN
                PRINT 'Column {col} already exists';
            END
            """
            conn.execute(text(sql))
        
        # Set default values cho các cột mới nếu đang NULL
        conn.execute(text("""
            UPDATE dbo.IngestionJobs
            SET RetryCount = ISNULL(RetryCount, 0),
                MaxRetry = ISNULL(MaxRetry, 3),
                ProgressPercent = ISNULL(ProgressPercent, 0),
                UpdatedAt = ISNULL(UpdatedAt, GETDATE())
        """))
        
        # Nếu có job pending cũ nhưng thiếu file path thì đánh dấu failed để worker không kẹt
        conn.execute(text("""
            UPDATE dbo.IngestionJobs
            SET Status = 'failed'
            WHERE Status = 'pending'
              AND (TenFile IS NULL OR FilePath IS NULL)
        """))
        
    print("Đã kiểm tra/sửa schema bảng IngestionJobs xong.")

if __name__ == "__main__":
    import sys
    sys.stdout.reconfigure(encoding='utf-8')
    fix_ingestionjobs_schema()