"""Offline Query Decomposition pilot contract and gate."""

from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import subprocess

import pytest

from mech_chatbot.governance.query_activation_contract import (
    QUERY_ACTIVATION_AUTHORIZATION,
    runtime_consumption_authorization_status,
)
from scripts.ops.query_decomposition_pilot import (
    PILOT_AUTHORIZATION,
    finalize_pilot_authorization,
    prepare_pilot_launch_packet,
    record_pilot_completion,
)
from scripts.ops.query_decomposition_pilot_gate import build_pilot_gate
from scripts.ops.query_decomposition_pilot_launch import (
    CONSOLIDATED_AUTHORIZATION,
    finalize_consolidated_launch,
    prepare_consolidated_launch,
)


def _write_json(path: Path, value: object) -> str:
    path.parent.mkdir(parents=True, exist_ok=True)
    raw = (json.dumps(value, ensure_ascii=False, indent=2) + "\n").encode()
    path.write_bytes(raw)
    return hashlib.sha256(raw).hexdigest()


def _answered_evidence() -> dict:
    return {
        "route": "query_decomposition",
        "query_result_status": "valid",
        "completion_outcome": "answered",
        "refusal_reason_code": None,
        "refusal_template_passed": False,
        "owner_review_required": False,
        "security_passed": True,
        "citation_structure_passed": True,
        "provenance_passed": True,
        "leakage_detected": False,
        "planner_calls": 0,
        "subquery_count": 2,
        "correction_count": 0,
        "intent_count": 2,
        "intent_coverage_complete": True,
        "deterministic_split_used": True,
        "intent_overflow": False,
        "final_latency_ms": 250,
        "request_deadline_ms": 120000,
        "estimated_cost": 0.0003,
        "provider_retries": 0,
        "final_generations": 1,
    }


@pytest.fixture(autouse=True)
def _accept_synthetic_activation_fixture(monkeypatch):
    """Keep pilot tests focused; the full activation validator has its own suite."""
    monkeypatch.setattr(
        "scripts.ops.query_decomposition_pilot."
        "validate_query_activation_authorization",
        lambda *_args, **_kwargs: True,
    )
    monkeypatch.setattr(
        "mech_chatbot.governance.query_activation_contract."
        "validate_query_activation_authorization",
        lambda *_args, **_kwargs: True,
    )


def _git_root(path: Path) -> str:
    (path / ".gitignore").write_text(".local/\n", encoding="utf-8")
    (path / "source.txt").write_text("query pilot\n", encoding="utf-8")
    subprocess.run(["git", "init", "-q"], cwd=path, check=True)
    subprocess.run(
        ["git", "config", "user.email", "test@example.com"], cwd=path,
        check=True,
    )
    subprocess.run(
        ["git", "config", "user.name", "Test"], cwd=path, check=True,
    )
    subprocess.run(["git", "add", "."], cwd=path, check=True)
    subprocess.run(["git", "commit", "-qm", "source"], cwd=path, check=True)
    return subprocess.check_output(
        ["git", "rev-parse", "HEAD"], cwd=path, text=True,
    ).strip()


