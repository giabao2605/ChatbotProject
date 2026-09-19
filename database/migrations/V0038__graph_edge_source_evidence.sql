-- Replace synthetic provenance labels with evidence captured from the source
-- page or structured BOM row. Approved deterministic edges without source
-- evidence remain fail-closed.
UPDATE dbo.KnowledgeGraphEdge
SET ServingStatus = 'disabled', SourceQuote = NULL
WHERE Origin = 'deterministic'
  AND SourceQuote LIKE N'Approved deterministic relation %';
GO

UPDATE edge
SET SourceQuote = LEFT(COALESCE(
        NULLIF(LTRIM(RTRIM(page.TextExtract)), N''),
        NULLIF(LTRIM(RTRIM(page.LocalOCRText)), N''),
        NULLIF(LTRIM(RTRIM(page.VisionSummary)), N'')
    ), 2000)
FROM dbo.KnowledgeGraphEdge edge
JOIN dbo.DocumentPages page
  ON page.DocID = edge.SourceDocID
 AND page.PageNo = edge.SourcePage
WHERE edge.Origin = 'deterministic'
  AND edge.RelationType IN ('HAS_VERSION', 'SUPERSEDES', 'HAS_PAGE')
  AND COALESCE(
        NULLIF(LTRIM(RTRIM(page.TextExtract)), N''),
        NULLIF(LTRIM(RTRIM(page.LocalOCRText)), N''),
        NULLIF(LTRIM(RTRIM(page.VisionSummary)), N'')
      ) IS NOT NULL;
GO

UPDATE edge
SET SourceQuote = LEFT(NULLIF(LTRIM(RTRIM(bom.RawRowJson)), N''), 2000)
FROM dbo.KnowledgeGraphEdge edge
JOIN dbo.KnowledgeGraphNode source_node ON source_node.NodeID = edge.SourceNodeID
JOIN dbo.KnowledgeGraphNode target_node ON target_node.NodeID = edge.TargetNodeID
JOIN dbo.BangKeVatTu bom
  ON bom.DocID = edge.SourceDocID
 AND bom.TrangSo = edge.SourcePage
 AND (
      (edge.RelationType = 'CONTAINS_PART'
       AND target_node.CanonicalKey = 'part:' + LOWER(LTRIM(RTRIM(bom.MaHang))))
      OR
      (edge.RelationType = 'USES_MATERIAL'
       AND source_node.CanonicalKey = 'part:' + LOWER(LTRIM(RTRIM(bom.MaHang)))
       AND target_node.CanonicalKey = 'material:' + LOWER(LTRIM(RTRIM(
           COALESCE(NULLIF(bom.NormalizedMaterial, ''), bom.VatLieu)
       ))))
 )
WHERE edge.Origin = 'deterministic'
  AND edge.RelationType IN ('CONTAINS_PART', 'USES_MATERIAL')
  AND NULLIF(LTRIM(RTRIM(bom.RawRowJson)), N'') IS NOT NULL;
GO

UPDATE dbo.KnowledgeGraphEdge
SET ServingStatus = 'disabled'
WHERE Origin = 'deterministic'
  AND ServingStatus = 'approved'
  AND NULLIF(LTRIM(RTRIM(SourceQuote)), N'') IS NULL;
GO

UPDATE edge
SET ServingStatus = 'approved'
FROM dbo.KnowledgeGraphEdge edge
JOIN dbo.TaiLieu document ON document.DocID = edge.SourceDocID
WHERE edge.Origin = 'deterministic'
  AND edge.ServingStatus = 'disabled'
  AND NULLIF(LTRIM(RTRIM(edge.SourceQuote)), N'') IS NOT NULL
  AND document.Servable = 1
  AND document.IsCurrent = 1
  AND document.PublicationState = 'published'
  AND document.LifecycleStatus = 'published'
  AND document.ReviewStatus = 'approved';
GO
