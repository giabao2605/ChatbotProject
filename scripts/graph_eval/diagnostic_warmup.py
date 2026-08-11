"""Pure contract helpers for the Graph latency diagnostic warm-up."""

from __future__ import annotations

from typing import Any, Mapping

from scripts.graph_eval.diagnostic_metrics import (
    _provider_failures,
    _provider_retries,
)


WARMUP_CASE_ID = "graph-uses-material"
WARMUP_PAIR_PLAN = (
    {"ordinal": 1, "arm_order": "baseline-first"},
    {"ordinal": 2, "arm_order": "candidate-first"},
)
MEASUREMENT_DESIGN = "mirrored_two_pair_warmup_then_case_paired_interleaved"


def build_warmup_plan(
    case_plan: list[Mapping[str, Any]],
) -> list[dict[str, Any]]:
    if WARMUP_CASE_ID not in {
        str(item.get("case_id") or "") for item in case_plan
    }:
        raise ValueError(f"warm-up case is missing from the manifest: {WARMUP_CASE_ID}")
    return [
        {
            "ordinal": int(item["ordinal"]),
            "case_id": WARMUP_CASE_ID,
            "arm_order": str(item["arm_order"]),
        }
        for item in WARMUP_PAIR_PLAN
    ]


def declaration_warmup() -> dict[str, Any]:
    return {
        "enabled": True,
        "measured": False,
        "interpretation": "warm_state_supporting_diagnostic_only",
        "case_id": WARMUP_CASE_ID,
        "pair_plan": [dict(item) for item in WARMUP_PAIR_PLAN],
        "runs_per_arm": len(WARMUP_PAIR_PLAN),
        "health_gate_before_each_pair": True,
        "trace_scope": "separate_from_measured",
    }


def validate_warmup_arm(
    arm: Mapping[str, Any],
    *,
    enabled: bool,
) -> None:
    stages = (arm.get("latency") or {}).get("stage_summary") or {}
    for stage in ("retrieval", "generation"):
        if int((stages.get(stage) or {}).get("sample_count") or 0) != 1:
            raise RuntimeError(f"warm-up {stage} stage must have one sample")
    graph_samples = int(
        (stages.get("graph_retrieval") or {}).get("sample_count") or 0
    )
    if graph_samples != int(enabled):
        raise RuntimeError("warm-up Graph stage does not match the declared arm")


def _trace_count(pair: Mapping[str, Any], arm: str, field: str) -> int:
    return int(((pair.get(arm) or {}).get("trace") or {}).get(field) or 0)


def build_warmup_summary(
    pairs: tuple[Mapping[str, Any], ...],
    warmup_plan: list[Mapping[str, Any]],
) -> dict[str, Any]:
    expected = [
        (int(item["ordinal"]), str(item["case_id"]), str(item["arm_order"]))
        for item in warmup_plan
    ]
    observed = [
        (
            int(pair.get("ordinal") or 0),
            str(pair.get("id") or ""),
            str(pair.get("arm_order") or ""),
        )
        for pair in pairs
    ]
    health_failures = sum(
        (pair.get("health") or {}).get("passed") is not True for pair in pairs
    )
    execution_failures = sum(
        isinstance(pair.get("execution_failure"), Mapping) for pair in pairs
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
    fallback_events = sum(
        _trace_count(pair, arm, "fallback_event_count")
        for pair in pairs
        for arm in ("baseline", "candidate")
    )
    retry_events = sum(
        _trace_count(pair, arm, "retry_event_count")
        for pair in pairs
        for arm in ("baseline", "candidate")
    )
    complete = observed == expected and all(
        (pair.get("health") or {}).get("passed") is True
        and isinstance(pair.get("baseline"), Mapping)
        and isinstance(pair.get("candidate"), Mapping)
        for pair in pairs
    )
    passed = bool(
        complete
        and not health_failures
        and not execution_failures
        and not provider_failures
        and not provider_retries
        and not fallback_events
        and not retry_events
    )
    return {
        "schema": "graph-latency-diagnostic-warmup-v1",
        "measured": False,
        "interpretation": "warm_state_supporting_diagnostic_only",
        "passed": passed,
        "pair_count": len(pairs),
        "expected_pair_count": len(warmup_plan),
        "health_failure_count": health_failures,
        "execution_failure_count": execution_failures,
        "provider_failure_count": provider_failures,
        "provider_retry_count": provider_retries,
        "fallback_event_count": fallback_events,
        "retry_event_count": retry_events,
        "pairs": [
            {
                "ordinal": pair.get("ordinal"),
                "case_id": pair.get("id"),
                "arm_order": pair.get("arm_order"),
                "health_passed": (pair.get("health") or {}).get("passed") is True,
                "completed": all(
                    isinstance(pair.get(arm), Mapping)
                    for arm in ("baseline", "candidate")
                ),
            }
            for pair in pairs
        ],
    }


def wrap_v3_outcome(
    measured: Mapping[str, Any],
    warmup: Mapping[str, Any],
) -> dict[str, Any]:
    report = {
        **measured,
        "schema": "graph-latency-diagnostic-outcome-v3",
        "measurement_design": MEASUREMENT_DESIGN,
        "warmup": dict(warmup),
    }
    if warmup.get("passed") is True:
        return report
    return {
        **report,
        "status": "inconclusive",
        "health_failure_count": int(report["health_failure_count"])
        + int(warmup.get("health_failure_count") or 0),
        "execution_failure_count": int(report["execution_failure_count"])
        + int(warmup.get("execution_failure_count") or 0),
        "provider_failure_count": int(report["provider_failure_count"])
        + int(warmup.get("provider_failure_count") or 0),
        "provider_retry_count": int(report["provider_retry_count"])
        + int(warmup.get("provider_retry_count") or 0),
        "fallback_event_count": int(report["fallback_event_count"])
        + int(warmup.get("fallback_event_count") or 0),
        "retry_event_count": int(report["retry_event_count"])
        + int(warmup.get("retry_event_count") or 0),
        "diagnostic_target_met": False,
        "formal_window_authorized": False,
        "feature_enablement_authorized": False,
        "next_action": (
            "Inspect the sanitized warm-up failure before a new declaration."
        ),
    }


__all__ = [
    "MEASUREMENT_DESIGN",
    "build_warmup_plan",
    "build_warmup_summary",
    "declaration_warmup",
    "validate_warmup_arm",
    "wrap_v3_outcome",
]
