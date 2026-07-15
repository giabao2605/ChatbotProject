"""Shared artifact integrity checks for integrated evaluation commands."""

from __future__ import annotations

import hashlib
import json
import subprocess
from pathlib import Path


def assert_clean_status(status: str) -> None:
    if status.strip():
        raise RuntimeError("integrated evidence requires a clean git worktree")


def assert_clean_worktree(root: Path) -> None:
    status = subprocess.check_output(
        ["git", "status", "--porcelain", "--untracked-files=all"],
        cwd=root,
        text=True,
    )
    assert_clean_status(status)


def read_json_artifact(path: Path) -> tuple[dict, str]:
    raw = path.read_bytes()
    return json.loads(raw.decode("utf-8")), hashlib.sha256(raw).hexdigest()


def require_artifact_reference(reference: dict, *, root: Path) -> dict:
    path = Path(str(reference.get("path") or ""))
    if not path.is_absolute():
        path = root / path
    artifact, digest = read_json_artifact(path)
    if digest != str(reference.get("sha256") or "").lower():
        raise ValueError(f"artifact hash mismatch: {path}")
    if artifact.get("schema") != reference.get("schema"):
        raise ValueError(f"artifact schema mismatch: {path}")
    return artifact
