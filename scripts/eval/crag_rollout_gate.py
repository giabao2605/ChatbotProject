"""Compare labeled-eval and trace snapshots before enabling CRAG flags."""

from __future__ import annotations

import argparse
import hashlib
import json
import math
from pathlib import Path


def _valid_metric(value) -> bool:
    return (
        isinstance(value, (int, float))
        and not isinstance(value, bool)
        and math.isfinite(value)
        and value >= 0
    )


def compare_reports(
    baseline_eval: dict,
    candidate_eval: dict,
    baseline_trace: dict,
    candidate_trace: dict,
    *,
    max_latency_ratio: float = 1.25,
    max_cost_ratio: float = 1.50,
) -> dict:
    baseline_outcomes = baseline_eval.get("outcome_confusion", {})
    candidate_outcomes = candidate_eval.get("outcome_confusion", {})
    baseline_system = baseline_trace.get("system_metrics", {})
    candidate_system = candidate_trace.get("system_metrics", {})
    candidate_cases = candidate_eval.get("cases", [])
    required_corrections = {
        row.get("trace_id") for row in candidate_cases if row.get("requires_correction")
    }
    required_repairs = {
        row.get("trace_id") for row in candidate_cases if row.get("requires_repair")
    }
    correction_traces = set(candidate_system.get("correction_trace_ids", []))
    repair_traces = set(candidate_system.get("repair_trace_ids", []))
    flags = candidate_eval.get("feature_flags", {})

    baseline_wrong_refusal = baseline_outcomes.get("wrong_refusal", 0)
    candidate_wrong_refusal = candidate_outcomes.get("wrong_refusal", 0)
    baseline_latency = baseline_system.get("latency_p95_ms")
    candidate_latency = candidate_system.get("latency_p95_ms")
    baseline_cost = baseline_system.get("estimated_cost")
    candidate_cost = candidate_system.get("estimated_cost")
    correction_error_count = candidate_system.get("correction_error_count")
    checks = {
        "candidate_cases_passed": (
            candidate_eval.get("total_cases", 0) > 0
            and candidate_eval.get("passed_cases") == candidate_eval.get("total_cases")
        ),
        "candidate_features_enabled": (
            str(flags.get("crag", "")).lower() == "true"
            and str(flags.get("claim_repair", "")).lower() == "true"
            and str(flags.get("semantic_cache", "")).lower() == "false"
        ),
        "correction_fixture_present": bool(required_corrections),
        "repair_fixture_present": bool(required_repairs),
        "required_corrections_exercised": required_corrections <= correction_traces,
        "required_repairs_exercised": required_repairs <= repair_traces,
        "correction_errors_zero": (
            _valid_metric(correction_error_count)
            and correction_error_count == 0
        ),
        "wrong_refusal_reduced": (
            candidate_wrong_refusal < baseline_wrong_refusal
            if baseline_wrong_refusal > 0
            else candidate_wrong_refusal == 0
        ),
        "wrong_answer_not_increased": candidate_outcomes.get("wrong_answer", 0)
        <= baseline_outcomes.get("wrong_answer", 0),
        "refusal_types_correct": candidate_outcomes.get("wrong_refusal_type", 0) == 0,
        "leakage_zero": candidate_outcomes.get("leakage", 0) == 0,
        "latency_within_budget": (
            _valid_metric(baseline_latency)
            and _valid_metric(candidate_latency)
            and candidate_latency <= baseline_latency * max_latency_ratio
        ),
        "cost_within_budget": (
            _valid_metric(baseline_cost)
            and _valid_metric(candidate_cost)
            and candidate_cost <= baseline_cost * max_cost_ratio
        ),
        "correction_budget": (
            candidate_system.get("correction_rate", float("inf")) <= 1.0
            and candidate_system.get("max_corrections_per_query", 0) <= 1
        ),
        "repair_budget": (
            candidate_system.get("repair_rate", float("inf")) <= 1.0
            and candidate_system.get("max_repairs_per_query", 0) <= 1
        ),
        "retry_budget": candidate_system.get("retry_rate", float("inf")) <= 2.0,
    }
    return {
        "schema": "crag-rollout-gate-v1",
        "passed": all(checks.values()),
        "checks": checks,
        "limits": {
            "max_latency_ratio": max_latency_ratio,
            "max_cost_ratio": max_cost_ratio,
        },
    }


def _read(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("baseline_eval", type=Path)
    parser.add_argument("candidate_eval", type=Path)
    parser.add_argument("baseline_trace", type=Path)
    parser.add_argument("candidate_trace", type=Path)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    report = compare_reports(
        _read(args.baseline_eval),
        _read(args.candidate_eval),
        _read(args.baseline_trace),
        _read(args.candidate_trace),
    )
    report["inputs"] = {
        "baseline_eval_sha256": _sha256(args.baseline_eval),
        "candidate_eval_sha256": _sha256(args.candidate_eval),
        "baseline_trace_sha256": _sha256(args.baseline_trace),
        "candidate_trace_sha256": _sha256(args.candidate_trace),
    }
    payload = json.dumps(report, ensure_ascii=False, indent=2) + "\n"
    if args.output:
        args.output.write_text(payload, encoding="utf-8")
    else:
        print(payload, end="")
    return 0 if report["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
