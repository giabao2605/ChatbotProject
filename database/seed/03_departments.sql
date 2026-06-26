-- ============================================================================
-- Seed 14 PHONG BAN (data-driven) — nguon su that cho phong ban/domain/muc mat
-- Idempotent qua MERGE theo DeptCode. Chay sau baseline.
-- Domain: mechanical | tabular | generic
-- DefaultSecurity: public | internal | confidential
-- ============================================================================
USE Mech_Chatbot_DB;
GO

;WITH seed AS (
    SELECT * FROM (VALUES
        (N'Technical',      N'Technical',          N'mechanical', N'internal',     N'04.Technical'),
        (N'Production',     N'Production',         N'mechanical', N'internal',     N'08.Production'),
        (N'Maintenance',    N'Maintenance',        N'mechanical', N'internal',     N'11.Maintenance'),
        (N'Molding',        N'Molding',            N'mechanical', N'internal',     N'14.Molding'),
        (N'Accountant',     N'Accountant',         N'tabular',    N'confidential', N'02.Accountant'),
        (N'Purchasing',     N'Purchasing',         N'tabular',    N'internal',     N'07.Purchasing'),
        (N'Warehouse',      N'Warehouse',          N'tabular',    N'internal',     N'10.Warehouse'),
        (N'Sales',          N'Sales',              N'tabular',    N'internal',     N'05.Sales'),
        (N'HR',             N'Human Resources',    N'generic',    N'confidential', N'03.Human resources'),
        (N'Planning',       N'Planning',           N'generic',    N'internal',     N'06.Planning'),
        (N'QualityControl', N'Quality Control',    N'generic',    N'internal',     N'09.Quality Control'),
        (N'ISO',            N'ISO',                N'generic',    N'internal',     N'13.ISO'),
        (N'HSE_5S',         N'HSE & 5S',           N'generic',    N'internal',     N'12.HSE & 5S'),
        (N'IT',             N'IT',                 N'generic',    N'internal',     N'15.IT')
    ) v(DeptCode, DeptName, Domain, DefaultSecurity, FolderGoc)
)
MERGE dbo.Departments AS tgt
USING seed AS src ON tgt.DeptCode = src.DeptCode
WHEN MATCHED THEN UPDATE SET
    tgt.DeptName        = src.DeptName,
    tgt.Domain          = src.Domain,
    tgt.DefaultSecurity = src.DefaultSecurity,
    tgt.FolderGoc       = src.FolderGoc,
    tgt.IsActive        = 1
WHEN NOT MATCHED THEN
    INSERT (DeptCode, DeptName, Domain, DefaultSecurity, FolderGoc, IsActive)
    VALUES (src.DeptCode, src.DeptName, src.Domain, src.DefaultSecurity, src.FolderGoc, 1);
GO

PRINT 'Seed 14 phong ban hoan tat.';
GO
