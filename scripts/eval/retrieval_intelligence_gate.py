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


def _artifact_references_verified(integrity):
    references = integrity.get("artifact_references") or []
    if not references:
        return False
    for reference in references:
        try:
            path = Path(reference["path"])
            raw = path.read_bytes()
            decoded = raw.decode("utf-8")
            if reference.get("format") == "jsonl":
                rows = [json.loads(line) for line in decoded.splitlines() if line.strip()]
                if not rows:
                    return False
                artifact = None
            else:
                artifact = json.loads(decoded)
        except (KeyError, TypeError, OSError, UnicodeDecodeError, json.JSONDecodeError):
            return False
        if hashlib.sha256(raw).hexdigest() != reference.get("sha256"):
            return False
        if artifact is not None and artifact.get("schema") != reference.get("schema"):
            return False
    return True


def _matrix_evidence_recomputed(integrity, expected):
    references = integrity.get("artifact_references") or []
    manifests = [
        reference for reference in references
        if reference.get("schema") == "integrated-matrix-evidence-v1"
    ]
    if len(manifests) != 1:
        return False
    matrices = [
        reference for reference in references
        if reference.get("schema") == "integrated-feature-matrix-v1"
    ]
    if len(matrices) != 1:
        return False
    try:
        from scripts.integrated_eval.compose_gate_metadata import load_matrix_evidence

        path = Path(manifests[0]["path"])
        manifest = json.loads(path.read_text(encoding="utf-8"))
        feature_matrix = json.loads(
            Path(matrices[0]["path"]).read_text(encoding="utf-8")
        )
        actual, _ = load_matrix_evidence(
            manifest, feature_matrix=feature_matrix, root=path.parent
        )
    except (KeyError, OSError, UnicodeDecodeError, ValueError, json.JSONDecodeError):
        return False
    return actual == expected


def _fraction_complete(report, section, metric):
    value = (((report.get(section) or {}).get(metric) or {}).get("value"))
    return value is not None and float(value) == 1.0


def _metric_value(report, section, metric):
    value = (((report.get(section) or {}).get(metric) or {}).get("value"))
    return float(value) if value is not None else None


