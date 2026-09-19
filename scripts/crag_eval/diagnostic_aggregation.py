"""Aggregate singleton CRAG runs without exposing their raw content."""

from __future__ import annotations

import hashlib
import math
import statistics
from collections import defaultdict
from typing import Any, Mapping, Sequence

from scripts.eval.crag_rollout_gate import compare_reports

MAX_PAIR_LATENCY_RATIO_SPREAD = 0.10
MAX_PAIR_COST_RATIO_SPREAD = 0.10
PAIR_ORDER = (
    ("pair-01", "candidate-first"),
    ("pair-02", "baseline-first"),
)
STAGES = (
    "retrieval",
    "parent_context",
    "rerank",
    "generation",
    "correction",
    "claim_repair",
)


def provider_failures(arm: Mapping[str, Any] | None) -> int:
    if not arm:
        return 0
    report = arm.get("eval") or {}
    direct = int(report.get("provider_failure_count") or 0)
    cases = sum(bool(row.get("provider_failure")) for row in report.get("cases") or [])
    trace = arm.get("trace") or {}
    errors = int(trace.get("error_event_count") or 0)
    fallbacks = int(trace.get("fallback_event_count") or 0)
    return max(direct, cases, errors + fallbacks)


def provider_retries(arm: Mapping[str, Any] | None) -> int:
    if not arm:
        return 0
    report = arm.get("eval") or {}
    trace = arm.get("trace") or {}
    return max(
        int(report.get("provider_retries") or 0),
        int(trace.get("retry_event_count") or 0),
    )


def _arm_metric(
    pairs: list[Mapping[str, Any]], arm: str, metric: str
) -> float | None:
    values = []
    for pair in pairs:
        payload = pair.get(arm)
        if not isinstance(payload, Mapping):
            continue
        latency = payload.get("latency") or {}
        if metric == "estimated_cost":
            value = latency.get(metric)
        else:
            value = (latency.get("stage_summary") or {}).get(metric, {}).get(
                "latency_p50_ms"
            )
        if isinstance(value, (int, float)) and not isinstance(value, bool):
            values.append(float(value))
    return statistics.median(values) if values else None


def _pair_ratios(
    pairs: list[Mapping[str, Any]], metric: str
) -> list[float]:
    ratios = []
    for pair in pairs:
        baseline = _arm_metric([pair], "baseline", metric)
        candidate = _arm_metric([pair], "candidate", metric)
        if baseline and candidate is not None:
            ratios.append(candidate / baseline)
    return ratios


def _ratio_spread(values: list[float]) -> float | None:
    return max(values) - min(values) if len(values) == len(PAIR_ORDER) else None


def _failed_checks(pair: Mapping[str, Any]) -> frozenset[str]:
    checks = (pair.get("gate") or {}).get("checks") or {}
    return frozenset(name for name, passed in checks.items() if not passed)


def _dominant_pair_stage(pair: Mapping[str, Any]) -> str:
    deltas = {}
    for stage in STAGES:
        baseline = _arm_metric([pair], "baseline", stage)
        candidate = _arm_metric([pair], "candidate", stage)
        if candidate is not None and candidate - (baseline or 0.0) > 0:
            deltas[stage] = candidate - (baseline or 0.0)
    return max(deltas, key=deltas.get) if deltas else "none"


def provider_failures_for_pair(pair: Mapping[str, Any]) -> int:
    return sum(provider_failures(pair.get(arm)) for arm in ("baseline", "candidate"))


def _ratio(candidate: float | None, baseline: float | None) -> float | None:
    return candidate / baseline if baseline and candidate is not None else None


def _stage_deltas(pairs: list[Mapping[str, Any]]) -> dict[str, float]:
    deltas = {}
    for stage in STAGES:
        baseline = _arm_metric(pairs, "baseline", stage)
        candidate = _arm_metric(pairs, "candidate", stage)
        if candidate is not None:
            deltas[stage] = candidate - (baseline or 0.0)
    return deltas


