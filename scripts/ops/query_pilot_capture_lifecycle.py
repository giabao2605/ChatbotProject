"""Crash-resumable ciphertext deletion for a Query pilot run root."""

from __future__ import annotations

from datetime import datetime, timezone
import os
from pathlib import Path
from typing import Mapping

from scripts.ops.query_pilot_review_capture import (
    _canonical,
    _digest,
    _exclusive_atomic_bytes,
    _sha256,
    _strict_object,
    restrict_directory_acl,
)


JOURNAL_SCHEMA = "query-pilot-capture-deletion-journal-v1"


def _timestamp() -> str:
    return datetime.now(timezone.utc).isoformat(
        timespec="seconds",
    ).replace("+00:00", "Z")


def _write_replace(path: Path, value: Mapping[str, object]) -> None:
    raw = _canonical(dict(value)) + b"\n"
    temporary = path.with_name(path.name + ".next")
    _exclusive_atomic_bytes(temporary, raw)
    os.replace(temporary, path)


def _regular_file_hashes(directory: Path) -> dict[str, str]:
    result = {}
    for path in directory.iterdir():
        if path.is_symlink() or not path.is_file() or path.name != Path(path.name).name:
            raise ValueError("capture_deletion_set_invalid")
        result[path.name] = _sha256(path.read_bytes())
    return result


def _journal_value(
    *, operation: str, binding_sha256: str,
    expected_files: Mapping[str, str], status: str,
) -> dict:
    return {
        "schema": JOURNAL_SCHEMA,
        "operation": operation,
        "binding_sha256": binding_sha256,
        "expected_files": dict(sorted(expected_files.items())),
        "status": status,
        "remaining_capture_count": 0 if status == "finalized" else len(expected_files),
        "updated_at": _timestamp(),
    }


def _inputs_valid(
    operation: str, binding_sha256: str, expected_files: Mapping[str, str],
) -> bool:
    return bool(
        operation in {"owner_review", "terminal_cleanup"}
        and _digest(binding_sha256)
        and all(
            isinstance(name, str)
            and name == Path(name).name
            and name.endswith(".capture.json")
            and _digest(digest)
            for name, digest in expected_files.items()
        )
    )


def delete_capture_set(
    *, capture_dir: str | Path, journal_path: str | Path,
    operation: str, binding_sha256: str,
    expected_files: Mapping[str, str],
) -> tuple[dict, str]:
    """Atomically quarantine, delete, and journal an exact ciphertext set."""
    target = Path(capture_dir).resolve()
    journal_file = Path(journal_path).resolve()
    quarantine = target.with_name(target.name + ".quarantine")
    expected = dict(expected_files)
    if not all((
        target.name == "review-captures",
        journal_file == target.parent / "capture-deletion.journal.json",
        not target.is_symlink(),
        not quarantine.is_symlink(),
        _inputs_valid(operation, binding_sha256, expected),
    )):
        raise ValueError("capture_deletion_inputs_invalid")
    prepared = _journal_value(
        operation=operation, binding_sha256=binding_sha256,
        expected_files=expected, status="prepared",
    )
    if journal_file.exists():
        journal = _strict_object(journal_file.read_bytes())
        stable = {key: value for key, value in journal.items() if key not in {
            "status", "remaining_capture_count", "updated_at",
        }}
        prepared_stable = {key: value for key, value in prepared.items() if key not in {
            "status", "remaining_capture_count", "updated_at",
        }}
        if stable != prepared_stable:
            raise ValueError("capture_deletion_journal_invalid")
        if journal.get("status") == "finalized":
            if quarantine.exists() or _regular_file_hashes(target):
                raise ValueError("capture_deletion_incomplete")
            return journal, _sha256(journal_file.read_bytes())
    else:
        if _regular_file_hashes(target) != expected or quarantine.exists():
            raise ValueError("capture_deletion_set_invalid")
        _exclusive_atomic_bytes(journal_file, _canonical(prepared) + b"\n")
    if not quarantine.exists():
        current = _regular_file_hashes(target)
        if current:
            if current != expected:
                raise ValueError("capture_deletion_binding_invalid")
            os.replace(target, quarantine)
            target.mkdir()
            restrict_directory_acl(target)
    if quarantine.exists():
        remaining = _regular_file_hashes(quarantine)
        if any(expected.get(name) != digest for name, digest in remaining.items()):
            raise ValueError("capture_deletion_binding_invalid")
        for name in sorted(remaining):
            (quarantine / name).unlink()
        quarantine.rmdir()
    if _regular_file_hashes(target):
        raise ValueError("capture_deletion_incomplete")
    finalized = _journal_value(
        operation=operation, binding_sha256=binding_sha256,
        expected_files=expected, status="finalized",
    )
    _write_replace(journal_file, finalized)
    raw = journal_file.read_bytes()
    if raw != _canonical(finalized) + b"\n":
        raise ValueError("capture_deletion_journal_invalid")
    return finalized, _sha256(raw)


def cleanup_terminal_captures(
    *, capture_dir: str | Path, authorization_sha256: str,
) -> dict:
    """Delete any ciphertext retained by a terminal, non-acceptable run."""
    target = Path(capture_dir).resolve()
    expected = _regular_file_hashes(target)
    journal, journal_sha256 = delete_capture_set(
        capture_dir=target,
        journal_path=target.parent / "capture-deletion.journal.json",
        operation="terminal_cleanup",
        binding_sha256=authorization_sha256,
        expected_files=expected,
    )
    receipt = {
        "schema": "query-pilot-terminal-capture-deletion-v1",
        "status": "terminal_cleanup_only",
        "authorization_sha256": authorization_sha256,
        "deleted_capture_count": len(expected),
        "capture_artifact_sha256": sorted(expected.values()),
        "remaining_capture_count": 0,
        "deletion_journal_sha256": journal_sha256,
        "pilot_accepted": False,
        "retry_authorized": False,
    }
    receipt_path = target.parent / "terminal-capture-deletion.json"
    if not receipt_path.exists():
        _exclusive_atomic_bytes(receipt_path, _canonical(receipt) + b"\n")
    elif _strict_object(receipt_path.read_bytes()) != receipt:
        raise ValueError("terminal_capture_receipt_invalid")
    if journal.get("status") != "finalized":
        raise ValueError("terminal_capture_deletion_incomplete")
    return receipt


__all__ = [
    "JOURNAL_SCHEMA", "cleanup_terminal_captures", "delete_capture_set",
]
