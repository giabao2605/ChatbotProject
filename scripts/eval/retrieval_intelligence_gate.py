"""Stage-specific rollout gates for retrieval-intelligence milestones."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path


def _ratio(candidate, baseline):
    if baseline == 0:
        return 1.0 if candidate == 0 else float("inf")
    return candidate / baseline


def _group_rate(report, name):
    return float((report.get("evaluation_groups", {}).get(name) or {}).get("pass_rate") or 0.0)


def _sha256(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _fraction_complete(report, section, metric):
    value = (((report.get(section) or {}).get(metric) or {}).get("value"))
    return value is not None and float(value) == 1.0


def _calculation_check_complete(report, check):
    totals = (((report.get("grounded_math_evaluation") or {}).get("check_totals") or {}).get(check) or {})
    applicable = int(totals.get("applicable") or 0)
    return applicable > 0 and int(totals.get("passed") or 0) == applicable


def compare(stage, baseline, candidate, metadata=None, reference=None):
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
        baseline_rate = _group_rate(baseline, "grounded_math")
        candidate_rate = _group_rate(candidate, "grounded_math")
        calculation = candidate.get("grounded_math_evaluation") or {}
        checks = {
            **common,
            "grounded_math_cases_passed": candidate_rate == 1.0,
            "grounded_math_improved": (
                candidate_rate > baseline_rate if baseline_rate < 1.0 else candidate_rate == 1.0
            ),
            "calculation_budget": max_calculations <= 1,
            "single_plan_exact": _calculation_check_complete(candidate, "single_plan"),
            "status_exact": _calculation_check_complete(candidate, "status"),
            "operation_exact": _calculation_check_complete(candidate, "operation"),
            "decimal_exact": _calculation_check_complete(candidate, "exact_decimal"),
            "display_value_exact": _calculation_check_complete(candidate, "display_value"),
            "formula_exact": _calculation_check_complete(candidate, "formula"),
            "unit_exact": _calculation_check_complete(candidate, "unit"),
            "provenance_exact": _calculation_check_complete(candidate, "provenance"),
            "unsupported_numbers_zero": (
                int(calculation.get("unsupported_number_count") or 0) == 0
                and _calculation_check_complete(candidate, "unsupported_numbers_zero")
            ),
            "citation_accuracy_complete": _fraction_complete(
                candidate, "citation_evaluation", "citation_accuracy"
            ),
            "citation_precision_complete": _fraction_complete(
                candidate, "citation_evaluation", "citation_precision"
            ),
            "latency_within_budget": _ratio(
                float(candidate.get("latency_p95_ms") or 0),
                float(baseline.get("latency_p95_ms") or 0),
            ) <= 1.25,
            "cost_within_budget": _ratio(
                float(candidate.get("total_estimated_cost") or 0),
                float(baseline.get("total_estimated_cost") or 0),
            ) <= 1.5,
        }
        limits = {
            "max_calculations_per_query": 1,
            "max_latency_ratio": 1.25,
            "max_cost_ratio": 1.5,
            "required_exact_rate": 1.0,
        }
    elif stage == "late_interaction":
        reference = reference or {}
        b_ranked = baseline.get("ranked_retrieval", {})
        c_ranked = candidate.get("ranked_retrieval", {})
        baseline_ndcg = float(b_ranked.get("ndcg_at_10") or 0.0)
        candidate_ndcg = float(c_ranked.get("ndcg_at_10") or 0.0)
        baseline_fallback = float(
            (baseline.get("fallback_coverage") or {}).get("fallback_rate") or 0.0
        )
        candidate_fallback = float(
            (candidate.get("fallback_coverage") or {}).get("fallback_rate") or 0.0
        )
        snapshots = {
            str((report.get("run_metadata") or {}).get("snapshot_fingerprint") or "")
            for report in (reference, baseline, candidate)
        }
        provider_hashes = {
            str((report.get("run_metadata") or {}).get("provider_configuration_sha256") or "")
            for report in (reference, baseline, candidate)
        }
        commits = {
            str((report.get("run_metadata") or {}).get("commit_sha") or "")
            for report in (reference, baseline, candidate)
        }
        manifests = {
            str((report.get("run_metadata") or {}).get("manifest_sha256") or "")
            for report in (reference, baseline, candidate)
        }
        checks = {
            **common,
            "voyage_baseline_valid": (
                baseline.get("variant") == "voyage" and baseline_fallback <= 0.10
            ),
            "shadow_candidate_valid": (
                candidate.get("variant") == "maxsim" and candidate_fallback == 0.0
            ),
            "rrf_reference_valid": reference.get("variant") == "rrf",
            "snapshot_frozen_across_variants": len(snapshots) == 1 and "" not in snapshots,
            "provider_configuration_frozen": len(provider_hashes) == 1 and "" not in provider_hashes,
            "commit_frozen_across_variants": len(commits) == 1 and "" not in commits,
            "manifest_frozen_across_variants": len(manifests) == 1 and "" not in manifests,
            "readiness_artifact_valid": metadata.get("schema") == "late-interaction-readiness-v1",
            "capability_passed": metadata.get("capability_passed") is True,
            "ready_for_serving": metadata.get("ready_for_serving") is True,
            "ndcg_relative_gain": baseline_ndcg > 0 and candidate_ndcg >= baseline_ndcg * 1.05,
            "recall_not_decreased": float(c_ranked.get("recall_at_10") or 0.0)
            >= float(b_ranked.get("recall_at_10") or 0.0),
            "latency_within_budget": _ratio(
                float(candidate.get("latency_p95_ms") or 0), float(baseline.get("latency_p95_ms") or 0)
            ) <= 1.25,
            "storage_within_budget": float(metadata.get("shadow_storage_ratio", float("inf"))) <= 25.0,
            "shadow_coverage_complete": float(metadata.get("shadow_coverage") or 0.0) >= 1.0,
            "governance_drift_zero": int(metadata.get("governance_drift", -1)) == 0,
            "provenance_drift_zero": int(metadata.get("provenance_drift", -1)) == 0,
            "vector_schema_rejected_zero": int(
                metadata.get("vector_schema_rejected", -1)
            ) == 0,
            "orphan_points_zero": int(metadata.get("orphan_points", -1)) == 0,
        }
        limits = {
            "min_ndcg_relative_gain": 0.05,
            "max_latency_ratio": 1.25,
            "max_storage_ratio": 25.0,
            "max_voyage_fallback_rate": 0.10,
            "max_shadow_fallback_rate": 0.0,
        }
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
    parser.add_argument("--reference", type=Path)
    parser.add_argument("--baseline-trace", type=Path, required=True)
    parser.add_argument("--candidate-trace", type=Path, required=True)
    parser.add_argument("--reference-trace", type=Path)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args(argv)
    read = lambda path: json.loads(path.read_text(encoding="utf-8"))
    if args.stage == "late_interaction" and (not args.reference or not args.reference_trace):
        parser.error("late_interaction requires --reference and --reference-trace for the RRF arm")
    result = compare(
        args.stage,
        read(args.baseline),
        read(args.candidate),
        read(args.metadata) if args.metadata else {},
        read(args.reference) if args.reference else None,
    )
    result["inputs"] = {
        "baseline_eval_sha256": _sha256(args.baseline),
        "candidate_eval_sha256": _sha256(args.candidate),
        "baseline_trace_sha256": _sha256(args.baseline_trace),
        "candidate_trace_sha256": _sha256(args.candidate_trace),
    }
    if args.reference:
        result["inputs"]["reference_eval_sha256"] = _sha256(args.reference)
        result["inputs"]["reference_trace_sha256"] = _sha256(args.reference_trace)
    payload = json.dumps(result, ensure_ascii=False, indent=2) + "\n"
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(payload, encoding="utf-8")
    else:
        print(payload, end="")
    return 0 if result["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
