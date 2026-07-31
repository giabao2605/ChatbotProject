from types import SimpleNamespace

import pytest

from scripts.ops import restore_sql_runtime
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
    def __init__(
        self,
        *,
        database_states,
        database_ids=(None, None, 42),
        status_hook=None,
    ):
        self.database_ids = iter(database_ids)
        self.database_states = iter(database_states)
        self.status_hook = status_hook
        self.status_timeouts = []
        self.calls = []

    def execute(self, statement, parameters=None):
        sql = str(statement)
        self.calls.append((sql, dict(parameters or {})))
        if "DB_ID" in sql:
            database_id = next(self.database_ids)
            if isinstance(database_id, BaseException):
                raise database_id
            return _Result(scalar=database_id)
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
            driver = getattr(
                getattr(self, "connection", None),
                "driver_connection",
                None,
            )
            self.status_timeouts.append(
                getattr(driver, "timeout", None),
            )
            if self.status_hook is not None:
                self.status_hook()
            state, access, has_access = next(self.database_states)
            return _Result(rows=({
                "state_desc": state,
                "user_access_desc": access,
                "has_db_access": has_access,
            },))
        return _Result()


class _DriverCursor:
    def __init__(self, driver, *, nextset_error=None):
        self.driver = driver
        self.calls = []
        self.timeout_values = []
        self.nextset_calls = 0
        self.closed = False
        self.nextset_error = nextset_error
        self._nextsets = iter((True, False))

    def execute(self, statement, parameters=()):
        self.timeout_values.append(self.driver.timeout)
        self.calls.append((statement, tuple(parameters)))
        return self

    def nextset(self):
        self.nextset_calls += 1
        if self.nextset_error is not None:
            raise self.nextset_error
        return next(self._nextsets)

    def close(self):
        self.closed = True


class _DriverConnection:
    def __init__(self, *, nextset_errors=(), timeout=77):
        self.cursors = []
        self.nextset_errors = iter(nextset_errors)
        self.timeout = timeout

    def cursor(self):
        cursor = _DriverCursor(
            self,
            nextset_error=next(self.nextset_errors, None),
        )
        self.cursors.append(cursor)
        return cursor


class _DriverBackedConnection(_Connection):
    def __init__(
        self,
        *,
        database_states,
        database_ids=(None, None, 42),
        nextset_errors=(),
        status_hook=None,
        driver_timeout=77,
    ):
        super().__init__(
            database_states=database_states,
            database_ids=database_ids,
            status_hook=status_hook,
        )
        self.driver = _DriverConnection(
            nextset_errors=nextset_errors,
            timeout=driver_timeout,
        )
        self.connection = SimpleNamespace(driver_connection=self.driver)


def _restore(connection, suffix, *, timeout_seconds=60):
    return restore_sql_backup(
        connection,
        source_database="Mech_Chatbot_DB",
        target_database=f"Mech_Chatbot_DB_RestoreTest_{suffix}",
        backup_path=r"D:\Backups\source.bak",
        data_dir=r"D:\SqlData",
        timeout_seconds=timeout_seconds,
    )


def _walk_exceptions(error):
    yield error
    if isinstance(error, BaseExceptionGroup):
        for child in error.exceptions:
            yield from _walk_exceptions(child)
    if error.__cause__ is not None:
        yield from _walk_exceptions(error.__cause__)


def _exception_messages(error):
    return tuple(str(item) for item in _walk_exceptions(error))


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


def test_sql_restore_reconciles_nextset_error_after_online_completion():
    connection = _DriverBackedConnection(
        database_states=(("ONLINE", "MULTI_USER", 1),),
        nextset_errors=(RuntimeError("transport closed after completion"),),
    )

    report = _restore(connection, "CompletedAfterError")

    assert report["restored"] is True
    assert report["state_desc"] == "ONLINE"
    assert connection.driver.cursors[0].closed is True


def test_sql_restore_dispatch_failure_is_conservative_without_recovery():
    connection = _DriverBackedConnection(
        database_ids=(None, None, None),
        database_states=(),
        nextset_errors=(RuntimeError("restore failed before target"),),
    )

    with pytest.raises(PartialRestoreError) as raised:
        _restore(connection, "FailedBeforeTarget")

    assert raised.value.details == {
        "target_database": (
            "Mech_Chatbot_DB_RestoreTest_FailedBeforeTarget"
        ),
        "recovery_attempted": False,
        "target_may_exist": True,
    }


