-- GD6: bounded comprehensive scan snapshots and per-source progress.
IF EXISTS (SELECT 1 FROM dbo._SchemaVersions WHERE Version = 'V0050') RETURN;
GO
IF OBJECT_ID('dbo.RagComprehensiveScan', 'U') IS NULL
BEGIN
    CREATE TABLE dbo.RagComprehensiveScan (
        ScanID NVARCHAR(128) NOT NULL CONSTRAINT PK_RagComprehensiveScan PRIMARY KEY,
        UserID INT NULL,
        Username NVARCHAR(255) NULL,
        QueryFingerprint CHAR(64) NOT NULL,
        AccessScopeFingerprint CHAR(64) NOT NULL,
        SearchMode NVARCHAR(32) NOT NULL CONSTRAINT CK_RagComprehensiveScan_Mode CHECK (SearchMode = 'comprehensive'),
        SnapshotFingerprint CHAR(64) NOT NULL,
        Status NVARCHAR(32) NOT NULL CONSTRAINT CK_RagComprehensiveScan_Status CHECK (Status IN ('active','complete','stale','stopped')),
        NextOffset INT NOT NULL CONSTRAINT CK_RagComprehensiveScan_Offset CHECK (NextOffset >= 0),
        TotalSources INT NOT NULL CONSTRAINT CK_RagComprehensiveScan_Total CHECK (TotalSources >= 0),
        CheckedCount INT NOT NULL DEFAULT 0,
        ErrorCount INT NOT NULL DEFAULT 0,
        CreatedAt DATETIME2(3) NOT NULL DEFAULT SYSUTCDATETIME(),
        UpdatedAt DATETIME2(3) NOT NULL DEFAULT SYSUTCDATETIME()
    );
END;
GO
IF OBJECT_ID('dbo.RagComprehensiveSource', 'U') IS NULL
BEGIN
    CREATE TABLE dbo.RagComprehensiveSource (
        ScanID NVARCHAR(128) NOT NULL,
        SourceOrdinal INT NOT NULL,
        DocID INT NULL,
        VersionNo NVARCHAR(100) NULL,
        SourceIdentityJson NVARCHAR(MAX) NOT NULL,
        Status NVARCHAR(32) NOT NULL CONSTRAINT DF_RagComprehensiveSource_Status DEFAULT 'pending',
        ErrorCode NVARCHAR(100) NULL,
        CheckedAt DATETIME2(3) NULL,
        PageOffset INT NOT NULL DEFAULT 0 CONSTRAINT CK_RagComprehensiveSource_PageOffset CHECK (PageOffset >= 0),
        TextOffset INT NOT NULL DEFAULT 0 CONSTRAINT CK_RagComprehensiveSource_TextOffset CHECK (TextOffset >= 0),
        CONSTRAINT PK_RagComprehensiveSource PRIMARY KEY (ScanID, SourceOrdinal),
        CONSTRAINT FK_RagComprehensiveSource_Scan FOREIGN KEY (ScanID) REFERENCES dbo.RagComprehensiveScan(ScanID),
        CONSTRAINT CK_RagComprehensiveSource_Status CHECK (Status IN ('pending','checked','error','denied'))
    );
END;
GO
IF OBJECT_ID('dbo.RagComprehensiveBatch', 'U') IS NULL
BEGIN
    CREATE TABLE dbo.RagComprehensiveBatch (
        ScanID NVARCHAR(128) NOT NULL,
        Revision INT NOT NULL,
        ResultsJson NVARCHAR(MAX) NOT NULL,
        CONSTRAINT PK_RagComprehensiveBatch PRIMARY KEY (ScanID, Revision),
        CONSTRAINT FK_RagComprehensiveBatch_Scan FOREIGN KEY (ScanID) REFERENCES dbo.RagComprehensiveScan(ScanID)
    );
END;
GO
IF NOT EXISTS (SELECT 1 FROM dbo._SchemaVersions WHERE Version = 'V0050')
    INSERT INTO dbo._SchemaVersions (Version, Description, AppliedAt)
    VALUES ('V0050', 'GD6 comprehensive scan snapshot and bounded continuation progress', SYSUTCDATETIME());
GO
