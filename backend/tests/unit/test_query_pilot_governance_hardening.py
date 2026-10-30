"""Focused fail-closed regressions for Query pilot governance inputs."""

from __future__ import annotations

from pathlib import Path

import pytest

from scripts.ops.query_decomposition_pilot import _read_json, _wal_rows


def test_read_json_rejects_duplicate_fields(tmp_path: Path) -> None:
    artifact = tmp_path / "authorization.json"
    artifact.write_text('{"schema":"first","schema":"second"}', encoding="utf-8")

    with pytest.raises(ValueError, match="json_duplicate_field"):
        _read_json(artifact)


def test_wal_rejects_duplicate_fields(tmp_path: Path) -> None:
    wal = tmp_path / "pilot.wal.jsonl"
    wal.write_text('{"card_id":"001","card_id":"002"}\n', encoding="utf-8")

    with pytest.raises(ValueError, match="json_duplicate_field"):
        _wal_rows(wal)
