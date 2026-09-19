-- Fail closed any approved community version below the locked 10-point gain.
IF OBJECT_ID(N'dbo.GraphCommunityVersion', N'U') IS NULL RETURN;
GO

UPDATE dbo.GraphCommunityVersion
SET Status = 'disabled'
WHERE Status = 'approved'
  AND MinGlobalAnswerGain < 0.10000;
GO

IF EXISTS (
    SELECT 1 FROM sys.check_constraints
    WHERE name = 'CK_GraphCommunityVersion_Readiness'
      AND parent_object_id = OBJECT_ID(N'dbo.GraphCommunityVersion')
)
    ALTER TABLE dbo.GraphCommunityVersion
    DROP CONSTRAINT CK_GraphCommunityVersion_Readiness;
GO

ALTER TABLE dbo.GraphCommunityVersion WITH CHECK
ADD CONSTRAINT CK_GraphCommunityVersion_Readiness CHECK (
    Status <> 'approved' OR (
        PrerequisiteGraphGatePassed=1
        AND StructuredCoverage >= 0.80000
        AND ReviewedEdgePrecision >= 0.95000
        AND MinGlobalAnswerGain >= 0.10000
    )
);
GO