def _metric_not_decreased(baseline, candidate, section, metric):
    baseline_section = baseline.get(section) or {}
    candidate_section = candidate.get(section) or {}
    before = _metric_value(baseline, section, metric)
    after = _metric_value(candidate, section, metric)
    return (
        int(baseline_section.get("applicable_cases") or 0) > 0
        and int(candidate_section.get("applicable_cases") or 0) > 0
        and before is not None
        and after is not None
        and after >= before
    )


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
        decomposition = candidate.get("decomposition_evaluation") or {}
        checks = {
            **common,
            "complex_answer_gain": _group_rate(candidate, "complex")
            >= _group_rate(baseline, "complex") + 0.10,
            "simple_planner_calls_zero": int(decomposition.get("simple_planner_calls", -1)) == 0,
            "branch_accuracy_complete": float(decomposition.get("branch_accuracy") or 0.0) == 1.0,
            "branch_citations_complete": float(decomposition.get("citation_accuracy") or 0.0) == 1.0,
            "request_budgets_respected": int(decomposition.get("budget_violations", -1)) == 0,
            "latency_within_budget": _ratio(
                float(candidate.get("latency_p95_ms") or 0), float(baseline.get("latency_p95_ms") or 0)
            ) <= 1.5,
            "cost_within_budget": _ratio(
                float(candidate.get("total_estimated_cost") or 0),
                float(baseline.get("total_estimated_cost") or 0),
            ) <= 1.5,
        }
        limits = {
            "min_complex_answer_gain": 0.10,
            "max_latency_ratio": 1.5,
            "max_cost_ratio": 1.5,
            "max_subqueries": 3,
            "max_corrections": 1,
            "max_final_generations": 1,
        }
    elif stage == "graph_retrieval":
        graph = candidate.get("graph_evaluation") or {}
        baseline_graph = baseline.get("graph_evaluation") or {}
        domains = metadata.get("domain_coverage") or {}
        checks = {
            **common,
            "relational_accuracy_gain": float(
                graph.get("relational_answer_accuracy") or 0.0
            ) >= float(
                baseline_graph.get("relational_answer_accuracy") or 0.0
            ) + 0.10,
            "reviewed_edge_precision": float(metadata.get("reviewed_edge_precision", 0.0)) >= 0.95,
            "review_workflow_fixture_passed": metadata.get("workflow_fixture_passed") is True,
            "review_sample_is_independent": metadata.get("review_sample_source") == "independent",
            "review_sample_size_sufficient": int(metadata.get("review_sample_count", 0)) >= 20,
            "approved_edge_pool_sufficient": int(metadata.get("approved_edge_count", 0)) >= 20,
            "structured_coverage": float(metadata.get("structured_coverage", 0.0)) >= 0.80,
            "provenance_complete": float(metadata.get("provenance_completeness", 0.0)) == 1.0,
            "pilot_domains_covered": all(bool(domains.get(name)) for name in ("Technical", "Production", "Maintenance")),
            "pending_edges_never_served": int(metadata.get("pending_serving_edges", -1)) == 0,
            "traversal_budget_respected": int(graph.get("budget_violations", -1)) == 0,
            "router_scope_respected": int(graph.get("non_relational_graph_calls", -1)) == 0,
            "latency_within_budget": _ratio(
                float(candidate.get("latency_p95_ms") or 0), float(baseline.get("latency_p95_ms") or 0)
            ) <= 1.5,
        }
        limits = {
            "min_accuracy_gain": 0.10, "min_structured_coverage": 0.80,
            "min_reviewed_edge_precision": 0.95, "max_latency_ratio": 1.5,
            "min_independent_review_sample": 20,
            "min_approved_edge_pool": 20,
            "max_hops": 2, "max_edges": 50,
        }
    elif stage == "community_summaries":
        target = float(metadata.get("min_global_answer_gain") or 0.0)
        max_indexing_latency = float(
            metadata.get("max_indexing_latency_ms") or 0.0
        )
        checks = {
            **common,
            "readiness_schema_valid": (
                metadata.get("schema") == "community-summary-readiness-v1"
            ),
            "graph_gate_prerequisite": (
                metadata.get("prerequisite_graph_gate_passed") is True
            ),
            "reviewed_edge_precision": float(
                metadata.get("reviewed_edge_precision") or 0.0
            ) >= 0.95,
            "quality_target_locked": (
                metadata.get("target_locked_before_benchmark") is True
                and target > 0.0
            ),
            "global_answer_gain": _group_rate(candidate, "global")
            >= _group_rate(baseline, "global") + target,
            "local_quality_not_decreased": _group_rate(candidate, "local")
            >= _group_rate(baseline, "local"),
            "relational_quality_not_decreased": _group_rate(candidate, "relational")
            >= _group_rate(baseline, "relational"),
            "claim_precision_not_decreased": _metric_not_decreased(
                baseline, candidate, "claim_evaluation", "claim_precision"
            ),
            "citation_accuracy_not_decreased": _metric_not_decreased(
                baseline, candidate, "citation_evaluation", "citation_accuracy"
            ),
            "citation_precision_not_decreased": _metric_not_decreased(
                baseline, candidate, "citation_evaluation", "citation_precision"
            ),
            "provenance_complete": float(
                metadata.get("provenance_completeness") or 0.0
            ) == 1.0,
            "pending_summaries_never_served": int(
                metadata.get("pending_summaries_served", -1)
            ) == 0,
            "stale_summary_behavior": int(
                metadata.get("stale_summary_violations", -1)
            ) == 0,
            "serving_epoch_valid": metadata.get("serving_epoch_valid") is True,
            "indexing_latency_within_budget": (
                max_indexing_latency > 0
                and float(metadata.get("indexing_latency_ms") or float("inf"))
                <= max_indexing_latency
            ),
            "latency_within_budget": _ratio(
                float(candidate.get("latency_p95_ms") or 0),
                float(baseline.get("latency_p95_ms") or 0),
            ) <= 1.5,
            "cost_within_budget": _ratio(
                float(candidate.get("total_estimated_cost") or 0),
                float(baseline.get("total_estimated_cost") or 0),
            ) <= 1.5,
        }
        limits = {
            "min_reviewed_edge_precision": 0.95,
            "min_global_answer_gain": target,
            "max_latency_ratio": 1.5,
            "max_cost_ratio": 1.5,
            "max_indexing_latency_ms": max_indexing_latency,
        }
    elif stage == "integrated_hardening":
        integrity = metadata.get("artifact_integrity") or {}
        matrix_evidence = metadata.get("combination_matrix_evidence") or {}
        combination_results = matrix_evidence.get("combination_results") or []
        prerequisites = metadata.get("prerequisites") or {}
        required_prerequisites = {
            "crag", "grounded_math", "late_interaction",
            "query_decomposition", "graph_retrieval",
        }
        required_combinations = {
            "crag_repair", "crag_grounded_math", "crag_late_interaction",
            "crag_query_decomposition", "crag_graph_retrieval",
            "decomposition_graph", "decomposition_late_interaction",
        }
        checks = {
            **common,
            "artifact_integrity_verified": (
                metadata.get("schema") == "integrated-gate-metadata-v1"
                and
                integrity.get("passed") is True
                and integrity.get("benchmark_conditions_match") is True
                and _artifact_references_verified(integrity)
                and _matrix_evidence_recomputed(integrity, matrix_evidence)
            ),
            "gate_arms_bound_to_primary_combination": (
                metadata.get("primary_gate_inputs")
                == metadata.get("_actual_gate_inputs")
                and bool(metadata.get("primary_gate_inputs"))
            ),
            "combination_matrix_valid": (
                (metadata.get("matrix_validation") or {}).get("passed") is True
            ),
            "combination_results_complete": (
                matrix_evidence.get("passed") is True
                and {row.get("combination_id") for row in combination_results}
                == required_combinations
            ),
            "all_combination_quality_gates_passed": (
                len(combination_results) == len(required_combinations)
                and all(row.get("passed") is True for row in combination_results)
            ),
            "strict_buffered_stream_verified": (
                (metadata.get("strict_stream_evidence") or {}).get("passed") is True
            ),
            "cache_isolation_verified": (
                (metadata.get("cache_isolation") or {}).get("passed") is True
            ),
            "rollback_verified": (
                (metadata.get("rollback_evidence") or {}).get("passed") is True
            ),
            "prerequisites_completed": (
                set(prerequisites) == required_prerequisites
                and all(prerequisites.values())
            ),
            "release_decisions_complete": (
                metadata.get("release_decisions_complete") is True
            ),
            "claim_precision_not_decreased": _metric_not_decreased(
                baseline, candidate, "claim_evaluation", "claim_precision"
            ),
            "citation_accuracy_not_decreased": _metric_not_decreased(
                baseline, candidate, "citation_evaluation", "citation_accuracy"
            ),
            "citation_precision_not_decreased": _metric_not_decreased(
                baseline, candidate, "citation_evaluation", "citation_precision"
            ),
        }
        limits = {
            "max_planners": 1, "max_subqueries": 3,
            "max_corrections": 1, "max_repairs": 1,
            "max_calculations": 1, "max_graph_edges": 50,
            "max_provider_retries": 2, "max_final_generations": 1,
            "max_latency_ratio": 1.5, "max_cost_ratio": 1.5,
        }
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
    parser.add_argument("stage", choices=(
        "grounded_math", "late_interaction", "query_decomposition",
        "graph_retrieval", "community_summaries",
        "integrated_hardening",
    ))
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
    baseline = read(args.baseline)
    candidate = read(args.candidate)
    metadata = read(args.metadata) if args.metadata else {}
    if args.stage == "integrated_hardening":
        metadata["_actual_gate_inputs"] = {
            "baseline_eval_sha256": _sha256(args.baseline),
            "candidate_eval_sha256": _sha256(args.candidate),
            "baseline_trace_sha256": _sha256(args.baseline_trace),
            "candidate_trace_sha256": _sha256(args.candidate_trace),
        }
    result = compare(
        args.stage,
        baseline,
        candidate,
        metadata,
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
