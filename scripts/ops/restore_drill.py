"""Restore SQL/Qdrant backups into new disposable targets without cleanup."""

from __future__ import annotations

import argparse
from contextlib import contextmanager
from dataclasses import replace
from datetime import datetime, timezone
import hashlib
import json
import math
from pathlib import Path, PureWindowsPath
import re
import sys
from tempfile import TemporaryDirectory
import time
from urllib.parse import unquote, urlsplit

ROOT = Path(__file__).resolve().parents[2]
for import_root in (ROOT, ROOT / "src"):
    if str(import_root) not in sys.path:
        sys.path.insert(0, str(import_root))

import requests
from sqlalchemy import text

from mech_chatbot.adapters.qdrant_runtime import build_qdrant_admin_runtime
from mech_chatbot.config.settings import (
    QdrantSettings,
    SqlSettings,
    load_settings,
)
from mech_chatbot.db.engine import build_database_runtime
from scripts.eval.verify_failure_family_rollback import clean_git_sha
from scripts.ops.restore_sql_runtime import (
    SqlRestoreVerificationError,
    execute_sql_restore,
    wait_for_sql_accessible,
)
_DISPOSABLE_NAME = re.compile(r"^[A-Za-z][A-Za-z0-9_-]{2,120}$")
_SHA256 = re.compile(r"^[0-9a-fA-F]{64}$")
_GIT_SHA = re.compile(r"^[0-9a-fA-F]{40}$")
_DEFAULT_SQL_WAIT_SECONDS = 60.0
_DEFAULT_QDRANT_WAIT_SECONDS = 300.0


class PartialRestoreError(RuntimeError):
    def __init__(self, message: str, *, section: str, details: dict):
        super().__init__(message)
        self.section = section
        self.details = dict(details)


def validate_disposable_name(name: str, *, source_name: str) -> str:
    value = str(name or "").strip()
    lowered = value.casefold()
    if (
        not _DISPOSABLE_NAME.fullmatch(value)
        or "restore" not in lowered
        or "test" not in lowered
        or lowered == str(source_name or "").strip().casefold()
        or not lowered.startswith(f"{str(source_name).strip().casefold()}_")
    ):
        raise ValueError("target must be a source-prefixed disposable RestoreTest name")
    return value


def _server_path(value: str, *, suffix: str | None = None) -> PureWindowsPath:
    path = PureWindowsPath(str(value or "").strip())
    if (
        not path.is_absolute()
        or str(path.drive).startswith("\\\\")
        or ".." in path.parts
    ):
        raise ValueError(
            "SQL Server path must be local, absolute and traversal-free"
        )
    if suffix and path.suffix.casefold() != suffix:
        raise ValueError(f"SQL Server path must end with {suffix}")
    return path


def _database_id(connection, target_database: str):
    return connection.execute(
        text("SELECT DB_ID(:target_database)"),
        {"target_database": target_database},
    ).scalar_one_or_none()


def _assert_sql_target_absent(connection, target_database: str) -> None:
    if _database_id(connection, target_database) is not None:
        raise ValueError(f"target database already exists: {target_database}")


def _backup_set_identity(header: dict) -> str:
    fields = (
        "DatabaseName",
        "BackupSetGUID",
        "FirstLSN",
        "LastLSN",
        "CheckpointLSN",
        "DatabaseBackupLSN",
        "BackupType",
        "Position",
    )
    identity = {
        field: str(header.get(field) or "").strip()
        for field in fields
    }
    if (
        any(not identity[field] for field in fields)
        or identity["BackupType"] != "1"
    ):
        raise ValueError("SQL backup-set identity is incomplete or not full")
    return _sha256_text(json.dumps(
        identity,
        sort_keys=True,
        separators=(",", ":"),
    ))


