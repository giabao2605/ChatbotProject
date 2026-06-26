-- =====================================================================
-- P2 Migration: Tu dien ma vat tu / dong nghia (quan tri qua UI)
-- Thay cho danh sach hardcode trong mechanical_extractors.py,
-- repository.normalize_material_name va rag.service.KNOWN_MATERIALS.
-- Idempotent: chay lai nhieu lan an toan.
-- Chay: sqlcmd -S <server> -d <db> -I -i database\migrations\p2_material_dictionary.sql
-- =====================================================================
SET NOCOUNT ON;
GO

-- 1) Bang tu dien vat lieu chuan
IF NOT EXISTS (SELECT 1 FROM sys.objects WHERE object_id = OBJECT_ID(N'dbo.MaterialDictionary') AND type = 'U')
BEGIN
    CREATE TABLE dbo.MaterialDictionary (
        MaterialID    INT IDENTITY(1,1) PRIMARY KEY,
        CanonicalCode NVARCHAR(100) NOT NULL,
        DisplayName   NVARCHAR(255) NOT NULL,
        Category      NVARCHAR(100) NULL,
        IsActive      BIT NOT NULL DEFAULT 1,
        CreatedAt     DATETIME NOT NULL DEFAULT GETDATE(),
        UpdatedAt     DATETIME NULL,
        CONSTRAINT UQ_MaterialDictionary_Code UNIQUE (CanonicalCode)
    );
END
GO

-- 2) Bang tu dong nghia (mapping ve 1 ma chuan)
IF NOT EXISTS (SELECT 1 FROM sys.objects WHERE object_id = OBJECT_ID(N'dbo.MaterialSynonym') AND type = 'U')
BEGIN
    CREATE TABLE dbo.MaterialSynonym (
        SynonymID  INT IDENTITY(1,1) PRIMARY KEY,
        MaterialID INT NOT NULL,
        Synonym    NVARCHAR(255) NOT NULL,
        IsActive   BIT NOT NULL DEFAULT 1,
        CreatedAt  DATETIME NOT NULL DEFAULT GETDATE(),
        CONSTRAINT FK_MaterialSynonym_Material FOREIGN KEY (MaterialID)
            REFERENCES dbo.MaterialDictionary(MaterialID) ON DELETE CASCADE
    );
END
GO

-- Index tra cuu synonym
IF NOT EXISTS (SELECT 1 FROM sys.indexes WHERE name = 'IX_MaterialSynonym_Synonym' AND object_id = OBJECT_ID(N'dbo.MaterialSynonym'))
    CREATE INDEX IX_MaterialSynonym_Synonym ON dbo.MaterialSynonym(Synonym);
GO

-- 3) Seed vat lieu goc (idempotent qua MERGE theo CanonicalCode)
;WITH seed AS (
    SELECT * FROM (VALUES
        (N'SUS304', N'SUS 304', N'stainless steel'),
        (N'SUS316', N'SUS 316', N'stainless steel'),
        (N'SS400',  N'SS400',   N'carbon steel'),
        (N'SPCC',   N'SPCC',    N'carbon steel'),
        (N'AL6061', N'AL 6061', N'aluminum'),
        (N'A5052',  N'A5052',   N'aluminum'),
        (N'S45C',   N'S45C',    N'carbon steel'),
        (N'SKD11',  N'SKD11',   N'tool steel'),
        (N'SKD61',  N'SKD61',   N'tool steel')
    ) v(CanonicalCode, DisplayName, Category)
)
MERGE dbo.MaterialDictionary AS tgt
USING seed AS src ON tgt.CanonicalCode = src.CanonicalCode
WHEN NOT MATCHED THEN
    INSERT (CanonicalCode, DisplayName, Category, IsActive)
    VALUES (src.CanonicalCode, src.DisplayName, src.Category, 1);
GO

-- 4) Seed tu dong nghia
;WITH syn AS (
    SELECT * FROM (VALUES
        (N'SUS304', N'sus304'),
        (N'SUS304', N'ss304'),
        (N'SUS304', N'inox 304'),
        (N'SUS316', N'sus316'),
        (N'SUS316', N'ss316'),
        (N'AL6061', N'al6061'),
        (N'AL6061', N'a6061'),
        (N'A5052',  N'al5052')
    ) v(CanonicalCode, Synonym)
)
MERGE dbo.MaterialSynonym AS tgt
USING (
    SELECT m.MaterialID, s.Synonym
    FROM syn s JOIN dbo.MaterialDictionary m ON m.CanonicalCode = s.CanonicalCode
) AS src ON tgt.MaterialID = src.MaterialID AND tgt.Synonym = src.Synonym
WHEN NOT MATCHED THEN
    INSERT (MaterialID, Synonym, IsActive) VALUES (src.MaterialID, src.Synonym, 1);
GO

PRINT 'P2 migration hoan tat: MaterialDictionary, MaterialSynonym (seeded).';
GO
