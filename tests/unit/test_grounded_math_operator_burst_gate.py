from __future__ import annotations

import copy
import hashlib
import json

import pytest

from scripts.ops import grounded_math_operator_burst_gate as burst_gate


def _sha256_json(value: object) -> str:
    raw = json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")
    return hashlib.sha256(raw).hexdigest()


def _sha256(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _artifacts() -> dict:
    cards = [
        {
            "card_id": f"card-{index:03d}",
            "prompt_sha256": _sha256(f"prompt-{index}"),
        }
        for index in range(1, 101)
    ]
    manifest = {
        "schema": "grounded-math-operator-burst-v1",
        "campaign_id": "burst-001",
        "traffic_class": "owner_authorized_operator_generated_burst",
        "count_toward_pilot": False,
        "qualifies_as_7_day_pilot": False,
        "max_concurrency": 1,
        "max_requests": 100,
        "started_at": "2026-08-13T00:00:00Z",
        "cards": cards,
    }
    runtime = {"pilot": {"git_sha": "7b9d575"}, "main": {"git_sha": "7b9d575"}}
    state = {
        "window_sha256": _sha256("burst-window"),
        "source_commit": "7b9d57562a669984b843d48d6d7ddf09048c472d",
        "expected_runtime": runtime,
    }
    release_decisions = {
        "status": "incomplete",
        "decisions": {"RAG_GROUNDED_MATH_ENABLED": {"decision": None}},
    }
    declaration = {
        "schema": "grounded-math-operator-burst-owner-declaration-v1",
        "owner": "bao.nguyen",
        "actor": {"user_id": 81, "username": "admin_bao"},
        "approved_at": "2026-08-12T23:58:00Z",
        "declared_at": "2026-08-12T23:59:00Z",
        "approval_source": "codex_task_user_authorization",
        "campaign_id": manifest["campaign_id"],
        "traffic_class": manifest["traffic_class"],
        "transport": "internal_rag_sse",
        "scope": "controlled_demo",
        "count_toward_pilot": False,
        "qualifies_as_7_day_pilot": False,
        "duration_claim_allowed": False,
        "organic_claim_allowed": False,
        "quality_claim_allowed": False,
        "ui_parity_claim_allowed": False,
        "default_rollout_authorized": False,
        "request_count": 100,
        "max_requests": 100,
        "concurrency": 1,
        "retry_policy": "none",
        "abort_on_ambiguous": True,
        "selection_bias_disclosed": True,
        "generator_used_structured_values": True,
        "runtime_bindings": runtime,
        "bindings": {
            "manifest_sha256": _sha256_json(manifest),
            "state_sha256": _sha256_json(state),
            "window_sha256": state["window_sha256"],
            "release_decisions_sha256": _sha256_json(release_decisions),
            "operator_tool_sha256": "a" * 64,
            "execution_root_sha256": "b" * 64,
        },
    }
    invocation = {
        "schema": "grounded-math-operator-burst-invocation-v1",
        "status": "completed",
        "campaign_id": manifest["campaign_id"],
        "completed": 100,
        "count_toward_pilot": False,
        "qualifies_as_7_day_pilot": False,
        "default_rollout_authorized": False,
        "execution_root_sha256": "b" * 64,
    }
    trace_hashes = [_sha256(f"trace-{index}") for index in range(1, 101)]
    wal = []
    for index, (card, trace_hash) in enumerate(
        zip(cards, trace_hashes, strict=True), start=1
    ):
        common = {
            "schema": "grounded-math-operator-wal-v1",
            "card_id": card["card_id"],
            "ts": f"2026-08-13T00:{(index - 1) // 60:02d}:{(index - 1) % 60:02d}Z",
            "prompt_sha256": card["prompt_sha256"],
        }
        wal.extend(
            [
                {**common, "event": "attempt_started"},
                {
                    **common,
                    "event": "attempt_completed",
                    "trace_id_sha256": trace_hash,
                },
            ]
        )
    base_gate = {
        "schema": "grounded-math-production-pilot-gate-v1",
        "window_sha256": state["window_sha256"],
        "eligible_trace_count": 100,
        "trace_id_sha256": trace_hashes,
        "checks": {
            "security": True,
            "citation_structure": True,
            "provenance": True,
            "budgets": True,
            "provider_errors": True,
            "leakage": True,
            "runtime_identity": False,
        },
    }
    return {
        "declaration": declaration,
        "manifest": manifest,
        "wal": wal,
        "base_gate": base_gate,
        "state": state,
        "release_decisions": release_decisions,
        "current_release_decisions": copy.deepcopy(release_decisions),
        "invocation": invocation,
    }


def _evaluate(artifacts: dict) -> dict:
    return burst_gate.evaluate_burst_gate(
        artifacts["declaration"],
        artifacts["manifest"],
        artifacts["wal"],
        artifacts["base_gate"],
        artifacts["state"],
        artifacts["release_decisions"],
        artifacts["current_release_decisions"],
        artifacts["invocation"],
    )


def test_burst_gate_accepts_exactly_100_reconciled_eligible_requests():
    artifact = _evaluate(_artifacts())

    assert artifact["passed"] is True
    assert artifact["decision"] == "throughput_evidence_only"
    assert artifact["eligible_trace_count"] == 100
    assert artifact["completed_request_count"] == 100
    assert all(artifact["checks"].values())
    assert len(artifact["trace_id_sha256"]) == 100


def test_burst_gate_rejects_missing_one_shot_invocation():
    artifacts = _artifacts()
    artifacts["invocation"] = {}

    result = _evaluate(artifacts)

    assert result["passed"] is False
    assert result["checks"]["one_shot_invocation"] is False
    assert result["count_toward_pilot"] is False


def test_burst_gate_rejects_non_chronological_wal():
    artifacts = _artifacts()
    artifacts["wal"][2]["ts"] = "2026-08-12T23:59:59Z"

    result = _evaluate(artifacts)

    assert result["passed"] is False
    assert result["checks"]["wal_exactly_once"] is False
    assert result["qualifies_as_7_day_pilot"] is False
    assert result["count_toward_pilot"] is False
    assert result["duration_claim_allowed"] is False
    assert result["organic_claim_allowed"] is False
    assert result["quality_claim_allowed"] is False
    assert result["ui_parity_claim_allowed"] is False
    assert result["default_rollout_authorized"] is False


def _drop_last_request(artifacts: dict) -> None:
    artifacts["manifest"]["cards"].pop()
    del artifacts["wal"][-2:]
    artifacts["base_gate"]["eligible_trace_count"] = 99
    artifacts["base_gate"]["trace_id_sha256"].pop()


def _duplicate_card_id(artifacts: dict) -> None:
    artifacts["manifest"]["cards"][1]["card_id"] = artifacts["manifest"]["cards"][0][
        "card_id"
    ]


def _duplicate_prompt(artifacts: dict) -> None:
    artifacts["manifest"]["cards"][1]["prompt_sha256"] = artifacts["manifest"][
        "cards"
    ][0]["prompt_sha256"]


def _duplicate_trace(artifacts: dict) -> None:
    artifacts["wal"][3]["trace_id_sha256"] = artifacts["wal"][1]["trace_id_sha256"]


def _ambiguous_attempt(artifacts: dict) -> None:
    artifacts["wal"][1]["event"] = "attempt_ambiguous"


def _extra_wal_row(artifacts: dict) -> None:
    artifacts["wal"].append(copy.deepcopy(artifacts["wal"][0]))


def _noneligible_trace(artifacts: dict) -> None:
    artifacts["base_gate"]["eligible_trace_count"] = 99


def _failed_base_check(artifacts: dict) -> None:
    artifacts["base_gate"]["checks"]["provenance"] = False


def _window_mismatch(artifacts: dict) -> None:
    artifacts["base_gate"]["window_sha256"] = _sha256("other-window")


def _default_enabled(artifacts: dict) -> None:
    artifacts["current_release_decisions"]["decisions"]["RAG_GROUNDED_MATH_ENABLED"][
        "decision"
    ] = True


def _binding_tamper(artifacts: dict) -> None:
    artifacts["declaration"]["bindings"]["manifest_sha256"] = _sha256("tampered")


def _paired_exclusion_tamper(artifacts: dict) -> None:
    artifacts["manifest"]["count_toward_pilot"] = True
    artifacts["declaration"]["count_toward_pilot"] = True
    artifacts["declaration"]["bindings"]["manifest_sha256"] = _sha256_json(
        artifacts["manifest"]
    )


def _owner_tamper(artifacts: dict) -> None:
    artifacts["declaration"]["owner"] = "someone.else"


@pytest.mark.parametrize(
    "tamper",
    [
        _drop_last_request,
        _duplicate_card_id,
        _duplicate_prompt,
        _duplicate_trace,
        _ambiguous_attempt,
        _extra_wal_row,
        _noneligible_trace,
        _failed_base_check,
        _window_mismatch,
        _default_enabled,
        _binding_tamper,
        _paired_exclusion_tamper,
        _owner_tamper,
    ],
)
def test_burst_gate_rejects_incomplete_duplicate_ambiguous_extra_or_tampered_evidence(
    tamper,
):
    artifacts = _artifacts()
    tamper(artifacts)

    artifact = _evaluate(artifacts)

    assert artifact["passed"] is False
    assert artifact["decision"] == "rejected"
    assert artifact["qualifies_as_7_day_pilot"] is False
    assert artifact["count_toward_pilot"] is False
    assert artifact["organic_claim_allowed"] is False
    assert artifact["quality_claim_allowed"] is False
    assert artifact["ui_parity_claim_allowed"] is False
    assert artifact["default_rollout_authorized"] is False


def test_burst_gate_rejects_unhashable_trace_tamper_without_crashing():
    artifacts = _artifacts()
    artifacts["base_gate"]["trace_id_sha256"][0] = {"injected": "value"}

    artifact = _evaluate(artifacts)

    assert artifact["passed"] is False
    assert artifact["decision"] == "rejected"


def test_burst_gate_rejects_non_object_artifact_without_crashing():
    artifacts = _artifacts()
    artifacts["declaration"] = ["tampered"]

    artifact = _evaluate(artifacts)

    assert artifact["passed"] is False
    assert artifact["checks"]["input_contract"] is False