def _inputs(tmp_path: Path):
    manifest = tmp_path / "data" / "decomposition.jsonl"
    manifest.parent.mkdir(parents=True)
    rows = [
        {
            "manifest_schema": "rag-eval-manifest-v2",
            "id": f"complex-{index:02d}",
            "question": f"private complex question {index}",
            "evaluation_group": "complex",
            "evaluation_scope": "query_only",
        }
        for index in range(1, 11)
    ] + [
        {
            "manifest_schema": "rag-eval-manifest-v2",
            "id": f"simple-{index:02d}",
            "question": f"private simple question {index}",
            "evaluation_group": "simple",
            "evaluation_scope": "query_only",
        }
        for index in range(1, 4)
    ]
    manifest.write_text(
        "".join(json.dumps(row) + "\n" for row in rows), encoding="utf-8",
    )
    commit = _git_root(tmp_path)
    activation = tmp_path / ".local" / "activation.json"
    activation_sha = _write_json(activation, {
        "schema": "query-controlled-demo-activation-authorization-v1",
        "source_commit": commit,
        "activation_owner": "bao.nguyen",
        "feature_activation_authorized": True,
        "runtime_consumption_authorized": False,
        "runtime_start_authorized": False,
        "provider_traffic_authorized": False,
        "pilot_dispatch_authorized": False,
        "default_rollout_authorized": False,
        "push_authorized": False,
        "merge_authorized": False,
    })
    bundle = tmp_path / ".local" / "bundle.json"
    bundle_sha = _write_json(bundle, {
        "schema": "rag-activation-bundle-v1",
        "scope": "controlled_demo",
        "activation_profile": "selective",
        "source_commit": commit,
        "feature_flags": {"RAG_QUERY_DECOMPOSITION_ENABLED": True},
    })
    finalization = tmp_path / ".local" / "finalization.json"
    _write_json(finalization, {
        "schema": "query-controlled-demo-activation-finalization-v1",
        "source_commit": commit,
        "authorization": {
            "path": str(activation), "sha256": activation_sha,
        },
        "bundle": {"path": str(bundle), "sha256": bundle_sha},
        "runtime_consumption_authorized": False,
        "runtime_start_authorized": False,
        "provider_traffic_authorized": False,
        "pilot_dispatch_authorized": False,
    })
    return commit, manifest, bundle, finalization


def _prepared(tmp_path: Path):
    commit, manifest, bundle, finalization = _inputs(tmp_path)
    output = tmp_path / ".local" / "pilot"
    packet = prepare_pilot_launch_packet(
        source_root=tmp_path,
        source_commit=commit,
        manifest_path=manifest,
        activation_bundle_path=bundle,
        activation_finalization_path=finalization,
        output_dir=output,
        owner="bao.nguyen",
    )
    return commit, output, packet


def test_prepare_freezes_100_metadata_only_slots_over_24_hours(tmp_path):
    commit, output, packet = _prepared(tmp_path)
    template = json.loads(
        (output / "schedule-template.json").read_text(encoding="utf-8")
    )
    draft = json.loads(
        (output / "pilot-authorization-draft.json").read_text(encoding="utf-8")
    )
    runbook = json.loads(
        (output / "operator-runbook.json").read_text(encoding="utf-8")
    )

    assert packet["status"] == "AWAITING_CONSOLIDATED_PILOT_APPROVAL"
    assert template["source_commit"] == commit
    assert template["pilot_contract_version"] == "query-decomposition-24h-100-v1"
    assert len(template["cards"]) == 100
    assert template["cards"][0]["offset_seconds"] == 0
    assert template["cards"][-1]["offset_seconds"] == 86400
    assert {card["case_id"] for card in template["cards"]} == {
        f"complex-{index:02d}" for index in range(1, 11)
    }
    assert all("question" not in card for card in template["cards"])
    assert draft["requested_authorization"] == PILOT_AUTHORIZATION
    assert draft["activation_bundle_sha256"] == packet["bundle_sha256"]
    assert runbook["launch"]["runtime_start_requires_fresh_approval"] is True
    assert runbook["launch"]["provider_traffic_requires_fresh_approval"] is True
    assert runbook["rollback"]["target_profile"] == "all_off"
    assert runbook["mutations"] == {
        "env_file": False,
        "scheduled_task": False,
        "git_remote": False,
    }
    assert packet["operator_runbook"]["sha256"] == hashlib.sha256(
        (output / "operator-runbook.json").read_bytes()
    ).hexdigest()


def test_prepare_rejects_activation_that_fails_full_governance_validation(
    tmp_path, monkeypatch,
):
    commit, manifest, bundle, finalization = _inputs(tmp_path)
    monkeypatch.setattr(
        "scripts.ops.query_decomposition_pilot."
        "validate_query_activation_authorization",
        lambda *_args, **_kwargs: False,
    )

    with pytest.raises(ValueError, match="query_activation_evidence_invalid"):
        prepare_pilot_launch_packet(
            source_root=tmp_path,
            source_commit=commit,
            manifest_path=manifest,
            activation_bundle_path=bundle,
            activation_finalization_path=finalization,
            output_dir=tmp_path / ".local" / "pilot",
            owner="bao.nguyen",
        )


