"""Executable contracts for the rollout principles in roadmap section 2.1."""

from __future__ import annotations

import copy
import hashlib
import importlib.util
import json
from pathlib import Path

import pytest

from mech_chatbot.evaluation.rollout_guardrails import (
    evaluate_rollout_pair,
    evaluate_rollout_series,
)


pytestmark = pytest.mark.unit


def _artifact_reference(tmp_path, name, schema, **payload):
    path = tmp_path / f"{name}.json"
    path.write_text(json.dumps({"schema": schema, **payload}), encoding="utf-8")
    return {
        "artifact_path": str(path),
        "artifact_schema": schema,
        "artifact_sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
    }


def _context(tmp_path, *, arm="baseline", run_id="run-1", **overrides):
    context = {
        "git_sha": "abc123",
        "manifest_sha256": "manifest-v1",
        "snapshot_fingerprint": "snapshot-v1",
        "provider_configuration_sha256": "provider-v1",
        "concurrency": 1,
        "governance_scope_sha256": "governance-v1",
        "collection": "MechChatbot_CRAG_Eval_v1",
        "started_at": (
            "2026-07-14T00:00:00Z" if arm == "baseline"
            else "2026-07-14T00:02:00Z"
        ),
        "completed_at": (
            "2026-07-14T00:01:00Z" if arm == "baseline"
            else "2026-07-14T00:03:00Z"
        ),
    }
    context.update(overrides)
    evaluation = _artifact_reference(
        tmp_path, f"{run_id}-{arm}-eval", "rag-labeled-eval-v4",
        run_id=run_id,
        run_label=arm,
        git_sha=context["git_sha"],
        manifest_sha256s=[context.get("manifest_sha256")],
        snapshot_fingerprint=context.get("snapshot_fingerprint"),
        provider_configuration_sha256=context.get("provider_configuration_sha256"),
        governance_scope_sha256=context.get("governance_scope_sha256"),
        benchmark_concurrency=context.get("concurrency"),
        collection=context.get("collection"),
    )
    trace = _artifact_reference(
        tmp_path, f"{run_id}-{arm}-trace", "rag-refusal-snapshot-v1",
        run_id=run_id,
        source={"git_sha": context["git_sha"]},
        filters={
            "start": context["started_at"], "end": context["completed_at"],
            "execution_contexts": ["evaluation"],
        },
    )
    context.update({
        **evaluation,
        "trace_path": trace["artifact_path"],
        "trace_schema": trace["artifact_schema"],
        "trace_sha256": trace["artifact_sha256"],
    })
    return context


def _pair(tmp_path, **overrides):
    run_id = overrides.pop("run_id", "run-1")
    stage = overrides.pop("stage", "crag")
    gate_checks = overrides.pop("gate_checks", {
        "wrong_answer_not_increased": True, "leakage_zero": True,
    })
    gate_schema = (
        "crag-rollout-gate-v1"
        if stage == "crag"
        else "retrieval-intelligence-gate-v1"
    )
    baseline = overrides.pop("baseline", None)
    if baseline is None:
        baseline = _context(tmp_path, arm="baseline", run_id=run_id)
    candidate = overrides.pop("candidate", None)
    if candidate is None:
        candidate = _context(tmp_path, arm="candidate", run_id=run_id)
    gate = _artifact_reference(
        tmp_path, f"{run_id}-gate", gate_schema,
        stage=stage if stage != "crag" else None,
        passed=all(gate_checks.values()), checks=gate_checks,
        inputs={
            "baseline_eval_sha256": baseline["artifact_sha256"],
            "candidate_eval_sha256": candidate["artifact_sha256"],
            "baseline_trace_sha256": baseline["trace_sha256"],
            "candidate_trace_sha256": candidate["trace_sha256"],
        },
    )
    rollback = _artifact_reference(
        tmp_path, f"{run_id}-rollback", "rollback-test-evidence-v1",
        git_sha="abc123", passed=True,
        flags=["RAG_CRAG_ENABLED", "RAG_CLAIM_REPAIR_ENABLED"],
    )
    pair = {
        "schema": "rollout-evidence-pair-v1",
        "run_id": run_id,
        "stage": stage,
        "evidence_type": "staging_evaluation",
        "baseline": baseline,
        "candidate": candidate,
        "data_plane": {
            "production_collection": "TaiLieuKyThuat_v2",
            "mutation_mode": "staging",
        },
        "gate": gate,
        "rollback": {
            "flags": ["RAG_CRAG_ENABLED", "RAG_CLAIM_REPAIR_ENABLED"],
            "defaults_disabled": True,
            **rollback,
        },
    }
    pair.update(overrides)
    return pair


