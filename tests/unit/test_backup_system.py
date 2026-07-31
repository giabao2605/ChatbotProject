from datetime import datetime, timedelta
from os import utime
from os.path import join
from types import SimpleNamespace

import pytest

from scripts.ops import backup_system
from scripts.ops.backup_system import _full_backup_options, cleanup_old


def test_express_backup_omits_unsupported_compression():
    assert _full_backup_options(4) == "WITH INIT"
    assert _full_backup_options(3) == "WITH INIT, COMPRESSION"


class _Cursor:
    def __init__(self, result_sets=0):
        self.remaining = result_sets
        self.drained = result_sets == 0

    def nextset(self):
        if self.remaining:
            self.remaining -= 1
            return True
        self.drained = True
        return None


class _Result:
    def __init__(self, *, scalar=None, result_sets=0):
        self.value = scalar
        self.cursor = _Cursor(result_sets)

    def scalar(self):
        return self.value


class _BackupConnection:
    def __init__(self):
        self.backup_result = None
        self.verified = False

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
        if "BACKUP DATABASE" in sql:
            self.backup_result = _Result(result_sets=2)
            return self.backup_result
        assert self.backup_result.cursor.drained
        if "RESTORE VERIFYONLY" in sql:
            self.verified = True
            return _Result(result_sets=1)
        if "DATABASEPROPERTYEX" in sql:
            return _Result(scalar="SIMPLE")
        raise AssertionError(sql)


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
