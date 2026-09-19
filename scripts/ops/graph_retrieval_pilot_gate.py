"""Build a metadata-only, fail-closed Graph Retrieval production pilot gate."""

from __future__ import annotations

import argparse
import hashlib
import json
from collections import Counter
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path

from mech_chatbot.governance.review_governance import review_governance_status
from mech_chatbot.governance.graph_pilot_review import (
    APPROVED_GRAPH_REFUSALS,
    review_contract,
    review_result_valid,
    select_review_cases,
)
from scripts.ops.grounded_math_pilot_gate import (
    _atomic_json,
    _decimal,
    _error_like,
    _event_time_valid,
    _is_sha256,
    _load_json,
    _material,
    _provider_failure,
    _read_trace,
    _sha256,
    _timestamp,
)


CHECKS = (
    "runtime_identity",
    "security",
    "citation_structure",
    "provenance",
    "budgets",
    "provider_errors",
    "leakage",
)
FEATURE_FLAGS = (
    "RAG_CRAG_ENABLED",
    "RAG_CLAIM_REPAIR_ENABLED",
    "RAG_GROUNDED_MATH_ENABLED",
    "RAG_LATE_INTERACTION_ENABLED",
    "RAG_QUERY_DECOMPOSITION_ENABLED",
    "RAG_GRAPH_RETRIEVAL_ENABLED",
    "RAG_GRAPH_COMMUNITY_SUMMARIES_ENABLED",
)
RUNTIME_BINDINGS = (
    "git_sha",
    "deployment_id",
    "activation_profile",
    "feature_flags",
    "snapshot_fingerprint",
    "provider_configuration_sha256",
    "qdrant_collection",
    "sql_database",
    "activation_bundle_sha256",
    "restore_evidence_sha256",
    "request_deadline_seconds",
    "runtime_identity_sha256",
)
PILOT_EVIDENCE_FIELDS = frozenset({
    "ts", "event", "trace_id", "execution_context",
    "runtime_identity_sha256", "route", "graph_result_status",
    "completion_outcome", "refusal_reason_code", "refusal_template_passed",
    "low_confidence", "owner_review_required", "security_passed",
    "citation_structure_passed", "provenance_passed", "leakage_detected",
    "graph_edges", "graph_max_hops", "graph_evidence_count",
    "rendered_citation_count", "graph_citation_count", "final_latency_ms",
    "request_deadline_ms", "estimated_cost", "provider_retries",
    "final_generations",
})
def _graph_error_like(event: dict) -> bool:
    return event.get("event") == "graph_retrieval" and any((
        bool(event.get("error")),
        event.get("has_error") is True,
        str(event.get("status") or "").casefold() in {"error", "failed"},
    ))


def _graph_only_flags(value: object) -> bool:
    return isinstance(value, dict) and value == {
        name: name == "RAG_GRAPH_RETRIEVAL_ENABLED" for name in FEATURE_FLAGS
    }


def _all_off_flags(value: object) -> bool:
    return isinstance(value, dict) and value == {
        name: False for name in FEATURE_FLAGS
    }


