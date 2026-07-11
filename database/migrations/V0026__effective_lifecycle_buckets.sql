-- V0026: Canonical document effectiveness state and indexes for the unified
-- document catalogue lifecycle buckets.

IF COL_LENGTH('dbo.TaiLieu', 'EffectiveStatus') IS NOT NULL
BEGIN
    UPDATE dbo.TaiLieu
    SET EffectiveStatus = 'effective'
    WHERE EffectiveStatus IS NULL
       OR LTRIM(RTRIM(EffectiveStatus)) = ''
       OR LOWER(LTRIM(RTRIM(EffectiveStatus))) = 'active';

    DECLARE @EffectiveDefault SYSNAME;
    SELECT @EffectiveDefault = dc.name
    FROM sys.default_constraints dc
    JOIN sys.columns c
      ON c.object_id = dc.parent_object_id
     AND c.column_id = dc.parent_column_id
    WHERE dc.parent_object_id = OBJECT_ID('dbo.TaiLieu')
      AND c.name = 'EffectiveStatus';

    IF @EffectiveDefault IS NOT NULL
    BEGIN
        DECLARE @DropEffectiveDefaultSql NVARCHAR(MAX);
        SET @DropEffectiveDefaultSql =
            N'ALTER TABLE dbo.TaiLieu DROP CONSTRAINT ' + QUOTENAME(@EffectiveDefault);
        EXEC sys.sp_executesql @DropEffectiveDefaultSql;
    END

    IF NOT EXISTS (
        SELECT 1 FROM sys.default_constraints dc
        JOIN sys.columns c
          ON c.object_id = dc.parent_object_id
         AND c.column_id = dc.parent_column_id
        WHERE dc.parent_object_id = OBJECT_ID('dbo.TaiLieu')
          AND c.name = 'EffectiveStatus'
    )
        ALTER TABLE dbo.TaiLieu ADD CONSTRAINT DF_TaiLieu_EffectiveStatus
            DEFAULT 'effective' FOR EffectiveStatus;
END
GO

IF NOT EXISTS (
    SELECT 1 FROM sys.indexes
    WHERE object_id = OBJECT_ID('dbo.TaiLieu')
      AND name = 'IX_TaiLieu_ServableLifecycleBuckets'
)
    CREATE INDEX IX_TaiLieu_ServableLifecycleBuckets
        ON dbo.TaiLieu
           (LifecycleStatus, ReviewStatus, IsCurrent, Servable, PublicationState,
            EffectiveStatus, ExpiryDate, ReviewDate)
        INCLUDE (ThuMuc, SecurityLevel, Site, NgayTaiLen);
GO

IF NOT EXISTS (SELECT 1 FROM dbo._SchemaVersions WHERE Version = 'V0026')
    INSERT INTO dbo._SchemaVersions (Version, Description, AppliedAt)
    VALUES ('V0026', 'Canonical effective status and unified lifecycle bucket index', GETDATE());
GO
