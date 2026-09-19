-- Governed SQL Server graph. LLM proposals remain isolated until reviewed.
IF OBJECT_ID(N'dbo.KnowledgeGraphNode', N'U') IS NULL
BEGIN
    CREATE TABLE dbo.KnowledgeGraphNode (
        NodeID BIGINT IDENTITY(1,1) NOT NULL PRIMARY KEY,
        NodeType NVARCHAR(50) NOT NULL,
        CanonicalKey NVARCHAR(500) NOT NULL,
        DisplayName NVARCHAR(500) NULL,
        SourceDocID INT NULL,
        SourcePage INT NULL,
        SourceVersion INT NULL,
        Department NVARCHAR(100) NULL,
        Site NVARCHAR(100) NULL,
        SecurityLevel NVARCHAR(30) NOT NULL CONSTRAINT DF_KGNode_Security DEFAULT 'confidential',
        CreatedAt DATETIME2 NOT NULL CONSTRAINT DF_KGNode_Created DEFAULT SYSUTCDATETIME(),
        CONSTRAINT UQ_KGNode_TypeKey UNIQUE (NodeType, CanonicalKey),
        CONSTRAINT FK_KGNode_Document FOREIGN KEY (SourceDocID) REFERENCES dbo.TaiLieu(DocID)
    );
END
GO

IF OBJECT_ID(N'dbo.KnowledgeGraphEdge', N'U') IS NULL
BEGIN
    CREATE TABLE dbo.KnowledgeGraphEdge (
        EdgeID BIGINT IDENTITY(1,1) NOT NULL PRIMARY KEY,
        SourceNodeID BIGINT NOT NULL,
        TargetNodeID BIGINT NOT NULL,
        RelationType NVARCHAR(100) NOT NULL,
        Origin NVARCHAR(30) NOT NULL,
        ServingStatus NVARCHAR(30) NOT NULL,
        Confidence DECIMAL(6,5) NULL,
        SourceDocID INT NOT NULL,
        SourcePage INT NOT NULL,
        SourceVersion INT NOT NULL,
        Department NVARCHAR(100) NULL,
        Site NVARCHAR(100) NULL,
        SecurityLevel NVARCHAR(30) NOT NULL CONSTRAINT DF_KGEdge_Security DEFAULT 'confidential',
        ReviewedBy NVARCHAR(255) NULL,
        ReviewedAt DATETIME2 NULL,
        CreatedAt DATETIME2 NOT NULL CONSTRAINT DF_KGEdge_Created DEFAULT SYSUTCDATETIME(),
        CONSTRAINT CK_KGEdge_Origin CHECK (Origin IN ('deterministic', 'llm')),
        CONSTRAINT CK_KGEdge_Status CHECK (ServingStatus IN ('approved', 'rejected', 'disabled')),
        CONSTRAINT UQ_KGEdge UNIQUE (SourceNodeID, TargetNodeID, RelationType, SourceDocID, SourcePage),
        CONSTRAINT FK_KGEdge_SourceNode FOREIGN KEY (SourceNodeID) REFERENCES dbo.KnowledgeGraphNode(NodeID),
        CONSTRAINT FK_KGEdge_TargetNode FOREIGN KEY (TargetNodeID) REFERENCES dbo.KnowledgeGraphNode(NodeID),
        CONSTRAINT FK_KGEdge_Document FOREIGN KEY (SourceDocID) REFERENCES dbo.TaiLieu(DocID)
    );
END
GO

IF OBJECT_ID(N'dbo.GraphExtractionProposal', N'U') IS NULL
BEGIN
    CREATE TABLE dbo.GraphExtractionProposal (
        ProposalID BIGINT IDENTITY(1,1) NOT NULL PRIMARY KEY,
        SourceNodeID BIGINT NOT NULL,
        TargetNodeID BIGINT NOT NULL,
        RelationType NVARCHAR(100) NOT NULL,
        SourceDocID INT NOT NULL,
        SourcePage INT NOT NULL,
        SourceVersion INT NOT NULL,
        Confidence DECIMAL(6,5) NULL,
        EvidenceJson NVARCHAR(MAX) NULL,
        Status NVARCHAR(30) NOT NULL CONSTRAINT DF_GraphProposal_Status DEFAULT 'pending',
        ProposedBy NVARCHAR(255) NULL,
        ReviewedBy NVARCHAR(255) NULL,
        ReviewNote NVARCHAR(1000) NULL,
        CreatedAt DATETIME2 NOT NULL CONSTRAINT DF_GraphProposal_Created DEFAULT SYSUTCDATETIME(),
        ReviewedAt DATETIME2 NULL,
        CONSTRAINT CK_GraphProposal_Status CHECK (Status IN ('pending', 'approved', 'rejected')),
        CONSTRAINT FK_GraphProposal_SourceNode FOREIGN KEY (SourceNodeID) REFERENCES dbo.KnowledgeGraphNode(NodeID),
        CONSTRAINT FK_GraphProposal_TargetNode FOREIGN KEY (TargetNodeID) REFERENCES dbo.KnowledgeGraphNode(NodeID),
        CONSTRAINT FK_GraphProposal_Document FOREIGN KEY (SourceDocID) REFERENCES dbo.TaiLieu(DocID)
    );
END
GO

IF NOT EXISTS (SELECT 1 FROM sys.indexes WHERE name = 'IX_KGEdge_SourceServing' AND object_id = OBJECT_ID(N'dbo.KnowledgeGraphEdge'))
    CREATE INDEX IX_KGEdge_SourceServing ON dbo.KnowledgeGraphEdge (SourceNodeID, ServingStatus, RelationType);
GO
IF NOT EXISTS (SELECT 1 FROM sys.indexes WHERE name = 'IX_GraphProposal_Status' AND object_id = OBJECT_ID(N'dbo.GraphExtractionProposal'))
    CREATE INDEX IX_GraphProposal_Status ON dbo.GraphExtractionProposal (Status, CreatedAt DESC);
GO
