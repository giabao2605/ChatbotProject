-- Additive evaluation/review storage. Does not enable an advanced capability.
-- Apply to staging first and take a backup before a production application.
IF OBJECT_ID(N'dbo.ReviewedAssemblySequences', N'U') IS NULL
BEGIN
    CREATE TABLE dbo.ReviewedAssemblySequences (
        DocID INT NOT NULL PRIMARY KEY,
        SourceVersion INT NOT NULL,
        SnapshotID NVARCHAR(128) NOT NULL,
        SourceHash VARCHAR(64) NOT NULL,
        ManifestJson NVARCHAR(MAX) NOT NULL,
        CompletenessReviewed BIT NOT NULL DEFAULT 0,
        ReviewedBy INT NULL,
        ReviewNote NVARCHAR(2000) NULL,
        UpdatedAt DATETIME2 NOT NULL DEFAULT SYSUTCDATETIME(),
        CONSTRAINT FK_ReviewedAssemblySequences_Document FOREIGN KEY (DocID) REFERENCES dbo.TaiLieu(DocID),
        CONSTRAINT CK_ReviewedAssemblySequences_Manifest CHECK (ISJSON(ManifestJson) = 1),
        CONSTRAINT CK_ReviewedAssemblySequences_Hash CHECK (LEN(SourceHash) = 64)
    );
END;
GO
IF OBJECT_ID(N'dbo.StructuredTextFact', N'U') IS NULL
BEGIN
    CREATE TABLE dbo.StructuredTextFact (
        FactID NVARCHAR(450) NOT NULL PRIMARY KEY,
        SourceDocID INT NOT NULL,
        SourceVersion INT NOT NULL,
        SnapshotID NVARCHAR(128) NOT NULL,
        SourceHash VARCHAR(64) NOT NULL,
        PayloadJson NVARCHAR(MAX) NOT NULL,
        ReviewedPayloadJson NVARCHAR(MAX) NULL,
        ReviewStatus VARCHAR(16) NOT NULL DEFAULT 'pending',
        ReviewedBy INT NULL,
        ReviewNote NVARCHAR(2000) NULL,
        UpdatedAt DATETIME2 NOT NULL DEFAULT SYSUTCDATETIME(),
        CONSTRAINT FK_StructuredTextFact_Document FOREIGN KEY (SourceDocID) REFERENCES dbo.TaiLieu(DocID),
        CONSTRAINT CK_StructuredTextFact_Payload CHECK (ISJSON(PayloadJson) = 1),
        CONSTRAINT CK_StructuredTextFact_Hash CHECK (LEN(SourceHash) = 64),
        CONSTRAINT CK_StructuredTextFact_Review CHECK (ReviewStatus IN ('pending', 'approved', 'rejected'))
    );
    CREATE INDEX IX_StructuredTextFact_Source ON dbo.StructuredTextFact(SourceDocID, SourceVersion, SnapshotID);
END;
GO
IF OBJECT_ID(N'dbo.StructuredSourceSnapshot', N'U') IS NULL
BEGIN
    CREATE TABLE dbo.StructuredSourceSnapshot (
        DocID INT NOT NULL,
        VersionNo INT NOT NULL,
        SnapshotID NVARCHAR(128) NOT NULL,
        SourceHash VARCHAR(64) NOT NULL,
        CurrentSnapshot BIT NOT NULL DEFAULT 1,
        CONSTRAINT PK_StructuredSourceSnapshot PRIMARY KEY (DocID, VersionNo, SnapshotID),
        CONSTRAINT FK_StructuredSourceSnapshot_Document FOREIGN KEY (DocID) REFERENCES dbo.TaiLieu(DocID),
        CONSTRAINT CK_StructuredSourceSnapshot_Hash CHECK (LEN(SourceHash) = 64)
    );
    CREATE UNIQUE INDEX UX_StructuredSourceSnapshot_Current
        ON dbo.StructuredSourceSnapshot(DocID) WHERE CurrentSnapshot = 1;