def _arm_order_consistent(
    complete: bool,
    gate_states: Sequence[bool],
    failed_check_sets: Sequence[frozenset[str]],
    dominant_stages: Sequence[str],
    latency_spread: float | None,
    cost_spread: float | None,
) -> bool:
    return bool(
        complete
        and len(set(gate_states)) == 1
        and len(set(failed_check_sets)) == 1
        and len(set(dominant_stages)) == 1
        and latency_spread is not None
        and latency_spread <= MAX_PAIR_LATENCY_RATIO_SPREAD
        and cost_spread is not None
        and cost_spread <= MAX_PAIR_COST_RATIO_SPREAD
    )


def _outcome_metrics(pairs: list[Mapping[str, Any]]) -> dict[str, Any]:
    failures = sum(provider_failures_for_pair(pair) for pair in pairs)
    retries = sum(
        provider_retries(pair.get(arm))
        for pair in pairs
        for arm in ("baseline", "candidate")
    )
    complete = len(pairs) == len(PAIR_ORDER) and all(
        isinstance(pair.get("baseline"), Mapping)
        and isinstance(pair.get("candidate"), Mapping)
        for pair in pairs
    )
    latency_ratios = _pair_ratios(pairs, "total")
    cost_ratios = _pair_ratios(pairs, "estimated_cost")
    latency_spread = _ratio_spread(latency_ratios)
    cost_spread = _ratio_spread(cost_ratios)
    latency_ratio = _ratio(
        _arm_metric(pairs, "candidate", "total"),
        _arm_metric(pairs, "baseline", "total"),
    )
    cost_ratio = _ratio(
        _arm_metric(pairs, "candidate", "estimated_cost"),
        _arm_metric(pairs, "baseline", "estimated_cost"),
    )
    gate_states = [bool((pair.get("gate") or {}).get("passed")) for pair in pairs]
    dominant_stages = [_dominant_pair_stage(pair) for pair in pairs]
    consistent = _arm_order_consistent(
        complete,
        gate_states,
        [_failed_checks(pair) for pair in pairs],
        dominant_stages,
        latency_spread,
        cost_spread,
    )
    return {
        "failures": failures,
        "retries": retries,
        "complete": complete,
        "latency_ratios": latency_ratios,
        "cost_ratios": cost_ratios,
        "latency_spread": latency_spread,
        "cost_spread": cost_spread,
        "latency_ratio": latency_ratio,
        "cost_ratio": cost_ratio,
        "gate_states": gate_states,
        "dominant_stages": dominant_stages,
        "consistent": consistent,
        "stage_deltas": _stage_deltas(pairs),
    }


def _target_met(metrics: Mapping[str, Any]) -> bool:
    return bool(
        not metrics["failures"]
        and not metrics["retries"]
        and metrics["consistent"]
        and metrics["complete"]
        and all(metrics["gate_states"])
        and metrics["latency_ratio"] is not None
        and metrics["latency_ratio"] <= 1.25
        and metrics["cost_ratio"] is not None
        and metrics["cost_ratio"] <= 1.5
    )


def _legacy_next_action(metrics: Mapping[str, Any]) -> str:
    if metrics["failures"] or metrics["retries"]:
        return "Wait for externally confirmed provider recovery before a new declaration."
    if metrics["complete"] and not metrics["consistent"]:
        return "Treat the delta as order-sensitive provider variance and use a new declaration."
    if not metrics["complete"]:
        return "Complete the predeclared diagnostic before choosing a code fix."
    return "Use the dominant stage to choose one root fix; keep both flags off."


def _dominant_overhead(positive: Mapping[str, float], unavailable: bool) -> str:
    if unavailable:
        return "unavailable"
    return max(positive, key=positive.get) if positive else "none"


