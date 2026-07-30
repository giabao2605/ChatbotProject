"""Restore SQL/Qdrant backups into new disposable targets without cleanup."""

from __future__ import annotations

import argparse
from dataclasses import replace
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path, PureWindowsPath
import re
import sys
from urllib.parse import unquote, urlsplit

ROOT = Path(__file__).resolve().parents[2]
for import_root in (ROOT, ROOT / "src"):
    if str(import_root) not in sys.path:
        sys.path.insert(0, str(import_root))

from sqlalchemy import text

from mech_chatbot.adapters.qdrant_runtime import build_qdrant_admin_runtime
from mech_chatbot.config.settings import (
    QdrantSettings,
    SqlSettings,
    load_settings,
)
from mech_chatbot.db.engine import build_database_runtime
from scripts.eval.verify_failure_family_rollback import clean_git_sha
_DISPOSABLE_NAME = re.compile(r"^[A-Za-z][A-Za-z0-9_-]{2,120}$")


class PartialRestoreError(RuntimeError):
    def __init__(self, message: str, *, details: dict):
        super().__init__(message)
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
    if not path.is_absolute() or ".." in path.parts:
        raise ValueError("SQL Server path must be absolute and traversal-free")
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


def restore_sql_backup(
    connection,
    *,
    source_database: str,
    target_database: str,
    backup_path: str,
    data_dir: str,
) -> dict:
    target = validate_disposable_name(
        target_database,
        source_name=source_database,
    )
    backup = _server_path(backup_path, suffix=".bak")
    destination_root = _server_path(data_dir)
    connection = connection.execution_options(isolation_level="AUTOCOMMIT")
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
    rows = connection.execute(
        text("RESTORE FILELISTONLY FROM DISK = :backup_path"),
        {"backup_path": str(backup)},
    ).mappings().all()
    destinations = _destination_files(
        rows,
        target_database=target,
        data_dir=destination_root,
    )
    parameters = {"backup_path": str(backup)}
    moves = []
    for index, (logical_name, physical_path) in enumerate(destinations):
        parameters[f"logical_{index}"] = logical_name
        parameters[f"physical_{index}"] = physical_path
        moves.append(f"MOVE :logical_{index} TO :physical_{index}")
    _assert_sql_target_absent(connection, target)
    connection.execute(
        text(
            f"RESTORE DATABASE [{target}] FROM DISK = :backup_path WITH "
            + ", ".join((*moves, "RECOVERY"))
        ),
        parameters,
    )
    if _database_id(connection, target) is None:
        raise RuntimeError("restored database is not visible")
    return {
        "target_database": target,
        "logical_file_count": len(destinations),
        "restored": True,
    }


def restore_qdrant_snapshot(
    client,
    *,
    source_collection: str,
    target_collection: str,
    snapshot_name: str,
    snapshot_location: str,
) -> dict:
    target = validate_disposable_name(
        target_collection,
        source_name=source_collection,
    )
    location = str(snapshot_location or "").strip()
    snapshot = str(snapshot_name or "").strip()
    if not snapshot or not location:
        raise ValueError("snapshot name and location are required")
    parsed_location = urlsplit(location)
    location_path = (
        parsed_location.path
        if parsed_location.scheme and parsed_location.netloc
        else location
    )
    location_name = (
        unquote(location_path).replace("\\", "/").rstrip("/").rsplit("/", 1)[-1]
    )
    if location_name != snapshot:
        raise ValueError("snapshot location must end with snapshot name")
    if not client.collection_exists(source_collection):
        raise ValueError("source collection does not exist")
    source_snapshots = {
        str(getattr(item, "name", "") or "").strip()
        for item in client.list_snapshots(source_collection)
    }
    if snapshot not in source_snapshots:
        raise ValueError("snapshot does not belong to the source collection")
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
    try:
        recovered = client.recover_snapshot(
            collection_name=target,
            location=location,
            wait=True,
        )
        if recovered is False or not client.collection_exists(target):
            raise RuntimeError("Qdrant snapshot restore did not create the target")
        target_points = int(client.count(target, exact=True).count)
        if target_points != source_points:
            raise RuntimeError("Qdrant restored point count does not match")
    except Exception as error:
        raise PartialRestoreError(
            "Qdrant restore may have created the target",
            details=partial_details,
        ) from error
    return {
        "source_collection": source_collection,
        "target_collection": target,
        "snapshot_name": snapshot,
        "source_points": source_points,
        "target_points": target_points,
        "restored": True,
    }


def _sha256_text(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--sql-backup-path", required=True)
    parser.add_argument("--sql-data-dir", required=True)
    parser.add_argument("--sql-target-database", required=True)
    parser.add_argument("--qdrant-snapshot-location", required=True)
    parser.add_argument("--qdrant-snapshot-name", required=True)
    parser.add_argument("--qdrant-target-collection", required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--execute", action="store_true")
    args = parser.parse_args(argv)
    if not args.execute:
        parser.error("--execute is required to create disposable restore targets")
    if args.output.exists():
        parser.error(f"output already exists: {args.output}")

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
        "qdrant_snapshot_location_sha256": _sha256_text(
            args.qdrant_snapshot_location
        ),
        "qdrant_snapshot_name": args.qdrant_snapshot_name,
        "passed": False,
        "sql": None,
        "qdrant": None,
        "error_type": None,
        "automatic_cleanup": False,
    }
    master_runtime = None
    qdrant_runtime = None
    try:
        master_runtime = build_database_runtime(
            replace(SqlSettings.from_settings(settings), database="master")
        )
        qdrant_runtime = build_qdrant_admin_runtime(
            QdrantSettings.from_settings(settings)
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
            )
            report["qdrant"] = restore_qdrant_snapshot(
                qdrant_runtime.client,
                source_collection=settings.QDRANT_COLLECTION,
                target_collection=qdrant_target,
                snapshot_name=args.qdrant_snapshot_name,
                snapshot_location=args.qdrant_snapshot_location,
            )
            report["passed"] = True
    except Exception as error:
        report["error_type"] = type(error).__name__
        if isinstance(error, PartialRestoreError):
            report["qdrant"] = dict(error.details)
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