END;
GO
IF OBJECT_ID(N'dbo.StructuredEvidenceScope', N'U') IS NULL
BEGIN
    CREATE TABLE dbo.StructuredEvidenceScope (
        ScopeID NVARCHAR(255) NOT NULL PRIMARY KEY,
        SnapshotID NVARCHAR(128) NOT NULL,
        ManifestJson NVARCHAR(MAX) NOT NULL,
        CompletenessReviewed BIT NOT NULL DEFAULT 0,
        ReviewedBy INT NULL,
        ReviewNote NVARCHAR(2000) NULL,
        UpdatedAt DATETIME2 NOT NULL DEFAULT SYSUTCDATETIME(),
        CONSTRAINT CK_StructuredEvidenceScope_Manifest CHECK (ISJSON(ManifestJson) = 1)
    );
END;
GO
IF OBJECT_ID(N'dbo.StructuredNumericFact', N'U') IS NULL
BEGIN
    CREATE TABLE dbo.StructuredNumericFact (
        FactID NVARCHAR(450) NOT NULL PRIMARY KEY,
        ScopeID NVARCHAR(255) NOT NULL,
        SourceDocID INT NOT NULL,
        SourceVersion INT NOT NULL,
        SourceHash VARCHAR(64) NOT NULL,
        PayloadJson NVARCHAR(MAX) NOT NULL,
        ReviewedPayloadJson NVARCHAR(MAX) NULL,
        ReviewStatus VARCHAR(16) NOT NULL DEFAULT 'pending',
        ReviewedBy INT NULL,
        ReviewNote NVARCHAR(2000) NULL,
        UpdatedAt DATETIME2 NOT NULL DEFAULT SYSUTCDATETIME(),
        CONSTRAINT FK_StructuredNumericFact_Scope FOREIGN KEY (ScopeID) REFERENCES dbo.StructuredEvidenceScope(ScopeID),
        CONSTRAINT FK_StructuredNumericFact_Document FOREIGN KEY (SourceDocID) REFERENCES dbo.TaiLieu(DocID),
        CONSTRAINT CK_StructuredNumericFact_Payload CHECK (ISJSON(PayloadJson) = 1),
        CONSTRAINT CK_StructuredNumericFact_Review CHECK (ReviewStatus IN ('pending', 'approved', 'rejected'))
    );
    CREATE INDEX IX_StructuredNumericFact_Document ON dbo.StructuredNumericFact(SourceDocID, SourceVersion, ReviewStatus);
END;
GO
IF OBJECT_ID(N'dbo.ReviewedAssemblyBindings', N'U') IS NULL
BEGIN
    CREATE TABLE dbo.ReviewedAssemblyBindings (
        BindingID INT IDENTITY(1,1) NOT NULL PRIMARY KEY,
        PartKey NVARCHAR(255) NOT NULL,
        ProductScope NVARCHAR(255) NOT NULL,
        Site NVARCHAR(100) NOT NULL,
        DocID INT NOT NULL,
        VersionNo INT NOT NULL,
        SourceDocID INT NOT NULL,
        SourceVersion INT NOT NULL,
        SourcePage INT NOT NULL,
        SourceQuote NVARCHAR(2000) NOT NULL,
        ReviewStatus VARCHAR(16) NOT NULL DEFAULT 'pending',
        ReviewedBy INT NULL,
        ReviewedAt DATETIME2 NULL,
        CONSTRAINT FK_ReviewedAssemblyBindings_Doc FOREIGN KEY (DocID) REFERENCES dbo.TaiLieu(DocID),
        CONSTRAINT FK_ReviewedAssemblyBindings_Source FOREIGN KEY (SourceDocID) REFERENCES dbo.TaiLieu(DocID),
        CONSTRAINT CK_ReviewedAssemblyBindings_Review CHECK (ReviewStatus IN ('pending', 'approved', 'rejected')),
        CONSTRAINT CK_ReviewedAssemblyBindings_Page CHECK (SourcePage > 0)
    );
    CREATE INDEX IX_ReviewedAssemblyBindings_Part ON dbo.ReviewedAssemblyBindings(ProductScope, PartKey, Site, ReviewStatus);
