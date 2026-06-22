import os
import sys

# Add root folder to sys.path
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..')))

from db_logic import engine, text

def run_migration():
    if engine is None:
        print("Error: Database engine is not initialized.")
        return

    try:
        with engine.begin() as conn:
            # 1. Create DocumentFamily table if not exists
            conn.execute(text("""
                IF NOT EXISTS (SELECT * FROM sysobjects WHERE name='DocumentFamily' and xtype='U')
                BEGIN
                    CREATE TABLE DocumentFamily (
                        FamilyID INT IDENTITY(1,1) PRIMARY KEY,
                        BaseCode NVARCHAR(255) NOT NULL,
                        FamilyName NVARCHAR(500),
                        Department NVARCHAR(255),
                        Description NVARCHAR(MAX),
                        CreatedAt DATETIME DEFAULT GETDATE(),
                        UpdatedAt DATETIME DEFAULT GETDATE()
                    );
                    PRINT 'Created table DocumentFamily';
                END
            """))

            # 2. Add new columns to TaiLieu
            columns_to_add = {
                "FamilyID": "INT NULL",
                "BaseCode": "NVARCHAR(255) NULL",
                "VersionNo": "INT NULL",
                "VersionLabel": "NVARCHAR(50) NULL",
                "VariantCode": "NVARCHAR(255) NULL",
                "VariantGroup": "NVARCHAR(255) NULL",
                "LifecycleStatus": "NVARCHAR(50) DEFAULT 'draft'",
                "ReviewStatus": "NVARCHAR(50) DEFAULT 'pending_review'",
                "IsCurrent": "BIT DEFAULT 0",
                "IsArchived": "BIT DEFAULT 0",
                "SupersedesDocID": "INT NULL",
                "PublishedAt": "DATETIME NULL",
                "ArchivedAt": "DATETIME NULL",
                "UploadedBy": "NVARCHAR(255) NULL",
                "ReviewedBy": "NVARCHAR(255) NULL",
                "ClassificationConfidence": "FLOAT NULL",
                "ClassificationJson": "NVARCHAR(MAX) NULL"
            }

            for col, dtype in columns_to_add.items():
                conn.execute(text(f"""
                    IF NOT EXISTS (
                        SELECT * FROM sys.columns 
                        WHERE Name = N'{col}' AND Object_ID = Object_ID(N'dbo.TaiLieu')
                    )
                    BEGIN
                        ALTER TABLE dbo.TaiLieu ADD {col} {dtype};
                        PRINT 'Added column {col} to TaiLieu';
                    END
                """))
            
            # 3. Add foreign key if not exists
            conn.execute(text("""
                IF NOT EXISTS (SELECT * FROM sys.foreign_keys WHERE name = 'FK_TaiLieu_Family')
                BEGIN
                    ALTER TABLE TaiLieu ADD CONSTRAINT FK_TaiLieu_Family FOREIGN KEY (FamilyID) REFERENCES DocumentFamily(FamilyID);
                    PRINT 'Added foreign key FK_TaiLieu_Family';
                END
            """))
            
        print("Migration completed successfully.")
    except Exception as e:
        print(f"Error during migration: {e}")

if __name__ == "__main__":
    run_migration()
