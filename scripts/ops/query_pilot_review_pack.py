"""Build a metadata-only Query pilot owner-review pack."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from scripts.ops.query_pilot_review_artifacts import (
    REVIEW_PACK_SCHEMA,
    build_review_pack,
    load_metadata_artifact,
    load_metadata_rows,
    write_metadata_artifact,
)
from scripts.ops.query_pilot_review_capture import (
    _sha256,
    _strict_object,
)
from scripts.ops.query_pilot_review_integrity import (
    review_tool_hashes,
    validate_authorized_source,
)


def _inside(path: Path, root: Path) -> bool:
    try:
        path.resolve().relative_to(root.resolve())
    except ValueError:
        return False
    return True


def _expected_review_pack_from_paths(
    *, source_root: str | Path, authorization_path: str | Path,
    schedule_path: str | Path, wal_path: str | Path, trace_path: str | Path,
    capture_dir: str | Path, pack_path: str | Path,
) -> dict:
    local = Path(source_root).resolve() / ".local"
    authorization_file = Path(authorization_path).resolve()
    schedule_file = Path(schedule_path).resolve()
    wal_file = Path(wal_path).resolve()
    trace_file = Path(trace_path).resolve()
    captures = Path(capture_dir).resolve()
    pack_file = Path(pack_path).resolve()
    if not all(
        _inside(path, local)
        for path in (
            authorization_file, schedule_file, wal_file, trace_file,
            captures, pack_file,
        )
    ):
        raise ValueError("review_artifact_outside_dot_local")
    authorization_raw = authorization_file.read_bytes()
    schedule_raw = schedule_file.read_bytes()
    authorization = _strict_object(authorization_raw)
    schedule = _strict_object(schedule_raw)
    if not all((
        authorization.get("schema")
        == "query-controlled-demo-pilot-authorization-v1",
        schedule.get("schema") == "query-decomposition-pilot-schedule-v1",
    )):
        raise ValueError("review_pack_source_schema_invalid")
    run_root_value = authorization.get("pilot_run_root")
    run_root = (
        (Path(source_root).resolve() / run_root_value).resolve()
        if isinstance(run_root_value, str) else local.parent
    )
    if not all((
        isinstance(run_root_value, str),
        run_root_value == Path(str(run_root_value)).as_posix(),
        _inside(run_root, local),
        wal_file == run_root / "pilot.wal.jsonl",
        trace_file == run_root / "trace.jsonl",
        captures == run_root / "review-captures",
        pack_file.parent == run_root,
        wal_file.is_file() and not wal_file.is_symlink(),
        trace_file.is_file() and not trace_file.is_symlink(),
    )):
        raise ValueError("review_artifact_run_root_mismatch")
    validate_authorized_source(source_root, str(authorization.get("source_commit")))
    wal_raw = wal_file.read_bytes()
    trace_raw = trace_file.read_bytes()
    return build_review_pack(
        authorization=authorization,
        authorization_sha256=_sha256(authorization_raw),
        schedule=schedule,
        schedule_sha256=_sha256(schedule_raw),
        rows=load_metadata_rows(wal_file),
        capture_dir=captures,
        wal_sha256=_sha256(wal_raw),
        trace_artifact_sha256=_sha256(trace_raw),
        review_tool_sha256=review_tool_hashes(source_root),
    )


def build_review_pack_from_paths(
    *, source_root: str | Path, authorization_path: str | Path,
    schedule_path: str | Path, wal_path: str | Path, trace_path: str | Path,
    capture_dir: str | Path, output_path: str | Path,
) -> dict:
    """Build and exclusively write a review pack from one completed run root."""
    pack = _expected_review_pack_from_paths(
        source_root=source_root,
        authorization_path=authorization_path,
        schedule_path=schedule_path,
        wal_path=wal_path,
        trace_path=trace_path,
        capture_dir=capture_dir,
        pack_path=output_path,
    )
    pack_sha256 = write_metadata_artifact(output_path, pack)
    return {
        "schema": "query-decomposition-pilot-review-pack-build-v1",
        "status": "ready_for_local_owner_review",
        "review_pack_sha256": pack_sha256,
        "capture_count": pack["capture_count"],
        "review_item_count": pack["review_item_count"],
        "raw_content_emitted": False,
    }


def validate_review_pack_from_paths(
    *, source_root: str | Path, authorization_path: str | Path,
    schedule_path: str | Path, wal_path: str | Path, trace_path: str | Path,
    capture_dir: str | Path, pack_path: str | Path,
) -> dict:
    """Rebuild and compare a persisted pack before review or deletion."""
    expected = _expected_review_pack_from_paths(
        source_root=source_root,
        authorization_path=authorization_path,
        schedule_path=schedule_path,
        wal_path=wal_path,
        trace_path=trace_path,
        capture_dir=capture_dir,
        pack_path=pack_path,
    )
    actual = load_metadata_artifact(pack_path, schema=REVIEW_PACK_SCHEMA)
    if actual != expected:
        raise ValueError("review_pack_run_binding_invalid")
    return actual


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--source-root", type=Path, required=True)
    parser.add_argument("--authorization", type=Path, required=True)
    parser.add_argument("--schedule", type=Path, required=True)
    parser.add_argument("--wal", type=Path, required=True)
    parser.add_argument("--trace", type=Path, required=True)
    parser.add_argument("--capture-dir", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args(argv)
    try:
        result = build_review_pack_from_paths(
            source_root=args.source_root,
            authorization_path=args.authorization,
            schedule_path=args.schedule,
            wal_path=args.wal,
            trace_path=args.trace,
            capture_dir=args.capture_dir,
            output_path=args.output,
        )
    except Exception:
        return 1
    print(json.dumps(result, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