def _diagnostic_outcome_payload(
    pairs: list[Mapping[str, Any]],
    source_commit: str,
    declaration_sha256: str,
    metrics: Mapping[str, Any],
) -> dict[str, Any]:
    target_met = _target_met(metrics)
    unavailable = not metrics["complete"] or bool(metrics["failures"])
    positive = {
        stage: value
        for stage, value in metrics["stage_deltas"].items()
        if value > 0
    }
    inconclusive = bool(
        metrics["failures"]
        or metrics["retries"]
        or not metrics["complete"]
        or not metrics["consistent"]
    )
    return {
        "schema": "crag-stage-latency-diagnostic-outcome-v2",
        "formal_evidence": False,
        "status": "inconclusive" if inconclusive else "passed" if target_met else "failed",
        "source_commit": source_commit,
        "declaration_sha256": declaration_sha256,
        "pair_count": len(pairs),
        "provider_failure_count": metrics["failures"],
        "provider_retry_count": metrics["retries"],
        "arm_order_consistent": metrics["consistent"],
        "pair_latency_ratios": metrics["latency_ratios"],
        "pair_cost_ratios": metrics["cost_ratios"],
        "latency_ratio_spread": metrics["latency_spread"],
        "cost_ratio_spread": metrics["cost_spread"],
        "dominant_pair_stages": metrics["dominant_stages"],
        "latency_ratio": None if unavailable else metrics["latency_ratio"],
        "cost_ratio": None if unavailable else metrics["cost_ratio"],
        "diagnostic_target_met": target_met,
        "stage_deltas_ms": {} if unavailable else metrics["stage_deltas"],
        "dominant_overhead_stage": _dominant_overhead(positive, unavailable),
        "pairs": [
            {"id": pair.get("id"), "gate_passed": bool((pair.get("gate") or {}).get("passed"))}
            for pair in pairs
        ],
        "formal_window_authorized": False,
        "feature_enablement_authorized": False,
        "next_action": _legacy_next_action(metrics),
    }


def build_diagnostic_outcome(
    pairs: list[Mapping[str, Any]],
    *,
    source_commit: str,
    declaration_sha256: str,
) -> dict[str, Any]:
    return _diagnostic_outcome_payload(
        pairs,
        source_commit,
        declaration_sha256,
        _outcome_metrics(pairs),
    )


def _number(value: object, *, name: str) -> float:
    if (
        not isinstance(value, (int, float))
        or isinstance(value, bool)
        or not math.isfinite(value)
        or value < 0
    ):
        raise ValueError(f"{name} must be a finite non-negative number")
    return float(value)


def _nearest_rank(values: Sequence[float], percentile: float) -> float:
    if not values:
        raise ValueError("latency aggregation requires at least one value")
    ordered = sorted(values)
    rank = max(1, math.ceil(len(ordered) * percentile))
    return ordered[min(rank, len(ordered)) - 1]


def validate_singleton_evaluation(
    case_id: str,
    arm: Mapping[str, Any],
) -> None:
    evaluation = arm.get("eval") or {}
    cases = evaluation.get("cases") or []
    if (
        evaluation.get("total_cases") != 1
        or len(cases) != 1
        or cases[0].get("id") != case_id
    ):
        raise ValueError("case arm must contain exactly the selected case")


def validate_singleton_arm(
    case_id: str,
    arm: Mapping[str, Any],
) -> None:
    validate_singleton_evaluation(case_id, arm)
    cases = (arm.get("eval") or {}).get("cases") or []
    trace_id = str(cases[0].get("trace_id") or "")
    system = (arm.get("trace") or {}).get("system_metrics") or {}
    latency = arm.get("latency") or {}
    traces = latency.get("traces") or []
    expected_hash = hashlib.sha256(trace_id.encode("utf-8")).hexdigest()
    if (
        not trace_id
        or system.get("query_count") != 1
        or latency.get("query_count") != 1
        or latency.get("parse_errors") != 0
        or len(traces) != 1
        or traces[0].get("trace_id_sha256") != expected_hash
    ):
        raise ValueError("case arm trace must contain exactly the selected case")


