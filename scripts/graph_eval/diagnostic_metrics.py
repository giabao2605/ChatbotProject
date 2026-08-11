"""Pure aggregation for the case-paired Graph latency diagnostic."""

from __future__ import annotations

import math
import statistics
from typing import Any, Mapping


MAX_LATENCY_P95_RATIO = 1.25
MAX_COST_RATIO = 1.5
MAX_ARM_ORDER_LATENCY_RATIO_SPREAD = 0.10


def _provider_failures(arm: Mapping[str, Any] | None) -> int:
    if not arm:
        return 0
    report = arm.get("eval") or {}
    direct = int(report.get("provider_failure_count") or 0)
    cases = sum(
        bool(row.get("provider_failure"))
        for row in report.get("cases") or []
        if isinstance(row, Mapping)
    )
    trace = arm.get("trace") or {}
    trace_anomalies = int(trace.get("error_event_count") or 0) + int(
        trace.get("fallback_event_count") or 0
    )
    return max(direct, cases, trace_anomalies)


def _provider_retries(arm: Mapping[str, Any] | None) -> int:
    if not arm:
        return 0
    return max(
        int((arm.get("eval") or {}).get("provider_retries") or 0),
        int((arm.get("trace") or {}).get("retry_event_count") or 0),
    )


def _finite_number(value: Any) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    result = float(value)
    return result if math.isfinite(result) and result >= 0 else None


def _eval_metric(arm: Mapping[str, Any] | None, metric: str) -> float | None:
    if not isinstance(arm, Mapping):
        return None
    return _finite_number((arm.get("eval") or {}).get(metric))


def _stage_metric(
    arm: Mapping[str, Any] | None,
    stage: str,
) -> float | None:
    if not isinstance(arm, Mapping):
        return None
    summary = (arm.get("latency") or {}).get("stage_summary") or {}
    return _finite_number((summary.get(stage) or {}).get("latency_p95_ms"))


