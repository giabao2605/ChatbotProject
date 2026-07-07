-- V0016__add_rejectreason_to_ingestionjobs.sql
-- Fix loi: (pyodbc.ProgrammingError) Invalid column name 'RejectReason'.
-- Ham reject_ingestion_job() ghi ly do tu choi vao IngestionJobs.RejectReason,
-- nhung cot nay chua ton tai trong baseline lan cac migration truoc.
-- Idempotent + tuong thich nguoc (chi them cot NULL, khong doi du lieu/logic).
SET NOCOUNT ON;
GO

IF COL_LENGTH('dbo.IngestionJobs','RejectReason') IS NULL
    ALTER TABLE dbo.IngestionJobs ADD RejectReason NVARCHAR(MAX) NULL;
GO

IF NOT EXISTS (SELECT 1 FROM dbo._SchemaVersions WHERE Version = 'V0016')
    INSERT INTO dbo._SchemaVersions (Version, Description)
    VALUES ('V0016', 'Them cot IngestionJobs.RejectReason de luu ly do tu choi tai lieu (fix Invalid column name).');
GO
