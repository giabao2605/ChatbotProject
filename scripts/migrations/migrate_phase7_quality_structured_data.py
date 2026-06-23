from sqlalchemy import text
import os
import sys

sys.path.append(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from db_logic import engine

def migrate_phase7():
    queries = [
        """
        IF COL_LENGTH('dbo.IngestionJobs', 'FailureType') IS NULL
            ALTER TABLE dbo.IngestionJobs ADD FailureType NVARCHAR(50) NULL;
        """,
        """
        IF COL_LENGTH('dbo.IngestionJobs', 'NextRetryAt') IS NULL
            ALTER TABLE dbo.IngestionJobs ADD NextRetryAt DATETIME NULL;
        """,
        """
        IF COL_LENGTH('dbo.IngestionJobs', 'ExtractionReport') IS NULL
            ALTER TABLE dbo.IngestionJobs ADD ExtractionReport NVARCHAR(MAX) NULL;
        """,
        """
        IF COL_LENGTH('dbo.IngestionJobs', 'QualityScore') IS NULL
            ALTER TABLE dbo.IngestionJobs ADD QualityScore INT NULL;
        """,
        """
        IF COL_LENGTH('dbo.IngestionJobs', 'QualityStatus') IS NULL
            ALTER TABLE dbo.IngestionJobs ADD QualityStatus NVARCHAR(50) NULL;
        """,
        """
        IF OBJECT_ID('dbo.DocumentPages', 'U') IS NULL
        BEGIN
            CREATE TABLE dbo.DocumentPages (
                PageID INT IDENTITY(1,1) PRIMARY KEY,
                DocID INT NOT NULL,
                FileName NVARCHAR(500) NOT NULL,
                PageNo INT NOT NULL,
                TextExtract NVARCHAR(MAX) NULL,
                LocalOCRText NVARCHAR(MAX) NULL,
                VisionSummary NVARCHAR(MAX) NULL,
                LocalOCRConfidence FLOAT NULL,
                ExtractionStatus NVARCHAR(50) NULL,
                ImagePath NVARCHAR(1000) NULL,
                CreatedAt DATETIME DEFAULT GETDATE(),
                UpdatedAt DATETIME DEFAULT GETDATE()
            );
        END;
        """,
        """
        IF OBJECT_ID('dbo.TechnicalAttributes', 'U') IS NULL
        BEGIN
            CREATE TABLE dbo.TechnicalAttributes (
                AttributeID INT IDENTITY(1,1) PRIMARY KEY,
                DocID INT NOT NULL,
                FileName NVARCHAR(500) NOT NULL,
                PageNo INT NULL,
                AttributeType NVARCHAR(100) NOT NULL,
                AttributeName NVARCHAR(255) NULL,
                AttributeValue NVARCHAR(500) NOT NULL,
                Unit NVARCHAR(50) NULL,
                SourceText NVARCHAR(MAX) NULL,
                Confidence FLOAT NULL,
                ExtractedBy NVARCHAR(50) NULL,
                HumanVerified BIT DEFAULT 0,
                VerifiedBy NVARCHAR(255) NULL,
                VerifiedAt DATETIME NULL,
                CreatedAt DATETIME DEFAULT GETDATE()
            );
        END;
        """,
        """
        IF NOT EXISTS (
            SELECT 1 FROM sys.indexes 
            WHERE name = 'IX_DocumentPages_DocID_PageNo' 
            AND object_id = OBJECT_ID('dbo.DocumentPages')
        )
        CREATE INDEX IX_DocumentPages_DocID_PageNo 
        ON dbo.DocumentPages(DocID, PageNo);
        """,
        """
        IF NOT EXISTS (
            SELECT 1 FROM sys.indexes 
            WHERE name = 'IX_TechnicalAttributes_File_Type' 
            AND object_id = OBJECT_ID('dbo.TechnicalAttributes')
        )
        CREATE INDEX IX_TechnicalAttributes_File_Type 
        ON dbo.TechnicalAttributes(FileName, AttributeType);
        """,
        """
        IF NOT EXISTS (
            SELECT 1 FROM sys.indexes 
            WHERE name = 'IX_IngestionJobs_Status_NextRetryAt' 
            AND object_id = OBJECT_ID('dbo.IngestionJobs')
        )
        CREATE INDEX IX_IngestionJobs_Status_NextRetryAt 
        ON dbo.IngestionJobs(Status, NextRetryAt);
        """
    ]
    with engine.begin() as conn:
        for q in queries:
            conn.execute(text(q))
    print("Executed OK")

if __name__ == "__main__":
    migrate_phase7()