def _nearest_rank(values: list[float], percentile: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    rank = max(1, math.ceil(len(ordered) * percentile))
    return ordered[min(rank, len(ordered)) - 1]


def _safe_ratio(candidate: float | None, baseline: float | None) -> float | None:
    if candidate is None or baseline is None or baseline <= 0:
        return None
    return candidate / baseline


def _pairs_match_declaration(
    pairs: list[Mapping[str, Any]],
    case_plan: list[Mapping[str, Any]],
) -> bool:
    observed = [
        (
            int(pair.get("ordinal") or 0),
            str(pair.get("id") or ""),
            str(pair.get("arm_order") or ""),
        )
        for pair in pairs
    ]
    expected = [
        (
            int(item.get("ordinal") or 0),
            str(item.get("case_id") or ""),
            str(item.get("arm_order") or ""),
        )
        for item in case_plan
    ]
    return observed == expected


def _arm_quality_passed(arm: Mapping[str, Any] | None) -> bool:
    if not isinstance(arm, Mapping):
        return False
    report = arm.get("eval") or {}
    total = int(report.get("total_cases") or 0)
    return total == 1 and int(report.get("passed_cases") or 0) == total


def _trace_count(arm: Mapping[str, Any] | None, field: str) -> int:
    if not isinstance(arm, Mapping):
        return 0
    return int((arm.get("trace") or {}).get(field) or 0)


def _arm_order_metrics(
    pairs: list[Mapping[str, Any]],
    paired_ratios: list[float],
) -> tuple[dict[str, float], float | None, bool]:
    grouped = {
        order: [
            ratio
            for pair, ratio in zip(pairs, paired_ratios, strict=True)
            if pair.get("arm_order") == order
        ]
        for order in ("baseline-first", "candidate-first")
    } if len(paired_ratios) == len(pairs) else {
        "baseline-first": [],
        "candidate-first": [],
    }
    medians = {
        order: statistics.median(values)
        for order, values in grouped.items()
        if values
    }
    spread = (
        max(medians.values()) - min(medians.values())
        if len(medians) == 2
        else None
    )
    return medians, spread, bool(
        spread is not None
        and spread <= MAX_ARM_ORDER_LATENCY_RATIO_SPREAD + 1e-12
    )


def _stage_metrics(pairs: list[Mapping[str, Any]]) -> dict[str, dict[str, Any]]:
    result = {}
    for stage in ("retrieval", "generation", "graph_retrieval"):
        baseline_values = [
            value
            for pair in pairs
            if (value := _stage_metric(pair.get("baseline"), stage)) is not None
        ]
        candidate_values = [
            value
            for pair in pairs
            if (value := _stage_metric(pair.get("candidate"), stage)) is not None
        ]
        if not baseline_values and not candidate_values:
            continue
        baseline_p95 = _nearest_rank(baseline_values, 0.95)
        candidate_p95 = _nearest_rank(candidate_values, 0.95)
        result[stage] = {
            "baseline_sample_count": len(baseline_values),
            "candidate_sample_count": len(candidate_values),
            "baseline_p95_ms": baseline_p95,
            "candidate_p95_ms": candidate_p95,
            "delta_p95_ms": (
                candidate_p95 - baseline_p95
                if candidate_p95 is not None and baseline_p95 is not None
                else None
            ),
            "ratio": _safe_ratio(candidate_p95, baseline_p95),
        }
    return result


def _stage_evidence_complete(pairs: list[Mapping[str, Any]]) -> bool:
    retrieval_complete = all(
        _stage_metric(pair.get(arm), "retrieval") is not None
        for pair in pairs
        for arm in ("baseline", "candidate")
    )
    generation_presence = [
        (
            _stage_metric(pair.get("baseline"), "generation") is not None,
            _stage_metric(pair.get("candidate"), "generation") is not None,
        )
        for pair in pairs
    ]
    generation_balanced = bool(
        any(baseline and candidate for baseline, candidate in generation_presence)
        and all(baseline == candidate for baseline, candidate in generation_presence)
    )
    return bool(pairs and retrieval_complete and generation_balanced)


def build_diagnostic_outcome(
    pairs: list[Mapping[str, Any]],
    *,
    case_plan: list[Mapping[str, Any]],
    source_commit: str,
    declaration_sha256: str,
) -> dict[str, Any]:
    health_failures = sum(
        (pair.get("health") or {}).get("passed") is not True
        for pair in pairs
        if "health" in pair
    )
    execution_failures = sum(
        isinstance(pair.get("execution_failure"), Mapping)
        for pair in pairs
    )
    provider_failures = sum(
        _provider_failures(pair.get(arm))
        for pair in pairs
        for arm in ("baseline", "candidate")
    )
    provider_retries = sum(
        _provider_retries(pair.get(arm))
        for pair in pairs
        for arm in ("baseline", "candidate")
    )
    complete = _pairs_match_declaration(pairs, case_plan) and all(
        isinstance(pair.get("baseline"), Mapping)
        and isinstance(pair.get("candidate"), Mapping)
        and (pair.get("health") or {}).get("passed") is True
        for pair in pairs
    )
    baseline_latencies = [
        value
        for pair in pairs
        if (value := _eval_metric(pair.get("baseline"), "latency_p95_ms"))
        is not None
    ]
    candidate_latencies = [
        value
        for pair in pairs
        if (value := _eval_metric(pair.get("candidate"), "latency_p95_ms"))
        is not None
    ]
    paired_ratios = [
        ratio
        for pair in pairs
        if (
            ratio := _safe_ratio(
                _eval_metric(pair.get("candidate"), "latency_p95_ms"),
                _eval_metric(pair.get("baseline"), "latency_p95_ms"),
            )
        )
        is not None
    ]
    baseline_p95 = _nearest_rank(baseline_latencies, 0.95) if complete else None
    candidate_p95 = _nearest_rank(candidate_latencies, 0.95) if complete else None
    latency_ratio = _safe_ratio(candidate_p95, baseline_p95)
    baseline_costs = [
        value
        for pair in pairs
        if (value := _eval_metric(pair.get("baseline"), "total_estimated_cost"))
        is not None
    ]
    candidate_costs = [
        value
        for pair in pairs
        if (value := _eval_metric(pair.get("candidate"), "total_estimated_cost"))
        is not None
    ]
    cost_ratio = (
        _safe_ratio(sum(candidate_costs), sum(baseline_costs))
        if complete
        and len(baseline_costs) == len(case_plan)
        and len(candidate_costs) == len(case_plan)
        else None
    )
    order_medians, order_spread, order_balanced = _arm_order_metrics(
        pairs,
        paired_ratios,
    )
    arm_order_consistent = complete and order_balanced
    quality_passed = bool(
        complete
        and all(
            _arm_quality_passed(pair.get(arm))
            for pair in pairs
            for arm in ("baseline", "candidate")
        )
    )
    stage_metrics = _stage_metrics(pairs)
    stage_evidence_complete = bool(
        complete and _stage_evidence_complete(pairs)
    )
    positive_deltas = {
        stage: metrics["delta_p95_ms"]
        for stage, metrics in stage_metrics.items()
        if metrics["delta_p95_ms"] is not None and metrics["delta_p95_ms"] > 0
    }
    fallback_events = sum(
        _trace_count(pair.get(arm), "fallback_event_count")
        for pair in pairs
        for arm in ("baseline", "candidate")
    )
    retry_events = sum(
        _trace_count(pair.get(arm), "retry_event_count")
        for pair in pairs
        for arm in ("baseline", "candidate")
    )
    target_met = bool(
        not provider_failures
        and not provider_retries
        and arm_order_consistent
        and stage_evidence_complete
        and quality_passed
        and latency_ratio is not None
        and latency_ratio <= MAX_LATENCY_P95_RATIO
        and cost_ratio is not None
        and cost_ratio <= MAX_COST_RATIO
    )
    inconclusive = bool(
        health_failures
        or execution_failures
        or provider_failures
        or provider_retries
        or not complete
        or not arm_order_consistent
        or not stage_evidence_complete
    )
    return {
        "schema": "graph-latency-diagnostic-outcome-v2",
        "scope": "supporting_diagnostic_only",
        "formal_evidence": False,
        "status": (
            "inconclusive"
            if inconclusive
            else "passed" if target_met else "failed"
        ),
        "source_commit": source_commit,
        "declaration_sha256": declaration_sha256,
        "pair_count": len(pairs),
        "health_failure_count": health_failures,
        "execution_failure_count": execution_failures,
        "provider_failure_count": provider_failures,
        "provider_retry_count": provider_retries,
        "fallback_event_count": fallback_events,
        "retry_event_count": retry_events,
        "arm_order_consistent": arm_order_consistent,
        "arm_order_latency_ratio_medians": order_medians,
        "arm_order_latency_ratio_spread": order_spread,
        "paired_latency_ratios": paired_ratios,
        "paired_latency_ratio_median": (
            statistics.median(paired_ratios)
            if complete and paired_ratios
            else None
        ),
        "paired_latency_ratio_p95": (
            _nearest_rank(paired_ratios, 0.95) if complete else None
        ),
        "baseline_latency_p95_ms": baseline_p95,
        "candidate_latency_p95_ms": candidate_p95,
        "latency_p95_ratio": latency_ratio if not provider_failures else None,
        "cost_ratio": cost_ratio if complete and not provider_failures else None,
        "quality_passed": quality_passed,
        "stage_evidence_complete": stage_evidence_complete,
        "diagnostic_target_met": target_met,
        "stage_metrics": stage_metrics if complete and not provider_failures else {},
        "dominant_overhead_stage": (
            max(positive_deltas, key=positive_deltas.get)
            if complete and not provider_failures and positive_deltas
            else "none" if complete and not provider_failures else "unavailable"
        ),
        "pairs": [
            {
                "id": pair.get("id"),
                "ordinal": pair.get("ordinal"),
                "arm_order": pair.get("arm_order"),
                "health_passed": (pair.get("health") or {}).get("passed") is True,
                "quality_passed": all(
                    _arm_quality_passed(pair.get(arm))
                    for arm in ("baseline", "candidate")
                ),
            }
            for pair in pairs
        ],
        "full_quality_gate_executed": False,
        "formal_window_authorized": False,
        "feature_enablement_authorized": False,
        "next_action": (
            "Confirm Qdrant fixture health before declaring another window."
            if health_failures
            else "Inspect the sanitized arm failure before a new declaration."
            if execution_failures
            else "Wait for externally confirmed provider recovery before a new declaration."
            if provider_failures or provider_retries
            else "Complete balanced retrieval and generation stage evidence in a new declaration."
            if complete and not stage_evidence_complete
            else "Treat the result as arm-order-sensitive variance and use a new declaration."
            if complete and not arm_order_consistent
            else "Complete the predeclared diagnostic before choosing a code fix."
            if not complete
            else "Owner may adjudicate a new formal declaration; this artifact grants no authorization."
            if target_met
            else "Investigate the dominant stage; keep Graph and formal rollout off."
        ),
    }


__all__ = [
    "MAX_ARM_ORDER_LATENCY_RATIO_SPREAD",
    "MAX_COST_RATIO",
    "MAX_LATENCY_P95_RATIO",
    "_provider_failures",
    "_provider_retries",
    "build_diagnostic_outcome",
]
