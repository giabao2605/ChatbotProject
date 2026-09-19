import hashlib
import json
from pathlib import Path

import pytest

from scripts.ops.restore_drill import (
    build_restore_snapshot_fingerprint,
    verify_restore_evidence,
)


def test_restore_evidence_binds_current_commit_and_snapshot(
    tmp_path,
    monkeypatch,
):
    values = {
        "git_sha": "a" * 40,
        "source_database": "Mech_Chatbot_DB",
        "sql_backup_set_identity_sha256": "b" * 64,
        "source_collection": "TaiLieuKyThuat_v2",
        "snapshot_name": "snapshot-1",
        "snapshot_checksum": "c" * 64,
        "snapshot_location_sha256": "d" * 64,
        "expected_points": 238,
    }
    fingerprint = build_restore_snapshot_fingerprint(**values)
    assert fingerprint != build_restore_snapshot_fingerprint(
        **{**values, "expected_points": 237}
    )
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
        "qdrant_expected_points": values["expected_points"],
        "snapshot_fingerprint": fingerprint,
        "passed": True,
        "automatic_cleanup": False,
        "error_type": None,
        "sql": {
            "target_database": "Mech_Chatbot_DB_RestoreTest_Evidence",
            "backup_set_identity_sha256": values[
                "sql_backup_set_identity_sha256"
            ],
            "state_desc": "ONLINE",
            "user_access_desc": "MULTI_USER",
            "has_db_access": True,
            "restored": True,
        },
        "qdrant": {
            "target_collection": "TaiLieuKyThuat_v2_RestoreTest_Evidence",
            "snapshot_name": values["snapshot_name"],
            "snapshot_checksum": values["snapshot_checksum"],
            "source_points": 231,
            "expected_points": values["expected_points"],
            "target_points": values["expected_points"],
            "restored": True,
        },
    }
    path = tmp_path / "restore.json"
    raw = (json.dumps(artifact) + "\n").encode()
    path.write_bytes(raw)

    with monkeypatch.context() as patch:
        patch.setattr(
            Path,
            "read_text",
            lambda *_args, **_kwargs: pytest.fail(
                "evidence must not be read twice"
            ),
        )
        assert verify_restore_evidence(
            path,
            expected_sha256=hashlib.sha256(raw).hexdigest(),
            current_git_sha=values["git_sha"],
            source_database=values["source_database"],
            source_collection=values["source_collection"],
            allowed_root=tmp_path,
        ) == fingerprint

    for invalid_status in (
        {"state_desc": "RESTORING"},
        {"user_access_desc": "SINGLE_USER"},
        {"has_db_access": False},
    ):
        invalid_sql = {
            **artifact,
            "sql": {
                **artifact["sql"],
                **invalid_status,
            },
        }
        invalid_raw = (json.dumps(invalid_sql) + "\n").encode()
        path.write_bytes(invalid_raw)
        with pytest.raises(ValueError, match="current commit"):
            verify_restore_evidence(
                path,
                expected_sha256=hashlib.sha256(invalid_raw).hexdigest(),
                current_git_sha=values["git_sha"],
                source_database=values["source_database"],
                source_collection=values["source_collection"],
                allowed_root=tmp_path,
            )

    for invalid_qdrant in (
        {"expected_points": 0},
        {"target_points": 237},
    ):
        invalid_artifact = {
            **artifact,
            "qdrant": {
                **artifact["qdrant"],
                **invalid_qdrant,
            },
        }
        invalid_raw = (json.dumps(invalid_artifact) + "\n").encode()
        path.write_bytes(invalid_raw)
        with pytest.raises(ValueError, match="current commit"):
            verify_restore_evidence(
                path,
                expected_sha256=hashlib.sha256(invalid_raw).hexdigest(),
                current_git_sha=values["git_sha"],
                source_database=values["source_database"],
                source_collection=values["source_collection"],
                allowed_root=tmp_path,
            )

    old_artifact = dict(artifact)
    old_artifact.pop("qdrant_expected_points")
    old_raw = (json.dumps(old_artifact) + "\n").encode()
    path.write_bytes(old_raw)
    with pytest.raises(ValueError, match="current commit"):
        verify_restore_evidence(
            path,
            expected_sha256=hashlib.sha256(old_raw).hexdigest(),
            current_git_sha=values["git_sha"],
            source_database=values["source_database"],
            source_collection=values["source_collection"],
            allowed_root=tmp_path,
        )

    path.write_bytes(raw)
    with pytest.raises(ValueError, match="commit"):
        verify_restore_evidence(
            path,
            expected_sha256=hashlib.sha256(raw).hexdigest(),
            current_git_sha="f" * 40,
            source_database=values["source_database"],
            source_collection=values["source_collection"],
            allowed_root=tmp_path,
        )
