-- Versioned, reviewed community summaries. Generated content is pending by default.
IF OBJECT_ID(N'dbo.GraphCommunityVersion', N'U') IS NULL
BEGIN
    CREATE TABLE dbo.GraphCommunityVersion (
        CommunityVersionID BIGINT IDENTITY(1,1) NOT NULL PRIMARY KEY,
        DetectionVersion NVARCHAR(100) NOT NULL,
        ServingEpoch NVARCHAR(100) NOT NULL,
        GraphFingerprint CHAR(64) NOT NULL,
        Algorithm NVARCHAR(100) NOT NULL,
        ParametersJson NVARCHAR(MAX) NULL,
        PrerequisiteGraphGatePassed BIT NOT NULL,
        GraphGateArtifactSha CHAR(64) NOT NULL,
        StructuredCoverage DECIMAL(6,5) NOT NULL,
        ReviewedEdgePrecision DECIMAL(6,5) NOT NULL,
        EvalManifestSha CHAR(64) NOT NULL,
        MinGlobalAnswerGain DECIMAL(6,5) NOT NULL,
        Status NVARCHAR(30) NOT NULL CONSTRAINT DF_GraphCommunityVersion_Status DEFAULT 'pending',
        CreatedBy NVARCHAR(255) NULL,
        ReviewedBy NVARCHAR(255) NULL,
        ReviewNote NVARCHAR(1000) NULL,
        CreatedAt DATETIME2 NOT NULL CONSTRAINT DF_GraphCommunityVersion_Created DEFAULT SYSUTCDATETIME(),
        ReviewedAt DATETIME2 NULL,
        CONSTRAINT UQ_GraphCommunityVersion UNIQUE (DetectionVersion, ServingEpoch, GraphFingerprint),
        CONSTRAINT CK_GraphCommunityVersion_Status CHECK (Status IN ('pending','approved','rejected','disabled')),
        CONSTRAINT CK_GraphCommunityVersion_Parameters CHECK (ParametersJson IS NULL OR ISJSON(ParametersJson)=1),
        CONSTRAINT CK_GraphCommunityVersion_Readiness CHECK (
            Status <> 'approved' OR (
                PrerequisiteGraphGatePassed=1
                AND StructuredCoverage >= 0.80000
                AND ReviewedEdgePrecision >= 0.95000
                AND MinGlobalAnswerGain > 0
            )
        )
    );
END
GO

IF OBJECT_ID(N'dbo.GraphCommunityMembership', N'U') IS NULL
BEGIN
    CREATE TABLE dbo.GraphCommunityMembership (
        CommunityVersionID BIGINT NOT NULL,
        CommunityKey NVARCHAR(200) NOT NULL,
        NodeID BIGINT NOT NULL,
        CreatedAt DATETIME2 NOT NULL CONSTRAINT DF_GraphCommunityMembership_Created DEFAULT SYSUTCDATETIME(),
        CONSTRAINT PK_GraphCommunityMembership PRIMARY KEY (CommunityVersionID, CommunityKey, NodeID),
        CONSTRAINT FK_GraphCommunityMembership_Version FOREIGN KEY (CommunityVersionID)
            REFERENCES dbo.GraphCommunityVersion(CommunityVersionID),
        CONSTRAINT FK_GraphCommunityMembership_Node FOREIGN KEY (NodeID)
            REFERENCES dbo.KnowledgeGraphNode(NodeID)
    );
END
GO

IF OBJECT_ID(N'dbo.GraphCommunitySummary', N'U') IS NULL
BEGIN
    CREATE TABLE dbo.GraphCommunitySummary (
        SummaryID BIGINT IDENTITY(1,1) NOT NULL PRIMARY KEY,
        CommunityVersionID BIGINT NOT NULL,
        CommunityKey NVARCHAR(200) NOT NULL,
        SummaryText NVARCHAR(MAX) NOT NULL,
        SummarySha256 CHAR(64) NOT NULL,
        NodeKeysJson NVARCHAR(MAX) NOT NULL,
        EdgeIDsJson NVARCHAR(MAX) NOT NULL,
        SourceProvenanceJson NVARCHAR(MAX) NOT NULL,
        Status NVARCHAR(30) NOT NULL CONSTRAINT DF_GraphCommunitySummary_Status DEFAULT 'pending',
        GeneratedBy NVARCHAR(255) NULL,
        ReviewedBy NVARCHAR(255) NULL,
        ReviewNote NVARCHAR(1000) NULL,
        CreatedAt DATETIME2 NOT NULL CONSTRAINT DF_GraphCommunitySummary_Created DEFAULT SYSUTCDATETIME(),
        ReviewedAt DATETIME2 NULL,
        CONSTRAINT UQ_GraphCommunitySummary UNIQUE (CommunityVersionID, CommunityKey, SummarySha256),
        CONSTRAINT CK_GraphCommunitySummary_Status CHECK (Status IN ('pending','approved','rejected','disabled')),
        CONSTRAINT CK_GraphCommunitySummary_Nodes CHECK (ISJSON(NodeKeysJson)=1),
        CONSTRAINT CK_GraphCommunitySummary_Edges CHECK (ISJSON(EdgeIDsJson)=1),
        CONSTRAINT CK_GraphCommunitySummary_Provenance CHECK (ISJSON(SourceProvenanceJson)=1),
        CONSTRAINT FK_GraphCommunitySummary_Version FOREIGN KEY (CommunityVersionID)
            REFERENCES dbo.GraphCommunityVersion(CommunityVersionID)
    );
END
GO

IF NOT EXISTS (
    SELECT 1 FROM sys.indexes
    WHERE name='IX_GraphCommunitySummary_Status'
      AND object_id=OBJECT_ID(N'dbo.GraphCommunitySummary')
)
    CREATE INDEX IX_GraphCommunitySummary_Status
    ON dbo.GraphCommunitySummary (Status, CommunityVersionID, CommunityKey);
GO