def test_finalize_creates_one_26_hour_authorization_and_absolute_schedule(tmp_path):
    commit, output, _ = _prepared(tmp_path)
    draft_path = output / "pilot-authorization-draft.json"
    draft_sha = hashlib.sha256(draft_path.read_bytes()).hexdigest()
    approval = output / "pilot-approval.json"
    _write_json(approval, {
        "schema": "query-decomposition-pilot-approval-v1",
        "draft_sha256": draft_sha,
        "actor": "bao.nguyen",
        "authorized_at": "2026-08-27T00:00:00Z",
        "expires_at": "2026-08-28T02:00:00Z",
        "authorization": PILOT_AUTHORIZATION,
    })

    authorization, _ = finalize_pilot_authorization(
        draft_path=draft_path,
        approval_path=approval,
        output_dir=output / "authorized",
        now=datetime(2026, 8, 27, 0, 1, tzinfo=timezone.utc),
    )
    schedule = json.loads(
        (output / "authorized" / "schedule.json").read_text(encoding="utf-8")
    )

    assert authorization["source_commit"] == commit
    assert authorization["pilot_dispatch_authorized"] is True
    assert authorization["default_rollout_authorized"] is False
    assert schedule["cards"][0]["scheduled_at"] == "2026-08-27T00:05:00Z"
    assert schedule["cards"][-1]["scheduled_at"] == "2026-08-28T00:05:00Z"
    assert schedule["minimum_runtime_until"] == "2026-08-28T00:05:00Z"