def _aggregate_evaluations(reports: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    cases = [case for report in reports for case in (report.get("cases") or [])]
    flags = [dict(report.get("feature_flags") or {}) for report in reports]
    if not flags or any(value != flags[0] for value in flags[1:]):
        raise ValueError("feature flags drifted between singleton case runs")
    confusion: dict[str, int] = defaultdict(int)
    for report in reports:
        for name, value in (report.get("outcome_confusion") or {}).items():
            confusion[str(name)] += int(value or 0)
    return {
        "total_cases": len(cases),
        "passed_cases": sum(bool(case.get("passed")) for case in cases),
        "provider_failure_count": sum(
            bool(case.get("provider_failure")) for case in cases
        ),
        "provider_retries": sum(
            int(report.get("provider_retries") or 0) for report in reports
        ),
        "total_estimated_cost": sum(
            _number(
                report.get("total_estimated_cost", 0),
                name="evaluation cost",
            )
            for report in reports
        ),
        "feature_flags": flags[0],
        "outcome_confusion": dict(confusion),
        "cases": cases,
    }


def _trace_ids(systems: Sequence[Mapping[str, Any]], field: str) -> list[str]:
    return [
        str(trace_id)
        for row in systems
        for trace_id in (row.get(field) or [])
    ]


def _aggregate_traces(reports: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    systems = [dict(report.get("system_metrics") or {}) for report in reports]
    query_count = sum(int(row.get("query_count") or 0) for row in systems)
    correction_ids = _trace_ids(systems, "correction_trace_ids")
    repair_ids = _trace_ids(systems, "repair_trace_ids")
    latencies = [
        _number(row.get("latency_p95_ms"), name="trace latency")
        for row in systems
    ]
    retry_count = sum(
        _number(row.get("retry_rate", 0), name="retry rate")
        * int(row.get("query_count") or 0)
        for row in systems
    )
    return {
        "system_metrics": {
            "query_count": query_count,
            "latency_p50_ms": _nearest_rank(latencies, 0.50),
            "latency_p95_ms": _nearest_rank(latencies, 0.95),
            "estimated_cost": sum(
                _number(row.get("estimated_cost", 0), name="trace cost")
                for row in systems
            ),
            "correction_rate": len(correction_ids) / query_count,
            "repair_rate": len(repair_ids) / query_count,
            "max_corrections_per_query": max(
                int(row.get("max_corrections_per_query") or 0)
                for row in systems
            ),
            "max_repairs_per_query": max(
                int(row.get("max_repairs_per_query") or 0)
                for row in systems
            ),
            "correction_trace_ids": correction_ids,
            "repair_trace_ids": repair_ids,
            "correction_error_count": sum(
                int(row.get("correction_error_count") or 0)
                for row in systems
            ),
            "retry_rate": retry_count / query_count,
        },
    }


def _aggregate_latencies(reports: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    stage_values: dict[str, list[float]] = defaultdict(list)
    for report in reports:
        for trace in report.get("traces") or []:
            for stage, value in (trace.get("stages_ms") or {}).items():
                stage_values[str(stage)].append(
                    _number(value, name=f"{stage} latency")
                )
    stage_summary = {
        stage: {
            "sample_count": len(values),
            "latency_p50_ms": _nearest_rank(values, 0.50),
            "latency_p95_ms": _nearest_rank(values, 0.95),
            "latency_max_ms": max(values),
        }
        for stage, values in stage_values.items()
    }
    return {
        "query_count": sum(int(report.get("query_count") or 0) for report in reports),
        "parse_errors": sum(int(report.get("parse_errors") or 0) for report in reports),
        "estimated_cost": sum(
            _number(report.get("estimated_cost", 0), name="latency cost")
            for report in reports
        ),
        "stage_summary": stage_summary,
    }


def _sanitize_arm(
    evaluation: Mapping[str, Any],
    latency: Mapping[str, Any],
    case_arms: Sequence[Mapping[str, Any]],
) -> dict[str, Any]:
    return {
        "eval": {
            "provider_failure_count": int(
                evaluation.get("provider_failure_count") or 0
            ),
            "provider_retries": int(evaluation.get("provider_retries") or 0),
            "total_cases": int(evaluation.get("total_cases") or 0),
            "passed_cases": int(evaluation.get("passed_cases") or 0),
        },
        "latency": {
            "estimated_cost": float(latency.get("estimated_cost") or 0.0),
            "stage_summary": dict(latency.get("stage_summary") or {}),
        },
        "artifacts": [dict(arm.get("artifacts") or {}) for arm in case_arms],
    }


def build_series_summary(
    *,
    series_id: str,
    arm_order: str,
    case_pairs: Sequence[Mapping[str, Any]],
) -> dict[str, Any]:
    """Build one suite-level gate from adjacent singleton case pairs."""
    case_ids = [str(pair.get("case_id") or "") for pair in case_pairs]
    if not case_ids or any(not case_id for case_id in case_ids):
        raise ValueError("case-paired series requires non-empty case IDs")
    if len(set(case_ids)) != len(case_ids):
        raise ValueError("case-paired series contains duplicate case IDs")
    if arm_order not in {"baseline-first", "candidate-first"}:
        raise ValueError("unsupported arm order")
    for pair, case_id in zip(case_pairs, case_ids, strict=True):
        validate_singleton_arm(case_id, pair.get("baseline") or {})
        validate_singleton_arm(case_id, pair.get("candidate") or {})

    arms: dict[str, dict[str, Any]] = {}
    gate_inputs: dict[str, dict[str, Any]] = {}
    for label in ("baseline", "candidate"):
        case_arms = [pair[label] for pair in case_pairs]
        evaluation = _aggregate_evaluations(
            [arm["eval"] for arm in case_arms]
        )
        trace = _aggregate_traces([arm["trace"] for arm in case_arms])
        latency = _aggregate_latencies(
            [arm["latency"] for arm in case_arms]
        )
        arms[label] = _sanitize_arm(evaluation, latency, case_arms)
        gate_inputs[label] = {"eval": evaluation, "trace": trace}
    gate = compare_reports(
        gate_inputs["baseline"]["eval"],
        gate_inputs["candidate"]["eval"],
        gate_inputs["baseline"]["trace"],
        gate_inputs["candidate"]["trace"],
    )
    return {
        "id": series_id,
        "arm_order": arm_order,
        "case_count": len(case_ids),
        "case_ids": case_ids,
        **arms,
        "gate": gate,
    }


def finalize_case_paired_outcome(
    base: Mapping[str, Any],
    series: Sequence[Mapping[str, Any]],
    *,
    expected_case_ids: tuple[str, ...],
    completed_case_pairs: int,
    arm_run_count: int,
    execution_failure: Mapping[str, Any] | None = None,
    stopped_case: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    for item in series:
        if tuple(item.get("case_ids") or ()) != expected_case_ids:
            raise ValueError("series case order drifted from the declaration")
    failures = int((stopped_case or {}).get("provider_failure_count") or 0)
    retries = int((stopped_case or {}).get("provider_retry_count") or 0)
    result = dict(base)
    if execution_failure or failures or retries:
        result = {
            **result,
            "status": "inconclusive",
            "diagnostic_target_met": False,
            "provider_failure_count": result["provider_failure_count"] + failures,
            "provider_retry_count": result["provider_retry_count"] + retries,
        }
    return {
        **result,
        "schema": "crag-stage-latency-diagnostic-outcome-v3",
        "measurement_design": "mirrored_case_paired_interleaved",
        "series_count": len(series),
        "case_pair_count": completed_case_pairs,
        "arm_run_count": arm_run_count,
        "expected_case_count": len(expected_case_ids),
        "case_ids": list(expected_case_ids),
        "series": _public_series(series),
        "execution_failure_count": int(bool(execution_failure)),
        "execution_failure": dict(execution_failure or {}),
        "stopped_case": dict(stopped_case or {}),
        "controlled_demo_pilot_authorized": False,
        "default_rollout_authorized": False,
        "formal_window_authorized": False,
        "feature_enablement_authorized": False,
        "next_action": _case_paired_next_action(
            result,
            failures=failures,
            retries=retries,
            execution_failure=execution_failure,
        ),
    }


def _public_series(
    series: Sequence[Mapping[str, Any]],
) -> list[dict[str, Any]]:
    return [
        {
            "id": item.get("id"),
            "arm_order": item.get("arm_order"),
            "case_count": item.get("case_count"),
            "gate_passed": bool((item.get("gate") or {}).get("passed")),
        }
        for item in series
    ]


def _case_paired_next_action(
    result: Mapping[str, Any],
    *,
    failures: int,
    retries: int,
    execution_failure: Mapping[str, Any] | None,
) -> str:
    if result["status"] == "passed":
        return "Owner may adjudicate a new formal declaration; keep both flags off."
    if failures or retries:
        return "Wait for provider recovery and use a new declaration."
    if execution_failure:
        return "Treat the execution stop as a tombstone and use a new declaration."
    return str(result["next_action"])
