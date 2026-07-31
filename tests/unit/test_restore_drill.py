import hashlib
from io import BytesIO
import json
import os
from pathlib import Path
import subprocess
import sys
from types import SimpleNamespace

import pytest
from sqlalchemy.exc import InvalidRequestError

from scripts.ops import restore_drill as restore_module
from scripts.ops.restore_drill import (
    PartialRestoreError,
    build_restore_snapshot_fingerprint,
    restore_qdrant_snapshot,
    restore_sql_backup,
    validate_disposable_name,
    verify_restore_evidence,
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
    def __init__(
        self,
        *,
        database_ids,
        backup_database="Mech_Chatbot_DB",
        backup_set_guid="11111111-1111-1111-1111-111111111111",
    ):
        self.database_ids = iter(database_ids)
        self.backup_database = backup_database
        self.backup_set_guid = backup_set_guid
        self.calls = []

    def execution_options(self, **_kwargs):
        return self

    def execute(self, statement, parameters=None):
        sql = str(statement)
        self.calls.append((sql, dict(parameters or {})))
        if "DB_ID" in sql:
            return _Result(scalar=next(self.database_ids))
        if "RESTORE HEADERONLY" in sql:
            return _Result(rows=({
                "DatabaseName": self.backup_database,
                "BackupSetGUID": self.backup_set_guid,
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
        return _Result()


class _PartialSqlConnection(_Connection):
    def execute(self, statement, parameters=None):
        if "RESTORE DATABASE" in str(statement):
            raise RuntimeError("connection lost after restore started")
        return super().execute(statement, parameters)


class _AutobeginSensitiveConnection(_Connection):
    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self.transaction_started = False

    def execution_options(self, **_kwargs):
        if self.transaction_started:
            raise InvalidRequestError(
                "isolation_level may not be altered after autobegin"
            )
        return self

    def execute(self, statement, parameters=None):
        self.transaction_started = True
        return super().execute(statement, parameters)


class _Qdrant:
    def __init__(
        self,
        *,
        source_exists=True,
        target_exists=False,
        counts=(7, 7),
        snapshots=("snapshot-1",),
        snapshot_checksum="a" * 64,
    ):
        self.source_exists = source_exists
        self.target_exists = target_exists
        self.counts = iter(counts)
        self.snapshots = snapshots
        self.snapshot_checksum = snapshot_checksum
        self.calls = []
        self.http = SimpleNamespace(
            snapshots_api=SimpleNamespace(
                recover_from_uploaded_snapshot=self._recover_uploaded_snapshot,
            )
        )

    def collection_exists(self, name):
        self.calls.append(("collection_exists", name))
        return self.source_exists if name == "TaiLieuKyThuat_v2" else self.target_exists

    def recover_snapshot(self, **kwargs):
        self.calls.append(("recover_snapshot", kwargs))
        self.target_exists = True
        return True

    def _recover_uploaded_snapshot(self, **kwargs):
        self.calls.append(("recover_from_uploaded_snapshot", kwargs))
        self.target_exists = True
        return SimpleNamespace(result=None)

    def list_snapshots(self, name):
        self.calls.append(("list_snapshots", name))
        return [
            SimpleNamespace(name=value, checksum=self.snapshot_checksum)
            for value in self.snapshots
        ]

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
    assert len(report["backup_set_identity_sha256"]) == 64
    assert "RESTORE FILELISTONLY" in sql
    assert "RESTORE HEADERONLY" in sql
    assert "RESTORE DATABASE [Mech_Chatbot_DB_RestoreTest_20260730]" in sql
    assert "REPLACE" not in sql
    assert "DROP DATABASE" not in sql
    other_set = restore_sql_backup(
        _Connection(
            database_ids=(None, None, 42),
            backup_set_guid="22222222-2222-2222-2222-222222222222",
        ),
        source_database="Mech_Chatbot_DB",
        target_database="Mech_Chatbot_DB_RestoreTest_OtherSet",
        backup_path=r"D:\Backups\source.bak",
        data_dir=r"D:\SqlData",
    )
    assert (
        other_set["backup_set_identity_sha256"]
        != report["backup_set_identity_sha256"]
    )

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
    with pytest.raises(ValueError, match="absolute"):
        restore_sql_backup(
            _Connection(database_ids=(None,)),
            source_database="Mech_Chatbot_DB",
            target_database="Mech_Chatbot_DB_RestoreTest_Unc",
            backup_path=r"\\fileserver\backups\source.bak",
            data_dir=r"D:\SqlData",
        )


def test_sql_restore_reuses_autocommit_connection_after_precheck():
    connection = _AutobeginSensitiveConnection(
        database_ids=(None, None, None, 42),
    )
    connection.execute(
        "SELECT DB_ID(:target_database)",
        {"target_database": "Mech_Chatbot_DB_RestoreTest_Prechecked"},
    )

    report = restore_sql_backup(
        connection,
        source_database="Mech_Chatbot_DB",
        target_database="Mech_Chatbot_DB_RestoreTest_Prechecked",
        backup_path=r"D:\Backups\source.bak",
        data_dir=r"D:\SqlData",
    )

    assert report["restored"] is True


def test_sql_partial_restore_reports_target_may_exist():
    with pytest.raises(PartialRestoreError) as raised:
        restore_sql_backup(
            _PartialSqlConnection(database_ids=(None, None)),
            source_database="Mech_Chatbot_DB",
            target_database="Mech_Chatbot_DB_RestoreTest_Partial",
            backup_path=r"D:\Backups\source.bak",
            data_dir=r"D:\SqlData",
        )

    assert raised.value.section == "sql"
    assert raised.value.details == {
        "target_database": "Mech_Chatbot_DB_RestoreTest_Partial",
        "recovery_attempted": True,
        "target_may_exist": True,
    }


def test_qdrant_restore_requires_absent_target_and_never_deletes(monkeypatch):
    payload = b"snapshot"
    checksum = hashlib.sha256(payload).hexdigest()
    monkeypatch.setattr(
        restore_module,
        "_open_snapshot",
        lambda *_args, **_kwargs: BytesIO(payload),
    )
    client = _Qdrant(snapshot_checksum=checksum)

    report = restore_qdrant_snapshot(
        client,
        source_collection="TaiLieuKyThuat_v2",
        target_collection="TaiLieuKyThuat_v2_RestoreTest_20260730",
        snapshot_name="snapshot-1",
        snapshot_checksum=checksum,
        snapshot_api_key="test-key",
        snapshot_location=(
            "http://127.0.0.1:6333/collections/"
            "TaiLieuKyThuat_v2/snapshots/snapshot-1"
        ),
        allowed_snapshot_origin="http://127.0.0.1:6333",
    )

    assert report == {
        "source_collection": "TaiLieuKyThuat_v2",
        "target_collection": "TaiLieuKyThuat_v2_RestoreTest_20260730",
        "snapshot_name": "snapshot-1",
        "snapshot_checksum": checksum,
        "source_points": 7,
        "target_points": 7,
        "restored": True,
    }
    recover_call = next(
        call
        for call in client.calls
        if call[0] == "recover_from_uploaded_snapshot"
    )
    assert recover_call[1]["checksum"] == checksum
    assert recover_call[1]["wait"] is False
    assert all(call[0] != "delete_collection" for call in client.calls)

    with pytest.raises(ValueError, match="API key"):
        restore_qdrant_snapshot(
            _Qdrant(),
            source_collection="TaiLieuKyThuat_v2",
            target_collection="TaiLieuKyThuat_v2_RestoreTest_MissingKey",
            snapshot_name="snapshot-1",
            snapshot_checksum="a" * 64,
            snapshot_api_key="",
            snapshot_location=(
                "http://127.0.0.1:6333/collections/"
                "TaiLieuKyThuat_v2/snapshots/snapshot-1"
            ),
            allowed_snapshot_origin="http://127.0.0.1:6333",
        )

    existing = _Qdrant(target_exists=True)
    with pytest.raises(ValueError, match="already exists"):
        restore_qdrant_snapshot(
            existing,
            source_collection="TaiLieuKyThuat_v2",
            target_collection="TaiLieuKyThuat_v2_RestoreTest_Existing",
            snapshot_name="snapshot-1",
            snapshot_checksum="a" * 64,
            snapshot_api_key="test-key",
            snapshot_location=(
                "http://127.0.0.1:6333/collections/"
                "TaiLieuKyThuat_v2/snapshots/snapshot-1"
            ),
            allowed_snapshot_origin="http://127.0.0.1:6333",
        )
    missing_snapshot = _Qdrant(snapshots=())
    with pytest.raises(ValueError, match="source collection"):
        restore_qdrant_snapshot(
            missing_snapshot,
            source_collection="TaiLieuKyThuat_v2",
            target_collection="TaiLieuKyThuat_v2_RestoreTest_MissingSnapshot",
            snapshot_name="snapshot-1",
            snapshot_checksum="a" * 64,
            snapshot_api_key="test-key",
            snapshot_location=(
                "http://127.0.0.1:6333/collections/"
                "TaiLieuKyThuat_v2/snapshots/snapshot-1"
            ),
            allowed_snapshot_origin="http://127.0.0.1:6333",
        )
    with pytest.raises(ValueError, match="snapshot location"):
        restore_qdrant_snapshot(
            _Qdrant(),
            source_collection="TaiLieuKyThuat_v2",
            target_collection="TaiLieuKyThuat_v2_RestoreTest_WrongLocation",
            snapshot_name="snapshot-1",
            snapshot_checksum="a" * 64,
            snapshot_api_key="test-key",
            snapshot_location=(
                "http://127.0.0.1:6333/collections/"
                "TaiLieuKyThuat_v2/snapshots/snapshot-2"
            ),
            allowed_snapshot_origin="http://127.0.0.1:6333",
        )
    with pytest.raises(ValueError, match="snapshot origin"):
        restore_qdrant_snapshot(
            _Qdrant(),
            source_collection="TaiLieuKyThuat_v2",
            target_collection="TaiLieuKyThuat_v2_RestoreTest_WrongOrigin",
            snapshot_name="snapshot-1",
            snapshot_checksum="a" * 64,
            snapshot_api_key="test-key",
            snapshot_location=(
                "http://169.254.169.254/collections/"
                "TaiLieuKyThuat_v2/snapshots/snapshot-1"
            ),
            allowed_snapshot_origin="http://127.0.0.1:6333",
        )
    with pytest.raises(ValueError, match="snapshot location"):
        restore_qdrant_snapshot(
            _Qdrant(),
            source_collection="TaiLieuKyThuat_v2",
            target_collection="TaiLieuKyThuat_v2_RestoreTest_Prefixed",
            snapshot_name="snapshot-1",
            snapshot_checksum="a" * 64,
            snapshot_api_key="test-key",
            snapshot_location=(
                "http://127.0.0.1:6333/unconfigured/collections/"
                "TaiLieuKyThuat_v2/snapshots/snapshot-1"
            ),
            allowed_snapshot_origin="http://127.0.0.1:6333",
        )
    with pytest.raises(ValueError, match="checksum"):
        restore_qdrant_snapshot(
            _Qdrant(),
            source_collection="TaiLieuKyThuat_v2",
            target_collection="TaiLieuKyThuat_v2_RestoreTest_WrongChecksum",
            snapshot_name="snapshot-1",
            snapshot_checksum="b" * 64,
            snapshot_api_key="test-key",
            snapshot_location=(
                "http://127.0.0.1:6333/collections/"
                "TaiLieuKyThuat_v2/snapshots/snapshot-1"
            ),
            allowed_snapshot_origin="http://127.0.0.1:6333",
        )


def test_qdrant_partial_restore_reports_target_may_exist(monkeypatch):
    payload = b"snapshot"
    checksum = hashlib.sha256(payload).hexdigest()
    monkeypatch.setattr(
        restore_module,
        "_open_snapshot",
        lambda *_args, **_kwargs: BytesIO(payload),
    )
    with pytest.raises(PartialRestoreError) as raised:
        restore_qdrant_snapshot(
            _Qdrant(counts=(7, 8), snapshot_checksum=checksum),
            source_collection="TaiLieuKyThuat_v2",
            target_collection="TaiLieuKyThuat_v2_RestoreTest_CountMismatch",
            snapshot_name="snapshot-1",
            snapshot_checksum=checksum,
            snapshot_api_key="test-key",
            snapshot_location=(
                "http://127.0.0.1:6333/collections/"
                "TaiLieuKyThuat_v2/snapshots/snapshot-1"
            ),
            allowed_snapshot_origin="http://127.0.0.1:6333",
        )

    assert raised.value.details == {
        "source_collection": "TaiLieuKyThuat_v2",
        "target_collection": "TaiLieuKyThuat_v2_RestoreTest_CountMismatch",
        "snapshot_name": "snapshot-1",
        "recovery_attempted": True,
        "target_may_exist": True,
    }
    assert "point count" in str(raised.value.__cause__)


def test_restore_evidence_binds_current_commit_and_snapshot(tmp_path):
    values = {
        "git_sha": "a" * 40,
        "source_database": "Mech_Chatbot_DB",
        "sql_backup_set_identity_sha256": "b" * 64,
        "source_collection": "TaiLieuKyThuat_v2",
        "snapshot_name": "snapshot-1",
        "snapshot_checksum": "c" * 64,
        "snapshot_location_sha256": "d" * 64,
    }
    fingerprint = build_restore_snapshot_fingerprint(**values)
    artifact = {
        "schema": "backup-restore-drill-v1",
        "git_sha": values["git_sha"],
        "source_database": values["source_database"],
        "source_collection": values["source_collection"],
        "target_database": "Mech_Chatbot_DB_RestoreTest_Evidence",
        "target_collection": "TaiLieuKyThuat_v2_RestoreTest_Evidence",
        "sql_backup_set_identity_sha256": values[
            "sql_backup_set_identity_sha256"
        ],
        "qdrant_snapshot_location_sha256": values[
            "snapshot_location_sha256"
        ],
        "qdrant_snapshot_name": values["snapshot_name"],
        "qdrant_snapshot_checksum": values["snapshot_checksum"],
        "snapshot_fingerprint": fingerprint,
        "passed": True,
        "automatic_cleanup": False,
        "error_type": None,
        "sql": {
            "target_database": "Mech_Chatbot_DB_RestoreTest_Evidence",
            "backup_set_identity_sha256": values[
                "sql_backup_set_identity_sha256"
            ],
            "restored": True,
        },
        "qdrant": {
            "target_collection": "TaiLieuKyThuat_v2_RestoreTest_Evidence",
            "snapshot_name": values["snapshot_name"],
            "snapshot_checksum": values["snapshot_checksum"],
            "restored": True,
        },
    }
    path = tmp_path / "restore.json"
    raw = (json.dumps(artifact) + "\n").encode()
    path.write_bytes(raw)

    assert verify_restore_evidence(
        path,
        expected_sha256=hashlib.sha256(raw).hexdigest(),
        current_git_sha=values["git_sha"],
        source_database=values["source_database"],
        source_collection=values["source_collection"],
        allowed_root=tmp_path,
    ) == fingerprint

    with pytest.raises(ValueError, match="commit"):
        verify_restore_evidence(
            path,
            expected_sha256=hashlib.sha256(raw).hexdigest(),
            current_git_sha="f" * 40,
            source_database=values["source_database"],
            source_collection=values["source_collection"],
            allowed_root=tmp_path,
        )