def test_collector_and_gate_require_exactly_once_100_request_contract(tmp_path):
    _, output, _ = _prepared(tmp_path)
    draft = output / "pilot-authorization-draft.json"
    approval = output / "pilot-approval.json"
    _write_json(approval, {
        "schema": "query-decomposition-pilot-approval-v1",
        "draft_sha256": hashlib.sha256(draft.read_bytes()).hexdigest(),
        "actor": "bao.nguyen",
        "authorized_at": "2026-08-27T00:00:00Z",
        "expires_at": "2026-08-28T02:00:00Z",
        "authorization": PILOT_AUTHORIZATION,
    })
    authorization, _ = finalize_pilot_authorization(
        draft_path=draft,
        approval_path=approval,
        output_dir=output / "authorized",
        now=datetime(2026, 8, 27, 0, 1, tzinfo=timezone.utc),
    )
    schedule_path = output / "authorized" / "schedule.json"
    schedule = json.loads(schedule_path.read_text(encoding="utf-8"))
    wal_path = output / "pilot.wal.jsonl"
    for index, card in enumerate(schedule["cards"]):
        evidence = {
            "route": "query_decomposition",
            "query_result_status": "valid",
            "completion_outcome": "answered",
            "refusal_reason_code": None,
            "refusal_template_passed": False,
            "owner_review_required": False,
            "security_passed": True,
            "citation_structure_passed": True,
            "provenance_passed": True,
            "leakage_detected": False,
            "planner_calls": 0,
            "subquery_count": 2,
            "correction_count": 0,
            "intent_count": 2,
            "intent_coverage_complete": True,
            "deterministic_split_used": True,
            "intent_overflow": False,
            "final_latency_ms": 250,
            "request_deadline_ms": 120000,
            "estimated_cost": 0.0003,
            "provider_retries": 0,
            "final_generations": 1,
        }
        if index == 0:
            evidence = {
                **evidence,
                "query_result_status": "safe_refusal",
                "completion_outcome": "refused",
                "refusal_reason_code": "evidence_gate",
                "refusal_template_passed": True,
                "owner_review_required": True,
                "citation_structure_passed": False,
                "provenance_passed": False,
                "final_generations": 0,
            }
        record_pilot_completion(
            schedule_path=schedule_path,
            authorization_path=output / "authorized" / "pilot-authorization.json",
            wal_path=wal_path,
            card_id=card["card_id"],
            attempted_at=card["scheduled_at"],
            completed_at=(
                "2026-08-28T00:05:01Z" if index == 99
                else card["scheduled_at"]
            ),
            trace_id=f"private-trace-{index}",
            runtime_identity_sha256="a" * 64,
            evidence=evidence,
        )

    gate = build_pilot_gate(
        schedule_path=schedule_path,
        authorization_path=output / "authorized" / "pilot-authorization.json",
        wal_path=wal_path,
        runtime_identity_sha256="a" * 64,
    )

    assert gate["eligible_request_count"] == 100
    assert gate["automated_gate_passed"] is True
    assert gate["pilot_accepted"] is False
    assert gate["reason"] == "human_review_pending"
    assert "private-trace" not in wal_path.read_text(encoding="utf-8")
    original_wal = wal_path.read_text(encoding="utf-8")
    caught_up = [json.loads(line) for line in original_wal.splitlines()]
    catch_up_time = schedule["cards"][-1]["scheduled_at"]
    for row in caught_up[1:]:
        row["attempted_at"] = catch_up_time
        row["completed_at"] = catch_up_time
    caught_up[-1]["completed_at"] = "2026-08-28T00:05:01Z"
    wal_path.write_text(
        "".join(json.dumps(row) + "\n" for row in caught_up),
        encoding="utf-8",
    )
    catch_up_gate = build_pilot_gate(
        schedule_path=schedule_path,
        authorization_path=output / "authorized" / "pilot-authorization.json",
        wal_path=wal_path,
        runtime_identity_sha256="a" * 64,
    )
    assert catch_up_gate["automated_gate_passed"] is False
    assert catch_up_gate["checks"]["frozen_schedule_cadence"] is False
    wal_path.write_text(original_wal, encoding="utf-8")

    review_path = output / "review-result.json"
    _write_json(review_path, {
        "schema": "query-decomposition-pilot-review-result-v1",
        "source_commit": authorization["source_commit"],
        "schedule_sha256": hashlib.sha256(schedule_path.read_bytes()).hexdigest(),
        "reviewer": authorization["actor"],
        "accepted_trace_sha256": [f"{index:064x}" for index in range(20)],
        "all_accepted": True,
    })
    fake_review_gate = build_pilot_gate(
        schedule_path=schedule_path,
        authorization_path=output / "authorized" / "pilot-authorization.json",
        wal_path=wal_path,
        runtime_identity_sha256="a" * 64,
        review_result_path=review_path,
    )
    assert fake_review_gate["human_review_passed"] is False
    forged_wal = [json.loads(line) for line in original_wal.splitlines()]
    forged_wal[0]["trace_id_sha256"] = "z" * 64
    wal_path.write_text(
        "".join(json.dumps(row) + "\n" for row in forged_wal),
        encoding="utf-8",
    )
    forged_gate = build_pilot_gate(
        schedule_path=schedule_path,
        authorization_path=output / "authorized" / "pilot-authorization.json",
        wal_path=wal_path,
        runtime_identity_sha256="a" * 64,
    )
    assert forged_gate["automated_gate_passed"] is False
    wal_path.write_text(original_wal, encoding="utf-8")
    wal_rows = [json.loads(line) for line in original_wal.splitlines()]
    _write_json(review_path, {
        "schema": "query-decomposition-pilot-review-result-v1",
        "source_commit": authorization["source_commit"],
        "schedule_sha256": hashlib.sha256(schedule_path.read_bytes()).hexdigest(),
        "reviewer": authorization["actor"],
        "accepted_trace_sha256": [
            row["trace_id_sha256"] for row in wal_rows[:20]
        ],
        "all_accepted": True,
    })
    accepted_gate = build_pilot_gate(
        schedule_path=schedule_path,
        authorization_path=output / "authorized" / "pilot-authorization.json",
        wal_path=wal_path,
        runtime_identity_sha256="a" * 64,
        review_result_path=review_path,
    )
    assert accepted_gate["pilot_accepted"] is True
    with pytest.raises(ValueError, match="card_already_recorded"):
        record_pilot_completion(
            schedule_path=schedule_path,
            authorization_path=output / "authorized" / "pilot-authorization.json",
            wal_path=wal_path,
            card_id=schedule["cards"][0]["card_id"],
            attempted_at=schedule["cards"][0]["scheduled_at"],
            completed_at=schedule["cards"][0]["scheduled_at"],
            trace_id="retry",
            runtime_identity_sha256="a" * 64,
            evidence={},
        )


