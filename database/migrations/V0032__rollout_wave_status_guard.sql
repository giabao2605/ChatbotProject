-- V0032: Close the database-side gap that allowed pilot outside Wave 1.

CREATE OR ALTER TRIGGER dbo.TR_DepartmentRolloutPlan_Invariants
ON dbo.DepartmentRolloutPlan
AFTER INSERT, UPDATE
AS
BEGIN
    SET NOCOUNT ON;

    IF EXISTS (
        SELECT 1 FROM dbo.DepartmentRolloutPlan
        GROUP BY WaveNumber
        HAVING COUNT(*) > CASE WaveNumber WHEN 1 THEN 3 ELSE 4 END
    )
        THROW 51030, 'Wave capacity exceeds the 3 -> 4 -> 4 -> 4 rollout plan.', 1;

    IF EXISTS (SELECT 1 FROM inserted WHERE RolloutStatus = 'pilot' AND WaveNumber <> 1)
        THROW 51033, 'Pilot status is reserved for Wave 1.', 1;

    IF EXISTS (
        SELECT 1 FROM inserted i JOIN deleted d ON d.DeptCode = i.DeptCode
        WHERE i.WaveNumber <> d.WaveNumber AND d.RolloutStatus NOT IN ('planned', 'blocked')
    )
        THROW 51031, 'An operated department cannot be reassigned to another wave.', 1;

    IF EXISTS (
        SELECT 1 FROM inserted i JOIN deleted d ON d.DeptCode = i.DeptCode
        WHERE NOT (
            (d.RolloutStatus = 'planned' AND i.RolloutStatus IN ('planned', 'pilot', 'dark_launch', 'blocked'))
            OR (d.RolloutStatus = 'pilot' AND i.RolloutStatus IN ('pilot', 'dark_launch', 'active', 'blocked'))
            OR (d.RolloutStatus = 'dark_launch' AND i.RolloutStatus IN ('dark_launch', 'active', 'blocked'))
            OR (d.RolloutStatus = 'blocked' AND i.RolloutStatus IN ('blocked', 'planned'))
            OR (d.RolloutStatus = 'active' AND i.RolloutStatus = 'active')
        )
    )
        THROW 51032, 'Invalid department rollout status transition.', 1;
END;
GO

IF NOT EXISTS (SELECT 1 FROM dbo._SchemaVersions WHERE Version = 'V0032')
    INSERT INTO dbo._SchemaVersions (Version, Description, AppliedAt)
    VALUES ('V0032', 'Restrict pilot rollout status to Wave 1', GETDATE());
GO