def _runtime_identity(runtime: dict) -> str:
    raw = json.dumps(
        {
            name: runtime[name]
            for name in RUNTIME_BINDINGS
            if name != "runtime_identity_sha256"
        },
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return _sha256(raw)


def _runtime_valid(runtime: object, *, candidate: bool) -> bool:
    if not isinstance(runtime, dict) or any(
        name not in runtime for name in RUNTIME_BINDINGS
    ):
        return False
    required_sha = (
        "snapshot_fingerprint",
        "provider_configuration_sha256",
        "restore_evidence_sha256",
    )
    if not all(_is_sha256(runtime.get(name)) for name in required_sha):
        return False
    if not all(str(runtime.get(name) or "").strip() for name in (
        "git_sha",
        "deployment_id",
        "qdrant_collection",
        "sql_database",
    )):
        return False
    try:
        deadline = _decimal(runtime["request_deadline_seconds"])
    except ValueError:
        return False
    identity_valid = (
        _is_sha256(runtime.get("runtime_identity_sha256"))
        and runtime["runtime_identity_sha256"] == _runtime_identity(runtime)
    )
    if deadline <= 0 or not identity_valid:
        return False
    if candidate:
        return all((
            runtime.get("activation_profile") == "selective",
            _graph_only_flags(runtime.get("feature_flags")),
            _is_sha256(runtime.get("activation_bundle_sha256")),
        ))
    return all((
        runtime.get("activation_profile") == "all_off",
        _all_off_flags(runtime.get("feature_flags")),
        runtime.get("activation_bundle_sha256") in {None, ""},
    ))


def _formal_budget_maxima(sources: object, base: Path, commit: str):
    if not isinstance(sources, dict) or sources.get("source_commit") != commit:
        return None
    pairs = sources.get("pairs")
    if not isinstance(pairs, list) or len(pairs) != 3:
        return None
    latencies = ()
    costs = ()
    identities = frozenset()
    for pair in pairs:
        if not isinstance(pair, dict):
            return None
        loaded_eval = _material(pair.get("eval"), base)
        loaded_gate = _material(pair.get("gate"), base)
        if loaded_eval is None or loaded_gate is None:
            return None
        evaluation, eval_sha256 = loaded_eval
        gate, gate_sha256 = loaded_gate
        identities = identities | {(eval_sha256, gate_sha256)}
        limits = gate.get("limits")
        inputs = gate.get("inputs")
        cases = evaluation.get("cases")
        if not all((
            evaluation.get("schema") == "rag-labeled-eval-v4",
            evaluation.get("git_sha") == commit,
            isinstance(cases, list) and bool(cases),
            gate.get("schema") == "retrieval-intelligence-gate-v1",
            gate.get("stage") == "graph_retrieval",
            gate.get("passed") is True,
            isinstance(limits, dict),
            limits.get("max_latency_ratio") == 1.5,
            limits.get("max_cost_ratio") == 1.5,
            limits.get("max_hops") == 2,
            limits.get("max_edges") == 50,
            isinstance(inputs, dict),
            inputs.get("candidate_eval_sha256") == eval_sha256,
        )):
            return None
        try:
            latencies = (*latencies, *(
                _decimal(case["latency_ms"]) for case in cases
            ))
            costs = (*costs, *(
                _decimal(case["estimated_cost"]) for case in cases
            ))
        except (KeyError, TypeError, ValueError):
            return None
    if len(identities) != 3:
        return None
    return max(latencies), max(costs)


def _series_valid(reference: object, base: Path, commit: str, provider: str) -> bool:
    loaded = _material(reference, base)
    if loaded is None:
        return False
    series = loaded[0]
    checks = series.get("checks")
    return all((
        series.get("schema") == "rollout-guardrail-series-v1",
        series.get("stage") == "graph_retrieval",
        series.get("source_commit") == commit,
        series.get("provider_configuration_sha256") == provider,
        series.get("pair_count") == 3,
        isinstance(series.get("run_ids"), list),
        len(set(series.get("run_ids") or ())) == 3,
        series.get("review_mode") == "multi_reviewer",
        series.get("review_source") == "independent",
        series.get("passed") is True,
        series.get("production_eligible") is True,
        isinstance(checks, dict) and bool(checks) and all(checks.values()),
    ))


def _governance_valid(reference: object, base: Path, commit: str) -> bool:
    loaded = _material(reference, base)
    if loaded is None:
        return False
    status = review_governance_status(
        loaded[0], source_commit=commit, scope="controlled_demo"
    )
    return status.valid and status.mode == "single_owner"


def _review_contract_details(window: dict, base: Path, commit: str):
    governance = _material(window.get("review_governance"), base)
    contract = _material(window.get("review_contract"), base)
    series = _material(window.get("formal_series"), base)
    if governance is None or contract is None or series is None:
        return None
    return review_contract(
        contract[0],
        governance_sha256=governance[1],
        formal_series_sha256=series[1],
        source_commit=commit,
    )


def _window_budget_valid(budget: dict, sources: dict, maxima) -> bool:
    try:
        latency_multiplier = _decimal(sources["latency_multiplier"])
        cost_multiplier = _decimal(sources["cost_multiplier"])
        max_latency = _decimal(budget["max_final_latency_ms"])
        max_cost = _decimal(budget["max_estimated_cost"])
    except (KeyError, TypeError, ValueError):
        return False
    latency, cost = maxima
    return all((
        budget.get("max_graph_edges") == 50,
        budget.get("max_graph_hops") == 2,
        budget.get("max_provider_retries") == 0,
        budget.get("max_final_generations") == 1,
        latency_multiplier == Decimal("1.5"),
        cost_multiplier == Decimal("1.5"),
        max_latency == latency * latency_multiplier,
        max_cost == cost * cost_multiplier,
    ))


def _window_valid(window: dict, now: datetime, base: Path) -> bool:
    try:
        started = _timestamp(window["started_at"])
        minimum_until = _timestamp(window["minimum_runtime_until"])
        expected = window["expected_runtime"]
        candidate = expected["candidate"]
        control = expected["control"]
        budget = window["pilot_budget"]
        sources = window["formal_budget_sources"]
    except (KeyError, TypeError, ValueError):
        return False
    if not _runtime_valid(candidate, candidate=True):
        return False
    if not _runtime_valid(control, candidate=False):
        return False
    maxima = _formal_budget_maxima(sources, base, candidate["git_sha"])
    if maxima is None:
        return False
    return all((
        window.get("schema") == "graph-lan-pilot-window-v1",
        minimum_until - started >= timedelta(days=7),
        now >= minimum_until,
        type(window.get("minimum_eligible_requests")) is int,
        window.get("minimum_eligible_requests", 0) >= 100,
        window.get("required_automated_checks") == list(CHECKS),
        candidate["git_sha"] == control["git_sha"] == sources["source_commit"],
        candidate["snapshot_fingerprint"] == control["snapshot_fingerprint"],
        candidate["provider_configuration_sha256"]
        == control["provider_configuration_sha256"],
        _window_budget_valid(budget, sources, maxima),
        _series_valid(
            window.get("formal_series"),
            base,
            candidate["git_sha"],
            candidate["provider_configuration_sha256"],
        ),
        _governance_valid(
            window.get("review_governance"), base, candidate["git_sha"]
        ),
        _review_contract_details(window, base, candidate["git_sha"]) is not None,
    ))


def _restore_valid(receipt: object, candidate: dict) -> bool:
    if not isinstance(receipt, dict):
        return False
    return all((
        receipt.get("schema") == "backup-restore-drill-v1",
        receipt.get("git_sha") == candidate.get("git_sha"),
        receipt.get("passed") is True,
        receipt.get("error_type") is None,
        receipt.get("automatic_cleanup") is False,
        receipt.get("target_database") == candidate.get("sql_database"),
        receipt.get("target_collection") == candidate.get("qdrant_collection"),
        receipt.get("snapshot_fingerprint") == candidate.get("snapshot_fingerprint"),
        "_RestoreTest_RAGPilot_" in str(receipt.get("target_database") or ""),
        "_RestoreTest_RAGPilot_" in str(receipt.get("target_collection") or ""),
    ))


def _state_valid(state: dict, window: dict, window_sha256: str, base: Path) -> bool:
    expected = window.get("expected_runtime")
    if not isinstance(expected, dict):
        return False
    candidate = expected.get("candidate")
    if not isinstance(candidate, dict):
        return False
    bundle = _material(
        {"path": state.get("activation_bundle"), "sha256": state.get("activation_bundle_sha256")},
        base,
    )
    restore = _material(
        {"path": state.get("restore_evidence"), "sha256": state.get("restore_evidence_sha256")},
        base,
    )
    if bundle is None or restore is None:
        return False
    bundle_value = bundle[0]
    return all((
        state.get("schema") == "rag-profile-pair-process-state-v1",
        state.get("window_sha256") == window_sha256,
        state.get("source_commit") == candidate.get("git_sha"),
        state.get("scope") == "controlled_demo",
        state.get("profile") == "selective",
        state.get("enabled_features") == ["RAG_GRAPH_RETRIEVAL_ENABLED"],
        state.get("snapshot_fingerprint") == candidate.get("snapshot_fingerprint"),
        bundle[1] == candidate.get("activation_bundle_sha256"),
        bundle_value.get("schema") == "rag-activation-bundle-v1",
        bundle_value.get("source_commit") == candidate.get("git_sha"),
        bundle_value.get("scope") == "controlled_demo",
        bundle_value.get("activation_profile") == "selective",
        _graph_only_flags(bundle_value.get("feature_flags")),
        restore[1] == candidate.get("restore_evidence_sha256"),
        restore[1] == expected.get("control", {}).get("restore_evidence_sha256"),
        _restore_valid(restore[0], candidate),
    ))


def _health_valid(health: dict, expected: dict, now: datetime) -> bool:
    try:
        checked_at = _timestamp(health["checked_at"])
        candidate = health["candidate"]
        control = health["control"]
    except (KeyError, TypeError, ValueError):
        return False
    if not isinstance(candidate, dict) or not isinstance(control, dict):
        return False
    bindings_match = all(
        actual.get(name) == expected[arm].get(name)
        for arm, actual in (("candidate", candidate), ("control", control))
        for name in RUNTIME_BINDINGS
    )
    return all((
        health.get("schema") == "graph-lan-pilot-health-capture-v1",
        timedelta(0) <= now - checked_at <= timedelta(minutes=15),
        candidate.get("status") == control.get("status") == "ok",
        candidate.get("rag_loaded") is True,
        control.get("rag_loaded") is True,
        bindings_match,
    ))


def _provider_smoke_status(path: Path, window: dict, base: Path):
    try:
        artifact, raw = _load_json(path)
        digest = _sha256(raw)
        declaration = window["provider_smoke"]
        declared = Path(str(declaration["path"]))
        if not declared.is_absolute():
            declared = base / declared
        if declared.resolve() != path.resolve() or declaration["sha256"] != digest:
            return False, "invalid_evidence_binding", digest
        candidate = window["expected_runtime"]["candidate"]
        completed = _timestamp(artifact["completed_at"])
        started = _timestamp(window["started_at"])
        outcome = artifact["provider_outcome"]
        if not all((
            artifact.get("schema") == "provider-smoke-v1",
            artifact.get("request_count") == 5,
            artifact.get("provider_retries") == 0,
            artifact.get("max_attempts_per_request") == 1,
            artifact.get("provider_configuration_sha256")
            == candidate["provider_configuration_sha256"],
            isinstance(outcome, dict),
            timedelta(0) < started - completed <= timedelta(minutes=30),
        )):
            return False, "invalid_artifact", digest
        if artifact.get("passed") is True and all((
            artifact.get("successful_requests") == 5,
            artifact.get("failed_requests") == 0,
            outcome.get("provider_blocked") is False,
        )):
            return True, None, digest
        if (
            outcome.get("provider_blocked") is True
            and outcome.get("reason") == "provider_capacity_unavailable"
            and artifact.get("failed_requests", 0) > 0
        ):
            return False, "provider_outage", digest
        return False, "invalid_artifact", digest
    except (KeyError, OSError, TypeError, UnicodeError, json.JSONDecodeError, ValueError):
        return False, "invalid_artifact", None


def _collect_graph_traces(rows: list[dict]):
    events_by_trace: dict[str, list[dict]] = {}
    candidate_ids: set[str] = set()
    orphan_error = False
    for event in rows:
        trace_id = event.get("trace_id")
        production = event.get("execution_context") == "production"
        valid_trace_id = isinstance(trace_id, str) and bool(trace_id.strip())
        if production and (_error_like(event) or _graph_error_like(event)) and not valid_trace_id:
            orphan_error = True
        if not valid_trace_id:
            continue
        events_by_trace = {
            **events_by_trace,
            trace_id: [*events_by_trace.get(trace_id, ()), event],
        }
        graph_event = event.get("event") == "graph_retrieval"
        if production and graph_event and (
            event.get("routed") is True or _graph_error_like(event)
        ):
            candidate_ids = candidate_ids | {trace_id}
    return events_by_trace, candidate_ids, orphan_error


def _eligible_trace_ids(events_by_trace, candidate_ids: set[str]) -> set[str]:
    return {
        trace_id
        for trace_id in candidate_ids
        if any(
            event.get("event") == "pilot_request_evidence"
            and event.get("route") == "graph_relational"
            for event in events_by_trace[trace_id]
        )
    }


def _core_trace_rows(events: list[dict]):
    counts = Counter(event.get("event") for event in events)
    graph = [event for event in events if event.get("event") == "graph_retrieval"]
    evidence = [
        event for event in events if event.get("event") == "pilot_request_evidence"
    ]
    completed = [event for event in events if event.get("event") == "rag_end"]
    return counts, graph, evidence, completed


def _cardinality_valid(core, candidate: dict, started: datetime, now: datetime):
    counts, graph, evidence, completed = core
    rows = graph + evidence + completed
    return all((
        counts["graph_retrieval"] == 1,
        counts["pilot_request_evidence"] == 1,
        counts["rag_end"] == 1,
        all(_event_time_valid(event, started, now) for event in rows),
        all(event.get("execution_context") == "production" for event in rows),
        all(
            event.get("runtime_identity_sha256")
            == candidate["runtime_identity_sha256"]
            for event in rows
        ),
    ))


def _graph_contract(graph: dict) -> bool:
    return all((
        graph.get("routed") is True,
        graph.get("route_scope") == "relational",
        type(graph.get("edge_count")) is int,
        0 < graph.get("edge_count", 0) <= 50,
        type(graph.get("hydrated_count")) is int,
        graph.get("hydrated_count", -1) >= 0,
        type(graph.get("max_hops")) is int,
        0 < graph.get("max_hops", 0) <= 2,
        graph.get("edge_limit") == 50,
        not _graph_error_like(graph),
    ))


def _result_contract(evidence: dict, completed: dict) -> bool:
    status = evidence.get("graph_result_status")
    if status == "valid":
        low_confidence = evidence.get("low_confidence")
        return all((
            evidence.get("completion_outcome") == "answered",
            evidence.get("refusal_reason_code") is None,
            evidence.get("refusal_template_passed") is False,
            type(low_confidence) is bool,
            evidence.get("owner_review_required") is low_confidence,
            completed.get("refusal") is False,
            evidence.get("graph_evidence_count", 0) > 0,
            evidence.get("rendered_citation_count", 0) > 0,
            evidence.get("graph_citation_count", 0) > 0,
        ))
    reason = evidence.get("refusal_reason_code")
    return all((
        status == "safe_refusal",
        evidence.get("completion_outcome") == "refused",
        reason in APPROVED_GRAPH_REFUSALS,
        evidence.get("refusal_template_passed") is True,
        evidence.get("low_confidence") is True,
        evidence.get("owner_review_required") is True,
        completed.get("refusal") is True,
        completed.get("refusal_reason") == reason,
        evidence.get("rendered_citation_count") == 0,
        evidence.get("graph_citation_count") == 0,
        evidence.get("final_generations") == 0,
    ))


def _budget_valid(evidence: dict, graph: dict, limits: dict) -> bool:
    try:
        return all((
            evidence.get("graph_edges") == graph.get("edge_count"),
            evidence.get("graph_max_hops") == graph.get("max_hops"),
            evidence.get("graph_evidence_count", 0)
            <= graph.get("hydrated_count", -1),
            _decimal(evidence["final_latency_ms"]) <= limits["max_latency"],
            _decimal(evidence["request_deadline_ms"]) == limits["deadline_ms"],
            _decimal(evidence["estimated_cost"]) <= limits["max_cost"],
            evidence.get("provider_retries") == 0,
            type(evidence.get("final_generations")) is int,
            0 <= evidence.get("final_generations", -1) <= 1,
        ))
    except (KeyError, TypeError, ValueError):
        return False


def _evaluate_trace(events: list[dict], candidate: dict, limits: dict) -> dict:
    core = _core_trace_rows(events)
    if not _cardinality_valid(
        core, candidate, limits["started"], limits["now"]
    ):
        return {name: False for name in CHECKS}
    _, graph_rows, evidence_rows, completed_rows = core
    graph, evidence, completed = graph_rows[0], evidence_rows[0], completed_rows[0]
    graph_valid = _graph_contract(graph)
    result_valid = _result_contract(evidence, completed)
    allowed_fields = set(evidence) <= PILOT_EVIDENCE_FIELDS
    return {
        "runtime_identity": graph_valid,
        "security": evidence.get("security_passed") is True,
        "citation_structure": (
            evidence.get("citation_structure_passed") is True and result_valid
        ),
        "provenance": all((
            evidence.get("provenance_passed") is True,
            result_valid,
            type(evidence.get("graph_evidence_count")) is int,
        )),
        "budgets": graph_valid and _budget_valid(evidence, graph, limits),
        "provider_errors": not _provider_failure(events, evidence),
        "leakage": evidence.get("leakage_detected") is False and allowed_fields,
    }


def _trace_limits(window: dict, now: datetime) -> dict:
    budget = window["pilot_budget"]
    candidate = window["expected_runtime"]["candidate"]
    return {
        "started": _timestamp(window["started_at"]),
        "now": now,
        "max_latency": _decimal(budget["max_final_latency_ms"]),
        "max_cost": _decimal(budget["max_estimated_cost"]),
        "deadline_ms": _decimal(candidate["request_deadline_seconds"]) * 1000,
    }


def _trace_checks(rows: list[dict], window: dict, now: datetime, parse_error: bool):
    events_by_trace, candidate_ids, orphan_error = _collect_graph_traces(rows)
    eligible_ids = _eligible_trace_ids(events_by_trace, candidate_ids)
    review_selection = select_review_cases(events_by_trace, candidate_ids)
    checks = {
        **{name: True for name in CHECKS},
        "runtime_identity": (
            candidate_ids == eligible_ids
            and len(eligible_ids) >= window["minimum_eligible_requests"]
        ),
        "provider_errors": not parse_error and not orphan_error,
    }
    candidate = window["expected_runtime"]["candidate"]
    limits = _trace_limits(window, now)
    for trace_id in eligible_ids:
        result = _evaluate_trace(events_by_trace[trace_id], candidate, limits)
        checks = {name: checks[name] and result[name] for name in CHECKS}
    if candidate_ids != eligible_ids:
        checks = {name: False for name in CHECKS}
    return checks, eligible_ids, review_selection


def _review_result_status(
    path: Path | None,
    *,
    window: dict,
    base: Path,
    trace_sha256: str,
    required: dict[str, str],
):
    if path is None:
        return False, "missing", None
    try:
        artifact, raw = _load_json(path)
    except (OSError, TypeError, UnicodeError, json.JSONDecodeError, ValueError):
        return False, "invalid", None
    digest = _sha256(raw)
    commit = window["expected_runtime"]["candidate"]["git_sha"]
    contract = _review_contract_details(window, base, commit)
    if contract is None:
        return False, "invalid", digest
    valid = review_result_valid(
        artifact,
        contract=contract,
        source_commit=commit,
        trace_sha256=trace_sha256,
        required=required,
    )
    return valid, None if valid else "invalid", digest


def _trace_evidence(rows, window, now, parse_error):
    try:
        return _trace_checks(rows, window, now, parse_error)
    except (KeyError, TypeError, ValueError):
        return {name: False for name in CHECKS}, set(), {}


def _pilot_decision(
    passed: bool,
    automated_passed: bool,
    review_reason: str | None,
    provider_reason: str | None,
) -> str:
    if passed:
        return "pending_owner_acceptance"
    if automated_passed and review_reason == "missing":
        return "pending_review"
    return "inconclusive" if provider_reason == "provider_outage" else "rejected"


def _artifact_payload(evidence: dict) -> dict:
    checks = evidence["checks"]
    trace_ids = evidence["trace_ids"]
    review_required = evidence["review_required"]
    return {
        "schema": "graph-retrieval-production-pilot-gate-v1",
        "passed": evidence["passed"],
        "decision": evidence["decision"],
        "automated_checks_passed": evidence["automated_passed"],
        "review_result_valid": evidence["review_valid"],
        "review_result_reason": evidence["review_reason"],
        "review_required_case_count": len(review_required),
        "review_required_trace_id_sha256": sorted(review_required),
        "review_result_sha256": evidence["review_sha256"],
        "default_rollout_authorized": False,
        "evaluated_at": evidence["now"].isoformat().replace("+00:00", "Z"),
        "eligible_trace_count": len(trace_ids),
        "checks": {name: checks.get(name) is True for name in CHECKS},
        "trace_id_sha256": sorted(
            hashlib.sha256(trace_id.encode("utf-8")).hexdigest()
            for trace_id in trace_ids
        ),
        **evidence["artifact_hashes"],
        "provider_smoke_valid": evidence["provider_valid"],
        "provider_smoke_reason": evidence["provider_reason"],
    }


def build_artifact(
    window_path: Path,
    state_path: Path,
    health_path: Path,
    trace_path: Path,
    provider_smoke_path: Path,
    review_result_path: Path | None = None,
    *,
    now: datetime | None = None,
) -> dict:
    now = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
    window, window_raw = _load_json(window_path)
    state, state_raw = _load_json(state_path)
    health, health_raw = _load_json(health_path)
    rows, trace_raw, parse_error = _read_trace(trace_path)
    provider = _provider_smoke_status(
        provider_smoke_path, window, window_path.parent
    )
    window_sha256 = _sha256(window_raw)
    base_valid = all((
        _window_valid(window, now, window_path.parent),
        _state_valid(state, window, window_sha256, state_path.parent),
        _health_valid(health, window.get("expected_runtime", {}), now),
    ))
    checks, trace_ids, required = _trace_evidence(rows, window, now, parse_error)
    checks = {
        **checks,
        "runtime_identity": base_valid and checks["runtime_identity"],
    }
    automated = provider[0] and all(checks.get(name) is True for name in CHECKS)
    trace_sha256 = _sha256(trace_raw)
    review = _review_result_status(
        review_result_path, window=window, base=window_path.parent,
        trace_sha256=trace_sha256, required=required,
    )
    passed = automated and review[0]
    return _artifact_payload({
        "passed": passed, "automated_passed": automated, "review_valid": review[0],
        "review_reason": review[1], "review_sha256": review[2], "checks": checks,
        "trace_ids": trace_ids, "review_required": required, "now": now,
        "provider_valid": provider[0], "provider_reason": provider[1],
        "decision": _pilot_decision(passed, automated, review[1], provider[1]),
        "artifact_hashes": {
            "window_sha256": window_sha256, "state_sha256": _sha256(state_raw),
            "health_capture_sha256": _sha256(health_raw), "trace_sha256": trace_sha256,
            "provider_smoke_sha256": provider[2],
        },
    })


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--window", type=Path, required=True)
    parser.add_argument("--state", type=Path, required=True)
    parser.add_argument("--health-capture", type=Path, required=True)
    parser.add_argument("--trace", type=Path, required=True)
    parser.add_argument("--provider-smoke", type=Path, required=True)
    parser.add_argument("--review-result", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args(argv)
    try:
        artifact = build_artifact(
            args.window,
            args.state,
            args.health_capture,
            args.trace,
            args.provider_smoke,
            args.review_result,
        )
    except (
        KeyError,
        OSError,
        TypeError,
        UnicodeError,
        json.JSONDecodeError,
        ValueError,
    ):
        artifact = {
            "schema": "graph-retrieval-production-pilot-gate-v1",
            "passed": False,
            "decision": "rejected",
            "checks": {name: False for name in CHECKS},
            "eligible_trace_count": 0,
            "trace_id_sha256": [],
            "automated_checks_passed": False,
            "review_result_valid": False,
            "review_required_case_count": 0,
            "review_required_trace_id_sha256": [],
            "default_rollout_authorized": False,
            "provider_smoke_valid": False,
            "provider_smoke_reason": "invalid_evidence",
        }
    _atomic_json(args.output, artifact)
    return 0 if artifact["passed"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
