"""Stage-specific rollout gates for retrieval-intelligence milestones."""

from __future__ import annotations

import argparse
import hashlib
import json
import math
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[2]
SRC = ROOT / "src"
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from mech_chatbot.evaluation.late_interaction import HARD_NEGATIVE_SCENARIOS
from mech_chatbot.evaluation.integrated_hardening import (
    REQUIRED_COMBINATIONS,
    REQUIRED_PREREQUISITES,
)
from mech_chatbot.governance.artifact_references import (
    load_bytes_reference,
    load_json_reference,
)
from mech_chatbot.governance.review_governance import (
    distinct_reviewer_count,
    independent_reviewer_diversity_valid,
    review_governance_status,
)
from scripts.graph.report import validate_review_samples


def _ratio(candidate, baseline):
    if baseline == 0:
        return 1.0 if candidate == 0 else float("inf")
    return candidate / baseline


def _finite_non_negative_number(value):
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    number = float(value)
    return number if math.isfinite(number) and number >= 0 else None


def _valid_group_rate(groups, name):
    group = groups.get(name)
    if not isinstance(group, dict):
        return None
    rate = _finite_non_negative_number(group.get("pass_rate"))
    return rate if rate is not None and rate <= 1 else None


def _group_rate(report, name):
    return float((report.get("evaluation_groups", {}).get(name) or {}).get("pass_rate") or 0.0)


def _graph_governance_payload(metadata, candidate):
    if metadata.get("review_mode") != "single_owner":
        return None
    governance = load_json_reference(
        metadata.get("review_governance"),
        root=ROOT,
    )
    if governance is None:
        return None
    status = review_governance_status(
        governance,
        source_commit=candidate.get("git_sha"),
        scope="controlled_demo",
    )
    return governance if (
        status.valid
        and status.mode == metadata.get("review_mode")
        and status.review_source == metadata.get("review_sample_source")
    ) else None


def _graph_review_samples_reference_valid(
    metadata,
    candidate,
    governance,
):
    raw = load_bytes_reference(
        metadata.get("review_samples"),
        root=ROOT,
        expected_format="jsonl",
    )
    if raw is None:
        return False
    try:
        samples = [
            json.loads(line)
            for line in raw.decode("utf-8").splitlines()
            if line.strip()
        ]
        if not all(isinstance(sample, dict) for sample in samples):
            return False
        approved_edge_ids = metadata.get("approved_edge_ids")
        approved_edge_count = metadata.get("approved_edge_count")
        if (
            not isinstance(approved_edge_ids, list)
            or type(approved_edge_count) is not int
            or len(approved_edge_ids) != approved_edge_count
            or len({str(value) for value in approved_edge_ids})
            != len(approved_edge_ids)
        ):
            return False
        source = metadata.get("review_sample_source")
        reviewed = validate_review_samples(
            samples,
            require_independent=source == "independent",
            allowed_edge_ids=set(approved_edge_ids),
            review_governance=governance,
            review_governance_source_commit=(
                candidate.get("git_sha") if governance else None
            ),
            review_governance_scope=(
                "controlled_demo" if governance else None
            ),
        )
        if source == "owner_review" and governance is None:
            return False
        correct = (
            sum(bool(sample.get("expected_correct")) for sample in reviewed)
            if source == "independent"
            else sum(
                (
                    bool(sample.get("expected_correct"))
                    and sample.get("decision") == "approved"
                )
                or (
                    not bool(sample.get("expected_correct"))
                    and sample.get("decision") == "rejected"
                )
                for sample in reviewed
            )
        )
        precision = correct / len(reviewed) if reviewed else 0.0
        observed_precision = float(
            metadata.get("reviewed_edge_precision")
        )
        return (
            type(metadata.get("review_sample_count")) is int
            and metadata.get("review_sample_count") == len(reviewed)
            and type(metadata.get("reviewer_count")) is int
            and metadata.get("reviewer_count") == distinct_reviewer_count(
                sample.get("reviewer") for sample in reviewed
            )
            and abs(observed_precision - precision) <= 1e-12
        )
    except (
        AttributeError,
        TypeError,
        UnicodeDecodeError,
        ValueError,
        json.JSONDecodeError,
    ):
        return False