def test_rollout_pair_requires_identical_baseline_and_candidate_conditions(tmp_path):
    candidate = _context(
        tmp_path, arm="candidate", provider_configuration_sha256="provider-v2"
    )

    report = evaluate_rollout_pair(_pair(tmp_path, candidate=candidate))

    assert report["passed"] is False
    assert report["checks"]["benchmark_conditions_match"] is False
    assert report["mismatches"] == ["provider_configuration_sha256"]


def test_rollout_pair_rejects_equally_missing_benchmark_identity(tmp_path):
    incomplete = _context(tmp_path)
    incomplete.pop("snapshot_fingerprint")

    report = evaluate_rollout_pair(
        _pair(tmp_path, baseline=dict(incomplete), candidate=dict(incomplete))
    )

    assert report["checks"]["benchmark_conditions_match"] is True
    assert report["checks"]["benchmark_conditions_complete"] is False
    assert report["passed"] is False


def test_rollout_pair_rejects_unit_evidence_and_production_collection_mutation(tmp_path):
    production = "TaiLieuKyThuat_v2"
    context = _context(tmp_path, collection=production)

    report = evaluate_rollout_pair(
        _pair(
            tmp_path,
            evidence_type="unit_test",
            baseline=context,
            candidate=context,
            data_plane={
                "production_collection": production,
                "mutation_mode": "in_place",
            },
        )
    )

    assert report["checks"]["live_artifact"] is False
    assert report["checks"]["production_collection_not_mutated"] is False
    assert report["passed"] is False


def test_rollout_pair_fails_closed_on_safety_or_rollback_gaps(tmp_path):
    pair = _pair(
        tmp_path,
        gate_checks={"wrong_answer_not_increased": True, "leakage_zero": False},
    )
    pair["rollback"]["artifact_sha256"] = "invented"

    report = evaluate_rollout_pair(pair)

    assert report["checks"]["safety_gate_passed"] is False
    assert report["checks"]["rollback_contract_valid"] is False
    assert report["production_eligible"] is False


def test_rollout_pair_recomputes_eval_trace_and_gate_artifact_hashes(tmp_path):
    pair = _pair(tmp_path)
    pair["baseline"]["artifact_sha256"] = "invented"
    pair["gate"]["artifact_sha256"] = "invented"

    report = evaluate_rollout_pair(pair)

    assert report["checks"]["evidence_artifacts_verified"] is False
    assert report["checks"]["gate_artifact_present"] is False
    assert report["production_eligible"] is False


def test_rollout_pair_binds_gate_inputs_and_artifact_context(tmp_path):
    pair = _pair(tmp_path, run_id="run-1")
    other = _pair(tmp_path, run_id="run-2")
    pair["gate"] = other["gate"]

    gate_report = evaluate_rollout_pair(pair)

    assert gate_report["checks"]["gate_inputs_bound"] is False
    assert gate_report["production_eligible"] is False

    pair = _pair(tmp_path, run_id="run-3")
    eval_path = Path(pair["baseline"]["artifact_path"])
    artifact = json.loads(eval_path.read_text(encoding="utf-8"))
    artifact["git_sha"] = "different"
    eval_path.write_text(json.dumps(artifact), encoding="utf-8")
    pair["baseline"]["artifact_sha256"] = hashlib.sha256(
        eval_path.read_bytes()
    ).hexdigest()
    gate_path = Path(pair["gate"]["artifact_path"])
    gate_artifact = json.loads(gate_path.read_text(encoding="utf-8"))
    gate_artifact["inputs"]["baseline_eval_sha256"] = pair["baseline"][
        "artifact_sha256"
    ]
    gate_path.write_text(json.dumps(gate_artifact), encoding="utf-8")
    pair["gate"]["artifact_sha256"] = hashlib.sha256(
        gate_path.read_bytes()
    ).hexdigest()

    context_report = evaluate_rollout_pair(pair)

    assert context_report["checks"]["artifact_context_valid"] is False
    assert context_report["production_eligible"] is False


def test_rollout_series_requires_three_comparable_live_pairs(tmp_path):
    pairs = [
        _pair(tmp_path, run_id="run-1"),
        _pair(tmp_path, run_id="run-2"),
        _pair(tmp_path, run_id="run-3"),
    ]

    foundation_artifact = {
        "schema": "evaluation-adjudication-artifact-v1",
        "cases": 1,
    }
    foundation_path = Path("docs/examples/evaluation-adjudication-v1.json")
    foundation_sha = hashlib.sha256(foundation_path.read_bytes()).hexdigest()
    foundation = {
        "evaluation_foundation": {
            "decision": "completed",
            "artifact": str(foundation_path),
            "artifact_schema": foundation_artifact["schema"],
            "artifact_sha256": foundation_sha,
        }
    }
    report = evaluate_rollout_series("crag", pairs, prior_decisions=foundation)

    assert report["checks"]["minimum_independent_pairs"] is True
    assert report["checks"]["series_conditions_match"] is True
    assert report["production_eligible"] is True

    too_small = evaluate_rollout_series(
        "crag", pairs[:2], prior_decisions=foundation
    )
    assert too_small["checks"]["minimum_independent_pairs"] is False
    assert too_small["production_eligible"] is False


