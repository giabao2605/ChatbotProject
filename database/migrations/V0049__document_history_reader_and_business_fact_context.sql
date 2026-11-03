-- GD4: add an explicit historical-reader role and document-context fields.
-- No user is granted the role here; administrators must assign it explicitly.
IF NOT EXISTS (SELECT 1 FROM dbo.Roles WHERE RoleName = 'document_history_reader')
BEGIN
    INSERT INTO dbo.Roles (RoleName) VALUES ('document_history_reader');
END;
GO
IF NOT EXISTS (SELECT 1 FROM dbo._SchemaVersions WHERE Version = 'V0049')
BEGIN
    INSERT INTO dbo._SchemaVersions (Version, Description)
    VALUES ('V0049', 'GD4 historical reader role; business fact context remains source-bound JSON');
END;
GO
