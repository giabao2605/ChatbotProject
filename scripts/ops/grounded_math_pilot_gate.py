"""Build a metadata-only, fail-closed Grounded Math production pilot gate."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import tempfile
from collections import Counter, defaultdict
from datetime import datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation
from pathlib import Path


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
HEALTH_BINDINGS = RUNTIME_BINDINGS
PILOT_EVIDENCE_FIELDS = frozenset({
    "ts",
    "event",
    "trace_id",
    "execution_context",
    "runtime_identity_sha256",
    "route",
    "calculation_result_status",
    "security_passed",
    "citation_structure_passed",
    "provenance_passed",
    "leakage_detected",
    "calculations",
    "final_latency_ms",
    "request_deadline_ms",
    "estimated_cost",
    "provider_retries",
    "final_generations",
})


def _sha256(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def _is_sha256(value: object) -> bool:
    text = str(value or "")
    return len(text) == 64 and all(character in "0123456789abcdef" for character in text)


def _timestamp(value: object) -> datetime:
    text = str(value or "").strip()
    parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        raise ValueError("timestamp_timezone_missing")
    return parsed.astimezone(timezone.utc)


def _decimal(value: object) -> Decimal:
    if isinstance(value, bool):
        raise ValueError("numeric_value_invalid")
    try:
        result = Decimal(str(value))
    except (InvalidOperation, ValueError) as exc:
        raise ValueError("numeric_value_invalid") from exc
    if not result.is_finite() or result < 0:
        raise ValueError("numeric_value_invalid")
    return result


def _load_json(path: Path) -> tuple[dict, bytes]:
    raw = path.read_bytes()
    value = json.loads(raw)
    if not isinstance(value, dict):
        raise ValueError("json_object_required")
    return value, raw


def _runtime_identity(runtime: dict) -> str:
    payload = {
        name: runtime[name]
        for name in RUNTIME_BINDINGS
        if name != "runtime_identity_sha256"
    }
    raw = json.dumps(
        payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")
    return _sha256(raw)


def _material(reference: object, base: Path) -> tuple[dict, str] | None:
    if not isinstance(reference, dict) or not _is_sha256(reference.get("sha256")):
        return None
    path = Path(str(reference.get("path") or ""))
    if not path.is_absolute():
        path = base / path
    try:
        value, raw = _load_json(path)
    except (OSError, UnicodeError, json.JSONDecodeError, ValueError):
        return None
    digest = _sha256(raw)
    if digest != reference["sha256"]:
        return None
    return value, digest


def _math_only_flags(value: object) -> bool:
    return isinstance(value, dict) and value == {
        name: name == "RAG_GROUNDED_MATH_ENABLED" for name in FEATURE_FLAGS
    }


def _all_off_flags(value: object) -> bool:
    return isinstance(value, dict) and value == {name: False for name in FEATURE_FLAGS}


def _runtime_valid(runtime: object, *, pilot: bool) -> bool:
    if not isinstance(runtime, dict) or any(name not in runtime for name in RUNTIME_BINDINGS):
        return False
    if not all(str(runtime.get(name) or "").strip() for name in (
        "git_sha", "deployment_id", "snapshot_fingerprint",
        "provider_configuration_sha256", "qdrant_collection", "sql_database",
        "restore_evidence_sha256",
    )):
        return False
    if not all(_is_sha256(runtime.get(name)) for name in (
        "snapshot_fingerprint", "provider_configuration_sha256",
        "restore_evidence_sha256",
    )):
        return False
    try:
        deadline = _decimal(runtime["request_deadline_seconds"])
        identity_valid = (
            _is_sha256(runtime["runtime_identity_sha256"])
            and runtime["runtime_identity_sha256"] == _runtime_identity(runtime)
        )
    except ValueError:
        return False
    if deadline <= 0 or not identity_valid:
        return False
    if pilot:
        return (
            runtime["activation_profile"] == "selective"
            and _math_only_flags(runtime["feature_flags"])
            and _is_sha256(runtime["activation_bundle_sha256"])
        )
    return (
        runtime["activation_profile"] == "all_off"
        and _all_off_flags(runtime["feature_flags"])
        and runtime["activation_bundle_sha256"] in {None, ""}
    )


def _formal_budget_maxima(sources: object, base: Path, commit: str):
    if not isinstance(sources, dict) or sources.get("source_commit") != commit:
        return None
    pairs = sources.get("pairs")
    if not isinstance(pairs, list) or len(pairs) != 3:
        return None
    latencies = []
    costs = []
    identities = set()
    for pair in pairs:
        if not isinstance(pair, dict):
            return None
        loaded_eval = _material(pair.get("eval"), base)
        loaded_gate = _material(pair.get("gate"), base)
        if loaded_eval is None or loaded_gate is None:
            return None
        evaluation, eval_sha256 = loaded_eval
        gate, gate_sha256 = loaded_gate
        identities.add((eval_sha256, gate_sha256))
        limits = gate.get("limits")
        inputs = gate.get("inputs")
        cases = evaluation.get("cases")
        if not all(
            (
                evaluation.get("schema") == "rag-labeled-eval-v4",
                evaluation.get("git_sha") == commit,
                isinstance(cases, list) and bool(cases),
                gate.get("schema") == "retrieval-intelligence-gate-v1",
                gate.get("stage") == "grounded_math",
                gate.get("passed") is True,
                isinstance(limits, dict),
                limits.get("max_calculations_per_query") == 1,
                limits.get("max_latency_ratio") == 1.25,
                limits.get("max_cost_ratio") == 1.5,
                isinstance(inputs, dict),
                inputs.get("baseline_eval_sha256") == eval_sha256,
            )
        ):
            return None
        try:
            latencies.extend(_decimal(case["latency_ms"]) for case in cases)
            costs.extend(_decimal(case["estimated_cost"]) for case in cases)
        except (KeyError, TypeError, ValueError):
            return None
    if len(identities) != 3:
        return None
    return max(latencies), max(costs)


def _window_valid(window: dict, now: datetime, base: Path) -> bool:
    try:
        started = _timestamp(window["started_at"])
        minimum_until = _timestamp(window["minimum_runtime_until"])
        runtime = window["expected_runtime"]
        pilot = runtime["pilot"]
        main = runtime["main"]
        budget = window["pilot_budget"]
        sources = window["formal_budget_sources"]
        provider_smoke = window["provider_smoke"]
        latency_multiplier = _decimal(sources["latency_multiplier"])
        cost_multiplier = _decimal(sources["cost_multiplier"])
        max_latency = _decimal(budget["max_final_latency_ms"])
        max_cost = _decimal(budget["max_estimated_cost"])
    except (KeyError, TypeError, ValueError):
        return False
    if not _runtime_valid(pilot, pilot=True) or not _runtime_valid(main, pilot=False):
        return False
    maxima = _formal_budget_maxima(sources, base, str(pilot.get("git_sha") or ""))
    if maxima is None:
        return False
    baseline_latency, baseline_cost = maxima
    return all(
        (
            window.get("schema") == "math-lan-pilot-window-v1",
            minimum_until - started >= timedelta(days=7),
            now >= minimum_until,
            type(window.get("minimum_eligible_requests")) is int,
            window.get("minimum_eligible_requests", 0) >= 100,
            window.get("required_automated_checks") == list(CHECKS),
            pilot["git_sha"] == main["git_sha"] == sources.get("source_commit"),
            type(budget.get("max_calculations")) is int,
            budget.get("max_calculations") == 1,
            type(budget.get("max_provider_retries")) is int,
            budget.get("max_provider_retries") == 0,
            type(budget.get("max_final_generations")) is int,
            budget.get("max_final_generations") == 1,
            latency_multiplier == Decimal("1.25"),
            cost_multiplier == Decimal("1.5"),
            max_latency == baseline_latency * latency_multiplier,
            max_cost == baseline_cost * cost_multiplier,
            isinstance(provider_smoke, dict),
            isinstance(provider_smoke.get("path"), str)
            and bool(provider_smoke.get("path").strip()),
            _is_sha256(provider_smoke.get("sha256")),
        )
    )


def _state_valid(
    state: dict, window: dict, window_sha256: str, base: Path
) -> bool:
    bundle = _material(state.get("activation_bundle"), base)
    restore = _material(state.get("restore_receipt"), base)
    expected = window.get("expected_runtime")
    if bundle is None or restore is None or not isinstance(expected, dict):
        return False
    bundle_value = bundle[0]
    restore_value = restore[0]
    pilot = expected.get("pilot", {})
    return all(
        (
            state.get("schema") == "math-lan-pilot-process-state-v1",
            state.get("window_sha256") == window_sha256,
            state.get("expected_runtime") == expected,
            bundle[1] == pilot.get("activation_bundle_sha256"),
            bundle_value.get("schema") == "rag-activation-bundle-v1",
            bundle_value.get("source_commit") == pilot.get("git_sha"),
            bundle_value.get("activation_profile") == "selective",
            _math_only_flags(bundle_value.get("feature_flags")),
            restore[1] == pilot.get("restore_evidence_sha256"),
            restore[1] == expected.get("main", {}).get("restore_evidence_sha256"),
            _restore_receipt_valid(restore_value, pilot),
        )
    )


def _restore_receipt_valid(receipt: object, pilot: dict) -> bool:
    if not isinstance(receipt, dict):
        return False
    sql = receipt.get("sql")
    qdrant = receipt.get("qdrant")
    history = receipt.get("execution_history")
    if not all(isinstance(value, dict) for value in (sql, qdrant, history)):
        return False
    reconciliation = history.get("reconciliation")
    if not isinstance(reconciliation, dict):
        return False
    target_database = receipt.get("target_database")
    target_collection = receipt.get("target_collection")
    expected_points = qdrant.get("expected_points")
    hashes_match = all(
        _is_sha256(reconciliation.get(source))
        and reconciliation.get(source) == reconciliation.get(target)
        for source, target in (
            ("sql_source_identity_sha256", "sql_target_identity_sha256"),
            ("qdrant_source_content_sha256", "qdrant_target_content_sha256"),
            ("qdrant_source_config_sha256", "qdrant_target_config_sha256"),
        )
    )
    return all(
        (
            receipt.get("schema") == "backup-restore-drill-v1",
            receipt.get("git_sha") == pilot.get("git_sha"),
            receipt.get("passed") is True,
            receipt.get("error_type") is None,
            receipt.get("automatic_cleanup") is False,
            target_database == pilot.get("sql_database"),
            target_collection == pilot.get("qdrant_collection"),
            target_database != receipt.get("source_database"),
            target_collection != receipt.get("source_collection"),
            "_RestoreTest_RAGPilot_" in str(target_database or ""),
            "_RestoreTest_RAGPilot_" in str(target_collection or ""),
            receipt.get("snapshot_fingerprint")
            == pilot.get("snapshot_fingerprint"),
            sql.get("target_database") == target_database,
            sql.get("state_desc") == "ONLINE",
            sql.get("has_db_access") is True,
            sql.get("restored") is True,
            qdrant.get("target_collection") == target_collection,
            qdrant.get("status") == "green",
            qdrant.get("restored") is True,
            type(expected_points) is int and expected_points > 0,
            qdrant.get("source_points") == expected_points,
            qdrant.get("target_points") == expected_points,
            history.get("mode") == "release-candidate-read-only-reconciliation",
            history.get("new_restore_mutation_sent") is False,
            reconciliation.get("capture_completed") is True,
            reconciliation.get("qdrant_target_exists") is True,
            reconciliation.get("qdrant_target_status") == "green",
            reconciliation.get("qdrant_source_points") == expected_points,
            reconciliation.get("qdrant_target_points") == expected_points,
            hashes_match,
        )
    )


def _health_valid(health: dict, expected: dict, now: datetime) -> bool:
    try:
        checked_at = _timestamp(health["checked_at"])
        pilot = health["pilot"]
        main = health["main"]
    except (KeyError, TypeError, ValueError):
        return False
    if not isinstance(pilot, dict) or not isinstance(main, dict):
        return False
    fresh = timedelta(0) <= now - checked_at <= timedelta(minutes=15)
    bindings_match = all(
        actual.get(name) == expected[arm].get(name)
        for arm, actual in (("pilot", pilot), ("main", main))
        for name in HEALTH_BINDINGS
    )
    return all(
        (
            health.get("schema") == "math-lan-pilot-health-capture-v1",
            fresh,
            pilot.get("status") == main.get("status") == "ok",
            pilot.get("rag_loaded") is True,
            main.get("rag_loaded") is True,
            bindings_match,
        )
    )


def _read_trace(path: Path) -> tuple[list[dict], bytes, bool]:
    raw = path.read_bytes()
    rows = []
    parse_error = False
    for line in raw.splitlines():
        if not line.strip():
            continue
        try:
            value = json.loads(line)
        except (json.JSONDecodeError, UnicodeDecodeError):
            parse_error = True
            continue
        if not isinstance(value, dict):
            parse_error = True
            continue
        rows.append(value)
    return rows, raw, parse_error


def _provider_smoke_status(
    path: Path, window: dict, base: Path
) -> tuple[bool, str | None, str | None]:
    """Validate a predeclared smoke and classify provider outage separately."""
    try:
        artifact, raw = _load_json(path)
        digest = _sha256(raw)
        declaration = window["provider_smoke"]
        declared_path = Path(str(declaration["path"]))
        if not declared_path.is_absolute():
            declared_path = base / declared_path
        if declared_path.resolve() != path.resolve() or declaration["sha256"] != digest:
            return False, "invalid_evidence_binding", digest
        pilot = window["expected_runtime"]["pilot"]
        completed = _timestamp(artifact["completed_at"])
        started = _timestamp(window["started_at"])
        outcome = artifact["provider_outcome"]
        identity_valid = all(
            (
                artifact.get("schema") == "provider-smoke-v1",
                artifact.get("request_count") == 5,
                artifact.get("provider_retries") == 0,
                artifact.get("max_attempts_per_request") == 1,
                artifact.get("provider_configuration_sha256")
                == pilot["provider_configuration_sha256"],
                isinstance(outcome, dict),
            )
        )
        if not identity_valid:
            return False, "invalid_artifact", digest
        if not timedelta(0) < started - completed <= timedelta(minutes=30):
            return False, "stale_artifact", digest
        if artifact.get("passed") is True and all(
            (
                artifact.get("successful_requests") == 5,
                artifact.get("failed_requests") == 0,
                outcome.get("provider_blocked") is False,
            )
        ):
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


def _event_time_valid(event: dict, started: datetime, now: datetime) -> bool:
    try:
        timestamp = _timestamp(event["ts"])
    except (KeyError, ValueError):
        return False
    return started <= timestamp <= now


def _error_like(event: dict) -> bool:
    name = str(event.get("event") or "").casefold()
    return name in {"rag_error", "provider_retry", "llm_retry"} or (
        name == "external_ai_call"
        and (
            str(event.get("status") or "").casefold() not in {"ok", "success"}
            or event.get("ok") is False
            or bool(event.get("error"))
            or event.get("has_error") is True
            or event.get("provider_retries", 0) != 0
        )
    )


def _provider_failure(events: list[dict], evidence: dict) -> bool:
    if evidence.get("provider_retries") != 0:
        return True
    for event in events:
        name = str(event.get("event") or "").casefold()
        if _error_like(event) or event.get("retry_attempted") is True:
            return True
        fallback = (
            event.get("fallback") is True
            or event.get("fallback_used") is True
            or bool(event.get("fallback_reason"))
        )
        declared = (
            event.get("fallback_declared") is True
            or event.get("declared_fallback") is True
        )
        backend = str(event.get("backend") or "").casefold()
        if (
            name == "rerank" or "hybrid" in name or "hybrid" in backend
        ) and fallback and not declared:
            return True
    return False


def _trace_checks(rows: list[dict], window: dict, now: datetime, parse_error: bool):
    started = _timestamp(window["started_at"])
    events_by_trace: dict[str, list[dict]] = defaultdict(list)
    candidate_ids = set()
    orphan_production_error = False
    for event in rows:
        trace_id = event.get("trace_id")
        if (
            event.get("execution_context") == "production"
            and _error_like(event)
            and (not isinstance(trace_id, str) or not trace_id.strip())
        ):
            orphan_production_error = True
        if not isinstance(trace_id, str) or not trace_id.strip():
            continue
        events_by_trace[trace_id].append(event)
        if event.get("execution_context") != "production":
            continue
        if event.get("event") == "grounded_math_generation" or (
            event.get("event") == "pilot_request_evidence"
            and event.get("route") == "calculation"
        ):
            candidate_ids.add(trace_id)

    eligible_ids = {
        trace_id
        for trace_id in candidate_ids
        if any(
            event.get("event") == "pilot_request_evidence"
            and event.get("route") == "calculation"
            and event.get("calculation_result_status") == "valid"
            for event in events_by_trace[trace_id]
        )
    }
    runtime_identity = (
        candidate_ids == eligible_ids
        and len(eligible_ids) >= window["minimum_eligible_requests"]
    )
    security = citation = provenance = budgets = leakage = True
    provider_errors = not parse_error and not orphan_production_error
    pilot_budget = window["pilot_budget"]
    max_latency = _decimal(pilot_budget["max_final_latency_ms"])
    max_cost = _decimal(pilot_budget["max_estimated_cost"])
    deadline_ms = _decimal(
        window["expected_runtime"]["pilot"]["request_deadline_seconds"]
    ) * 1000

    for trace_id in eligible_ids:
        events = events_by_trace[trace_id]
        counts = Counter(event.get("event") for event in events)
        evidence_rows = [
            event for event in events if event.get("event") == "pilot_request_evidence"
        ]
        core_events = [
            event
            for event in events
            if event.get("event") in {
                "grounded_math_generation", "pilot_request_evidence", "rag_end"
            }
        ]
        cardinality_valid = (
            counts["grounded_math_generation"] == 1
            and counts["pilot_request_evidence"] == 1
            and counts["rag_end"] == 1
            and all(_event_time_valid(event, started, now) for event in core_events)
            and all(
                event.get("execution_context") == "production"
                for event in core_events
            )
            and all(
                event.get("runtime_identity_sha256")
                == window["expected_runtime"]["pilot"][
                    "runtime_identity_sha256"
                ]
                for event in core_events
            )
        )
        generation_rows = [
            event for event in events if event.get("event") == "grounded_math_generation"
        ]
        generation_valid = all(
            type(event.get("calculations")) is int
            and event.get("calculations") == 1
            and event.get("calculation_result_status") == "valid"
            and event.get("validation_status") == "passed"
            for event in generation_rows
        )
        cardinality_valid = cardinality_valid and generation_valid
        runtime_identity = runtime_identity and cardinality_valid
        if len(evidence_rows) != 1:
            security = citation = provenance = budgets = leakage = False
            provider_errors = False
            continue
        evidence = evidence_rows[0]
        runtime_identity = runtime_identity and evidence.get("route") == "calculation"
        security = security and evidence.get("security_passed") is True
        citation = citation and evidence.get("citation_structure_passed") is True
        provenance = provenance and evidence.get("provenance_passed") is True
        leakage = (
            leakage
            and evidence.get("leakage_detected") is False
            and set(evidence) <= PILOT_EVIDENCE_FIELDS
        )
        try:
            budget_valid = (
                type(evidence.get("calculations")) is int
                and evidence.get("calculations") == 1
                and type(evidence.get("provider_retries")) is int
                and evidence.get("provider_retries") == 0
                and type(evidence.get("final_generations")) is int
                and 0 <= evidence.get("final_generations") <= 1
                and _decimal(evidence.get("final_latency_ms")) <= max_latency
                and _decimal(evidence.get("final_latency_ms"))
                <= _decimal(evidence.get("request_deadline_ms"))
                and _decimal(evidence.get("request_deadline_ms")) == deadline_ms
                and _decimal(evidence.get("estimated_cost")) <= max_cost
            )
        except ValueError:
            budget_valid = False
        budgets = budgets and budget_valid
        provider_errors = provider_errors and not _provider_failure(events, evidence)

    return {
        "runtime_identity": runtime_identity,
        "security": security and bool(eligible_ids),
        "citation_structure": citation and bool(eligible_ids),
        "provenance": provenance and bool(eligible_ids),
        "budgets": budgets and bool(eligible_ids),
        "provider_errors": provider_errors and bool(eligible_ids),
        "leakage": leakage and bool(eligible_ids),
    }, eligible_ids


def build_artifact(
    window_path: Path,
    state_path: Path,
    health_path: Path,
    trace_path: Path,
    provider_smoke_path: Path,
    *,
    now: datetime | None = None,
) -> dict:
    now = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
    window, window_raw = _load_json(window_path)
    state, state_raw = _load_json(state_path)
    health, health_raw = _load_json(health_path)
    rows, trace_raw, parse_error = _read_trace(trace_path)
    provider_smoke_valid, provider_smoke_reason, provider_smoke_sha256 = _provider_smoke_status(
        provider_smoke_path, window, window_path.parent
    )
    window_sha256 = _sha256(window_raw)
    base_runtime_valid = (
        _window_valid(window, now, window_path.parent)
        and _state_valid(state, window, window_sha256, state_path.parent)
        and _health_valid(health, window.get("expected_runtime", {}), now)
    )
    try:
        trace_checks, trace_ids = _trace_checks(rows, window, now, parse_error)
    except (KeyError, TypeError, ValueError):
        trace_checks = {name: False for name in CHECKS}
        trace_ids = set()
    trace_checks["runtime_identity"] = (
        base_runtime_valid and trace_checks["runtime_identity"]
    )
    passed = provider_smoke_valid and all(
        trace_checks.get(name) is True for name in CHECKS
    )
    decision = (
        "pending_review"
        if passed
        else "inconclusive"
        if provider_smoke_reason == "provider_outage"
        or not trace_checks.get("provider_errors")
        else "rejected"
    )
    return {
        "schema": "grounded-math-production-pilot-gate-v1",
        "passed": passed,
        "decision": decision,
        "evaluated_at": now.isoformat().replace("+00:00", "Z"),
        "eligible_trace_count": len(trace_ids),
        "checks": {name: trace_checks.get(name) is True for name in CHECKS},
        "trace_id_sha256": sorted(
            hashlib.sha256(trace_id.encode("utf-8")).hexdigest()
            for trace_id in trace_ids
        ),
        "window_sha256": window_sha256,
        "state_sha256": _sha256(state_raw),
        "health_capture_sha256": _sha256(health_raw),
        "trace_sha256": _sha256(trace_raw),
        "provider_smoke_sha256": provider_smoke_sha256,
        "provider_smoke_valid": provider_smoke_valid,
        "provider_smoke_reason": provider_smoke_reason,
    }


def _atomic_json(path: Path, value: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    raw = (json.dumps(value, ensure_ascii=False, indent=2) + "\n").encode("utf-8")
    descriptor, temporary = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    try:
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(raw)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    except BaseException:
        try:
            os.unlink(temporary)
        except FileNotFoundError:
            pass
        raise


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--window", type=Path, required=True)
    parser.add_argument("--state", type=Path, required=True)
    parser.add_argument("--health-capture", type=Path, required=True)
    parser.add_argument("--trace", type=Path, required=True)
    parser.add_argument("--provider-smoke", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args(argv)
    try:
        artifact = build_artifact(
            args.window,
            args.state,
            args.health_capture,
            args.trace,
            args.provider_smoke,
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
            "schema": "grounded-math-production-pilot-gate-v1",
            "passed": False,
            "decision": "rejected",
            "checks": {name: False for name in CHECKS},
            "eligible_trace_count": 0,
            "trace_id_sha256": [],
            "provider_smoke_valid": False,
            "provider_smoke_reason": "invalid_evidence",
        }
    _atomic_json(args.output, artifact)
    return 0 if artifact["passed"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
