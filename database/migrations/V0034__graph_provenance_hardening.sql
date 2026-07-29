-- Fail closed on graph evidence that cannot identify a positive source page.
UPDATE dbo.KnowledgeGraphEdge SET ServingStatus='disabled' WHERE SourcePage <= 0;
UPDATE dbo.GraphExtractionProposal SET Status='rejected', ReviewNote='invalid source page' WHERE SourcePage <= 0 AND Status='pending';
GO

IF NOT EXISTS (SELECT 1 FROM sys.check_constraints WHERE name='CK_KGEdge_SourcePage')
    ALTER TABLE dbo.KnowledgeGraphEdge WITH CHECK
    ADD CONSTRAINT CK_KGEdge_SourcePage CHECK (SourcePage > 0 OR ServingStatus='disabled');
GO

IF NOT EXISTS (SELECT 1 FROM sys.check_constraints WHERE name='CK_GraphProposal_SourcePage')
    ALTER TABLE dbo.GraphExtractionProposal WITH CHECK
    ADD CONSTRAINT CK_GraphProposal_SourcePage CHECK (SourcePage > 0 OR Status='rejected');
GO
