"""Execute retry UPDATEs against SQLite with the SQL Server boundary adapted."""

from contextlib import contextmanager
from fnmatch import fnmatchcase

import pytest
from sqlalchemy import create_engine, text

from mech_chatbot.db.repositories import jobs


pytestmark = pytest.mark.unit


class _SqliteJobEngine:
    def __init__(self, retry_count, max_retry):
        self.engine = create_engine("sqlite://")
        with self.engine.begin() as connection:
            raw = connection.connection.driver_connection
            raw.create_function("CHARINDEX", 2, lambda needle, value: str(value).find(needle) + 1)
            # SQLite LIKE treats brackets literally; match SQL Server's bracket
            # sets here so the original AUTH_ERROR pattern reproduces the bug.
            raw.create_function("like", 2, lambda pattern, value: int(fnmatchcase(
                str(value).lower(), pattern.lower().replace("%", "*").replace("_", "?"))))
            connection.exec_driver_sql("ATTACH DATABASE ':memory:' AS dbo")
            connection.exec_driver_sql("""CREATE TABLE dbo.IngestionJobs (
                JobID INTEGER PRIMARY KEY, RetryCount INTEGER, MaxRetry INTEGER,
                Status TEXT, ErrorMessage TEXT, LockedBy TEXT, LockedAt TEXT,
                ProgressPercent INTEGER, UpdatedAt TEXT, FailureType TEXT, NextRetryAt TEXT)""")
            connection.execute(text("""INSERT INTO dbo.IngestionJobs
                (JobID, RetryCount, MaxRetry, Status, LockedBy, LockedAt, ProgressPercent)
                VALUES (17, :retry, :max_retry, 'processing', 'worker-1', 'locked', 50)"""),
                {"retry": retry_count, "max_retry": max_retry})

    @contextmanager
    def begin(self):
        with self.engine.begin() as connection:
            class Boundary:
                def execute(self, statement, params):
                    sql = str(statement).replace("ISNULL(", "IFNULL(").replace("GETDATE()", "CURRENT_TIMESTAMP")
                    sql = sql.replace("DATEADD(hour, :h, CURRENT_TIMESTAMP)", "datetime('now', '+' || :h || ' hours')")
                    return connection.execute(text(sql), params)
            yield Boundary()

    def job(self):
        with self.engine.connect() as connection:
            return connection.execute(text("SELECT * FROM dbo.IngestionJobs WHERE JobID = 17")).mappings().one()


@pytest.mark.parametrize("message,retry,max_retry,status", [
    ("temporary timeout", 0, 3, "pending_retry"),
    ("extract failed", 1, 3, "pending_retry"),
    ("[AUTH_ERROR] invalid credentials", 0, 3, "failed"),
    ("AUTH_ERROR without marker", 0, 3, "pending_retry"),
    ("[AUTH_ERRORISH] temporary provider failure", 0, 3, "pending_retry"),
    ("temporary timeout", 2, 3, "failed"),
    ("temporary timeout", None, None, "pending_retry"),
])
def test_mark_job_failed_uses_literal_auth_marker_and_retry_limit(message, retry, max_retry, status):
    engine = _SqliteJobEngine(retry, max_retry)
    try:
        jobs.mark_job_failed(17, message, db_engine=engine)
        row = engine.job()
        assert row["Status"] == status
        assert row["RetryCount"] == (retry or 0) + 1
        assert row["ErrorMessage"] == message
        assert row["LockedBy"] is row["LockedAt"] is None
        assert row["ProgressPercent"] == 0
    finally:
        engine.engine.dispose()


def test_quota_failure_keeps_separate_retry_window_and_retry_count():
    engine = _SqliteJobEngine(1, 3)
    try:
        jobs.mark_job_failed(17, "[QUOTA_EXCEEDED] try later", db_engine=engine)
        row = engine.job()
        assert row["Status"] == "waiting_quota"
        assert row["FailureType"] == "vision_quota"
        assert row["RetryCount"] == 1
        assert row["NextRetryAt"] is not None
        assert row["LockedBy"] is row["LockedAt"] is None
    finally:
        engine.engine.dispose()
