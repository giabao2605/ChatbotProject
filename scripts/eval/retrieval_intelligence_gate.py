"""Stage-specific rollout gates for retrieval-intelligence milestones."""

from __future__ import annotations

import argparse
import json
from pathlib import Path


def _ratio(candidate, baseline):
    if baseline == 0:
        return 1.0 if candidate == 0 else float("inf")
    return candidate / baseline


def _group_rate(report, name):
    return float((report.get("evaluation_groups", {}).get(name) or {}).get("pass_rate") or 0.0)


def compare(stage, baseline, candidate, metadata=None):
    metadata = metadata or {}
    baseline_outcomes = baseline.get("outcome_confusion", {})
    candidate_outcomes = candidate.get("outcome_confusion", {})
    common = {
        "wrong_answer_not_increased": candidate_outcomes.get("wrong_answer", 0)
        <= baseline_outcomes.get("wrong_answer", 0),
        "leakage_zero": candidate_outcomes.get("leakage", 0) == 0,
        "provider_retries_not_increased": int(candidate.get("provider_retries") or 0)
        <= int(baseline.get("provider_retries") or 0),
    }
    if stage == "grounded_math":
        max_calculations = max(
            (int(row.get("calculation_count") or 0) for row in candidate.get("cases", [])),
            default=0,
        )
        checks = {
            **common,
            "grounded_math_cases_passed": _group_rate(candidate, "grounded_math") == 1.0,
            "calculation_budget": max_calculations <= 1,
        }
        limits = {"max_calculations_per_query": 1}
    elif stage == "late_interaction":
        b_ranked = baseline.get("ranked_retrieval", {})
        c_ranked = candidate.get("ranked_retrieval", {})
        baseline_ndcg = float(b_ranked.get("ndcg_at_10") or 0.0)
        candidate_ndcg = float(c_ranked.get("ndcg_at_10") or 0.0)
        checks = {
            **common,
            "ndcg_relative_gain": baseline_ndcg > 0 and candidate_ndcg >= baseline_ndcg * 1.05,
            "recall_not_decreased": float(c_ranked.get("recall_at_10") or 0.0)
            >= float(b_ranked.get("recall_at_10") or 0.0),
            "latency_within_budget": _ratio(
                float(candidate.get("latency_p95_ms") or 0), float(baseline.get("latency_p95_ms") or 0)
            ) <= 1.25,
            "storage_within_budget": float(metadata.get("shadow_storage_ratio", float("inf"))) <= 25.0,
            "shadow_coverage_complete": float(metadata.get("shadow_coverage", 1.0)) >= 1.0,
        }
        limits = {"min_ndcg_relative_gain": 0.05, "max_latency_ratio": 1.25, "max_storage_ratio": 25.0}
    elif stage == "query_decomposition":
        checks = {
            **common,
            "complex_answer_gain": _group_rate(candidate, "complex")
            >= _group_rate(baseline, "complex") + 0.10,
            "simple_planner_calls_zero": int(metadata.get("simple_planner_calls", -1)) == 0,
            "latency_within_budget": _ratio(
                float(candidate.get("latency_p95_ms") or 0), float(baseline.get("latency_p95_ms") or 0)
            ) <= 1.5,
            "cost_within_budget": _ratio(
                float(candidate.get("total_estimated_cost") or 0),
                float(baseline.get("total_estimated_cost") or 0),
            ) <= 1.5,
        }
        limits = {"min_complex_answer_gain": 0.10, "max_latency_ratio": 1.5, "max_cost_ratio": 1.5}
    elif stage == "graph_retrieval":
        checks = {
            **common,
            "relational_accuracy_gain": _group_rate(candidate, "relational")
            >= _group_rate(baseline, "relational") + 0.10,
            "reviewed_edge_precision": float(metadata.get("reviewed_edge_precision", 0.0)) >= 0.95,
            "latency_within_budget": _ratio(
                float(candidate.get("latency_p95_ms") or 0), float(baseline.get("latency_p95_ms") or 0)
            ) <= 1.5,
        }
        limits = {"min_accuracy_gain": 0.10, "min_reviewed_edge_precision": 0.95, "max_latency_ratio": 1.5}
    else:
        raise ValueError(f"unknown stage: {stage}")
    return {
        "schema": "retrieval-intelligence-gate-v1",
        "stage": stage,
        "passed": all(checks.values()),
        "checks": checks,
        "limits": limits,
    }


def main(argv=None):
    parser = argparse.ArgumentParser()
    parser.add_argument("stage", choices=("grounded_math", "late_interaction", "query_decomposition", "graph_retrieval"))
    parser.add_argument("baseline", type=Path)
    parser.add_argument("candidate", type=Path)
    parser.add_argument("--metadata", type=Path)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args(argv)
    read = lambda path: json.loads(path.read_text(encoding="utf-8"))
    result = compare(args.stage, read(args.baseline), read(args.candidate), read(args.metadata) if args.metadata else {})
    payload = json.dumps(result, ensure_ascii=False, indent=2) + "\n"
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(payload, encoding="utf-8")
    else:
        print(payload, end="")
    return 0 if result["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
