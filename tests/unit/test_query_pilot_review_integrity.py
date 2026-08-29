"""Source and review-tool integrity checks for Query owner review."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

from scripts.ops import query_pilot_review_integrity as integrity


def _completed(*, stdout: str = "", returncode: int = 0):
    return SimpleNamespace(stdout=stdout, returncode=returncode)


def test_authorized_source_requires_exact_clean_head(tmp_path: Path, monkeypatch):
    calls = []

    def run(command, **kwargs):
        calls.append((command, kwargs))
        if command[-2:] == ["rev-parse", "HEAD"]:
            return _completed(stdout="a" * 40 + "\n")
        return _completed(stdout="")

    monkeypatch.setattr(integrity.subprocess, "run", run)

    assert integrity.validate_authorized_source(tmp_path, "a" * 40) is None
    assert calls[1][0][-3:] == [
        "--porcelain=v1", "--untracked-files=all", "--ignored=no",
    ]

    def dirty(command, **_kwargs):
        if command[-2:] == ["rev-parse", "HEAD"]:
            return _completed(stdout="a" * 40 + "\n")
        return _completed(stdout=" M scripts/ops/query_pilot_review_ui.py\n")

    monkeypatch.setattr(integrity.subprocess, "run", dirty)
    with pytest.raises(ValueError, match="source_worktree_must_be_clean"):
        integrity.validate_authorized_source(tmp_path, "a" * 40)


def test_review_tool_hashes_cover_every_executable_review_seam(
    tmp_path: Path,
):
    assert set(integrity.REVIEW_TOOL_RELATIVE_PATHS) == {
        "scripts/ops/query_pilot_capture_lifecycle.py",
        "scripts/ops/query_pilot_review_capture.py",
        "scripts/ops/query_pilot_review_artifacts.py",
        "scripts/ops/query_pilot_review_content.py",
        "scripts/ops/query_pilot_review_integrity.py",
        "scripts/ops/query_pilot_review_pack.py",
        "scripts/ops/query_pilot_review_ui.py",
    }
    for relative in integrity.REVIEW_TOOL_RELATIVE_PATHS:
        target = tmp_path / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(relative.encode("utf-8"))

    hashes = integrity.review_tool_hashes(tmp_path)

    assert set(hashes) == set(integrity.REVIEW_TOOL_RELATIVE_PATHS)
    assert all(len(value) == 64 for value in hashes.values())
    changed = tmp_path / integrity.REVIEW_TOOL_RELATIVE_PATHS[0]
    changed.write_bytes(b"changed")
    with pytest.raises(ValueError, match="review_tool_binding_invalid"):
        integrity.validate_review_tool_hashes(tmp_path, hashes)
