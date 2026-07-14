"""Contracts for the CRAG production-pilot control plane."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
import hashlib
import importlib.util
import json
from pathlib import Path

import pytest

from mech_chatbot.evaluation.crag_pilot import (
    PilotConfig,
    assign_pilot_route,
    build_pilot_artifact,
    build_replay_request,
    load_pilot_config,
    refresh_replay_auth,
    should_sample_for_adjudication,
    validate_deployment_contract,
    verify_replay_signature,
)
from mech_chatbot.rag import semantic_cache


pytestmark = pytest.mark.unit


def _config() -> PilotConfig:
    return PilotConfig(
        experiment_id="crag-pilot-2026-07",
        assignment_salt="test-only-salt",
        eligible_department="Technical",
        cohort_sha256="cohort-v1",
        control_url="http://control:8100",
        candidate_url="http://candidate:8100",
        control_deployment_id="control-c155670",
        candidate_deployment_id="candidate-c155670",
        snapshot_fingerprint="snapshot-v1",
    )


def test_stable_canary_assignment_uses_identity_not_query_text():
    config = _config()

    first = assign_pilot_route(
        config, user_id="42", department="Technical", request_id="turn-1"
    )
    second = assign_pilot_route(
        config, user_id="42", department="Technical", request_id="turn-2"
    )
    outside = assign_pilot_route(
        config, user_id="42", department="HR", request_id="turn-3"
    )

    assert first.arm == second.arm
    assert first.deployment_url == second.deployment_url
    assert first.matched_pair_id != second.matched_pair_id
    assert first.actor_hash == second.actor_hash
    assert outside.arm == "control"
    assert outside.eligible is False


def test_replay_targets_other_arm_and_disables_side_effects():
    route = assign_pilot_route(
        _config(), user_id="42", department="Technical", request_id="turn-1"
    )
    payload = {
        "user_id": 42,
        "username": "viewer",
        "user_question": "Quy trình bảo trì?",
        "chat_history": [],
    }

    replay = build_replay_request(route, payload, original_trace_id="rag-a")

    assert replay.target_arm != route.arm
    assert replay.target_url != route.deployment_url
    assert replay.payload == payload
    assert replay.payload is not payload
    assert replay.headers["X-RAG-Pilot-Replay"] == "true"
    assert replay.headers["X-RAG-Pilot-Experiment-ID"] == route.experiment_id
    assert len(replay.headers["X-RAG-Pilot-Replay-Signature"]) == 64
    assert replay.headers["X-RAG-Matched-Pair-ID"] == route.matched_pair_id
    assert replay.headers["X-RAG-Original-Trace-ID"] == "rag-a"
    assert "prompt" not in replay.metadata
    assert "username" not in replay.metadata
    refreshed = refresh_replay_auth(replay)
    assert refreshed.headers["X-RAG-Pilot-Replay-Nonce"] != replay.headers[
        "X-RAG-Pilot-Replay-Nonce"
    ]
    assert verify_replay_signature(
        assignment_salt="test-only-salt",
        experiment_id=route.experiment_id,
        matched_pair_id=route.matched_pair_id,
        assigned_arm=route.arm,
        target_deployment_id=route.opposite_deployment_id,
        original_trace_id="rag-a",
        payload_sha256=replay.headers["X-RAG-Pilot-Payload-SHA256"],
        nonce=replay.headers["X-RAG-Pilot-Replay-Nonce"],
        expires_at=int(replay.headers["X-RAG-Pilot-Replay-Expires"]),
        signature=replay.headers["X-RAG-Pilot-Replay-Signature"],
    ) is True
    assert verify_replay_signature(
        assignment_salt="test-only-salt",
        experiment_id=route.experiment_id,
        matched_pair_id=route.matched_pair_id,
        assigned_arm=route.arm,
        target_deployment_id=route.opposite_deployment_id,
        original_trace_id="rag-a",
        payload_sha256=replay.headers["X-RAG-Pilot-Payload-SHA256"],
        nonce=replay.headers["X-RAG-Pilot-Replay-Nonce"],
        expires_at=int(replay.headers["X-RAG-Pilot-Replay-Expires"]),
        signature=replay.headers["X-RAG-Pilot-Replay-Signature"],
    ) is False
    assert verify_replay_signature(
        assignment_salt="wrong-salt",
        experiment_id=route.experiment_id,
        matched_pair_id=route.matched_pair_id,
        assigned_arm=route.arm,
        target_deployment_id=route.opposite_deployment_id,
        original_trace_id="rag-a",
        payload_sha256=replay.headers["X-RAG-Pilot-Payload-SHA256"],
        nonce="0" * 32,
        expires_at=int(replay.headers["X-RAG-Pilot-Replay-Expires"]),
        signature=replay.headers["X-RAG-Pilot-Replay-Signature"],
    ) is False


def test_adjudication_sampling_is_complete_for_risk_cases_and_stable_for_normal_answers():
    normal = {
        "refusal": False,
        "correction_count": 0,
        "repair_count": 0,
        "access_denied": False,
        "provider_error": False,
    }

    for field, value in (
        ("refusal", True),
        ("correction_count", 1),
        ("repair_count", 1),
        ("access_denied", True),
        ("provider_error", True),
    ):
        risk = {**normal, field: value}
        assert should_sample_for_adjudication("exp", f"pair-{field}", risk) is True

    first = should_sample_for_adjudication("exp", "pair-normal", normal)
    second = should_sample_for_adjudication("exp", "pair-normal", normal)
    assert first == second


def _pilot_inputs(*, pair_count=100, duration_days=8):
    start = datetime(2026, 7, 1, tzinfo=timezone.utc)
    pairs = []
    for index in range(pair_count):
        timestamp = start + timedelta(
            seconds=(duration_days * 24 * 3600 * index / max(1, pair_count - 1))
        )
        pair_id = f"pair-{index:03d}"
        pairs.append({
            "matched_pair_id": pair_id,
            "timestamp": timestamp.isoformat(),
            "assigned_arm": "control" if index % 2 == 0 else "candidate",
            "cohort_sha256": "cohort-v1",
            "adjudicated": True,
            "reviewer_ids": ["reviewer-a", "reviewer-b"],
            "resolved_by": "consensus",
            "adjudication": {
                "schema": "evaluation-adjudication-v1",
                "case_id": pair_id,
                "disagreement": False,
                "resolved_by": "consensus",
                "reviewer_ids": ["reviewer-a", "reviewer-b"],
                "outcome_label": "full_answer",
                "answer_correct": True,
                "citation_correct": True,
                "reason_codes": ["evidence_supported", "evidence_supported"],
            },
            "roles": ["viewer"],
            "sites": ["HQ"],
            "query_type": "technical",
            "control": {
                "wrong_refusal": 1 if index < 4 else 0,
                "wrong_answer": 0,
                "leakage": 0,
                "latency_ms": 100,
                "estimated_cost": 1.0,
                "provider_retries": 0,
                "fallback_count": 0,
                "correction_count": 0,
                "repair_count": 0,
                "claim_valid": 1,
                "claim_total": 1,
                "citation_valid": 1,
                "citation_total": 1,
            },
            "candidate": {
                "wrong_refusal": 1 if index < 2 else 0,
                "wrong_answer": 0,
                "leakage": 0,
                "latency_ms": 120,
                "estimated_cost": 1.2,
                "provider_retries": 0,
                "fallback_count": 0,
                "correction_count": 1 if index == 0 else 0,
                "repair_count": 1 if index == 1 else 0,
                "claim_valid": 1,
                "claim_total": 1,
                "citation_valid": 1,
                "citation_total": 1,
            },
        })
    config = {
        "run_id": "pilot-run-1",
        "experiment_id": "crag-pilot-2026-07",
        "git_sha": "c155670",
        "start_at": start.isoformat(),
        "end_at": (start + timedelta(days=duration_days)).isoformat(),
        "eligible_cohort": {"department": "Technical", "sha256": "cohort-v1"},
        "assignment_version": "hmac-sha256-v1",
        "sampling_version": "sha256-25pct-v1",
        "snapshot_fingerprint": "snapshot-v1",
        "deployments": {
            "control": {
                "id": "control-c155670",
                "flags": {"RAG_CRAG_ENABLED": False, "RAG_CLAIM_REPAIR_ENABLED": False},
            },
            "candidate": {
                "id": "candidate-c155670",
                "flags": {"RAG_CRAG_ENABLED": True, "RAG_CLAIM_REPAIR_ENABLED": True},
            },
        },
        "owners": {
            "rag": "rag-owner",
            "security_qa": "security-owner",
            "operations": "ops-owner",
        },
        "reviewer_signoff": {
            "rag": True,
            "security_qa": True,
            "operations": True,
        },
        "voyage_policy": "immediate_local_fallback_no_retry",
        "deployment_preflight": {
            "schema": "crag-pilot-deployment-preflight-v1",
            "passed": True,
            "git_sha": "c155670",
            "snapshot_fingerprint": "snapshot-v1",
            "deployments": {
                "control": {
                    "id": "control-c155670",
                    "feature_flags": {
                        "RAG_CRAG_ENABLED": False,
                        "RAG_CLAIM_REPAIR_ENABLED": False,
                    },
                },
                "candidate": {
                    "id": "candidate-c155670",
                    "feature_flags": {
                        "RAG_CRAG_ENABLED": True,
                        "RAG_CLAIM_REPAIR_ENABLED": True,
                    },
                },
            },
        },
        "sampling_summary": {
            "risk_eligible": 20,
            "risk_sampled": 20,
            "normal_eligible": 100,
            "normal_sampled": 20,
        },
        "trace_snapshot": {
            "schema": "rag-refusal-snapshot-v1",
            "source": {"git_sha": "c155670", "sha256": "e" * 64},
            "filters": {
                "start": start.isoformat(),
                "end": (start + timedelta(days=duration_days)).isoformat(),
                "execution_contexts": ["production"],
            },
            "parse_errors": 0,
            "system_metrics": {"query_count": pair_count},
            "voyage_rerank": {"call_count": 50},
        },
        "source_artifacts": {
            "assignments_sha256": "a" * 64,
            "pairs_sha256": "b" * 64,
            "windows_sha256": "c" * 64,
            "preflight_sha256": "d" * 64,
            "trace_snapshot_sha256": "f" * 64,
            "trace_sha256": "e" * 64,
        },
    }
    assignments = [
        {
            "matched_pair_id": pair["matched_pair_id"],
            "timestamp": pair["timestamp"],
            "assigned_arm": pair["assigned_arm"],
            "cohort_sha256": pair["cohort_sha256"],
            "department": "Technical",
            "roles": pair["roles"],
            "sites": pair["sites"],
            "query_type": pair["query_type"],
            "refusal": index < 20,
            "access_denied": False,
            "provider_error": False,
            "correction_count": 0,
            "repair_count": 0,
            "sampled_for_adjudication": True,
        }
        for index, pair in enumerate(pairs)
    ]
    windows = [
        {
            "kind": "performance",
            "start_at": start.isoformat(),
            "end_at": (start + timedelta(days=duration_days / 2)).isoformat(),
            "eligible_queries": 50,
            "control_p95_ms": 100,
            "candidate_p95_ms": 120,
            "control_cost": 10,
            "candidate_cost": 12,
            "trace_sha256": "e" * 64,
        },
        {
            "kind": "performance",
            "start_at": (start + timedelta(days=duration_days / 2)).isoformat(),
            "end_at": (start + timedelta(days=duration_days)).isoformat(),
            "eligible_queries": 50,
            "control_p95_ms": 100,
            "candidate_p95_ms": 120,
            "control_cost": 10,
            "candidate_cost": 12,
            "trace_sha256": "e" * 64,
        },
        {
            "kind": "voyage",
            "start_at": start.isoformat(),
            "end_at": (start + timedelta(minutes=5)).isoformat(),
            "voyage_calls": 50,
            "voyage_errors": 0,
            "trace_sha256": "e" * 64,
        },
    ]
    return config, pairs, assignments, windows


def test_pilot_artifact_accepts_qualified_matched_window():
    config, pairs, assignments, windows = _pilot_inputs()

    artifact = build_pilot_artifact(
        config, pairs, assignment_events=assignments, monitoring_windows=windows
    )

    assert artifact["schema"] == "crag-production-pilot-v1"
    assert artifact["decision"] == "accepted"
    assert artifact["passed"] is True
    assert artifact["matched_pair_count"] == 100
    assert artifact["metrics"]["candidate"]["latency_p95_ms"] == 120
    assert artifact["metrics"]["candidate"]["provider_retry_rate"] == 0
    assert artifact["metrics"]["control"]["outcome_confusion"] == {
        "correct_answer": 96,
        "correct_refusal": 0,
        "wrong_refusal": 4,
        "wrong_refusal_type": 0,
        "wrong_answer": 0,
        "leakage": 0,
        "admin_exception": 0,
    }
    assert artifact["checks"]["wrong_refusal_reduced"] is True
    assert artifact["checks"]["claim_precision_at_least_99"] is True
    assert artifact["checks"]["citation_accuracy_at_least_99"] is True
    assert artifact["abort"]["triggered"] is False
    assert artifact["balance"]["query_type"]["technical"] == {
        "control": 50, "candidate": 50
    }
    assert artifact["checks"]["daily_sampling_complete"] is True
    assert artifact["checks"]["sampled_pairs_complete"] is True


def test_pilot_aborts_on_two_bad_windows_or_voyage_error_budget():
    config, pairs, assignments, _ = _pilot_inputs()
    start = datetime(2026, 7, 1, tzinfo=timezone.utc)
    windows = []
    for index in range(2):
        windows.append({
            "kind": "performance",
            "start_at": (start + timedelta(hours=index)).isoformat(),
            "end_at": (start + timedelta(hours=index, minutes=30)).isoformat(),
            "eligible_queries": 50,
            "control_p95_ms": 100,
            "candidate_p95_ms": 130,
            "control_cost": 10,
            "candidate_cost": 16,
            "trace_sha256": str(index + 1) * 64,
        })
    windows.append({
        "kind": "voyage",
        "start_at": start.isoformat(),
        "end_at": (start + timedelta(minutes=5)).isoformat(),
        "voyage_calls": 50,
        "voyage_errors": 3,
        "trace_sha256": "3" * 64,
    })

    artifact = build_pilot_artifact(
        config, pairs, assignment_events=assignments, monitoring_windows=windows
    )

    assert artifact["decision"] == "aborted"
    assert artifact["passed"] is False
    assert set(artifact["abort"]["reasons"]) == {
        "two_consecutive_latency_or_cost_windows",
        "voyage_rerank_error_rate",
    }


def test_pilot_is_inconclusive_after_fourteen_days_without_enough_pairs():
    config, pairs, assignments, windows = _pilot_inputs(pair_count=99, duration_days=14)

    artifact = build_pilot_artifact(
        config, pairs, assignment_events=assignments, monitoring_windows=windows
    )

    assert artifact["decision"] == "inconclusive"
    assert artifact["passed"] is False
    assert artifact["checks"]["minimum_matched_pairs"] is False


def test_pilot_artifact_rejects_raw_prompt_or_credentials():
    config, pairs, assignments, windows = _pilot_inputs()
    pairs[0]["raw_prompt"] = "secret question"

    with pytest.raises(ValueError, match="forbidden raw field"):
        build_pilot_artifact(
            config, pairs, assignment_events=assignments, monitoring_windows=windows
        )


def test_pilot_artifact_rejects_missing_deployment_preflight():
    config, pairs, assignments, windows = _pilot_inputs()
    config.pop("deployment_preflight")

    artifact = build_pilot_artifact(
        config, pairs, assignment_events=assignments, monitoring_windows=windows
    )

    assert artifact["decision"] == "rejected"
    assert artifact["checks"]["deployment_preflight_passed"] is False


def test_pilot_config_is_disabled_by_default_and_fails_closed_when_incomplete():
    assert load_pilot_config({}) is None

    with pytest.raises(ValueError, match="CRAG_PILOT_ASSIGNMENT_SALT"):
        load_pilot_config({
            "CRAG_PILOT_ENABLED": "true",
            "CRAG_PILOT_EXPERIMENT_ID": "exp-1",
        })


def test_pilot_config_requires_isolated_deployments_and_pinned_snapshot():
    env = {
        "CRAG_PILOT_ENABLED": "true",
        "CRAG_PILOT_EXPERIMENT_ID": "exp-1",
        "CRAG_PILOT_ASSIGNMENT_SALT": "secret-ref-value",
        "CRAG_PILOT_DEPARTMENT": "Technical",
        "CRAG_PILOT_COHORT_SHA256": "cohort-v1",
        "CRAG_PILOT_CONTROL_URL": "http://control:8100",
        "CRAG_PILOT_CANDIDATE_URL": "http://candidate:8100",
        "CRAG_PILOT_CONTROL_DEPLOYMENT_ID": "control-1",
        "CRAG_PILOT_CANDIDATE_DEPLOYMENT_ID": "candidate-1",
        "CRAG_PILOT_SNAPSHOT_FINGERPRINT": "snapshot-v1",
    }

    config = load_pilot_config(env)

    assert config is not None
    assert config.control_url == "http://control:8100"
    assert config.candidate_url == "http://candidate:8100"


def test_replay_context_disables_semantic_cache_without_mutating_environment(monkeypatch):
    monkeypatch.setenv("SEMANTIC_CACHE_ENABLED", "true")

    assert semantic_cache.enabled() is True
    with semantic_cache.replay_cache_disabled(True):
        assert semantic_cache.enabled() is False
    assert semantic_cache.enabled() is True


def test_pilot_gate_cli_writes_isolated_json_and_markdown_artifacts(tmp_path):
    script = Path("scripts/eval/crag_pilot_gate.py")
    spec = importlib.util.spec_from_file_location("crag_pilot_gate", script)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader
    spec.loader.exec_module(module)
    config, pairs, assignments, windows = _pilot_inputs()
    config_path = tmp_path / "config.json"
    pairs_path = tmp_path / "pairs.jsonl"
    assignments_path = tmp_path / "assignments.jsonl"
    windows_path = tmp_path / "windows.jsonl"
    preflight_path = tmp_path / "preflight.json"
    raw_trace_path = tmp_path / "rag_trace.jsonl"
    trace_snapshot_path = tmp_path / "trace-snapshot.json"
    config_path.write_text(json.dumps(config), encoding="utf-8")
    pairs_path.write_text(
        "\n".join(json.dumps(pair) for pair in pairs) + "\n", encoding="utf-8"
    )
    assignments_path.write_text(
        "\n".join(json.dumps(row) for row in assignments) + "\n", encoding="utf-8"
    )
    raw_trace_path.write_text("{}\n", encoding="utf-8")
    raw_trace_sha256 = hashlib.sha256(raw_trace_path.read_bytes()).hexdigest()
    for window in windows:
        window["trace_sha256"] = raw_trace_sha256
    windows_path.write_text(
        "\n".join(json.dumps(row) for row in windows) + "\n", encoding="utf-8"
    )
    preflight_path.write_text(
        json.dumps(config["deployment_preflight"]), encoding="utf-8"
    )
    config["trace_snapshot"]["source"].update({
        "path": str(raw_trace_path.resolve()),
        "sha256": raw_trace_sha256,
    })
    trace_snapshot_path.write_text(
        json.dumps(config["trace_snapshot"]), encoding="utf-8"
    )
    output = tmp_path / "report"

    exit_code = module.main([
        "--config", str(config_path),
        "--pairs", str(pairs_path),
        "--assignments", str(assignments_path),
        "--windows", str(windows_path),
        "--preflight", str(preflight_path),
        "--trace-snapshot", str(trace_snapshot_path),
        "--output-dir", str(output),
    ])

    assert exit_code == 0
    assert json.loads((output / "pilot.json").read_text(encoding="utf-8"))[
        "decision"
    ] == "accepted"
    assert "Decision: `accepted`" in (output / "pilot.md").read_text(
        encoding="utf-8"
    )


def test_deployment_preflight_requires_same_commit_snapshot_and_opposite_flags():
    config = {
        "git_sha": "c155670",
        "snapshot_fingerprint": "snapshot-v1",
        "deployments": {
            "control": {"id": "control-1"},
            "candidate": {"id": "candidate-1"},
        },
    }
    control = {
        "status": "ok", "deployment_id": "control-1", "git_sha": "c155670",
        "snapshot_fingerprint": "snapshot-v1",
        "feature_flags": {
            "RAG_CRAG_ENABLED": False, "RAG_CLAIM_REPAIR_ENABLED": False,
        },
    }
    candidate = {
        "status": "ok", "deployment_id": "candidate-1", "git_sha": "c155670",
        "snapshot_fingerprint": "snapshot-v1",
        "feature_flags": {
            "RAG_CRAG_ENABLED": True, "RAG_CLAIM_REPAIR_ENABLED": True,
        },
    }

    passed = validate_deployment_contract(config, control, candidate)
    candidate["snapshot_fingerprint"] = "different"
    failed = validate_deployment_contract(config, control, candidate)

    assert passed["schema"] == "crag-pilot-deployment-preflight-v1"
    assert passed["passed"] is True
    assert failed["passed"] is False


def test_confusion_is_canonical_for_safety_gate_and_conflicts_fail_closed():
    config, pairs, assignments, windows = _pilot_inputs()
    pairs[0]["candidate"].pop("leakage")
    pairs[0]["candidate"]["outcome_confusion"] = {
        "correct_answer": 0,
        "correct_refusal": 0,
        "wrong_refusal": 1,
        "wrong_refusal_type": 0,
        "wrong_answer": 0,
        "leakage": 1,
        "admin_exception": 0,
    }

    artifact = build_pilot_artifact(
        config, pairs, assignment_events=assignments, monitoring_windows=windows
    )
    assert artifact["decision"] == "aborted"
    assert artifact["metrics"]["candidate"]["leakage"] == 1

    pairs[0]["candidate"]["leakage"] = 0
    with pytest.raises(ValueError, match="conflicting outcome confusion"):
        build_pilot_artifact(
            config, pairs, assignment_events=assignments, monitoring_windows=windows
        )


def test_missing_monitoring_or_daily_assignment_evidence_cannot_pass():
    config, pairs, assignments, windows = _pilot_inputs()

    missing_windows = build_pilot_artifact(
        config, pairs, assignment_events=assignments, monitoring_windows=[]
    )
    missing_assignments = build_pilot_artifact(
        config, pairs, assignment_events=[], monitoring_windows=windows
    )

    assert missing_windows["passed"] is False
    assert missing_windows["checks"]["monitoring_evidence_complete"] is False
    assert missing_assignments["passed"] is False
    assert missing_assignments["checks"]["daily_sampling_complete"] is False


def test_assignment_evidence_must_be_unique_and_match_pair_arm_and_cohort():
    config, pairs, assignments, windows = _pilot_inputs()
    assignments[0]["matched_pair_id"] = assignments[1]["matched_pair_id"]
    assignments[2]["assigned_arm"] = (
        "candidate" if pairs[2]["assigned_arm"] == "control" else "control"
    )
    assignments[3]["department"] = "HR"

    artifact = build_pilot_artifact(
        config, pairs, assignment_events=assignments, monitoring_windows=windows
    )

    assert artifact["checks"]["assignment_events_unique"] is False
    assert artifact["checks"]["pair_assignment_consistent"] is False
    assert artifact["checks"]["cohort_immutable"] is False
    assert artifact["passed"] is False


def test_pilot_metrics_reject_negative_nonfinite_or_invalid_ratios():
    config, pairs, assignments, windows = _pilot_inputs()
    pairs[0]["candidate"]["estimated_cost"] = -1
    with pytest.raises(ValueError, match="finite and non-negative"):
        build_pilot_artifact(
            config, pairs, assignment_events=assignments, monitoring_windows=windows
        )
    pairs[0]["candidate"]["estimated_cost"] = 1
    pairs[0]["candidate"]["latency_ms"] = float("nan")
    with pytest.raises(ValueError, match="finite and non-negative"):
        build_pilot_artifact(
            config, pairs, assignment_events=assignments, monitoring_windows=windows
        )
    pairs[0]["candidate"]["latency_ms"] = 1
    pairs[0]["candidate"]["claim_valid"] = 2
    with pytest.raises(ValueError, match="claim_valid cannot exceed claim_total"):
        build_pilot_artifact(
            config, pairs, assignment_events=assignments, monitoring_windows=windows
        )


def test_adjudication_reviewer_ids_are_nonempty_and_normalized():
    config, pairs, assignments, windows = _pilot_inputs()
    pairs[0]["adjudication"]["reviewer_ids"] = ["reviewer-a", " reviewer-a "]

    artifact = build_pilot_artifact(
        config, pairs, assignment_events=assignments, monitoring_windows=windows
    )

    assert artifact["checks"]["all_pairs_adjudicated"] is False
    assert artifact["passed"] is False


def test_voyage_abort_uses_independent_completed_fifty_call_window():
    config, pairs, assignments, windows = _pilot_inputs()
    windows[-1].update({"voyage_errors": 3, "eligible_queries": 3})

    artifact = build_pilot_artifact(
        config, pairs, assignment_events=assignments, monitoring_windows=windows
    )

    assert artifact["decision"] == "aborted"
    assert "voyage_rerank_error_rate" in artifact["abort"]["reasons"]


def test_deployment_preflight_is_bound_to_ids_and_flag_state():
    config, pairs, assignments, windows = _pilot_inputs()
    config["deployment_preflight"]["deployments"] = {
        "control": {
            "id": "other-control",
            "feature_flags": {
                "RAG_CRAG_ENABLED": False,
                "RAG_CLAIM_REPAIR_ENABLED": False,
            },
        },
        "candidate": {
            "id": "other-candidate",
            "feature_flags": {
                "RAG_CRAG_ENABLED": True,
                "RAG_CLAIM_REPAIR_ENABLED": True,
            },
        },
    }

    artifact = build_pilot_artifact(
        config, pairs, assignment_events=assignments, monitoring_windows=windows
    )
    assert artifact["checks"]["deployment_preflight_passed"] is False
    assert artifact["passed"] is False