def test_gate_returns_failed_artifact_for_malformed_wal_timestamp(tmp_path):
    _, output, _ = _prepared(tmp_path)
    draft = output / "pilot-authorization-draft.json"
    approval = output / "pilot-approval.json"
    _write_json(approval, {
        "schema": "query-decomposition-pilot-approval-v1",
        "draft_sha256": hashlib.sha256(draft.read_bytes()).hexdigest(),
        "actor": "bao.nguyen",
        "authorized_at": "2026-08-27T00:00:00Z",
        "expires_at": "2026-08-28T02:00:00Z",
        "authorization": PILOT_AUTHORIZATION,
    })
    finalize_pilot_authorization(
        draft_path=draft,
        approval_path=approval,
        output_dir=output / "authorized",
        now=datetime(2026, 8, 27, 0, 1, tzinfo=timezone.utc),
    )
    wal = output / "pilot.wal.jsonl"
    wal.write_text(json.dumps({
        "schema": "query-decomposition-pilot-wal-v1",
        "attempted_at": "not-a-timestamp",
    }) + "\n", encoding="utf-8")

    gate = build_pilot_gate(
        schedule_path=output / "authorized" / "schedule.json",
        authorization_path=(
            output / "authorized" / "pilot-authorization.json"
        ),
        wal_path=wal,
        runtime_identity_sha256="a" * 64,
    )

    assert gate["automated_gate_passed"] is False
    assert gate["reason"] == "automated_gate_failed"


def test_collector_rejects_authorization_boundary_drift(tmp_path):
    _, output, _ = _prepared(tmp_path)
    draft = output / "pilot-authorization-draft.json"
    approval = output / "pilot-approval.json"
    _write_json(approval, {
        "schema": "query-decomposition-pilot-approval-v1",
        "draft_sha256": hashlib.sha256(draft.read_bytes()).hexdigest(),
        "actor": "bao.nguyen",
        "authorized_at": "2026-08-27T00:00:00Z",
        "expires_at": "2026-08-28T02:00:00Z",
        "authorization": PILOT_AUTHORIZATION,
    })
    finalize_pilot_authorization(
        draft_path=draft,
        approval_path=approval,
        output_dir=output / "authorized",
        now=datetime(2026, 8, 27, 0, 1, tzinfo=timezone.utc),
    )
    authorization_path = output / "authorized" / "pilot-authorization.json"
    authorization = json.loads(authorization_path.read_text(encoding="utf-8"))
    _write_json(authorization_path, {
        **authorization, "provider_traffic_authorized": False,
    })
    schedule = json.loads(
        (output / "authorized" / "schedule.json").read_text(encoding="utf-8")
    )

    with pytest.raises(
        ValueError, match="pilot_authorization_or_schedule_invalid"
    ):
        record_pilot_completion(
            schedule_path=output / "authorized" / "schedule.json",
            authorization_path=authorization_path,
            wal_path=output / "pilot.wal.jsonl",
            card_id=schedule["cards"][0]["card_id"],
            attempted_at=schedule["cards"][0]["scheduled_at"],
            completed_at=schedule["cards"][0]["scheduled_at"],
            trace_id="private-trace",
            runtime_identity_sha256="a" * 64,
            evidence={},
        )
    gate = build_pilot_gate(
        schedule_path=output / "authorized" / "schedule.json",
        authorization_path=authorization_path,
        wal_path=output / "pilot.wal.jsonl",
        runtime_identity_sha256="a" * 64,
    )
    assert gate["automated_gate_passed"] is False
    assert gate["reason"] == "authorization_or_schedule_invalid"


