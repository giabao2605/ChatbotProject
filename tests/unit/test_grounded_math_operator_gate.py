from __future__ import annotations

import copy
import hashlib
import json
from datetime import datetime, timezone

from scripts.ops import grounded_math_operator_campaign as campaign
from scripts.ops import grounded_math_operator_gate as operator_gate


def _sha256_json(value: object) -> str:
    raw = json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode()
    return hashlib.sha256(raw).hexdigest()


def _artifacts():
    inventory = [
        {
            "doc_id": index,
            "file_name": f"9.3.{index:05d}(TEST)-ver01-Model{index}.pdf",
            "version": 1,
            "department": "Technical",
            "site": "PHONG_KY_THUAT",
            "operand_facts": [
                {
                    "label": f"PART-{index}-{item}",
                    "value": str(item),
                    "unit": "piece",
                    "page": 1,
                    "source_id": f"{index}-{item}",
                }
                for item in range(1, 9)
            ],
        }
        for index in range(1, 5)
    ]
    manifest, _private = campaign.build_campaign_cards(
        inventory, datetime(2026, 8, 12, tzinfo=timezone.utc)
    )
    trace_hashes = [hashlib.sha256(f"trace-{index}".encode()).hexdigest() for index in range(100)]
    wal = []
    for card, trace_hash in zip(manifest["cards"], trace_hashes, strict=True):
        wal.extend(
            [
                {
                    "schema": campaign.WAL_SCHEMA,
                    "event": "attempt_started",
                    "card_id": card["card_id"],
                    "ts": card["scheduled_at"],
                    "prompt_sha256": card["prompt_sha256"],
                },
                {
                    "schema": campaign.WAL_SCHEMA,
                    "event": "attempt_completed",
                    "card_id": card["card_id"],
                    "ts": card["scheduled_at"],
                    "prompt_sha256": card["prompt_sha256"],
                    "trace_id_sha256": trace_hash,
                },
            ]
        )
    window = {
        "status": "running",
        "feature": "grounded_math",
        "source_commit": campaign.EXPECTED_SERVING_COMMIT,
        "minimum_eligible_requests": 100,
        "expected_runtime": {
            "pilot": {
                "git_sha": campaign.EXPECTED_SERVING_COMMIT,
                "activation_profile": "selective",
                "feature_flags": {
                    "RAG_GROUNDED_MATH_ENABLED": True,
                    "RAG_CRAG_ENABLED": False,
                    "RAG_CLAIM_REPAIR_ENABLED": False,
                    "RAG_LATE_INTERACTION_ENABLED": False,
                    "RAG_QUERY_DECOMPOSITION_ENABLED": False,
                    "RAG_GRAPH_RETRIEVAL_ENABLED": False,
                    "RAG_GRAPH_COMMUNITY_SUMMARIES_ENABLED": False,
                },
            },
            "main": {
                "git_sha": campaign.EXPECTED_SERVING_COMMIT,
                "activation_profile": "all_off",
                "feature_flags": {
                    "RAG_GROUNDED_MATH_ENABLED": False,
                    "RAG_CRAG_ENABLED": False,
                    "RAG_CLAIM_REPAIR_ENABLED": False,
                    "RAG_LATE_INTERACTION_ENABLED": False,
                    "RAG_QUERY_DECOMPOSITION_ENABLED": False,
                    "RAG_GRAPH_RETRIEVAL_ENABLED": False,
                    "RAG_GRAPH_COMMUNITY_SUMMARIES_ENABLED": False,
                },
            },
        },
    }
    state = {
        "source_commit": campaign.EXPECTED_SERVING_COMMIT,
        "activation_scope": "controlled_demo",
        "enabled_features": ["RAG_GROUNDED_MATH_ENABLED"],
        "window_sha256": "b" * 64,
    }
    health = {
        "pilot": {"status": "ok", "activation_scope": "controlled_demo"},
        "main": {"status": "ok", "activation_scope": "controlled_demo"},
    }
    base_gate = {
        "schema": "grounded-math-production-pilot-gate-v1",
        "passed": True,
        "decision": "pending_review",
        "eligible_trace_count": 100,
        "checks": {
            "runtime_identity": True,
            "security": True,
            "citation_structure": True,
            "provenance": True,
            "budgets": True,
            "provider_errors": True,
            "leakage": True,
        },
        "trace_id_sha256": trace_hashes,
        "window_sha256": state["window_sha256"],
    }
    release_decisions = {
        "status": "incomplete",
        "decisions": {"RAG_GROUNDED_MATH_ENABLED": {"decision": None, "evidence": None}},
    }
    declaration = {
        "schema": "grounded-math-operator-owner-declaration-v1",
        "owner": "bao.nguyen",
        "approved_at": "2026-08-11T12:00:00Z",
        "declared_at": "2026-08-11T12:05:00Z",
        "approval_source": "codex_task_user_authorization",
        "actor": {"user_id": 81, "username": "admin_bao"},
        "traffic_class": campaign.TRAFFIC_CLASS,
        "transport": campaign.TRANSPORT,
        "count_toward_pilot": True,
        "organic_claim_allowed": False,
        "quality_claim_allowed": False,
        "ui_parity_claim_allowed": False,
        "scope": "controlled_demo",
        "default_rollout_authorized": False,
        "selection_bias_disclosed": True,
        "generator_used_structured_values": True,
        "unavailable_operations": campaign.UNAVAILABLE_OPERATIONS,
        "review_contract": {
            "primary_reviewers": ["bao.nguyen"],
            "stratified_cases": 20,
            "review_all_failures_and_low_confidence": True,
            "codex_role": "metadata_and_technical_assistance_only",
        },
        "runtime_bindings": window["expected_runtime"],
        "bindings": {
            "manifest_sha256": _sha256_json(manifest),
            "inventory_sha256": manifest["inventory_sha256"],
            "window_sha256": _sha256_json(window),
            "state_sha256": _sha256_json(state),
            "health_sha256": _sha256_json(health),
            "release_decisions_sha256": _sha256_json(release_decisions),
            "operator_tool_sha256": "a" * 64,
        },
    }
    return declaration, manifest, wal, base_gate, window, state, health, release_decisions


