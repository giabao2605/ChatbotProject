"""Checkpoint exit-code contracts for the CRAG controlled-demo gate."""

from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest


pytestmark = pytest.mark.unit


def _load_cli():
    script = Path("scripts/eval/crag_pilot_gate.py")
    spec = importlib.util.spec_from_file_location("crag_pilot_gate_checkpoint", script)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _safe_running_artifact(cli) -> dict:
    return {
        "schema": "crag-production-pilot-v1",
        "passed": False,
        "decision": "running",
        "matched_pair_count": 12,
        "duration_days": 1.0,
        "abort": {"triggered": False},
        "checks": {name: True for name in cli.CHECKPOINT_CHECKS},
    }


def test_final_mode_still_requires_a_passed_pilot():
    cli = _load_cli()

    running = _safe_running_artifact(cli)

    assert cli.pilot_exit_code(running, checkpoint=False) == 2
    assert cli.pilot_exit_code(
        {**running, "passed": True, "decision": "accepted"},
        checkpoint=False,
    ) == 0
    checkpoint = cli.build_checkpoint_artifact(running)
    assert checkpoint["decision"] == "running"
    assert cli.pilot_exit_code(
        checkpoint,
        checkpoint=True,
    ) == 0


@pytest.mark.parametrize(
    ("change", "missing_check", "decision"),
    [
        ({}, None, "running"),
        ({"matched_pair_count": 20}, None, "checkpoint_go"),
        ({"duration_days": 3.0}, None, "inconclusive"),
        ({"duration_days": 3.1}, None, "rejected"),
        ({"abort": {"triggered": True}}, None, "aborted"),
        ({"checks": {"deployment_preflight_passed": False}}, None, "rejected"),
        ({}, "snapshot_pinned", "inconclusive"),
    ],
)
def test_checkpoint_mode_writes_a_checkpoint_artifact(
    change,
    missing_check,
    decision,
):
    cli = _load_cli()
    pilot = _safe_running_artifact(cli)
    pilot.update({key: value for key, value in change.items() if key != "checks"})
    if "checks" in change:
        pilot["checks"] = {**pilot["checks"], **change["checks"]}
    if missing_check:
        pilot["checks"].pop(missing_check)

    checkpoint = cli.build_checkpoint_artifact(pilot)

    assert checkpoint["schema"] == "crag-controlled-demo-checkpoint-v1"
    assert checkpoint["source_pilot_decision"] == "running"
    assert checkpoint["decision"] == decision
    assert checkpoint["passed"] is (decision in {"running", "checkpoint_go"})
    assert len(checkpoint["source_pilot_sha256"]) == 64


@pytest.mark.parametrize(
    ("change", "expected"),
    [
        ({}, 0),
        ({"decision": "rejected"}, 2),
        ({"abort": {"triggered": True}}, 2),
        ({"checks": {"deployment_preflight_passed": False}}, 2),
        ({"checks": {"leakage_zero": False}}, 2),
    ],
)
def test_checkpoint_mode_allows_only_safe_running_or_accepted_artifacts(
    change,
    expected,
):
    cli = _load_cli()
    artifact = _safe_running_artifact(cli)
    artifact.update({key: value for key, value in change.items() if key != "checks"})
    if "checks" in change:
        artifact["checks"] = {**artifact["checks"], **change["checks"]}

    checkpoint = cli.build_checkpoint_artifact(artifact)

    assert cli.pilot_exit_code(checkpoint, checkpoint=True) == expected
