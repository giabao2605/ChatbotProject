from types import SimpleNamespace

import pytest

from scripts.ops import restore_drill as restore_module
from scripts.ops.restore_drill import PartialRestoreError, restore_sql_backup


class _Result:
    def __init__(self, *, scalar=None, rows=()):
        self._scalar = scalar
        self._rows = list(rows)

    def scalar_one_or_none(self):
        return self._scalar

    def mappings(self):
        return self

    def all(self):
        return list(self._rows)

    def one_or_none(self):
        if len(self._rows) > 1:
            raise AssertionError("expected at most one row")
        return self._rows[0] if self._rows else None


class _Connection:
    def __init__(self, *, database_states):
        self.database_ids = iter((None, None, 42))
        self.database_states = iter(database_states)
        self.calls = []

    def execute(self, statement, parameters=None):
        sql = str(statement)
        self.calls.append((sql, dict(parameters or {})))
        if "DB_ID" in sql:
            return _Result(scalar=next(self.database_ids))
        if "RESTORE HEADERONLY" in sql:
            return _Result(rows=({
                "DatabaseName": "Mech_Chatbot_DB",
                "BackupSetGUID": "11111111-1111-1111-1111-111111111111",
                "FirstLSN": "100",
                "LastLSN": "200",
                "CheckpointLSN": "150",
                "DatabaseBackupLSN": "90",
                "BackupType": 1,
                "Position": 1,
            },))
        if "RESTORE FILELISTONLY" in sql:
            return _Result(rows=(
                {"LogicalName": "source_data", "Type": "D"},
                {"LogicalName": "source_log", "Type": "L"},
            ))
        if "state_desc" in sql:
            state, access, has_access = next(self.database_states)
            return _Result(rows=({
                "state_desc": state,
                "user_access_desc": access,
                "has_db_access": has_access,
            },))
        return _Result()


class _DriverCursor:
    def __init__(self):
        self.calls = []
        self.nextset_calls = 0
        self.closed = False
        self._nextsets = iter((True, False))

    def execute(self, statement, parameters=()):
        self.calls.append((statement, tuple(parameters)))
        return self

    def nextset(self):
        self.nextset_calls += 1
        return next(self._nextsets)

    def close(self):
        self.closed = True


class _DriverConnection:
    def __init__(self):
        self.cursors = []

    def cursor(self):
        cursor = _DriverCursor()
        self.cursors.append(cursor)
        return cursor


class _DriverBackedConnection(_Connection):
    def __init__(self, *, database_states):
        super().__init__(database_states=database_states)
        self.driver = _DriverConnection()
        self.connection = SimpleNamespace(driver_connection=self.driver)


def _restore(connection, suffix):
    return restore_sql_backup(
        connection,
        source_database="Mech_Chatbot_DB",
        target_database=f"Mech_Chatbot_DB_RestoreTest_{suffix}",
        backup_path=r"D:\Backups\source.bak",
        data_dir=r"D:\SqlData",
    )


def test_sql_restore_recovers_and_verifies_online_access_before_success():
    connection = _DriverBackedConnection(
        database_states=(
            ("RESTORING", "MULTI_USER", 0),
            ("ONLINE", "MULTI_USER", 1),
        ),
    )

    report = _restore(connection, "Recovery")

    assert len(connection.driver.cursors) == 2
    recovery = connection.driver.cursors[-1]
    assert recovery.calls == [(
        "RESTORE DATABASE [Mech_Chatbot_DB_RestoreTest_Recovery] WITH RECOVERY",
        (),
    )]
    assert all(cursor.nextset_calls == 2 for cursor in connection.driver.cursors)
    assert all(cursor.closed for cursor in connection.driver.cursors)
    assert report["state_desc"] == "ONLINE"
    assert report["user_access_desc"] == "MULTI_USER"
    assert report["has_db_access"] is True
    assert report["restored"] is True


def test_sql_restore_drains_dbapi_result_sets_before_verifying_success():
    connection = _DriverBackedConnection(
        database_states=(("ONLINE", "MULTI_USER", 1),),
    )

    report = _restore(connection, "Drained")

    assert report["restored"] is True
    assert len(connection.driver.cursors) == 1
    cursor = connection.driver.cursors[0]
    assert cursor.nextset_calls == 2
    assert cursor.closed is True
    assert all(
        "RESTORE DATABASE" not in statement
        for statement, _ in connection.calls
    )


@pytest.mark.parametrize(
    ("state", "access", "has_access"),
    (
        ("SUSPECT", "MULTI_USER", 0),
        ("ONLINE", "SINGLE_USER", 1),
        ("ONLINE", "MULTI_USER", 0),
        ("ONLINE", "MULTI_USER", "0"),
    ),
)
def test_sql_restore_rejects_terminal_or_inaccessible_target(
    state,
    access,
    has_access,
):
    connection = _Connection(
        database_states=((state, access, has_access),),
    )

    with pytest.raises(PartialRestoreError) as raised:
        _restore(connection, "NotAccessible")

    assert raised.value.section == "sql"
    assert raised.value.details["target_may_exist"] is True


def test_sql_restore_times_out_without_reporting_success(monkeypatch):
    monotonic = iter((0, 0, 61))
    monkeypatch.setattr(
        restore_module,
        "time",
        SimpleNamespace(
            monotonic=lambda: next(monotonic),
            sleep=lambda _seconds: None,
        ),
    )
    connection = _Connection(
        database_states=(("RESTORING", "MULTI_USER", 0),),
    )

    with pytest.raises(PartialRestoreError) as raised:
        _restore(connection, "Timeout")

    assert "timeout" in str(raised.value.__cause__)
    assert raised.value.details["target_may_exist"] is True
