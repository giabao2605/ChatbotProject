"""Compare labeled-eval and trace snapshots before enabling CRAG flags."""

from __future__ import annotations

import argparse
import json
from pathlib import Path


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

    baseline_wrong_refusal = baseline_outcomes.get("wrong_refusal", 0)
    candidate_wrong_refusal = candidate_outcomes.get("wrong_refusal", 0)
    checks = {
        "wrong_refusal_reduced": (
            candidate_wrong_refusal < baseline_wrong_refusal
            if baseline_wrong_refusal > 0
            else candidate_wrong_refusal == 0
        ),
        "wrong_answer_not_increased": candidate_outcomes.get("wrong_answer", 0)
        <= baseline_outcomes.get("wrong_answer", 0),
        "leakage_zero": candidate_outcomes.get("leakage", 0) == 0,
        "latency_within_budget": candidate_system.get("latency_p95_ms", float("inf"))
        <= baseline_system.get("latency_p95_ms", 0) * max_latency_ratio,
        "cost_within_budget": candidate_system.get("estimated_cost", float("inf"))
        <= baseline_system.get("estimated_cost", 0) * max_cost_ratio,
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
    payload = json.dumps(report, ensure_ascii=False, indent=2) + "\n"
    if args.output:
        args.output.write_text(payload, encoding="utf-8")
    else:
        print(payload, end="")
    return 0 if report["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