def test_gate_reconciles_all_100_operator_traces_but_never_authorizes_default():
    artifact = operator_gate.evaluate_operator_gate(*_artifacts())

    assert artifact["passed"] is True
    assert artifact["decision"] == "pending_owner_review"
    assert artifact["eligible_operator_trace_count"] == 100
    assert all(artifact["checks"].values())
    assert artifact["traffic_class"] == campaign.TRAFFIC_CLASS
    assert artifact["organic_claim_allowed"] is False
    assert artifact["quality_claim_allowed"] is False
    assert artifact["ui_parity_claim_allowed"] is False
    assert artifact["default_rollout_authorized"] is False


def test_gate_fails_closed_when_owner_provenance_is_mislabelled():
    artifacts = list(_artifacts())
    artifacts[0] = {**artifacts[0], "organic_claim_allowed": True}

    result = operator_gate.evaluate_operator_gate(*artifacts)

    assert result["passed"] is False
    assert result["decision"] == "rejected"
    assert result["checks"]["owner_declaration"] is False
    assert result["default_rollout_authorized"] is False


def test_gate_fails_closed_when_base_trace_set_does_not_match_wal():
    artifacts = list(_artifacts())
    artifacts[3] = copy.deepcopy(artifacts[3])
    artifacts[3]["trace_id_sha256"][0] = "f" * 64

    result = operator_gate.evaluate_operator_gate(*artifacts)

    assert result["checks"]["trace_reconciliation"] is False
    assert result["passed"] is False


def test_gate_fails_closed_when_base_gate_trace_hash_count_is_not_exactly_100():
    artifacts = list(_artifacts())
    artifacts[3] = copy.deepcopy(artifacts[3])
    artifacts[3]["trace_id_sha256"].append(artifacts[3]["trace_id_sha256"][0])

    result = operator_gate.evaluate_operator_gate(*artifacts)

    assert result["checks"]["trace_reconciliation"] is False
    assert result["passed"] is False


def test_gate_fails_closed_if_grounded_math_has_a_default_release_decision():
    artifacts = list(_artifacts())
    decisions = copy.deepcopy(artifacts[7])
    decisions["status"] = "complete"
    decisions["decisions"]["RAG_GROUNDED_MATH_ENABLED"]["decision"] = "accepted"
    artifacts[7] = decisions

    result = operator_gate.evaluate_operator_gate(*artifacts)

    assert result["checks"]["default_rollout_still_off"] is False
    assert result["passed"] is False
    assert result["default_rollout_authorized"] is False


def test_gate_rejects_dispatch_before_a_card_schedule():
    artifacts = list(_artifacts())
    wal = copy.deepcopy(artifacts[2])
    wal[0]["ts"] = "2026-08-11T23:59:59Z"
    artifacts[2] = wal

    result = operator_gate.evaluate_operator_gate(*artifacts)

    assert result["checks"]["pacing"] is False
    assert result["passed"] is False


