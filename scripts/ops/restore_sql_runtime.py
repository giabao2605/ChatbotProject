"""Execute SQL restores and verify the restored database is accessible."""

from __future__ import annotations

import time

from sqlalchemy import text


_SQL_TRANSIENT_STATES = frozenset({"RECOVERING", "RESTORING"})
_MAX_DRIVER_TIMEOUT_SECONDS = 2_147_483_647


class SqlRestoreVerificationError(RuntimeError):
    def __init__(self, message: str, *, recovery_attempted: bool):
        super().__init__(message)
        self.recovery_attempted = bool(recovery_attempted)


def _driver_connection(connection):
    return getattr(
        getattr(connection, "connection", None),
        "driver_connection",
        None,
    )


def _remaining_seconds(deadline: float) -> float:
    remaining = deadline - time.monotonic()
    if remaining <= 0:
        raise TimeoutError(
            "SQL state/access verification deadline exceeded"
        )
    return remaining


def _call_with_deadline(connection, deadline: float, operation):
    remaining = _remaining_seconds(deadline)
    driver_connection = _driver_connection(connection)
    has_driver_timeout = (
        driver_connection is not None
        and hasattr(driver_connection, "timeout")
    )
    original_timeout = (
        driver_connection.timeout if has_driver_timeout else None
    )
    timeout_configured = False
    operation_error = None
    result = None
    try:
        if has_driver_timeout:
            driver_connection.timeout = min(
                _MAX_DRIVER_TIMEOUT_SECONDS,
                max(1, int(remaining)),
            )
            timeout_configured = True
        try:
            result = operation()
        except Exception as error:
            operation_error = error
    finally:
        completed_at = time.monotonic()
        if timeout_configured:
            driver_connection.timeout = original_timeout
    deadline_error = (
        TimeoutError("SQL state/access verification deadline exceeded")
        if completed_at >= deadline
        else None
    )
    if operation_error is not None and deadline_error is not None:
        raise ExceptionGroup(
            "SQL call failed after verification deadline",
            (operation_error, deadline_error),
        )
    if deadline_error is not None:
        raise deadline_error
    if operation_error is not None:
        raise operation_error
    return result


def execute_sql_restore(
    connection,
    *,
    target_database: str,
    backup_path: str | None = None,
    destinations=(),
) -> None:
    driver_connection = _driver_connection(connection)
    if backup_path is None:
        statement = f"RESTORE DATABASE [{target_database}] WITH RECOVERY"
        positional_parameters = ()
        fallback_statement = statement
        fallback_parameters = {}
    else:
        positional_moves = ", ".join("MOVE ? TO ?" for _ in destinations)
        statement = (
            f"RESTORE DATABASE [{target_database}] FROM DISK = ? WITH "
            f"{positional_moves}, RECOVERY"
        )
        positional_parameters = (
            backup_path,
            *(
                value
                for destination in destinations
                for value in destination
            ),
        )
        fallback_parameters = {
            "backup_path": backup_path,
            **{
                f"logical_{index}": logical_name
                for index, (logical_name, _) in enumerate(destinations)
            },
            **{
                f"physical_{index}": physical_path
                for index, (_, physical_path) in enumerate(destinations)
            },
        }
        fallback_moves = tuple(
            f"MOVE :logical_{index} TO :physical_{index}"
            for index, _ in enumerate(destinations)
        )
        fallback_statement = (
            f"RESTORE DATABASE [{target_database}] FROM DISK = :backup_path "
            f"WITH {', '.join((*fallback_moves, 'RECOVERY'))}"
        )
    if driver_connection is None:
        connection.execute(text(fallback_statement), fallback_parameters)
        return
    cursor = driver_connection.cursor()
    try:
        if positional_parameters:
            cursor.execute(statement, positional_parameters)
        else:
            cursor.execute(statement)
        while cursor.nextset():
            pass
    finally:
        cursor.close()


def _sql_database_status(connection, target_database: str) -> dict | None:
    row = connection.execute(
        text(
            "SELECT state_desc, user_access_desc, "
            "HAS_DBACCESS(name) AS has_db_access "
            "FROM sys.databases WHERE name = :target_database"
        ),
        {"target_database": target_database},
    ).mappings().one_or_none()
    if row is None:
        return None
    has_db_access = row.get("has_db_access")
    return {
        "state_desc": str(row.get("state_desc") or "").strip().upper(),
        "user_access_desc": str(
            row.get("user_access_desc") or "").strip().upper(),
        "has_db_access": has_db_access is True or has_db_access == 1,
    }


def wait_for_sql_accessible(
    connection,
    target_database: str,
    *,
    timeout_seconds: float,
) -> dict:
    deadline = time.monotonic() + timeout_seconds
    recovery_attempted = False
    last_status = None
    recovery_error = None
    while time.monotonic() < deadline:
        try:
            last_status = _call_with_deadline(
                connection,
                deadline,
                lambda: _sql_database_status(
                    connection,
                    target_database,
                ),
            )
            if last_status is None:
                time.sleep(2)
                continue
            state = last_status["state_desc"]
            if (
                state == "ONLINE"
                and last_status["user_access_desc"] == "MULTI_USER"
                and last_status["has_db_access"]
            ):
                return last_status
            if state == "RESTORING" and not recovery_attempted:
                recovery_attempted = True
                try:
                    _call_with_deadline(
                        connection,
                        deadline,
                        lambda: execute_sql_restore(
                            connection,
                            target_database=target_database,
                        ),
                    )
                except Exception as error:
                    recovery_error = error
                continue
            if state not in _SQL_TRANSIENT_STATES:
                raise RuntimeError(
                    "SQL restored database entered terminal state "
                    f"{state or 'UNKNOWN'}"
                )
        except Exception as error:
            cause = (
                ExceptionGroup(
                    "SQL recovery and status verification failed",
                    (recovery_error, error),
                )
                if recovery_error is not None
                else error
            )
            raise SqlRestoreVerificationError(
                str(error),
                recovery_attempted=recovery_attempted,
            ) from cause
        time.sleep(2)
    state = (last_status or {}).get("state_desc") or "ABSENT"
    timeout_error = SqlRestoreVerificationError(
        "SQL restored database did not become ONLINE and accessible "
        f"before timeout; last state {state}",
        recovery_attempted=recovery_attempted,
    )
    if recovery_error is not None:
        raise timeout_error from recovery_error
    raise timeout_error