def test_collector_rejects_non_hex_runtime_identity(tmp_path):
    _, output, _ = _prepared(tmp_path)
    draft = output / "pilot-authorization-draft.json"
    approval = output / "pilot-approval.json"
    _write_json(approval, {
        "schema": "query-decomposition-pilot-approval-v1",
        "draft_sha256": hashlib.sha256(draft.read_bytes()).hexdigest(),
        "actor": "bao.nguyen",
        "authorized_at": "2026-08-27T00:00:00Z",
        "expires_at": "2026-08-28T02:00:00Z",
        "authorization": PILOT_AUTHORIZATION,
    })
    finalize_pilot_authorization(
        draft_path=draft,
        approval_path=approval,
        output_dir=output / "authorized",
        now=datetime(2026, 8, 27, 0, 1, tzinfo=timezone.utc),
    )
    schedule_path = output / "authorized" / "schedule.json"
    schedule = json.loads(schedule_path.read_text(encoding="utf-8"))

    with pytest.raises(ValueError, match="pilot_completion_invalid"):
        record_pilot_completion(
            schedule_path=schedule_path,
            authorization_path=output / "authorized" / "pilot-authorization.json",
            wal_path=output / "pilot.wal.jsonl",
            card_id=schedule["cards"][0]["card_id"],
            attempted_at=schedule["cards"][0]["scheduled_at"],
            completed_at=schedule["cards"][0]["scheduled_at"],
            trace_id="private-trace",
            runtime_identity_sha256="z" * 64,
            evidence=_answered_evidence(),
        )
    record_pilot_completion(
        schedule_path=schedule_path,
        authorization_path=output / "authorized" / "pilot-authorization.json",
        wal_path=output / "pilot.wal.jsonl",
        card_id=schedule["cards"][0]["card_id"],
        attempted_at=schedule["cards"][0]["scheduled_at"],
        completed_at=schedule["cards"][0]["scheduled_at"],
        trace_id="private-trace-1",
        runtime_identity_sha256="a" * 64,
        evidence=_answered_evidence(),
    )
    with pytest.raises(ValueError, match="pilot_completion_invalid"):
        record_pilot_completion(
            schedule_path=schedule_path,
            authorization_path=(
                output / "authorized" / "pilot-authorization.json"
            ),
            wal_path=output / "pilot.wal.jsonl",
            card_id=schedule["cards"][1]["card_id"],
            attempted_at=schedule["cards"][-1]["scheduled_at"],
            completed_at=schedule["cards"][-1]["scheduled_at"],
            trace_id="private-trace-2",
            runtime_identity_sha256="a" * 64,
            evidence=_answered_evidence(),
        )


