-- V0017__add_user_preferred_language.sql
-- Luu ngon ngu UI uu tien theo tung tai khoan de login lai van giu lua chon cu.
-- Idempotent + tuong thich nguoc: mac dinh 'vi', chi chap nhan 'vi' hoac 'en'.
SET NOCOUNT ON;
GO

IF COL_LENGTH('dbo.Users','PreferredLanguage') IS NULL
    ALTER TABLE dbo.Users ADD PreferredLanguage NVARCHAR(10) NOT NULL CONSTRAINT DF_Users_PreferredLanguage DEFAULT 'vi';
GO

UPDATE dbo.Users
SET PreferredLanguage = 'vi'
WHERE PreferredLanguage IS NULL OR PreferredLanguage NOT IN ('vi','en');
GO

IF NOT EXISTS (
    SELECT 1
    FROM sys.check_constraints
    WHERE name = 'CHK_Users_PreferredLanguage'
      AND parent_object_id = OBJECT_ID(N'dbo.Users')
)
    ALTER TABLE dbo.Users WITH NOCHECK
    ADD CONSTRAINT CHK_Users_PreferredLanguage CHECK (PreferredLanguage IN ('vi','en'));
GO

IF NOT EXISTS (SELECT 1 FROM dbo._SchemaVersions WHERE Version = 'V0017')
    INSERT INTO dbo._SchemaVersions (Version, Description)
    VALUES ('V0017', 'Them Users.PreferredLanguage de luu ngon ngu UI theo tai khoan.');
GO
