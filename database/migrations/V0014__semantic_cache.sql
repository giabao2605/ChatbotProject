-- V0014__semantic_cache.sql
-- P2-9: Semantic cache cho cau hoi lap lai (giam cost GPT). Idempotent.
IF NOT EXISTS (SELECT 1 FROM sys.objects WHERE object_id = OBJECT_ID(N'dbo.SemanticCache') AND type = 'U')
BEGIN
    CREATE TABLE dbo.SemanticCache (
        CacheID      INT IDENTITY(1,1) PRIMARY KEY,
        QuestionText NVARCHAR(2000) NULL,
        Embedding    NVARCHAR(MAX) NULL,
        Answer       NVARCHAR(MAX) NULL,
        RefText      NVARCHAR(MAX) NULL,
        RefImages    NVARCHAR(MAX) NULL,
        SourceDocIDs NVARCHAR(MAX) NULL,
        ScopeSig     NVARCHAR(400) NULL,
        Model        NVARCHAR(100) NULL,
        EstCost      FLOAT NULL,
        HitCount     INT NOT NULL CONSTRAINT DF_SemanticCache_HitCount DEFAULT 0,
        CreatedAt    DATETIME NOT NULL CONSTRAINT DF_SemanticCache_CreatedAt DEFAULT GETDATE(),
        LastHitAt    DATETIME NULL
    );
END
GO
IF NOT EXISTS (SELECT 1 FROM sys.indexes WHERE name = 'IX_SemanticCache_Scope' AND object_id = OBJECT_ID(N'dbo.SemanticCache'))
    CREATE INDEX IX_SemanticCache_Scope ON dbo.SemanticCache (ScopeSig, CreatedAt DESC);
GO
IF NOT EXISTS (SELECT 1 FROM sys.objects WHERE object_id = OBJECT_ID(N'dbo.SemanticCacheStat') AND type = 'U')
BEGIN
    CREATE TABLE dbo.SemanticCacheStat (
        StatID    INT NOT NULL PRIMARY KEY,
        Lookups   INT NOT NULL CONSTRAINT DF_SCStat_Lookups DEFAULT 0,
        Hits      INT NOT NULL CONSTRAINT DF_SCStat_Hits DEFAULT 0,
        CostSaved FLOAT NOT NULL CONSTRAINT DF_SCStat_CostSaved DEFAULT 0
    );
    INSERT INTO dbo.SemanticCacheStat (StatID, Lookups, Hits, CostSaved) VALUES (1, 0, 0, 0);
END
GO