def test_gate_rejects_more_than_three_dispatches_in_rolling_thirty_minutes():
    artifacts = list(_artifacts())
    wal = copy.deepcopy(artifacts[2])
    fourth_schedule = wal[6]["ts"]
    for row in wal[:8]:
        row["ts"] = fourth_schedule
    artifacts[2] = wal

    result = operator_gate.evaluate_operator_gate(*artifacts)

    assert result["checks"]["pacing"] is False
    assert result["passed"] is False


def test_gate_rejects_missing_unavailable_divide_declaration():
    artifacts = list(_artifacts())
    declaration = copy.deepcopy(artifacts[0])
    declaration.pop("unavailable_operations")
    artifacts[0] = declaration

    result = operator_gate.evaluate_operator_gate(*artifacts)

    assert result["checks"]["owner_declaration"] is False
    assert result["passed"] is False


def test_gate_checks_current_release_decision_without_invalidating_start_binding():
    artifacts = list(_artifacts())
    current = copy.deepcopy(artifacts[7])
    current["status"] = "complete"
    current["decisions"]["RAG_GROUNDED_MATH_ENABLED"]["decision"] = "accepted"

    result = operator_gate.evaluate_operator_gate(*artifacts, current)

    assert result["checks"]["artifact_bindings"] is True
    assert result["checks"]["default_rollout_still_off"] is False
    assert result["default_rollout_authorized"] is False


def test_gate_does_not_fallback_when_current_release_ledger_is_empty():
    result = operator_gate.evaluate_operator_gate(*_artifacts(), {})

    assert result["checks"]["artifact_bindings"] is True
    assert result["checks"]["default_rollout_still_off"] is False
    assert result["passed"] is False


def test_gate_rejects_declaration_created_after_campaign_start():
    artifacts = list(_artifacts())
    artifacts[0] = {**artifacts[0], "declared_at": "2026-08-12T00:00:01Z"}

    result = operator_gate.evaluate_operator_gate(*artifacts)

    assert result["checks"]["owner_declaration"] is False
    assert result["passed"] is False


def test_gate_rejects_actor_provenance_drift():
    artifacts = list(_artifacts())
    artifacts[0] = {**artifacts[0], "actor": {"user_id": 999, "username": "other"}}

    result = operator_gate.evaluate_operator_gate(*artifacts)

    assert result["checks"]["owner_declaration"] is False


def test_gate_rejects_schema_less_or_different_window_base_gate():
    artifacts = list(_artifacts())
    artifacts[3] = {**artifacts[3], "schema": "other", "window_sha256": "f" * 64}

    result = operator_gate.evaluate_operator_gate(*artifacts)

    assert result["checks"]["base_gate"] is False
    assert result["passed"] is False


def test_gate_rejects_manifest_that_hides_an_unaccepted_operation():
    artifacts = list(_artifacts())
    manifest = copy.deepcopy(artifacts[1])
    row = next(
        item for item in manifest["preflight"]["by_operation"]
        if item["operation"] == "multiply"
    )
    row["accepted"] = 0
    artifacts[1] = manifest

    result = operator_gate.evaluate_operator_gate(*artifacts)

    assert result["checks"]["manifest_contract"] is False
    assert result["passed"] is False


def test_gate_rejects_malformed_manifest_timestamp_without_raising():
    artifacts = list(_artifacts())
    manifest = copy.deepcopy(artifacts[1])
    manifest["cards"][0]["scheduled_at"] = "not-a-timestamp"
    artifacts[1] = manifest

    result = operator_gate.evaluate_operator_gate(*artifacts)

    assert result["checks"]["manifest_contract"] is False
    assert result["passed"] is False


def test_gate_rejects_non_object_manifest_card_without_raising():
    artifacts = list(_artifacts())
    manifest = copy.deepcopy(artifacts[1])
    manifest["cards"][0] = None
    artifacts[1] = manifest

    result = operator_gate.evaluate_operator_gate(*artifacts)

    assert result["checks"]["manifest_contract"] is False
    assert result["passed"] is False


def test_gate_rejects_extra_manifest_or_preflight_fields():
    for target in ("manifest", "preflight"):
        artifacts = list(_artifacts())
        manifest = copy.deepcopy(artifacts[1])
        container = manifest if target == "manifest" else manifest["preflight"]
        container["raw_question"] = "must-not-be-accepted"
        artifacts[1] = manifest

        result = operator_gate.evaluate_operator_gate(*artifacts)

        assert result["checks"]["manifest_contract"] is False
        assert result["passed"] is False
