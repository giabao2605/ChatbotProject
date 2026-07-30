import os
from pathlib import Path
import subprocess
import sys
from types import SimpleNamespace

import pytest

from scripts.ops.restore_drill import (
    PartialRestoreError,
    restore_qdrant_snapshot,
    restore_sql_backup,
    validate_disposable_name,
)


ROOT = Path(__file__).resolve().parents[2]


def test_restore_drill_help_runs_as_a_direct_windows_script():
    environment = {**os.environ, "PYTHONPATH": str(ROOT / "src")}

    result = subprocess.run(
        [sys.executable, "scripts/ops/restore_drill.py", "--help"],
        cwd=ROOT,
        env=environment,
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr


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


class _Connection:
    def __init__(self, *, database_ids, backup_database="Mech_Chatbot_DB"):
        self.database_ids = iter(database_ids)
        self.backup_database = backup_database
        self.calls = []

    def execution_options(self, **_kwargs):
        return self

    def execute(self, statement, parameters=None):
        sql = str(statement)
        self.calls.append((sql, dict(parameters or {})))
        if "DB_ID" in sql:
            return _Result(scalar=next(self.database_ids))
        if "RESTORE HEADERONLY" in sql:
            return _Result(rows=({"DatabaseName": self.backup_database},))
        if "RESTORE FILELISTONLY" in sql:
            return _Result(rows=(
                {"LogicalName": "source_data", "Type": "D"},
                {"LogicalName": "source_log", "Type": "L"},
            ))
        return _Result()


class _Qdrant:
    def __init__(
        self,
        *,
        source_exists=True,
        target_exists=False,
        counts=(7, 7),
        snapshots=("snapshot-1",),
    ):
        self.source_exists = source_exists
        self.target_exists = target_exists
        self.counts = iter(counts)
        self.snapshots = snapshots
        self.calls = []

    def collection_exists(self, name):
        self.calls.append(("collection_exists", name))
        return self.source_exists if name == "TaiLieuKyThuat_v2" else self.target_exists

    def recover_snapshot(self, **kwargs):
        self.calls.append(("recover_snapshot", kwargs))
        self.target_exists = True
        return True

    def list_snapshots(self, name):
        self.calls.append(("list_snapshots", name))
        return [SimpleNamespace(name=value) for value in self.snapshots]

    def count(self, name, *, exact):
        self.calls.append(("count", name, exact))
        return SimpleNamespace(count=next(self.counts))


@pytest.mark.parametrize(
    "name",
    ("Mech_Chatbot_DB", "Mech_Chatbot_Restore", "RestoreTest", "../RestoreTest"),
)
def test_disposable_names_fail_closed(name):
    with pytest.raises(ValueError):
        validate_disposable_name(name, source_name="Mech_Chatbot_DB")


def test_sql_restore_requires_absent_target_and_never_replaces():
    connection = _Connection(database_ids=(None, None, 42))

    report = restore_sql_backup(
        connection,
        source_database="Mech_Chatbot_DB",
        target_database="Mech_Chatbot_DB_RestoreTest_20260730",
        backup_path=r"D:\Backups\source.bak",
        data_dir=r"D:\SqlData",
    )

    sql = "\n".join(statement for statement, _ in connection.calls)
    assert report["target_database"] == "Mech_Chatbot_DB_RestoreTest_20260730"
    assert report["restored"] is True
    assert "RESTORE FILELISTONLY" in sql
    assert "RESTORE HEADERONLY" in sql
    assert "RESTORE DATABASE [Mech_Chatbot_DB_RestoreTest_20260730]" in sql
    assert "REPLACE" not in sql
    assert "DROP DATABASE" not in sql

    existing = _Connection(database_ids=(7,))
    with pytest.raises(ValueError, match="already exists"):
        restore_sql_backup(
            existing,
            source_database="Mech_Chatbot_DB",
            target_database="Mech_Chatbot_DB_RestoreTest_Existing",
            backup_path=r"D:\Backups\source.bak",
            data_dir=r"D:\SqlData",
        )
    wrong_source = _Connection(
        database_ids=(None,),
        backup_database="Another_DB",
    )
    with pytest.raises(ValueError, match="source database"):
        restore_sql_backup(
            wrong_source,
            source_database="Mech_Chatbot_DB",
            target_database="Mech_Chatbot_DB_RestoreTest_WrongSource",
            backup_path=r"D:\Backups\source.bak",
            data_dir=r"D:\SqlData",
        )


def test_qdrant_restore_requires_absent_target_and_never_deletes():
    client = _Qdrant()

    report = restore_qdrant_snapshot(
        client,
        source_collection="TaiLieuKyThuat_v2",
        target_collection="TaiLieuKyThuat_v2_RestoreTest_20260730",
        snapshot_name="snapshot-1",
        snapshot_location="http://127.0.0.1/snapshot-1",
    )

    assert report == {
        "source_collection": "TaiLieuKyThuat_v2",
        "target_collection": "TaiLieuKyThuat_v2_RestoreTest_20260730",
        "snapshot_name": "snapshot-1",
        "source_points": 7,
        "target_points": 7,
        "restored": True,
    }
    assert any(call[0] == "recover_snapshot" for call in client.calls)
    assert all(call[0] != "delete_collection" for call in client.calls)

    existing = _Qdrant(target_exists=True)
    with pytest.raises(ValueError, match="already exists"):
        restore_qdrant_snapshot(
            existing,
            source_collection="TaiLieuKyThuat_v2",
            target_collection="TaiLieuKyThuat_v2_RestoreTest_Existing",
            snapshot_name="snapshot-1",
            snapshot_location="http://127.0.0.1/snapshot-1",
        )
    missing_snapshot = _Qdrant(snapshots=())
    with pytest.raises(ValueError, match="source collection"):
        restore_qdrant_snapshot(
            missing_snapshot,
            source_collection="TaiLieuKyThuat_v2",
            target_collection="TaiLieuKyThuat_v2_RestoreTest_MissingSnapshot",
            snapshot_name="snapshot-1",
            snapshot_location="http://127.0.0.1/snapshot-1",
        )
    with pytest.raises(ValueError, match="snapshot name"):
        restore_qdrant_snapshot(
            _Qdrant(),
            source_collection="TaiLieuKyThuat_v2",
            target_collection="TaiLieuKyThuat_v2_RestoreTest_WrongLocation",
            snapshot_name="snapshot-1",
            snapshot_location="http://127.0.0.1/snapshot-2",
        )


def test_qdrant_partial_restore_reports_target_may_exist():
    with pytest.raises(PartialRestoreError) as raised:
        restore_qdrant_snapshot(
            _Qdrant(counts=(7, 8)),
            source_collection="TaiLieuKyThuat_v2",
            target_collection="TaiLieuKyThuat_v2_RestoreTest_CountMismatch",
            snapshot_name="snapshot-1",
            snapshot_location="http://127.0.0.1/snapshot-1",
        )

    assert raised.value.details == {
        "source_collection": "TaiLieuKyThuat_v2",
        "target_collection": "TaiLieuKyThuat_v2_RestoreTest_CountMismatch",
        "snapshot_name": "snapshot-1",
        "recovery_attempted": True,
        "target_may_exist": True,
    }