def _provider_failure_evidence(report):
    if not isinstance(report, dict):
        return False, 1
    explicit = report.get("provider_failure_count")
    cases = report.get("cases")
    if (
        not isinstance(explicit, int)
        or isinstance(explicit, bool)
        or explicit < 0
        or not isinstance(cases, list)
        or any(
            not isinstance(row, dict)
            or not isinstance(row.get("provider_failure"), bool)
            for row in cases
        )
    ):
        return False, 1
    observed = sum(row["provider_failure"] for row in cases)
    return explicit == observed, max(explicit, observed)


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
    decisions = [
        reference for reference in references
        if reference.get("schema") == "integrated-release-decisions-v1"
    ]
    if len(matrices) != 1 or len(decisions) != 1:
        return False
    try:
        from scripts.integrated_eval.compose_gate_metadata import load_matrix_evidence

        path = Path(manifests[0]["path"])
        manifest = json.loads(path.read_text(encoding="utf-8"))
        feature_matrix = json.loads(
            Path(matrices[0]["path"]).read_text(encoding="utf-8")
        )
        release_decisions = json.loads(
            Path(decisions[0]["path"]).read_text(encoding="utf-8")
        )
        actual, _ = load_matrix_evidence(
            manifest,
            feature_matrix=feature_matrix,
            release_decisions=release_decisions,
            root=path.parent,
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
    baseline_provider_valid, baseline_provider_failures = (
        _provider_failure_evidence(baseline)
    )
    candidate_provider_valid, candidate_provider_failures = (
        _provider_failure_evidence(candidate)
    )
    candidate_cases = candidate.get("cases")
    candidate_case_rows = (
        candidate_cases
        if isinstance(candidate_cases, list)
        and all(isinstance(row, dict) for row in candidate_cases)
        else ()
    )
    common = {
        "baseline_provider_telemetry_valid": baseline_provider_valid,
        "candidate_provider_telemetry_valid": candidate_provider_valid,
        "baseline_provider_failures_zero": (
            baseline_provider_valid and baseline_provider_failures == 0
        ),
        "candidate_provider_failures_zero": (
            candidate_provider_valid and candidate_provider_failures == 0
        ),
        "wrong_answer_not_increased": candidate_outcomes.get("wrong_answer", 0)
        <= baseline_outcomes.get("wrong_answer", 0),
        "leakage_zero": candidate_outcomes.get("leakage", 0) == 0,
        "provider_retries_not_increased": int(candidate.get("provider_retries") or 0)
        <= int(baseline.get("provider_retries") or 0),
    }
    if stage == "grounded_math":
        max_calculations = max(
            (int(row.get("calculation_count") or 0) for row in candidate_case_rows),
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
        reference_provider_valid, reference_provider_failures = (
            _provider_failure_evidence(reference)
        )
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
        required_hard_negatives = set(HARD_NEGATIVE_SCENARIOS)
        family_sets = [set((report.get("query_families") or {}).keys()) for report in (
            reference, baseline, candidate,
        )]
        candidate_hard_negative = candidate.get("hard_negative_coverage") or {}
        baseline_families = baseline.get("query_families") or {}
        candidate_families = candidate.get("query_families") or {}

        def _family_recall(report, family):
            value = ((report.get("query_families") or {}).get(family) or {}).get(
                "recall_at_10"
            )
            return float(value) if value is not None else None

        family_recall_not_decreased = all(
            _family_recall(candidate, family) is not None
            and _family_recall(baseline, family) is not None
            and _family_recall(candidate, family) >= _family_recall(baseline, family)
            for family in required_hard_negatives
        )
        checks = {
            **common,
            "reference_provider_telemetry_valid": reference_provider_valid,
            "reference_provider_failures_zero": (
                reference_provider_valid and reference_provider_failures == 0
            ),
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
            "query_family_coverage_frozen": (
                bool(family_sets[0]) and family_sets[0] == family_sets[1] == family_sets[2]
            ),
            "hard_negative_coverage_complete": (
                candidate_hard_negative.get("complete") is True
                and set(candidate_hard_negative.get("required") or ())
                == required_hard_negatives
                and not candidate_hard_negative.get("missing")
                and required_hard_negatives <= set(candidate_families)
                and required_hard_negatives <= set(baseline_families)
            ),
            "query_family_recall_not_decreased": family_recall_not_decreased,
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
        review_source = metadata.get("review_sample_source")
        review_mode = metadata.get("review_mode") or (
            "multi_reviewer" if review_source == "independent" else None
        )
        review_sample_count = metadata.get("review_sample_count")
        approved_edge_count = metadata.get("approved_edge_count")
        reviewer_count = metadata.get("reviewer_count")
        baseline_cost = _finite_non_negative_number(
            baseline.get("total_estimated_cost")
        )
        candidate_cost = _finite_non_negative_number(
            candidate.get("total_estimated_cost")
        )
        baseline_groups = baseline.get("evaluation_groups")
        candidate_groups = candidate.get("evaluation_groups")
        baseline_groups = baseline_groups if isinstance(
            baseline_groups, dict
        ) else {}
        candidate_groups = candidate_groups if isinstance(
            candidate_groups, dict
        ) else {}
        non_relational_groups = set(baseline_groups) - {"relational"}
        non_relational_rates = [
            (
                _valid_group_rate(baseline_groups, name),
                _valid_group_rate(candidate_groups, name),
            )
            for name in non_relational_groups
        ]
        non_relational_quality_not_decreased = (
            bool(non_relational_groups)
            and set(candidate_groups) - {"relational"}
            == non_relational_groups
            and all(
                baseline_rate is not None
                and candidate_rate is not None
                and candidate_rate >= baseline_rate
                for baseline_rate, candidate_rate in non_relational_rates
            )
        )
        reviewer_diversity_requirement_met = (
            (
                review_mode == "single_owner"
                and type(reviewer_count) is int
                and reviewer_count == 1
            )
            or (
                review_mode == "multi_reviewer"
                and independent_reviewer_diversity_valid(
                    reviewer_count
                )
            )
        )
        review_governance_valid = (
            review_mode == "multi_reviewer"
            and review_source == "independent"
            and reviewer_diversity_requirement_met
        ) or (
            review_mode == "single_owner"
            and review_source == "owner_review"
            and metadata.get("review_governance_valid") is True
            and metadata.get("_review_governance_reference_valid") is True
            and reviewer_diversity_requirement_met
        )
        review_sample_reference_valid = (
            metadata.get("_review_samples_reference_valid") is True
        )
        review_governance_valid = (
            review_governance_valid and review_sample_reference_valid
        )
        checks = {
            **common,
            "relational_accuracy_gain": float(
                graph.get("relational_answer_accuracy") or 0.0
            ) >= float(
                baseline_graph.get("relational_answer_accuracy") or 0.0
            ) + 0.10,
            "reviewed_edge_precision": float(metadata.get("reviewed_edge_precision", 0.0)) >= 0.95,
            "review_workflow_fixture_passed": metadata.get("workflow_fixture_passed") is True,
            "review_sample_governance_valid": review_governance_valid,
            "review_sample_reference_valid": review_sample_reference_valid,
            "reviewer_diversity_requirement_met": (
                reviewer_diversity_requirement_met
            ),
            "review_sample_size_sufficient": (
                type(review_sample_count) is int
                and type(approved_edge_count) is int
                and review_sample_count >= max(20, approved_edge_count)
            ),
            "approved_edge_pool_sufficient": (
                type(approved_edge_count) is int
                and approved_edge_count >= 20
            ),
            "structured_coverage": float(metadata.get("structured_coverage", 0.0)) >= 0.80,
            "provenance_complete": float(metadata.get("provenance_completeness", 0.0)) == 1.0,
            "pilot_domains_covered": all(bool(domains.get(name)) for name in ("Technical", "Production", "Maintenance")),
            "pending_edges_never_served": int(metadata.get("pending_serving_edges", -1)) == 0,
            "traversal_budget_respected": int(graph.get("budget_violations", -1)) == 0,
            "router_scope_respected": int(graph.get("non_relational_graph_calls", -1)) == 0,
            "non_relational_quality_not_decreased": (
                non_relational_quality_not_decreased
            ),
            "latency_within_budget": _ratio(
                float(candidate.get("latency_p95_ms") or 0), float(baseline.get("latency_p95_ms") or 0)
            ) <= 1.5,
            "cost_within_budget": (
                baseline_cost is not None
                and candidate_cost is not None
                and _ratio(candidate_cost, baseline_cost) <= 1.5
            ),
        }
        limits = {
            "min_accuracy_gain": 0.10, "min_structured_coverage": 0.80,
            "min_reviewed_edge_precision": 0.95, "max_latency_ratio": 1.5,
            "max_cost_ratio": 1.5,
            "min_governed_review_sample": 20,
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
                and target >= 0.10
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
        required_prerequisites = set(REQUIRED_PREREQUISITES)
        required_combinations = set(REQUIRED_COMBINATIONS)
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
    if args.stage == "late_interaction" and (not args.reference or not args.reference_trace):
        parser.error("late_interaction requires --reference and --reference-trace for the RRF arm")
    def read_json_and_hash(path):
        raw = path.read_bytes()
        return json.loads(raw.decode("utf-8")), hashlib.sha256(raw).hexdigest()

    baseline, baseline_sha = read_json_and_hash(args.baseline)
    candidate, candidate_sha = read_json_and_hash(args.candidate)
    baseline_trace_sha = hashlib.sha256(
        args.baseline_trace.read_bytes()
    ).hexdigest()
    candidate_trace_sha = hashlib.sha256(
        args.candidate_trace.read_bytes()
    ).hexdigest()
    metadata = {}
    metadata_sha = None
    if args.metadata:
        metadata, metadata_sha = read_json_and_hash(args.metadata)
    reference = None
    reference_sha = None
    reference_trace_sha = None
    if args.reference:
        reference, reference_sha = read_json_and_hash(args.reference)
        reference_trace_sha = hashlib.sha256(
            args.reference_trace.read_bytes()
        ).hexdigest()
    if args.stage == "graph_retrieval":
        governance = _graph_governance_payload(metadata, candidate)
        metadata["_review_governance_reference_valid"] = (
            metadata.get("review_mode") != "single_owner"
            or governance is not None
        )
        metadata["_review_samples_reference_valid"] = (
            _graph_review_samples_reference_valid(
                metadata,
                candidate,
                governance,
            )
        )
    if args.stage == "integrated_hardening":
        metadata["_actual_gate_inputs"] = {
            "baseline_eval_sha256": baseline_sha,
            "candidate_eval_sha256": candidate_sha,
            "baseline_trace_sha256": baseline_trace_sha,
            "candidate_trace_sha256": candidate_trace_sha,
        }
    result = compare(
        args.stage,
        baseline,
        candidate,
        metadata,
        reference,
    )
    result["inputs"] = {
        "baseline_eval_sha256": baseline_sha,
        "candidate_eval_sha256": candidate_sha,
        "baseline_trace_sha256": baseline_trace_sha,
        "candidate_trace_sha256": candidate_trace_sha,
    }
    if args.stage == "graph_retrieval":
        result["inputs"]["metadata_sha256"] = metadata_sha
    if args.reference:
        result["inputs"]["reference_eval_sha256"] = reference_sha
        result["inputs"]["reference_trace_sha256"] = reference_trace_sha
    payload = json.dumps(result, ensure_ascii=False, indent=2) + "\n"
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(payload, encoding="utf-8")
    else:
        print(payload, end="")
    return 0 if result["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