def test_rollout_series_requires_completed_prior_milestone_decisions(tmp_path):
    pairs = [
        _pair(tmp_path, run_id=f"math-{index}", stage="grounded_math")
        for index in range(1, 4)
    ]

    blocked = evaluate_rollout_series("grounded_math", pairs)
    artifact = tmp_path / "crag-decision.json"
    artifact.write_text(
        json.dumps({"schema": "crag-decision-v1", "passed": True}),
        encoding="utf-8",
    )
    completed = evaluate_rollout_series(
        "grounded_math",
        pairs,
        prior_decisions={
            "crag": {
                "decision": "accepted",
                "artifact": str(artifact),
                "artifact_schema": "crag-decision-v1",
                "artifact_sha256": hashlib.sha256(artifact.read_bytes()).hexdigest(),
            }
        },
    )

    assert blocked["checks"]["prior_milestones_completed"] is False
    assert completed["checks"]["prior_milestones_completed"] is True
    assert completed["production_eligible"] is True


def test_rollout_series_rejects_copied_pairs_with_renamed_run_ids(tmp_path):
    source = _pair(tmp_path, run_id="run-1")
    pairs = [source]
    for run_id in ("run-2", "run-3"):
        copied = copy.deepcopy(source)
        copied["run_id"] = run_id
        pairs.append(copied)
    artifact = tmp_path / "foundation.json"
    artifact.write_text(
        json.dumps({"schema": "evaluation-foundation-completion-v1"}),
        encoding="utf-8",
    )
    decisions = {
        "evaluation_foundation": {
            "decision": "completed",
            "artifact": str(artifact),
            "artifact_schema": "evaluation-foundation-completion-v1",
            "artifact_sha256": hashlib.sha256(artifact.read_bytes()).hexdigest(),
        }
    }

    report = evaluate_rollout_series("crag", pairs, prior_decisions=decisions)

    assert report["checks"]["independent_pair_evidence"] is False
    assert report["production_eligible"] is False


def test_rollout_series_verifies_decision_artifact_hash_and_schema(tmp_path):
    artifact = tmp_path / "foundation.json"
    artifact.write_text(json.dumps({"schema": "actual-v1"}), encoding="utf-8")
    decisions = {
        "evaluation_foundation": {
            "decision": "completed",
            "artifact": str(artifact),
            "artifact_schema": "claimed-v1",
            "artifact_sha256": hashlib.sha256(artifact.read_bytes()).hexdigest(),
        }
    }

    report = evaluate_rollout_series(
        "crag",
        [_pair(tmp_path, run_id=f"run-{index}") for index in range(1, 4)],
        prior_decisions=decisions,
    )

    assert report["checks"]["prior_milestones_completed"] is False
    assert report["production_eligible"] is False


def test_rollout_guardrail_cli_writes_reproducible_series_artifact(tmp_path):
    script = Path("scripts/eval/rollout_guardrails.py")
    spec = importlib.util.spec_from_file_location("rollout_guardrail_cli", script)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader
    spec.loader.exec_module(module)

    pair_paths = []
    for index in range(1, 4):
        path = tmp_path / f"pair-{index}.json"
        path.write_text(
            json.dumps(_pair(tmp_path, run_id=f"run-{index}")), encoding="utf-8"
        )
        pair_paths.append(path)
    decisions = tmp_path / "decisions.json"
    foundation = tmp_path / "foundation.json"
    foundation.write_text(
        json.dumps({"schema": "evaluation-foundation-completion-v1"}),
        encoding="utf-8",
    )
    decisions.write_text(
        json.dumps(
            {
                "evaluation_foundation": {
                    "decision": "completed",
                    "artifact": str(foundation),
                    "artifact_schema": "evaluation-foundation-completion-v1",
                    "artifact_sha256": hashlib.sha256(
                        foundation.read_bytes()
                    ).hexdigest(),
                }
            }
        ),
        encoding="utf-8",
    )
    output = tmp_path / "guardrail.json"
    argv = ["--stage", "crag"]
    for path in pair_paths:
        argv.extend(["--pair", str(path)])
    argv.extend(["--decisions", str(decisions), "--output", str(output)])

    assert module.main(argv) == 0
    report = json.loads(output.read_text(encoding="utf-8"))
    assert report["schema"] == "rollout-guardrail-series-v1"
    assert report["production_eligible"] is True