def _destination_files(rows, *, target_database: str, data_dir: PureWindowsPath):
    destinations = []
    data_index = 0
    log_index = 0
    for row in rows:
        logical_name = str(row.get("LogicalName") or "").strip()
        file_type = str(row.get("Type") or "").strip().upper()
        if not logical_name or file_type not in {"D", "L"}:
            raise ValueError("backup file list is incomplete")
        if file_type == "D":
            data_index += 1
            suffix = ".mdf" if data_index == 1 else f"_{data_index}.ndf"
        else:
            log_index += 1
            suffix = ".ldf" if log_index == 1 else f"_{log_index}.ldf"
        destinations.append(
            (logical_name, str(data_dir / f"{target_database}{suffix}"))
        )
    if data_index == 0 or log_index == 0:
        raise ValueError("backup must contain data and log files")
    return destinations


@contextmanager
def _verified_snapshot_file(*, location: str, api_key: str, checksum: str):
    with TemporaryDirectory(prefix="qdrant-restore-") as temporary:
        path = Path(temporary) / "restore.snapshot"
        digest = hashlib.sha256()
        with requests.get(
            location,
            headers={"api-key": api_key},
            stream=True,
            timeout=120,
            allow_redirects=False,
        ) as response, path.open("wb") as output:
            if 300 <= response.status_code < 400:
                raise ValueError("snapshot download redirect is not allowed")
            response.raise_for_status()
            for chunk in response.iter_content(chunk_size=1024 * 1024):
                if chunk:
                    output.write(chunk)
                    digest.update(chunk)
        if digest.hexdigest() != checksum:
            raise ValueError("downloaded snapshot checksum does not match")
        with path.open("rb") as snapshot:
            yield snapshot


def _wait_for_restored_collection(
    client,
    target: str,
    expected_points: int,
    *,
    timeout_seconds: float = _DEFAULT_QDRANT_WAIT_SECONDS,
) -> int:
    wait_seconds = float(timeout_seconds)
    if not math.isfinite(wait_seconds) or wait_seconds <= 0:
        raise ValueError("Qdrant wait seconds must be positive and finite")
    deadline = time.monotonic() + wait_seconds
    target_points = None
    last_error = None
    while time.monotonic() < deadline:
        try:
            if client.collection_exists(target):
                target_points = int(client.count(target, exact=True).count)
                if target_points == expected_points:
                    return target_points
            last_error = None
        except Exception as error:
            last_error = error
        time.sleep(2)
    if target_points is not None:
        failure = RuntimeError("Qdrant restored point count does not match")
    else:
        failure = RuntimeError(
            "Qdrant restored collection did not become available"
        )
    if last_error is not None:
        raise failure from last_error
    raise failure


def restore_sql_backup(
    connection,
    *,
    source_database: str,
    target_database: str,
    backup_path: str,
    data_dir: str,
    timeout_seconds: float = _DEFAULT_SQL_WAIT_SECONDS,
) -> dict:
    target = validate_disposable_name(
        target_database,
        source_name=source_database,
    )
    backup = _server_path(backup_path, suffix=".bak")
    destination_root = _server_path(data_dir)
    wait_seconds = float(timeout_seconds)
    if not math.isfinite(wait_seconds) or wait_seconds <= 0:
        raise ValueError("SQL wait seconds must be positive and finite")
    _assert_sql_target_absent(connection, target)
    headers = connection.execute(
        text("RESTORE HEADERONLY FROM DISK = :backup_path"),
        {"backup_path": str(backup)},
    ).mappings().all()
    if (
        len(headers) != 1
        or str(headers[0].get("DatabaseName") or "").casefold()
        != str(source_database).strip().casefold()
    ):
        raise ValueError("backup does not match the configured source database")
    backup_set_identity = _backup_set_identity(headers[0])
    rows = connection.execute(
        text("RESTORE FILELISTONLY FROM DISK = :backup_path"),
        {"backup_path": str(backup)},
    ).mappings().all()
    destinations = _destination_files(
        rows,
        target_database=target,
        data_dir=destination_root,
    )
    _assert_sql_target_absent(connection, target)
    restore_error = None
    try:
        execute_sql_restore(
            connection,
            target_database=target,
            backup_path=str(backup),
            destinations=destinations,
        )
    except Exception as error:
        restore_error = error
    try:
        target_exists = _database_id(connection, target) is not None
    except Exception as error:
        cause = (
            ExceptionGroup(
                "SQL restore and target existence verification failed",
                (restore_error, error),
            )
            if restore_error is not None
            else error
        )
        raise PartialRestoreError(
            "SQL restore target existence could not be verified",
            section="sql",
            details={
                "target_database": target,
                "recovery_attempted": False,
                "target_may_exist": True,
            },
        ) from cause
    if not target_exists:
        error = restore_error or RuntimeError(
            "restored database is not visible"
        )
        raise PartialRestoreError(
            "SQL restore target is not yet visible",
            section="sql",
            details={
                "target_database": target,
                "recovery_attempted": False,
                "target_may_exist": True,
            },
        ) from error
    try:
        status = wait_for_sql_accessible(
            connection,
            target,
            timeout_seconds=wait_seconds,
        )
    except SqlRestoreVerificationError as error:
        cause = (
            ExceptionGroup(
                "SQL restore and status verification failed",
                (restore_error, error),
            )
            if restore_error is not None
            else error
        )
        raise PartialRestoreError(
            "SQL restore target is not verified accessible",
            section="sql",
            details={
                "target_database": target,
                "recovery_attempted": error.recovery_attempted,
                "target_may_exist": True,
            },
        ) from cause
    return {
        "target_database": target,
        "backup_set_identity_sha256": backup_set_identity,
        "logical_file_count": len(destinations),
        **status,
        "restored": True,
    }


