-- Additive source pins. Legacy bindings stay unavailable until a new proposal is reviewed.
-- Apply to staging first; take a backup before production. No backfill or approval changes.
IF COL_LENGTH('dbo.ReviewedAssemblyBindings', 'SourceHash') IS NULL
    ALTER TABLE dbo.ReviewedAssemblyBindings ADD SourceHash VARCHAR(64) NULL;
GO
IF COL_LENGTH('dbo.ReviewedAssemblyBindings', 'SnapshotID') IS NULL
    ALTER TABLE dbo.ReviewedAssemblyBindings ADD SnapshotID NVARCHAR(128) NULL;
GO
