"""Immutable JSON artifact references shared across governance boundaries."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path


def resolve_path(value: object, root: str | Path) -> Path:
    path = Path(str(value or ""))
    return path if path.is_absolute() else Path(root) / path


def _stored_path(path: Path, root: str | Path) -> str:
    try:
        return str(path.resolve().relative_to(Path(root).resolve()))
    except ValueError:
        return str(path.resolve())


def read_bytes_with_reference(
    path: str | Path,
    *,
    root: str | Path,
    expected_schema: str | None = None,
    expected_format: str | None = None,
) -> tuple[bytes, dict]:
    resolved = resolve_path(path, root)
    raw = resolved.read_bytes()
    if expected_schema:
        try:
            value = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise ValueError(f"{resolved} must contain JSON") from exc
        if not isinstance(value, dict) or value.get("schema") != expected_schema:
            raise ValueError(f"{resolved} must use schema {expected_schema}")
    reference = {
        "path": _stored_path(resolved, root),
        "sha256": hashlib.sha256(raw).hexdigest(),
    }
    if expected_schema:
        reference["schema"] = expected_schema
    if expected_format:
        reference["format"] = expected_format
    return raw, reference


def load_bytes_reference(
    reference: object,
    *,
    root: str | Path,
    expected_format: str | None = None,
) -> bytes | None:
    if not isinstance(reference, dict):
        return None
    if expected_format and reference.get("format") != expected_format:
        return None
    try:
        raw = resolve_path(reference.get("path"), root).read_bytes()
    except OSError:
        return None
    if hashlib.sha256(raw).hexdigest() != str(reference.get("sha256") or ""):
        return None
    return raw


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
    _, reference = read_bytes_with_reference(
        path,
        root=root,
        expected_schema=expected_schema,
    )
    return reference


__all__ = [
    "build_json_reference",
    "inspect_json_reference",
    "json_reference_report",
    "load_bytes_reference",
    "load_json_reference",
    "read_bytes_with_reference",
    "read_json_object",
    "resolve_path",
]
