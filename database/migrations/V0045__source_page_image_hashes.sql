-- Rendered image provenance is optional; rows without it cannot serve v2 previews.
IF COL_LENGTH('dbo.StructuredSourceSnapshot','PageImagesJson') IS NULL
    ALTER TABLE dbo.StructuredSourceSnapshot ADD PageImagesJson NVARCHAR(MAX) NULL;
GO
IF NOT EXISTS (SELECT 1 FROM sys.check_constraints WHERE name='CK_StructuredSourceSnapshot_PageImagesJson')
    ALTER TABLE dbo.StructuredSourceSnapshot ADD CONSTRAINT CK_StructuredSourceSnapshot_PageImagesJson
        CHECK (PageImagesJson IS NULL OR ISJSON(PageImagesJson)=1);
GO