def test_runtime_accepts_bound_pilot_authorization_for_whole_window(
    tmp_path, monkeypatch,
):
    commit, output, packet = _prepared(tmp_path)
    draft = output / "pilot-authorization-draft.json"
    approval = output / "pilot-approval.json"
    _write_json(approval, {
        "schema": "query-decomposition-pilot-approval-v1",
        "draft_sha256": hashlib.sha256(draft.read_bytes()).hexdigest(),
        "actor": "bao.nguyen",
        "authorized_at": "2026-08-27T00:00:00Z",
        "expires_at": "2026-08-28T02:00:00Z",
        "authorization": PILOT_AUTHORIZATION,
    })
    _, authorization_sha = finalize_pilot_authorization(
        draft_path=draft,
        approval_path=approval,
        output_dir=output / "authorized",
        now=datetime(2026, 8, 27, 0, 1, tzinfo=timezone.utc),
    )
    authorization_path = output / "authorized" / "pilot-authorization.json"
    environ = {
        "RAG_RUNTIME_CONSUMPTION_AUTHORIZATION_PATH": str(authorization_path),
        "RAG_RUNTIME_CONSUMPTION_AUTHORIZATION_SHA256": authorization_sha,
    }

    assert runtime_consumption_authorization_status(
        environ,
        root=tmp_path,
        source_commit=commit,
        activation_bundle_sha256=packet["bundle_sha256"],
        enabled_flags={"RAG_QUERY_DECOMPOSITION_ENABLED"},
        now=datetime(2026, 8, 27, 12, 0, tzinfo=timezone.utc),
    ) == "authorized"
    monkeypatch.setattr(
        "mech_chatbot.governance.query_activation_contract."
        "validate_query_activation_authorization",
        lambda *_args, **_kwargs: False,
    )
    assert runtime_consumption_authorization_status(
        environ,
        root=tmp_path,
        source_commit=commit,
        activation_bundle_sha256=packet["bundle_sha256"],
        enabled_flags={"RAG_QUERY_DECOMPOSITION_ENABLED"},
        now=datetime(2026, 8, 27, 12, 0, tzinfo=timezone.utc),
    ) == "invalid"
    monkeypatch.setattr(
        "mech_chatbot.governance.query_activation_contract."
        "validate_query_activation_authorization",
        lambda *_args, **_kwargs: True,
    )
    assert runtime_consumption_authorization_status(
        environ,
        root=tmp_path,
        source_commit=commit,
        activation_bundle_sha256=packet["bundle_sha256"],
        enabled_flags={"RAG_QUERY_DECOMPOSITION_ENABLED"},
        now=datetime(2026, 8, 28, 2, 0, 1, tzinfo=timezone.utc),
    ) == "expired"

    schedule = output / "authorized" / "schedule.json"
    schedule.write_text(schedule.read_text(encoding="utf-8") + " ", encoding="utf-8")
    assert runtime_consumption_authorization_status(
        environ,
        root=tmp_path,
        source_commit=commit,
        activation_bundle_sha256=packet["bundle_sha256"],
        enabled_flags={"RAG_QUERY_DECOMPOSITION_ENABLED"},
        now=datetime(2026, 8, 27, 12, 0, tzinfo=timezone.utc),
    ) == "invalid"


