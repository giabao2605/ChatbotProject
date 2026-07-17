-- Every graph edge that reaches serving must carry auditable provenance.
IF COL_LENGTH(N'dbo.KnowledgeGraphEdge', N'SourceQuote') IS NULL
    ALTER TABLE dbo.KnowledgeGraphEdge ADD SourceQuote NVARCHAR(2000) NULL;
GO

-- Prefer the exact quote captured with an approved extraction proposal.
UPDATE edge
SET SourceQuote = JSON_VALUE(proposal.EvidenceJson, '$.source_quote')
FROM dbo.KnowledgeGraphEdge edge
JOIN dbo.GraphExtractionProposal proposal
  ON proposal.Status = 'approved'
 AND proposal.SourceNodeID = edge.SourceNodeID
 AND proposal.TargetNodeID = edge.TargetNodeID
 AND proposal.RelationType = edge.RelationType
 AND proposal.SourceDocID = edge.SourceDocID
 AND proposal.SourcePage = edge.SourcePage
 AND proposal.SourceVersion = edge.SourceVersion
WHERE NULLIF(LTRIM(RTRIM(edge.SourceQuote)), '') IS NULL
  AND NULLIF(LTRIM(RTRIM(JSON_VALUE(proposal.EvidenceJson, '$.source_quote'))), '') IS NOT NULL;
GO

-- Deterministic edges receive a stable, source-addressable provenance
-- statement. It is not an LLM quotation and remains tied to the exact page.
UPDATE edge
SET SourceQuote = CONCAT(
        N'Approved deterministic relation ', edge.RelationType,
        N' between ', source_node.DisplayName, N' and ', target_node.DisplayName,
        N' from ', COALESCE(document.TenFile, CONCAT(N'DocID ', edge.SourceDocID)),
        N' page ', edge.SourcePage, N' version ', edge.SourceVersion
    )
FROM dbo.KnowledgeGraphEdge edge
JOIN dbo.KnowledgeGraphNode source_node ON source_node.NodeID = edge.SourceNodeID
JOIN dbo.KnowledgeGraphNode target_node ON target_node.NodeID = edge.TargetNodeID
LEFT JOIN dbo.TaiLieu document ON document.DocID = edge.SourceDocID
WHERE edge.Origin = 'deterministic'
  AND edge.SourcePage > 0
  AND NULLIF(LTRIM(RTRIM(edge.SourceQuote)), '') IS NULL;
GO

-- Existing approved rows without provenance are disabled rather than served.
UPDATE dbo.KnowledgeGraphEdge
SET ServingStatus = 'disabled'
WHERE ServingStatus = 'approved'
  AND NULLIF(LTRIM(RTRIM(SourceQuote)), '') IS NULL;
GO

IF NOT EXISTS (
    SELECT 1 FROM sys.check_constraints
    WHERE name = 'CK_KGEdge_SourceQuote'
      AND parent_object_id = OBJECT_ID(N'dbo.KnowledgeGraphEdge')
)
    ALTER TABLE dbo.KnowledgeGraphEdge WITH CHECK
    ADD CONSTRAINT CK_KGEdge_SourceQuote CHECK (
        ServingStatus <> 'approved'
        OR NULLIF(LTRIM(RTRIM(SourceQuote)), '') IS NOT NULL
    );
GO
