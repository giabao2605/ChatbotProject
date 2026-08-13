"""Validate accelerated Grounded Math traffic as throughput-only evidence."""

from __future__ import annotations

import hashlib
import json
from datetime import datetime


BURST_COUNT = 100
BURST_SCHEMA = "grounded-math-operator-burst-v1"
DECLARATION_SCHEMA = "grounded-math-operator-burst-owner-declaration-v1"
GATE_SCHEMA = "grounded-math-operator-burst-gate-v1"
TRAFFIC_CLASS = "owner_authorized_operator_generated_burst"
WAL_SCHEMA = "grounded-math-operator-wal-v1"
REQUIRED_BASE_CHECKS = (
    "security",
    "citation_structure",
    "provenance",
    "budgets",
    "provider_errors",
    "leakage",
)


def _canonical_sha256(value: object) -> str:
    raw = json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")
    return hashlib.sha256(raw).hexdigest()


def _is_sha256(value: object) -> bool:
    return (
        isinstance(value, str)
        and len(value) == 64
        and all(character in "0123456789abcdef" for character in value)
    )


def _timestamp(value: object) -> datetime:
    parsed = datetime.fromisoformat(str(value or "").replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        raise ValueError("timestamp_timezone_missing")
    return parsed


def _manifest_analysis(manifest: dict) -> tuple[bool, dict[str, str]]:
    cards = manifest.get("cards")
    if not isinstance(cards, list) or len(cards) != BURST_COUNT:
        return False, {}
    bindings: dict[str, str] = {}
    for card in cards:
        if not isinstance(card, dict):
            return False, {}
        card_id = card.get("card_id")
        prompt_hash = card.get("prompt_sha256")
        if not isinstance(card_id, str) or not card_id or not _is_sha256(prompt_hash):
            return False, {}
        if card_id in bindings or prompt_hash in bindings.values():
            return False, {}
        bindings[card_id] = prompt_hash
    return (
        manifest.get("schema") == BURST_SCHEMA
        and manifest.get("traffic_class") == TRAFFIC_CLASS
        and isinstance(manifest.get("campaign_id"), str)
        and bool(manifest.get("campaign_id"))
        and manifest.get("count_toward_pilot") is False
        and manifest.get("qualifies_as_7_day_pilot") is False
        and manifest.get("max_concurrency") == 1
        and manifest.get("max_requests") == BURST_COUNT,
        bindings,
    )


def _declaration_valid(
    declaration: dict,
    manifest: dict,
    state: dict,
    start_release_decisions: dict,
) -> bool:
    bindings = declaration.get("bindings")
    try:
        approved_at = _timestamp(declaration.get("approved_at"))
        declared_at = _timestamp(declaration.get("declared_at"))
        started_at = _timestamp(manifest.get("started_at"))
    except (TypeError, ValueError):
        return False
    return all(
        (
            declaration.get("schema") == DECLARATION_SCHEMA,
            declaration.get("owner") == "bao.nguyen",
            declaration.get("actor") == {"user_id": 81, "username": "admin_bao"},
            declaration.get("approval_source") == "codex_task_user_authorization",
            approved_at <= declared_at <= started_at,
            declaration.get("campaign_id") == manifest.get("campaign_id"),
            declaration.get("traffic_class") == TRAFFIC_CLASS,
            declaration.get("transport") == "internal_rag_sse",
            declaration.get("scope") == "controlled_demo",
            declaration.get("count_toward_pilot") is False,
            declaration.get("qualifies_as_7_day_pilot") is False,
            declaration.get("duration_claim_allowed") is False,
            declaration.get("organic_claim_allowed") is False,
            declaration.get("quality_claim_allowed") is False,
            declaration.get("ui_parity_claim_allowed") is False,
            declaration.get("default_rollout_authorized") is False,
            declaration.get("request_count") == BURST_COUNT,
            declaration.get("max_requests") == BURST_COUNT,
            declaration.get("concurrency") == 1,
            declaration.get("retry_policy") == "none",
            declaration.get("abort_on_ambiguous") is True,
            declaration.get("selection_bias_disclosed") is True,
            declaration.get("generator_used_structured_values") is True,
            state.get("source_commit")
            == "7b9d57562a669984b843d48d6d7ddf09048c472d",
            declaration.get("runtime_bindings") == state.get("expected_runtime"),
            isinstance(bindings, dict),
            _is_sha256(bindings.get("operator_tool_sha256"))
            if isinstance(bindings, dict)
            else False,
            _is_sha256(bindings.get("execution_root_sha256"))
            if isinstance(bindings, dict)
            else False,
            bindings.get("manifest_sha256") == _canonical_sha256(manifest)
            if isinstance(bindings, dict)
            else False,
            bindings.get("state_sha256") == _canonical_sha256(state)
            if isinstance(bindings, dict)
            else False,
            bindings.get("window_sha256") == state.get("window_sha256")
            if isinstance(bindings, dict)
            else False,
            bindings.get("release_decisions_sha256")
            == _canonical_sha256(start_release_decisions)
            if isinstance(bindings, dict)
            else False,
        )
    )


def _wal_analysis(
    wal_rows: list[dict], card_bindings: dict[str, str]
) -> tuple[bool, list[str]]:
    if len(wal_rows) != BURST_COUNT * 2:
        return False, []
    traces: list[str] = []
    seen_cards: set[str] = set()
    previous_completed = None
    for offset in range(0, len(wal_rows), 2):
        started, completed = wal_rows[offset : offset + 2]
        if not isinstance(started, dict) or not isinstance(completed, dict):
            return False, []
        card_id = started.get("card_id")
        prompt_hash = card_bindings.get(card_id)
        trace_hash = completed.get("trace_id_sha256")
        if not all(
            (
                set(started)
                == {"schema", "event", "card_id", "ts", "prompt_sha256"},
                set(completed)
                == {
                    "schema",
                    "event",
                    "card_id",
                    "ts",
                    "prompt_sha256",
                    "trace_id_sha256",
                },
                started.get("schema") == WAL_SCHEMA,
                completed.get("schema") == WAL_SCHEMA,
                started.get("event") == "attempt_started",
                completed.get("event") == "attempt_completed",
                isinstance(card_id, str) and card_id in card_bindings,
                completed.get("card_id") == card_id,
                card_id not in seen_cards,
                started.get("prompt_sha256") == prompt_hash,
                completed.get("prompt_sha256") == prompt_hash,
                isinstance(started.get("ts"), str) and bool(started.get("ts")),
                isinstance(completed.get("ts"), str) and bool(completed.get("ts")),
                _is_sha256(trace_hash),
            )
        ):
            return False, []
        try:
            started_at = _timestamp(started.get("ts"))
            completed_at = _timestamp(completed.get("ts"))
        except (TypeError, ValueError):
            return False, []
        if completed_at < started_at or (
            previous_completed is not None and started_at < previous_completed
        ):
            return False, []
        previous_completed = completed_at
        seen_cards.add(card_id)
        traces.append(trace_hash)
    return (
        len(seen_cards) == BURST_COUNT and len(set(traces)) == BURST_COUNT,
        traces,
    )


def _base_gate_valid(base_gate: dict, state: dict, traces: list[str]) -> bool:
    checks = base_gate.get("checks")
    base_traces = base_gate.get("trace_id_sha256")
    if (
        not isinstance(base_traces, list)
        or len(base_traces) != BURST_COUNT
        or not all(_is_sha256(value) for value in base_traces)
    ):
        return False
    return all(
        (
            base_gate.get("schema") == "grounded-math-production-pilot-gate-v1",
            _is_sha256(state.get("window_sha256")),
            base_gate.get("window_sha256") == state.get("window_sha256"),
            base_gate.get("eligible_trace_count") == BURST_COUNT,
            isinstance(checks, dict),
            all(checks.get(name) is True for name in REQUIRED_BASE_CHECKS)
            if isinstance(checks, dict)
            else False,
            len(set(base_traces)) == BURST_COUNT,
            set(base_traces) == set(traces),
        )
    )


def _default_still_off(release_decisions: dict) -> bool:
    decisions = release_decisions.get("decisions")
    grounded_math = (
        decisions.get("RAG_GROUNDED_MATH_ENABLED")
        if isinstance(decisions, dict)
        else None
    )
    return all(
        (
            release_decisions.get("status") == "incomplete",
            isinstance(grounded_math, dict),
            "decision" in grounded_math if isinstance(grounded_math, dict) else False,
            grounded_math.get("decision") is None
            if isinstance(grounded_math, dict)
            else False,
        )
    )


def evaluate_burst_gate(
    declaration: object,
    manifest: object,
    wal_rows: object,
    base_gate: object,
    state: object,
    start_release_decisions: object,
    current_release_decisions: object,
    invocation: object,
) -> dict:
    """Return a fail-closed assessment that can never authorize pilot rollout."""

    input_valid = all(
        (
            isinstance(declaration, dict),
            isinstance(manifest, dict),
            isinstance(wal_rows, list),
            isinstance(base_gate, dict),
            isinstance(state, dict),
            isinstance(start_release_decisions, dict),
            isinstance(current_release_decisions, dict),
            isinstance(invocation, dict),
        )
    )
    declaration = declaration if isinstance(declaration, dict) else {}
    manifest = manifest if isinstance(manifest, dict) else {}
    wal_rows = wal_rows if isinstance(wal_rows, list) else []
    base_gate = base_gate if isinstance(base_gate, dict) else {}
    state = state if isinstance(state, dict) else {}
    start_release_decisions = (
        start_release_decisions if isinstance(start_release_decisions, dict) else {}
    )
    current_release_decisions = (
        current_release_decisions
        if isinstance(current_release_decisions, dict)
        else {}
    )
    invocation = invocation if isinstance(invocation, dict) else {}
    manifest_valid, card_bindings = _manifest_analysis(manifest)
    wal_valid, traces = _wal_analysis(wal_rows, card_bindings)
    checks = {
        "input_contract": input_valid,
        "owner_declaration": _declaration_valid(
            declaration, manifest, state, start_release_decisions
        ),
        "manifest_contract": manifest_valid,
        "wal_exactly_once": wal_valid,
        "base_gate": _base_gate_valid(base_gate, state, traces),
        "trace_reconciliation": wal_valid and len(traces) == BURST_COUNT,
        "window_binding": _is_sha256(state.get("window_sha256"))
        and base_gate.get("window_sha256") == state.get("window_sha256"),
        "default_rollout_still_off": _default_still_off(
            current_release_decisions
        ),
        "one_shot_invocation": all(
            (
                invocation.get("schema")
                == "grounded-math-operator-burst-invocation-v1",
                invocation.get("status") == "completed",
                invocation.get("campaign_id") == manifest.get("campaign_id"),
                invocation.get("completed") == BURST_COUNT,
                invocation.get("count_toward_pilot") is False,
                invocation.get("qualifies_as_7_day_pilot") is False,
                invocation.get("default_rollout_authorized") is False,
                invocation.get("execution_root_sha256")
                == declaration.get("bindings", {}).get("execution_root_sha256"),
            )
        ),
    }
    passed = all(checks.values())
    return {
        "schema": GATE_SCHEMA,
        "passed": passed,
        "decision": "throughput_evidence_only" if passed else "rejected",
        "checks": checks,
        "eligible_trace_count": BURST_COUNT if passed else 0,
        "completed_request_count": len(traces),
        "traffic_class": TRAFFIC_CLASS,
        "qualifies_as_7_day_pilot": False,
        "count_toward_pilot": False,
        "duration_claim_allowed": False,
        "organic_claim_allowed": False,
        "quality_claim_allowed": False,
        "ui_parity_claim_allowed": False,
        "default_rollout_authorized": False,
        "trace_id_sha256": sorted(traces) if passed else [],
    }