def test_sql_restore_recovery_failure_reports_attempt(monkeypatch):
    monotonic = iter((0, 0, 0, 0, 1, 1, 1, 1, 1, 61))
    monkeypatch.setattr(
        restore_sql_runtime,
        "time",
        SimpleNamespace(
            monotonic=lambda: next(monotonic),
            sleep=lambda _seconds: None,
        ),
    )
    connection = _DriverBackedConnection(
        database_states=(
            ("RESTORING", "MULTI_USER", 0),
            ("RESTORING", "MULTI_USER", 0),
        ),
        nextset_errors=(
            RuntimeError("initial response lost"),
            RuntimeError("recovery response lost"),
        ),
    )

    with pytest.raises(PartialRestoreError) as raised:
        _restore(connection, "RecoveryFailed")

    assert len(connection.driver.cursors) == 2
    assert raised.value.details["recovery_attempted"] is True
    assert raised.value.details["target_may_exist"] is True
    messages = _exception_messages(raised.value)
    assert any(
        "initial response lost" in message
        for message in messages
    )
    assert any(
        "recovery response lost" in message
        for message in messages
    )
    assert any("before timeout" in message for message in messages)
    assert not any(
        isinstance(error, StopIteration)
        for error in _walk_exceptions(raised.value)
    )


def test_sql_restore_retains_initial_error_when_verification_fails():
    connection = _DriverBackedConnection(
        database_states=(("SUSPECT", "MULTI_USER", 0),),
        nextset_errors=(RuntimeError("initial response lost"),),
    )

    with pytest.raises(PartialRestoreError) as raised:
        _restore(connection, "InitialAndVerificationFailed")

    messages = _exception_messages(raised.value)
    assert any(
        "initial response lost" in message
        for message in messages
    )
    assert any(
        "terminal state SUSPECT" in message
        for message in messages
    )


def test_sql_restore_retains_initial_error_when_db_id_query_fails():
    connection = _DriverBackedConnection(
        database_ids=(
            None,
            None,
            RuntimeError("DB_ID query failed"),
        ),
        database_states=(),
        nextset_errors=(RuntimeError("initial response lost"),),
    )

    with pytest.raises(PartialRestoreError) as raised:
        _restore(connection, "InitialAndDatabaseIdFailed")

    messages = _exception_messages(raised.value)
    assert any("initial response lost" in message for message in messages)
    assert any("DB_ID query failed" in message for message in messages)
    assert raised.value.details["recovery_attempted"] is False
    assert raised.value.details["target_may_exist"] is True


def test_sql_restore_reconciles_recovery_nextset_error_after_completion():
    connection = _DriverBackedConnection(
        database_states=(
            ("RESTORING", "MULTI_USER", 0),
            ("ONLINE", "MULTI_USER", 1),
        ),
        nextset_errors=(
            None,
            RuntimeError("recovery response lost after completion"),
        ),
    )

    report = _restore(connection, "RecoveryCompletedAfterError")

    assert len(connection.driver.cursors) == 2
    assert report["restored"] is True
    assert report["state_desc"] == "ONLINE"


def test_sql_restore_rejects_online_status_returned_after_deadline(
    monkeypatch,
):
    clock = SimpleNamespace(value=0.0)
    monkeypatch.setattr(
        restore_sql_runtime,
        "time",
        SimpleNamespace(
            monotonic=lambda: clock.value,
            sleep=lambda seconds: setattr(
                clock,
                "value",
                clock.value + seconds,
            ),
        ),
    )
    connection = _Connection(
        database_states=(("ONLINE", "MULTI_USER", 1),),
        status_hook=lambda: setattr(clock, "value", 2.0),
    )

    with pytest.raises(PartialRestoreError) as raised:
        _restore(connection, "LateOnline", timeout_seconds=1)

    assert any(
        "deadline" in message
        for message in _exception_messages(raised.value)
    )
    assert raised.value.details["recovery_attempted"] is False


def test_sql_status_and_recovery_use_remaining_driver_timeout(
    monkeypatch,
):
    clock = SimpleNamespace(value=0.0, status_calls=0)

    def advance_first_status():
        clock.status_calls += 1
        if clock.status_calls == 1:
            clock.value = 2.0

    monkeypatch.setattr(
        restore_sql_runtime,
        "time",
        SimpleNamespace(
            monotonic=lambda: clock.value,
            sleep=lambda seconds: setattr(
                clock,
                "value",
                clock.value + seconds,
            ),
        ),
    )
    connection = _DriverBackedConnection(
        database_states=(
            ("RESTORING", "MULTI_USER", 0),
            ("ONLINE", "MULTI_USER", 1),
        ),
        status_hook=advance_first_status,
        driver_timeout=77,
    )

    report = _restore(
        connection,
        "BoundedDriverTimeout",
        timeout_seconds=5,
    )

    assert report["restored"] is True
    assert connection.status_timeouts == [5, 3]
    assert connection.driver.cursors[0].timeout_values == [77]
    assert connection.driver.cursors[1].timeout_values == [3]
    assert connection.driver.timeout == 77


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
    assert raised.value.details["recovery_attempted"] is False
    assert raised.value.details["target_may_exist"] is True


def test_sql_restore_times_out_without_reporting_success(monkeypatch):
    monotonic = iter((0, 0, 0, 0, 0, 0, 61))
    monkeypatch.setattr(
        restore_sql_runtime,
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

    assert (
        "did not become ONLINE and accessible before timeout"
        in str(raised.value.__cause__)
    )
    assert raised.value.details["recovery_attempted"] is True
    assert raised.value.details["target_may_exist"] is True