END;
GO
IF OBJECT_ID(N'dbo.ReviewedGraphOccurrences', N'U') IS NULL
BEGIN
    CREATE TABLE dbo.ReviewedGraphOccurrences (
        OccurrenceID NVARCHAR(200) NOT NULL PRIMARY KEY,
        EdgeID BIGINT NOT NULL,
        ProductScope NVARCHAR(255) NOT NULL,
        ParentDocID INT NOT NULL,
        ParentVersion INT NOT NULL,
        RowKey NVARCHAR(255) NOT NULL,
        PartKey NVARCHAR(255) NOT NULL,
        Quantity NVARCHAR(128) NOT NULL,
        Unit NVARCHAR(64) NOT NULL,
        QuantityBasis VARCHAR(32) NOT NULL,
        ChildBindingID INT NULL,
        IsLeaf BIT NOT NULL DEFAULT 0,
        ReviewStatus VARCHAR(16) NOT NULL DEFAULT 'pending',
        ReviewedBy INT NULL,
        ReviewedAt DATETIME2 NULL,
        SourcePage INT NOT NULL,
        SourceQuote NVARCHAR(2000) NOT NULL,
        Site NVARCHAR(100) NOT NULL,
        CONSTRAINT FK_ReviewedGraphOccurrences_Edge FOREIGN KEY (EdgeID) REFERENCES dbo.KnowledgeGraphEdge(EdgeID),
        CONSTRAINT FK_ReviewedGraphOccurrences_Parent FOREIGN KEY (ParentDocID) REFERENCES dbo.TaiLieu(DocID),
        CONSTRAINT FK_ReviewedGraphOccurrences_Binding FOREIGN KEY (ChildBindingID) REFERENCES dbo.ReviewedAssemblyBindings(BindingID),
        CONSTRAINT CK_ReviewedGraphOccurrences_Review CHECK (ReviewStatus IN ('pending', 'approved', 'rejected')),
        CONSTRAINT CK_ReviewedGraphOccurrences_Basis CHECK (QuantityBasis = 'per_parent'),
        CONSTRAINT CK_ReviewedGraphOccurrences_Page CHECK (SourcePage > 0),
        CONSTRAINT CK_ReviewedGraphOccurrences_Leaf CHECK (IsLeaf = 0 OR ChildBindingID IS NULL)
    );
    CREATE UNIQUE INDEX UX_ReviewedGraphOccurrences_Row
        ON dbo.ReviewedGraphOccurrences(ProductScope, ParentDocID, ParentVersion, RowKey);
END;
GO
IF OBJECT_ID(N'dbo.ReviewedGraphDeclarations', N'U') IS NULL
BEGIN
    CREATE TABLE dbo.ReviewedGraphDeclarations (
        DeclarationID INT IDENTITY(1,1) NOT NULL PRIMARY KEY,
        ProductScope NVARCHAR(255) NOT NULL,
        DocID INT NOT NULL,
        VersionNo INT NOT NULL,
        SnapshotID NVARCHAR(128) NOT NULL,
        RowsComplete BIT NOT NULL DEFAULT 0,
        TreeComplete BIT NOT NULL DEFAULT 0,
        ReviewStatus VARCHAR(16) NOT NULL DEFAULT 'pending',
        ReviewedBy INT NULL,
        ReviewedAt DATETIME2 NULL,
        SourcePage INT NOT NULL,
        SourceQuote NVARCHAR(2000) NOT NULL,
        ExpectedRows INT NOT NULL,
        SourceHash NVARCHAR(64) NOT NULL,
        CONSTRAINT FK_ReviewedGraphDeclarations_Doc FOREIGN KEY (DocID) REFERENCES dbo.TaiLieu(DocID),
        CONSTRAINT CK_ReviewedGraphDeclarations_Review CHECK (ReviewStatus IN ('pending', 'approved', 'rejected')),
        CONSTRAINT CK_ReviewedGraphDeclarations_Rows CHECK (ExpectedRows >= 0),
        CONSTRAINT CK_ReviewedGraphDeclarations_Page CHECK (SourcePage > 0),
        CONSTRAINT CK_ReviewedGraphDeclarations_Hash CHECK (LEN(SourceHash) = 64)
    );
    CREATE UNIQUE INDEX UX_ReviewedGraphDeclarations_Scope
        ON dbo.ReviewedGraphDeclarations(ProductScope, DocID, VersionNo, SnapshotID);
END;
GO
