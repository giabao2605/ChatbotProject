"""CLI contract for explicit CRAG controlled-demo review governance."""

from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest


pytestmark = pytest.mark.unit


def test_crag_demo_authorization_cli_requires_review_mode(tmp_path):
    script = Path("scripts/eval/crag_demo_authorization.py")
    spec = importlib.util.spec_from_file_location(
        "crag_demo_authorization_cli",
        script,
    )
    module = importlib.util.module_from_spec(spec)
    assert spec.loader
    spec.loader.exec_module(module)

    with pytest.raises(SystemExit) as exc_info:
        module.main([
            "--series", str(tmp_path / "series.json"),
            "--provider-smoke", str(tmp_path / "smoke-1.json"),
            "--provider-smoke", str(tmp_path / "smoke-2.json"),
            "--provider-smoke", str(tmp_path / "smoke-3.json"),
            "--output", str(tmp_path / "authorization.json"),
        ])

    assert exc_info.value.code == 2
