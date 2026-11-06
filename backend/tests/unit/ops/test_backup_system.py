from datetime import datetime, timedelta
from os import utime
from os.path import join
from types import SimpleNamespace

import pytest
import sys

from scripts.ops import backup_system
from scripts.ops.backup_system import _full_backup_options, cleanup_old


def test_express_backup_omits_unsupported_compression():
    assert _full_backup_options(4) == "WITH INIT"
    assert _full_backup_options(3) == "WITH INIT, COMPRESSION"


class _MaintenanceCursor:
    def __init__(self, owner):
        self.owner = owner
        self.command = ""
        self.remaining = 0

    def execute(self, statement, *_parameters):
        self.command = statement
        if "BACKUP DATABASE" in statement:
            self.remaining = 2
        elif "RESTORE VERIFYONLY" in statement:
            assert self.owner.backup_drained
            self.owner.verified = True
            self.remaining = 1
        else:
            raise AssertionError(statement)
        return self

    def nextset(self):
        if self.remaining:
            self.remaining -= 1
            return True
        if "BACKUP DATABASE" in self.command:
            self.owner.backup_drained = True
        return None

    def close(self):
        pass


class _DriverConnection:
    def __init__(self, owner):
        self.owner = owner

    def cursor(self):
        return _MaintenanceCursor(self.owner)


class _Result:
    def __init__(self, *, scalar=None):
        self.value = scalar

    def scalar(self):
        return self.value


class _BackupConnection:
    def __init__(self):
        self.backup_drained = False
        self.verified = False
        self.connection = SimpleNamespace(
            driver_connection=_DriverConnection(self),
        )

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def execution_options(self, **_kwargs):
        return self

    def execute(self, statement, _parameters=None):
        sql = str(statement)
        if "EngineEdition" in sql:
            return _Result(scalar=4)
        if "DATABASEPROPERTYEX" in sql:
            assert self.verified
            return _Result(scalar="SIMPLE")
        raise AssertionError("maintenance command must use the driver cursor")


def test_full_backup_drains_results_and_verifies_before_success(monkeypatch):
    connection = _BackupConnection()
    monkeypatch.setattr(backup_system, "_ensure_engine", lambda: None)
    monkeypatch.setattr(
        backup_system,
        "load_settings",
        lambda: SimpleNamespace(SQL_DATABASE="Mech_Chatbot_DB"),
    )
    monkeypatch.setattr(
        backup_system,
        "engine",
        SimpleNamespace(connect=lambda: connection),
    )
    monkeypatch.setattr(
        backup_system,
        "_timestamp",
        lambda: "20260731_120000",
    )

    created = backup_system.backup_sql(r"D:\Backups")

    assert created == [
        join(r"D:\Backups", "Mech_Chatbot_DB_full_20260731_120000.bak")
    ]
    assert connection.verified is True


def test_backup_cleanup_is_opt_in_and_database_scoped(tmp_path):
    backup = tmp_path / "existing.bak"
    backup.write_bytes(b"backup")
    owned = tmp_path / "Mech_Chatbot_DB_full_20260101_000000.bak"
    owned.write_bytes(b"owned")
    foreign = tmp_path / "Other_DB_full_20260101_000000.bak"
    foreign.write_bytes(b"foreign")
    overlapping = (
        tmp_path / "Mech_Chatbot_DB_full_archive_full_20260101_000000.bak"
    )
    overlapping.write_bytes(b"overlapping")
    old = (datetime.now() - timedelta(days=30)).timestamp()
    for path in (backup, owned, foreign, overlapping):
        utime(path, (old, old))

    cleanup_old(tmp_path, None, database="Mech_Chatbot_DB")

    assert backup.exists()
    assert owned.exists()
    cleanup_old(tmp_path, 14, database="Mech_Chatbot_DB")
    assert not owned.exists()
    assert foreign.exists()
    assert overlapping.exists()
    assert backup.exists()
    with pytest.raises(ValueError, match="positive integer"):
        cleanup_old(tmp_path, 0, database="Mech_Chatbot_DB")


def test_backup_help_never_builds_runtime(monkeypatch):
    from mech_chatbot.composition import maintenance_runtime

    def forbidden(*args, **kwargs):
        pytest.fail("--help must not build dependencies")

    monkeypatch.setattr(maintenance_runtime, "build_database_runtime", forbidden)
    monkeypatch.setattr(maintenance_runtime, "build_qdrant_admin_runtime", forbidden)
    monkeypatch.setattr(sys, "argv", ["backup_system.py", "--help"])
    with pytest.raises(SystemExit) as stopped:
        backup_system.main()
    assert stopped.value.code == 0


@pytest.mark.parametrize("flags, expected", [
    (["--skip-sql"], ["qdrant", "snapshot", "close-qdrant"]),
    (["--skip-qdrant"], ["sql", "backup", "close-sql"]),
    (["--skip-sql", "--skip-qdrant"], []),
])
def test_backup_builds_only_selected_dependencies(monkeypatch, flags, expected):
    from mech_chatbot.composition import maintenance_runtime
    from mech_chatbot.config.settings import Settings

    events = []
    def sql(*args):
        events.append("sql")
        return SimpleNamespace(engine=object(), close=lambda: events.append("close-sql"))
    def qdrant(*args):
        events.append("qdrant")
        return SimpleNamespace(client=object(), collection_name="backup", close=lambda: events.append("close-qdrant"))

    monkeypatch.setattr(maintenance_runtime, "build_database_runtime", sql)
    monkeypatch.setattr(maintenance_runtime, "build_qdrant_admin_runtime", qdrant)
    monkeypatch.setattr(backup_system, "load_settings", lambda: Settings.from_env({}))
    monkeypatch.setattr(backup_system, "backup_sql", lambda *args: events.append("backup"))
    monkeypatch.setattr(backup_system, "backup_qdrant", lambda: events.append("snapshot"))
    monkeypatch.setattr(sys, "argv", ["backup_system.py", *flags])
    backup_system.main()
    assert events == expected