def restore_qdrant_snapshot(
    client,
    *,
    source_collection: str,
    target_collection: str,
    snapshot_name: str,
    snapshot_checksum: str,
    snapshot_api_key: str,
    snapshot_location: str,
    allowed_snapshot_origin: str,
    expected_points: int,
    timeout_seconds: float = _DEFAULT_QDRANT_WAIT_SECONDS,
) -> dict:
    target = validate_disposable_name(
        target_collection,
        source_name=source_collection,
    )
    wait_seconds = float(timeout_seconds)
    if not math.isfinite(wait_seconds) or wait_seconds <= 0:
        raise ValueError("Qdrant wait seconds must be positive and finite")
    if (
        isinstance(expected_points, bool)
        or not isinstance(expected_points, int)
        or expected_points <= 0
    ):
        raise ValueError("Qdrant expected points must be a positive integer")
    location = str(snapshot_location or "").strip()
    snapshot = str(snapshot_name or "").strip()
    checksum = str(snapshot_checksum or "").strip().casefold()
    api_key = str(snapshot_api_key or "").strip()
    if (
        not snapshot
        or not location
        or not api_key
        or not _SHA256.fullmatch(checksum)
    ):
        raise ValueError(
            "snapshot name, checksum, location and API key are required"
        )
    parsed_location = urlsplit(location)
    parsed_origin = urlsplit(str(allowed_snapshot_origin or "").strip())
    if (
        parsed_location.scheme not in {"http", "https"}
        or parsed_location.scheme.casefold() != parsed_origin.scheme.casefold()
        or parsed_location.hostname is None
        or parsed_origin.hostname is None
        or parsed_location.hostname.casefold() != parsed_origin.hostname.casefold()
        or parsed_location.port != parsed_origin.port
        or parsed_location.username is not None
        or parsed_location.password is not None
        or parsed_location.query
        or parsed_location.fragment
    ):
        raise ValueError("snapshot origin must match the configured Qdrant origin")
    location_parts = [
        unquote(part)
        for part in parsed_location.path.replace("\\", "/").split("/")
        if part
    ]
    origin_parts = [
        unquote(part)
        for part in parsed_origin.path.replace("\\", "/").split("/")
        if part
    ]
    if location_parts != [
        *origin_parts,
        "collections",
        source_collection,
        "snapshots",
        snapshot,
    ]:
        raise ValueError(
            "snapshot location must be the source collection snapshot endpoint"
        )
    location_name = (
        unquote(parsed_location.path)
        .replace("\\", "/")
        .rstrip("/")
        .rsplit("/", 1)[-1]
    )
    if location_name != snapshot:
        raise ValueError("snapshot location must end with snapshot name")
    if not client.collection_exists(source_collection):
        raise ValueError("source collection does not exist")
    source_snapshots = [
        item
        for item in client.list_snapshots(source_collection)
        if str(getattr(item, "name", "") or "").strip() == snapshot
    ]
    if len(source_snapshots) != 1:
        raise ValueError("snapshot does not belong to the source collection")
    source_checksum = str(
        getattr(source_snapshots[0], "checksum", "") or ""
    ).strip().casefold()
    if source_checksum != checksum:
        raise ValueError("snapshot checksum does not match the source snapshot")
    if client.collection_exists(target):
        raise ValueError(f"target collection already exists: {target}")
    source_points = int(client.count(source_collection, exact=True).count)
    if client.collection_exists(target):
        raise ValueError(f"target collection already exists: {target}")
    partial_details = {
        "source_collection": source_collection,
        "target_collection": target,
        "snapshot_name": snapshot,
        "recovery_attempted": True,
        "target_may_exist": True,
    }
    recovery_error = None
    with _verified_snapshot_file(
        location=location,
        api_key=api_key,
        checksum=checksum,
    ) as snapshot_file:
        try:
            client.http.snapshots_api.recover_from_uploaded_snapshot(
                collection_name=target,
                wait=False,
                checksum=checksum,
                snapshot=snapshot_file,
            )
        except Exception as error:
            recovery_error = error
    try:
        target_points = _wait_for_restored_collection(
            client,
            target,
            expected_points,
            timeout_seconds=wait_seconds,
        )
    except Exception as error:
        if recovery_error is not None:
            try:
                raise error from recovery_error
            except Exception as chained_error:
                error = chained_error
        raise PartialRestoreError(
            "Qdrant restore may have created the target",
            section="qdrant",
            details=partial_details,
        ) from error
    return {
        "source_collection": source_collection,
        "target_collection": target,
        "snapshot_name": snapshot,
        "snapshot_checksum": checksum,
        "source_points": source_points,
        "expected_points": expected_points,
        "target_points": target_points,
        "restored": True,
    }