def test_consolidated_launch_uses_one_approval_for_new_commit(
    tmp_path, monkeypatch,
):
    commit, manifest, _, _ = _inputs(tmp_path)
    activation_draft = tmp_path / ".local" / "activation-draft.json"
    _write_json(activation_draft, {
        "schema": "query-controlled-demo-activation-draft-v1",
        "status": "AWAITING_EXACT_OWNER_APPROVAL",
        "source_root": str(tmp_path),
        "source_commit": commit,
        "evidence_source_commit": "e" * 40,
        "scope": "controlled_demo",
        "capability": "query_decomposition",
        "owner": "bao.nguyen",
        "owner_decision_root": str(tmp_path),
        "owner_decision": {"path": "decision.json", "sha256": "d" * 64},
        "owner_decision_finalization": {
            "path": "decision-finalization.json", "sha256": "f" * 64,
        },
        "requested_authorization": QUERY_ACTIVATION_AUTHORIZATION,
    })
    output = tmp_path / ".local" / "consolidated"

    packet = prepare_consolidated_launch(
        source_root=tmp_path,
        source_commit=commit,
        activation_draft_path=activation_draft,
        manifest_path=manifest,
        output_dir=output,
        owner="bao.nguyen",
    )
    draft = output / "consolidated-launch-draft.json"
    approval = output / "consolidated-launch-approval.json"
    _write_json(approval, {
        "schema": "query-decomposition-consolidated-launch-approval-v1",
        "draft_sha256": hashlib.sha256(draft.read_bytes()).hexdigest(),
        "actor": "bao.nguyen",
        "authorized_at": "2026-08-27T00:00:00Z",
        "expires_at": "2026-08-28T02:00:00Z",
        "authorization": CONSOLIDATED_AUTHORIZATION,
    })

    def materialize_activation(**kwargs):
        target = Path(kwargs["output_dir"])
        authorization_path = target / "query-activation-authorization.json"
        authorization_sha = _write_json(authorization_path, {
            "schema": "query-controlled-demo-activation-authorization-v1",
            "source_commit": commit,
            "activation_owner": "bao.nguyen",
            "feature_activation_authorized": True,
        })
        bundle_path = target / "query-controlled-demo-bundle.json"
        bundle_sha = _write_json(bundle_path, {
            "schema": "rag-activation-bundle-v1",
            "scope": "controlled_demo",
            "activation_profile": "selective",
            "source_commit": commit,
            "feature_flags": {"RAG_QUERY_DECOMPOSITION_ENABLED": True},
        })
        receipt_path = target / "finalization-receipt.json"
        _write_json(receipt_path, {
            "schema": "query-controlled-demo-activation-finalization-v1",
            "source_commit": commit,
            "authorization": {
                "path": str(authorization_path), "sha256": authorization_sha,
            },
            "bundle": {"path": str(bundle_path), "sha256": bundle_sha},
            "runtime_consumption_authorized": False,
            "runtime_start_authorized": False,
            "provider_traffic_authorized": False,
            "pilot_dispatch_authorized": False,
        })
        return {
            "bundle": {"path": str(bundle_path), "sha256": bundle_sha},
            "receipt": {"path": str(receipt_path)},
        }

    monkeypatch.setattr(
        "scripts.ops.query_decomposition_pilot_launch.finalize_activation",
        materialize_activation,
    )

    schedule_plan = output / "schedule-plan.json"
    schedule_plan_raw = schedule_plan.read_bytes()
    schedule_plan.write_bytes(schedule_plan_raw + b" ")
    with pytest.raises(ValueError, match="schedule_plan_invalid"):
        finalize_consolidated_launch(
            draft_path=draft,
            approval_path=approval,
            output_dir=output / "materialized",
            now=datetime(2026, 8, 27, 0, 1, tzinfo=timezone.utc),
        )
    assert not (output / "materialized").exists()
    schedule_plan.write_bytes(schedule_plan_raw)

    receipt = finalize_consolidated_launch(
        draft_path=draft,
        approval_path=approval,
        output_dir=output / "materialized",
        now=datetime(2026, 8, 27, 0, 1, tzinfo=timezone.utc),
    )

    assert packet["runtime_started"] is False
    assert packet["provider_traffic_generated"] is False
    assert receipt["activation_materialized"] is True
    assert receipt["pilot_authorization_materialized"] is True
    assert receipt["runtime_started"] is False
    assert receipt["provider_traffic_generated"] is False
    assert receipt["pilot_dispatched"] is False
    assert receipt["next_gate"] == "authorized_operator_runtime_launch"
    authorization_path = Path(receipt["pilot_authorization"]["path"])
    environment = {
        "RAG_RUNTIME_CONSUMPTION_AUTHORIZATION_PATH": str(authorization_path),
        "RAG_RUNTIME_CONSUMPTION_AUTHORIZATION_SHA256": (
            receipt["pilot_authorization"]["sha256"]
        ),
    }
    bundle = output / "materialized" / "activation" / (
        "query-controlled-demo-bundle.json"
    )
    bundle_sha = hashlib.sha256(bundle.read_bytes()).hexdigest()
    assert runtime_consumption_authorization_status(
        environment,
        root=tmp_path,
        source_commit=commit,
        activation_bundle_sha256=bundle_sha,
        enabled_flags={"RAG_QUERY_DECOMPOSITION_ENABLED"},
        now=datetime(2026, 8, 27, 12, 0, tzinfo=timezone.utc),
    ) == "authorized"

    approval.write_text(
        approval.read_text(encoding="utf-8") + " ", encoding="utf-8"
    )
    assert runtime_consumption_authorization_status(
        environment,
        root=tmp_path,
        source_commit=commit,
        activation_bundle_sha256=bundle_sha,
        enabled_flags={"RAG_QUERY_DECOMPOSITION_ENABLED"},
        now=datetime(2026, 8, 27, 12, 0, tzinfo=timezone.utc),
    ) == "invalid"
