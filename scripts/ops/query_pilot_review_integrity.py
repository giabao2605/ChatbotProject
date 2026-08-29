"""Commit and tool bindings for local Query pilot owner review."""

from __future__ import annotations

import hashlib
from pathlib import Path
import subprocess
from typing import Mapping


REVIEW_TOOL_RELATIVE_PATHS = (
    "scripts/ops/query_pilot_capture_lifecycle.py",
    "scripts/ops/query_pilot_review_capture.py",
    "scripts/ops/query_pilot_review_artifacts.py",
    "scripts/ops/query_pilot_review_content.py",
    "scripts/ops/query_pilot_review_integrity.py",
    "scripts/ops/query_pilot_review_pack.py",
    "scripts/ops/query_pilot_review_ui.py",
)


def _git(root: Path, *arguments: str) -> str:
    try:
        result = subprocess.run(
            ["git", *arguments], cwd=root, text=True, capture_output=True,
            check=False,
        )
    except OSError as exc:
        raise ValueError("source_git_identity_unavailable") from exc
    if result.returncode != 0:
        raise ValueError("source_git_identity_unavailable")
    return result.stdout.strip()


def validate_authorized_source(source_root: str | Path, source_commit: str) -> None:
    """Require the exact clean commit used to authorize the review tools."""
    root = Path(source_root).resolve()
    if _git(root, "rev-parse", "HEAD") != source_commit:
        raise ValueError("source_commit_mismatch")
    dirty = _git(
        root, "status", "--porcelain=v1", "--untracked-files=all", "--ignored=no",
    )
    if dirty:
        raise ValueError("source_worktree_must_be_clean")


def review_tool_hashes(source_root: str | Path) -> dict[str, str]:
    """Hash every local executable seam that builds or renders review data."""
    root = Path(source_root).resolve()
    hashes = {}
    for relative in REVIEW_TOOL_RELATIVE_PATHS:
        path = (root / relative).resolve()
        try:
            path.relative_to(root)
            raw = path.read_bytes()
        except (OSError, ValueError) as exc:
            raise ValueError("review_tool_binding_invalid") from exc
        hashes[relative] = hashlib.sha256(raw).hexdigest()
    return hashes


def validate_review_tool_hashes(
    source_root: str | Path, expected: Mapping[str, object],
) -> None:
    """Fail if the executable review surface differs from the frozen pack."""
    if dict(expected) != review_tool_hashes(source_root):
        raise ValueError("review_tool_binding_invalid")


__all__ = [
    "REVIEW_TOOL_RELATIVE_PATHS",
    "review_tool_hashes",
    "validate_authorized_source",
    "validate_review_tool_hashes",
]
