"""Immutable JSON artifact references shared across governance boundaries."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path


def resolve_path(value: object, root: str | Path) -> Path:
    path = Path(str(value or ""))
    return path if path.is_absolute() else Path(root) / path


def read_json_object(path: str | Path) -> tuple[dict, bytes] | tuple[None, None]:
    try:
        raw = Path(path).read_bytes()
        value = json.loads(raw.decode("utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError):
        return None, None
    return (value, raw) if isinstance(value, dict) else (None, None)


def inspect_json_reference(
    reference: object, *, root: str | Path,
) -> tuple[dict, bytes, Path] | None:
    value, raw, path, report = json_reference_report(reference, root=root)
    if not all(report.values()):
        return None
    return value, raw, path


def json_reference_report(
    reference: object, *, root: str | Path,
) -> tuple[dict, bytes | None, Path, dict[str, bool]]:
    if not isinstance(reference, dict):
        path = resolve_path(None, root)
        return {}, None, path, {
            "exists": False, "sha256_matches": False, "schema_matches": False,
        }
    path = resolve_path(reference.get("path"), root)
    value, raw = read_json_object(path)
    if value is None or raw is None:
        return {}, None, path, {
            "exists": False, "sha256_matches": False, "schema_matches": False,
        }
    expected_schema = str(reference.get("schema") or "").strip()
    report = {
        "exists": True,
        "sha256_matches": (
            hashlib.sha256(raw).hexdigest() == str(reference.get("sha256") or "")
        ),
        "schema_matches": (
            not expected_schema or value.get("schema") == expected_schema
        ),
    }
    return value, raw, path, report


def load_json_reference(reference: object, *, root: str | Path) -> dict | None:
    inspected = inspect_json_reference(reference, root=root)
    return inspected[0] if inspected is not None else None


def build_json_reference(
    path: str | Path,
    *,
    root: str | Path,
    expected_schema: str,
) -> dict:
    project_root = Path(root)
    resolved = resolve_path(path, project_root)
    value, raw = read_json_object(resolved)
    if value is None or raw is None or value.get("schema") != expected_schema:
        raise ValueError(f"{resolved} must use schema {expected_schema}")
    try:
        stored_path = str(resolved.resolve().relative_to(project_root.resolve()))
    except ValueError:
        stored_path = str(resolved.resolve())
    return {
        "path": stored_path,
        "sha256": hashlib.sha256(raw).hexdigest(),
        "schema": expected_schema,
    }


__all__ = [
    "build_json_reference",
    "inspect_json_reference",
    "json_reference_report",
    "load_json_reference",
    "read_json_object",
    "resolve_path",
]