def _sha256_text(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def build_restore_snapshot_fingerprint(
    *,
    git_sha: str,
    source_database: str,
    sql_backup_set_identity_sha256: str,
    source_collection: str,
    snapshot_name: str,
    snapshot_checksum: str,
    snapshot_location_sha256: str,
    expected_points: int,
) -> str:
    values = {
        "git_sha": str(git_sha).casefold(),
        "source_database": str(source_database),
        "sql_backup_set_identity_sha256": str(
            sql_backup_set_identity_sha256
        ).casefold(),
        "source_collection": str(source_collection),
        "snapshot_name": str(snapshot_name),
        "snapshot_checksum": str(snapshot_checksum).casefold(),
        "snapshot_location_sha256": str(snapshot_location_sha256).casefold(),
        "expected_points": expected_points,
    }
    if (
        not _GIT_SHA.fullmatch(values["git_sha"])
        or any(
            not _SHA256.fullmatch(values[field])
            for field in (
                "sql_backup_set_identity_sha256",
                "snapshot_checksum",
                "snapshot_location_sha256",
            )
        )
        or not values["source_database"].strip()
        or not values["source_collection"].strip()
        or not values["snapshot_name"].strip()
        or isinstance(expected_points, bool)
        or not isinstance(expected_points, int)
        or expected_points <= 0
    ):
        raise ValueError("restore snapshot fingerprint inputs are invalid")
    return _sha256_text(json.dumps(
        values,
        sort_keys=True,
        separators=(",", ":"),
    ))


def verify_restore_evidence(
    path: Path,
    *,
    expected_sha256: str,
    current_git_sha: str,
    source_database: str,
    source_collection: str,
    allowed_root: Path,
) -> str:
    evidence_path = Path(path).resolve()
    evidence_root = Path(allowed_root).resolve()
    if not evidence_path.is_relative_to(evidence_root) or not evidence_path.is_file():
        raise ValueError("restore evidence must be an existing local artifact")
    expected_digest = str(expected_sha256 or "").strip().casefold()
    raw = evidence_path.read_bytes()
    if (
        not _SHA256.fullmatch(expected_digest)
        or hashlib.sha256(raw).hexdigest() != expected_digest
    ):
        raise ValueError("restore evidence SHA-256 does not match")
    artifact = json.loads(raw.decode("utf-8"))
    sql = artifact.get("sql") if isinstance(artifact, dict) else None
    qdrant = artifact.get("qdrant") if isinstance(artifact, dict) else None
    expected_points = (
        artifact.get("qdrant_expected_points")
        if isinstance(artifact, dict)
        else None
    )
    if (
        not isinstance(sql, dict)
        or not isinstance(qdrant, dict)
        or artifact.get("schema") != "backup-restore-drill-v1"
        or artifact.get("passed") is not True
        or artifact.get("automatic_cleanup") is not False
        or artifact.get("error_type") is not None
        or artifact.get("git_sha") != current_git_sha
        or artifact.get("source_database") != source_database
        or artifact.get("source_collection") != source_collection
        or sql.get("restored") is not True
        or qdrant.get("restored") is not True
        or sql.get("target_database") != artifact.get("target_database")
        or sql.get("state_desc") != "ONLINE"
        or sql.get("user_access_desc") != "MULTI_USER"
        or sql.get("has_db_access") is not True
        or qdrant.get("target_collection") != artifact.get("target_collection")
        or qdrant.get("snapshot_name") != artifact.get("qdrant_snapshot_name")
        or qdrant.get("snapshot_checksum")
        != str(artifact.get("qdrant_snapshot_checksum") or "").casefold()
        or isinstance(expected_points, bool)
        or not isinstance(expected_points, int)
        or expected_points <= 0
        or qdrant.get("expected_points") != expected_points
        or qdrant.get("target_points") != expected_points
        or sql.get("backup_set_identity_sha256")
        != artifact.get("sql_backup_set_identity_sha256")
    ):
        raise ValueError("restore evidence does not match the current commit")
    validate_disposable_name(
        artifact["target_database"],
        source_name=source_database,
    )
    validate_disposable_name(
        artifact["target_collection"],
        source_name=source_collection,
    )
    fingerprint = build_restore_snapshot_fingerprint(
        git_sha=artifact["git_sha"],
        source_database=artifact["source_database"],
        sql_backup_set_identity_sha256=artifact[
            "sql_backup_set_identity_sha256"
        ],
        source_collection=artifact["source_collection"],
        snapshot_name=artifact["qdrant_snapshot_name"],
        snapshot_checksum=artifact["qdrant_snapshot_checksum"],
        snapshot_location_sha256=artifact[
            "qdrant_snapshot_location_sha256"
        ],
        expected_points=expected_points,
    )
    if artifact.get("snapshot_fingerprint") != fingerprint:
        raise ValueError("restore evidence snapshot fingerprint does not match")
    return fingerprint


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--sql-backup-path", required=True)
    parser.add_argument("--sql-data-dir", required=True)
    parser.add_argument("--sql-target-database", required=True)
    parser.add_argument(
        "--sql-wait-seconds",
        type=float,
        default=_DEFAULT_SQL_WAIT_SECONDS,
        help="Timeout for restored SQL state and access verification.",
    )
    parser.add_argument("--qdrant-snapshot-location", required=True)
    parser.add_argument("--qdrant-snapshot-name", required=True)
    parser.add_argument("--qdrant-snapshot-checksum", required=True)
    parser.add_argument("--qdrant-expected-points", type=int, required=True)
    parser.add_argument("--qdrant-target-collection", required=True)
    parser.add_argument(
        "--qdrant-wait-seconds",
        type=float,
        default=_DEFAULT_QDRANT_WAIT_SECONDS,
        help="Timeout for snapshot upload and restored-count settling.",
    )
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--execute", action="store_true")
    args = parser.parse_args(argv)
    if not args.execute:
        parser.error("--execute is required to create disposable restore targets")
    if args.output.exists():
        parser.error(f"output already exists: {args.output}")
    if (
        not math.isfinite(args.sql_wait_seconds)
        or args.sql_wait_seconds <= 0
    ):
        parser.error("--sql-wait-seconds must be positive and finite")
    if (
        not math.isfinite(args.qdrant_wait_seconds)
        or args.qdrant_wait_seconds <= 0
    ):
        parser.error("--qdrant-wait-seconds must be positive and finite")
    if args.qdrant_expected_points <= 0:
        parser.error("--qdrant-expected-points must be a positive integer")

    settings = load_settings()
    git_sha = clean_git_sha(ROOT)
    sql_target = validate_disposable_name(
        args.sql_target_database,
        source_name=settings.SQL_DATABASE,
    )
    qdrant_target = validate_disposable_name(
        args.qdrant_target_collection,
        source_name=settings.QDRANT_COLLECTION,
    )
    report = {
        "schema": "backup-restore-drill-v1",
        "git_sha": git_sha,
        "tested_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        "source_database": settings.SQL_DATABASE,
        "target_database": sql_target,
        "source_collection": settings.QDRANT_COLLECTION,
        "target_collection": qdrant_target,
        "sql_backup_path_sha256": _sha256_text(args.sql_backup_path),
        "sql_backup_set_identity_sha256": None,
        "qdrant_snapshot_location_sha256": _sha256_text(
            args.qdrant_snapshot_location
        ),
        "qdrant_snapshot_name": args.qdrant_snapshot_name,
        "qdrant_snapshot_checksum": args.qdrant_snapshot_checksum,
        "qdrant_expected_points": args.qdrant_expected_points,
        "passed": False,
        "sql": None,
        "qdrant": None,
        "error_type": None,
        "automatic_cleanup": False,
        "snapshot_fingerprint": None,
    }
    master_runtime = None
    qdrant_runtime = None
    try:
        master_runtime = build_database_runtime(
            replace(SqlSettings.from_settings(settings), database="master")
        )
        qdrant_runtime = build_qdrant_admin_runtime(
            QdrantSettings.from_settings(settings),
            timeout_seconds=args.qdrant_wait_seconds,
        )
        connection = master_runtime.engine.connect().execution_options(
            isolation_level="AUTOCOMMIT"
        )
        with connection:
            _assert_sql_target_absent(connection, sql_target)
            if qdrant_runtime.client.collection_exists(qdrant_target):
                raise ValueError(
                    f"target collection already exists: {qdrant_target}"
                )
            report["sql"] = restore_sql_backup(
                connection,
                source_database=settings.SQL_DATABASE,
                target_database=sql_target,
                backup_path=args.sql_backup_path,
                data_dir=args.sql_data_dir,
                timeout_seconds=args.sql_wait_seconds,
            )
            report["sql_backup_set_identity_sha256"] = report["sql"][
                "backup_set_identity_sha256"
            ]
            report["qdrant"] = restore_qdrant_snapshot(
                qdrant_runtime.client,
                source_collection=settings.QDRANT_COLLECTION,
                target_collection=qdrant_target,
                snapshot_name=args.qdrant_snapshot_name,
                snapshot_checksum=args.qdrant_snapshot_checksum,
                snapshot_api_key=settings.QDRANT_API_KEY or "",
                snapshot_location=args.qdrant_snapshot_location,
                allowed_snapshot_origin=settings.QDRANT_URL,
                expected_points=args.qdrant_expected_points,
                timeout_seconds=args.qdrant_wait_seconds,
            )
            report["snapshot_fingerprint"] = (
                build_restore_snapshot_fingerprint(
                    git_sha=git_sha,
                    source_database=settings.SQL_DATABASE,
                    sql_backup_set_identity_sha256=report[
                        "sql_backup_set_identity_sha256"
                    ],
                    source_collection=settings.QDRANT_COLLECTION,
                    snapshot_name=args.qdrant_snapshot_name,
                    snapshot_checksum=args.qdrant_snapshot_checksum,
                    snapshot_location_sha256=report[
                        "qdrant_snapshot_location_sha256"
                    ],
                    expected_points=args.qdrant_expected_points,
                )
            )
            report["passed"] = True
    except Exception as error:
        report["error_type"] = type(error).__name__
        if isinstance(error, PartialRestoreError):
            report[error.section] = dict(error.details)
    finally:
        if qdrant_runtime is not None:
            qdrant_runtime.close()
        if master_runtime is not None:
            master_runtime.close()

    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if report["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
