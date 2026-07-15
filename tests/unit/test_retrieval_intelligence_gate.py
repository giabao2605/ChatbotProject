import importlib.util
from pathlib import Path

import pytest


pytestmark = pytest.mark.unit


def _module():
    path = Path("scripts/eval/retrieval_intelligence_gate.py")
    spec = importlib.util.spec_from_file_location("retrieval_intelligence_gate", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def report(*, wrong=0, leakage=0, p95=100, cost=1, recall10=1, ndcg10=0.5, groups=None):
    return {
        "outcome_confusion": {"wrong_answer": wrong, "leakage": leakage},
        "latency_p95_ms": p95,
        "total_estimated_cost": cost,
        "ranked_retrieval": {"recall_at_10": recall10, "ndcg_at_10": ndcg10},
        "evaluation_groups": groups or {},
        "provider_retries": 0,
        "citation_evaluation": {
            "applicable_cases": 1,
            "citation_accuracy": {"value": 1.0},
            "citation_precision": {"value": 1.0},
        },
        "grounded_math_evaluation": {
            "applicable_cases": 1, "passed_cases": 1, "unsupported_number_count": 0,
            "check_totals": {
                name: {"passed": 1, "applicable": 1}
                for name in (
                    "single_plan", "status", "operation", "exact_decimal", "display_value",
                    "formula", "unit", "provenance", "unsupported_numbers_zero",
                )
            },
        },
    }


def late_arm(payload, variant):
    payload["variant"] = variant
    payload["fallback_coverage"] = {"fallback_rate": 0.0}
    payload["run_metadata"] = {
        "snapshot_fingerprint": "snapshot-1",
        "provider_configuration_sha256": "provider-1",
        "commit_sha": "commit-1",
        "manifest_sha256": "manifest-1",
    }
    return payload


def test_late_gate_enforces_quality_latency_and_storage():
    gate = _module()
    reference = late_arm(report(ndcg10=0.49), "rrf")
    baseline = late_arm(report(ndcg10=0.50), "voyage")
    candidate = late_arm(report(ndcg10=0.53, p95=120), "maxsim")

    readiness = {
        "schema": "late-interaction-readiness-v1",
        "capability_passed": True,
        "ready_for_serving": True,
        "shadow_storage_ratio": 20,
        "shadow_coverage": 1.0,
        "governance_drift": 0,
        "provenance_drift": 0,
        "vector_schema_rejected": 0,
        "orphan_points": 0,
    }
    passed = gate.compare("late_interaction", baseline, candidate, readiness, reference)
    failed = gate.compare(
        "late_interaction", baseline, candidate,
        {**readiness, "shadow_storage_ratio": 30}, reference,
    )

    assert passed["passed"] is True
    assert failed["checks"]["storage_within_budget"] is False


def test_late_gate_rejects_missing_or_non_serving_readiness_artifact():
    gate = _module()
    reference = late_arm(report(ndcg10=0.49), "rrf")
    baseline = late_arm(report(ndcg10=0.50), "voyage")
    candidate = late_arm(report(ndcg10=0.53, p95=120), "maxsim")

    missing = gate.compare("late_interaction", baseline, candidate, {}, reference)
    not_ready = gate.compare("late_interaction", baseline, candidate, {
        "schema": "late-interaction-readiness-v1",
        "capability_passed": True,
        "ready_for_serving": False,
        "shadow_storage_ratio": 20,
        "shadow_coverage": 1.0,
        "governance_drift": 0,
        "provenance_drift": 0,
        "vector_schema_rejected": 0,
        "orphan_points": 0,
    }, reference)

    assert missing["checks"]["readiness_artifact_valid"] is False
    assert not_ready["checks"]["ready_for_serving"] is False


def test_late_gate_rejects_provider_or_shadow_fallbacks():
    gate = _module()
    reference = late_arm(report(ndcg10=0.49), "rrf")
    baseline = late_arm(report(ndcg10=0.50), "voyage")
    baseline["fallback_coverage"] = {"fallback_rate": 0.25}
    candidate = late_arm(report(ndcg10=0.53, p95=120), "maxsim")
    candidate["fallback_coverage"] = {"fallback_rate": 0.05}
    readiness = {
        "schema": "late-interaction-readiness-v1",
        "capability_passed": True,
        "ready_for_serving": True,
        "shadow_storage_ratio": 20,
        "shadow_coverage": 1.0,
        "governance_drift": 0,
        "provenance_drift": 0,
        "vector_schema_rejected": 0,
        "orphan_points": 0,
    }

    result = gate.compare("late_interaction", baseline, candidate, readiness, reference)

    assert result["checks"]["voyage_baseline_valid"] is False
    assert result["checks"]["shadow_candidate_valid"] is False
    assert result["passed"] is False


def test_late_gate_binds_all_three_variants_snapshot_and_provider_configuration():
    gate = _module()
    reference = late_arm(report(ndcg10=0.49), "rrf")
    baseline = late_arm(report(ndcg10=0.50), "voyage")
    candidate = late_arm(report(ndcg10=0.53, p95=120), "maxsim")
    readiness = {
        "schema": "late-interaction-readiness-v1",
        "capability_passed": True,
        "ready_for_serving": True,
        "shadow_storage_ratio": 20,
        "shadow_coverage": 1.0,
        "governance_drift": 0,
        "provenance_drift": 0,
        "vector_schema_rejected": 0,
        "orphan_points": 0,
    }

    missing_reference = gate.compare("late_interaction", baseline, candidate, readiness)
    candidate["run_metadata"]["provider_configuration_sha256"] = "provider-2"
    drifted = gate.compare("late_interaction", baseline, candidate, readiness, reference)

    assert missing_reference["checks"]["rrf_reference_valid"] is False
    assert drifted["checks"]["provider_configuration_frozen"] is False


def test_late_gate_rejects_commit_or_manifest_drift_between_variants():
    gate = _module()
    reference = late_arm(report(ndcg10=0.49), "rrf")
    baseline = late_arm(report(ndcg10=0.50), "voyage")
    candidate = late_arm(report(ndcg10=0.53, p95=120), "maxsim")
    readiness = {
        "schema": "late-interaction-readiness-v1",
        "capability_passed": True,
        "ready_for_serving": True,
        "shadow_storage_ratio": 20,
        "shadow_coverage": 1.0,
        "governance_drift": 0,
        "provenance_drift": 0,
        "vector_schema_rejected": 0,
        "orphan_points": 0,
    }

    candidate["run_metadata"]["commit_sha"] = "commit-2"
    candidate["run_metadata"]["manifest_sha256"] = "manifest-2"
    result = gate.compare("late_interaction", baseline, candidate, readiness, reference)

    assert result["checks"]["commit_frozen_across_variants"] is False
    assert result["checks"]["manifest_frozen_across_variants"] is False


def test_decomposition_gate_requires_complex_gain_and_zero_simple_planner_calls():
    gate = _module()
    baseline = report(groups={"complex": {"pass_rate": 0.60}})
    candidate = report(
        groups={"complex": {"pass_rate": 0.71}},
        p95=140,
        cost=1.4,
    )
    candidate["decomposition_evaluation"] = {
        "branch_accuracy": 1.0,
        "citation_accuracy": 1.0,
        "simple_planner_calls": 0,
        "budget_violations": 0,
    }

    result = gate.compare("query_decomposition", baseline, candidate)

    assert result["passed"] is True


def test_decomposition_gate_rejects_branch_budget_or_simple_router_regression():
    gate = _module()
    baseline = report(groups={"complex": {"pass_rate": 0.50}})
    candidate = report(groups={"complex": {"pass_rate": 0.65}}, p95=120, cost=1.2)
    candidate["decomposition_evaluation"] = {
        "branch_accuracy": 0.9,
        "citation_accuracy": 1.0,
        "simple_planner_calls": 1,
        "budget_violations": 1,
    }

    result = gate.compare("query_decomposition", baseline, candidate)

    assert result["checks"]["simple_planner_calls_zero"] is False
    assert result["checks"]["branch_accuracy_complete"] is False
    assert result["checks"]["request_budgets_respected"] is False
    assert result["passed"] is False


def test_graph_gate_requires_coverage_precision_provenance_and_budgets():
    gate = _module()
    baseline = report(groups={"relational": {"pass_rate": 0.50}})
    candidate = report(groups={"relational": {"pass_rate": 0.61}}, p95=150)
    baseline["graph_evaluation"] = {"relational_answer_accuracy": 0.50}
    candidate["graph_evaluation"] = {
        "relational_answer_accuracy": 0.61,
        "budget_violations": 0, "non_relational_graph_calls": 0,
    }
    metadata = {
        "schema": "graph-readiness-v1", "structured_coverage": 0.8,
        "reviewed_edge_precision": 0.95, "provenance_completeness": 1.0,
        "workflow_fixture_passed": True, "review_sample_source": "independent",
        "review_sample_count": 20, "approved_edge_count": 20,
        "pending_serving_edges": 0,
        "domain_coverage": {"Technical": True, "Production": True, "Maintenance": True},
    }

    result = gate.compare("graph_retrieval", baseline, candidate, metadata)

    assert result["passed"] is True


def test_graph_gate_fails_closed_on_pending_edge_or_router_leak():
    gate = _module()
    baseline = report(groups={"relational": {"pass_rate": 0.50}})
    candidate = report(groups={"relational": {"pass_rate": 0.70}}, p95=120)
    candidate["graph_evaluation"] = {"budget_violations": 0, "non_relational_graph_calls": 1}
    metadata = {
        "schema": "graph-readiness-v1", "structured_coverage": 1.0,
        "reviewed_edge_precision": 1.0, "provenance_completeness": 1.0,
        "workflow_fixture_passed": True, "review_sample_source": "independent",
        "review_sample_count": 20, "approved_edge_count": 20,
        "pending_serving_edges": 1,
        "domain_coverage": {"Technical": True, "Production": True, "Maintenance": True},
    }

    result = gate.compare("graph_retrieval", baseline, candidate, metadata)

    assert result["checks"]["pending_edges_never_served"] is False
    assert result["checks"]["router_scope_respected"] is False


def test_graph_gate_rejects_scripted_or_too_small_review_sample():
    gate = _module()
    baseline = report(groups={"relational": {"pass_rate": 0.50}})
    candidate = report(groups={"relational": {"pass_rate": 0.70}}, p95=120)
    candidate["graph_evaluation"] = {"budget_violations": 0, "non_relational_graph_calls": 0}
    metadata = {
        "schema": "graph-readiness-v1", "structured_coverage": 1.0,
        "reviewed_edge_precision": 1.0, "provenance_completeness": 1.0,
        "workflow_fixture_passed": True, "review_sample_source": "scripted_workflow",
        "review_sample_count": 2, "approved_edge_count": 20, "pending_serving_edges": 0,
        "domain_coverage": {"Technical": True, "Production": True, "Maintenance": True},
    }

    result = gate.compare("graph_retrieval", baseline, candidate, metadata)

    assert result["checks"]["review_sample_is_independent"] is False
    assert result["checks"]["review_sample_size_sufficient"] is False
    assert result["passed"] is False


def test_community_summary_gate_requires_global_gain_without_local_regression():
    gate = _module()
    baseline = report(groups={
        "global": {"pass_rate": 0.20},
        "local": {"pass_rate": 0.80},
        "relational": {"pass_rate": 0.70},
    })
    candidate = report(p95=140, cost=1.4, groups={
        "global": {"pass_rate": 0.35},
        "local": {"pass_rate": 0.80},
        "relational": {"pass_rate": 0.70},
    })
    for arm in (baseline, candidate):
        arm["claim_evaluation"] = {
            "applicable_cases": 1, "claim_precision": {"value": 1.0}
        }
    metadata = {
        "schema": "community-summary-readiness-v1",
        "prerequisite_graph_gate_passed": True,
        "reviewed_edge_precision": 0.96,
        "target_locked_before_benchmark": True,
        "min_global_answer_gain": 0.10,
        "provenance_completeness": 1.0,
        "pending_summaries_served": 0,
        "stale_summary_violations": 0,
        "serving_epoch_valid": True,
        "indexing_latency_ms": 1000,
        "max_indexing_latency_ms": 60000,
    }

    result = gate.compare("community_summaries", baseline, candidate, metadata)

    assert result["passed"] is True


def test_community_summary_gate_fails_closed_when_quality_metrics_are_missing():
    gate = _module()
    baseline = report(groups={
        "global": {"pass_rate": 0.20}, "local": {"pass_rate": 0.80},
        "relational": {"pass_rate": 0.70},
    })
    candidate = report(groups={
        "global": {"pass_rate": 0.35}, "local": {"pass_rate": 0.80},
        "relational": {"pass_rate": 0.70},
    })
    baseline.pop("citation_evaluation")
    candidate.pop("citation_evaluation")
    metadata = {
        "schema": "community-summary-readiness-v1",
        "prerequisite_graph_gate_passed": True,
        "reviewed_edge_precision": 0.96,
        "target_locked_before_benchmark": True,
        "min_global_answer_gain": 0.10,
        "provenance_completeness": 1.0,
        "pending_summaries_served": 0,
        "stale_summary_violations": 0,
        "serving_epoch_valid": True,
        "indexing_latency_ms": 1000,
        "max_indexing_latency_ms": 60000,
    }
    result = gate.compare("community_summaries", baseline, candidate, metadata)
    assert result["checks"]["claim_precision_not_decreased"] is False
    assert result["checks"]["citation_accuracy_not_decreased"] is False
    assert result["passed"] is False


def test_community_summary_gate_fails_closed_without_graph_review_or_on_stale_summary():
    gate = _module()
    baseline = report(groups={"global": {"pass_rate": 0.20}})
    candidate = report(groups={"global": {"pass_rate": 0.40}})
    for arm in (baseline, candidate):
        arm["claim_evaluation"] = {
            "applicable_cases": 1, "claim_precision": {"value": 1.0}
        }
    metadata = {
        "schema": "community-summary-readiness-v1",
        "prerequisite_graph_gate_passed": False,
        "reviewed_edge_precision": 0.0,
        "target_locked_before_benchmark": True,
        "min_global_answer_gain": 0.10,
        "provenance_completeness": 1.0,
        "pending_summaries_served": 0,
        "stale_summary_violations": 1,
        "serving_epoch_valid": True,
        "indexing_latency_ms": 1000,
        "max_indexing_latency_ms": 60000,
    }

    result = gate.compare("community_summaries", baseline, candidate, metadata)

    assert result["checks"]["graph_gate_prerequisite"] is False
    assert result["checks"]["reviewed_edge_precision"] is False
    assert result["checks"]["stale_summary_behavior"] is False
    assert result["passed"] is False


def test_integrated_hardening_gate_requires_every_control_plane_report(tmp_path, monkeypatch):
    gate = _module()
    monkeypatch.setattr(gate, "_matrix_evidence_recomputed", lambda *_args: True)
    baseline = report()
    candidate = report(p95=140, cost=1.4)
    for arm in (baseline, candidate):
        arm["claim_evaluation"] = {
            "applicable_cases": 1, "claim_precision": {"value": 1.0}
        }
    evidence = tmp_path / "evidence.json"
    evidence.write_text('{"schema":"test-evidence-v1"}\n', encoding="utf-8")
    import hashlib
    digest = hashlib.sha256(evidence.read_bytes()).hexdigest()
    gate_inputs = {
        "baseline_eval_sha256": "baseline", "candidate_eval_sha256": "candidate",
        "baseline_trace_sha256": "baseline-trace",
        "candidate_trace_sha256": "candidate-trace",
    }
    combination_ids = [
        "crag_repair", "crag_grounded_math", "crag_late_interaction",
        "crag_query_decomposition", "crag_graph_retrieval",
        "decomposition_graph", "decomposition_late_interaction",
    ]
    metadata = {
        "schema": "integrated-gate-metadata-v1",
        "artifact_integrity": {
            "passed": True, "benchmark_conditions_match": True,
            "artifact_references": [{
                "path": str(evidence), "sha256": digest,
                "schema": "test-evidence-v1",
            }],
        },
        "primary_gate_inputs": gate_inputs,
        "_actual_gate_inputs": gate_inputs,
        "matrix_validation": {"passed": True},
        "combination_matrix_evidence": {
            "passed": True,
            "combination_results": [
                {"combination_id": name, "passed": True} for name in combination_ids
            ],
        },
        "strict_stream_evidence": {"passed": True},
        "cache_isolation": {"passed": True},
        "rollback_evidence": {"passed": True},
        "prerequisites": {name: True for name in (
            "crag", "grounded_math", "late_interaction",
            "query_decomposition", "graph_retrieval",
        )},
        "release_decisions_complete": True,
    }
    result = gate.compare("integrated_hardening", baseline, candidate, metadata)
    assert result["passed"] is True

    metadata["strict_stream_evidence"] = {"passed": False}
    failed = gate.compare("integrated_hardening", baseline, candidate, metadata)
    assert failed["checks"]["strict_buffered_stream_verified"] is False
    assert failed["passed"] is False

    metadata["strict_stream_evidence"] = {"passed": True}
    metadata["_actual_gate_inputs"] = {**gate_inputs, "candidate_trace_sha256": "other"}
    failed = gate.compare("integrated_hardening", baseline, candidate, metadata)
    assert failed["checks"]["gate_arms_bound_to_primary_combination"] is False

    metadata["_actual_gate_inputs"] = gate_inputs
    metadata["combination_matrix_evidence"]["combination_results"][0]["passed"] = False
    failed = gate.compare("integrated_hardening", baseline, candidate, metadata)
    assert failed["checks"]["all_combination_quality_gates_passed"] is False


def test_grounded_math_gate_uses_observed_per_query_calculation_budget():
    gate = _module()
    baseline = report(groups={"grounded_math": {"pass_rate": 0.0}})
    candidate = report(groups={"grounded_math": {"pass_rate": 1.0}})
    candidate["cases"] = [{"calculation_count": 2}]

    result = gate.compare("grounded_math", baseline, candidate)

    assert result["checks"]["calculation_budget"] is False
    assert result["passed"] is False


def test_grounded_math_gate_enforces_exactness_provenance_citations_latency_and_cost():
    gate = _module()
    baseline = report(groups={"grounded_math": {"pass_rate": 0.5}}, p95=100, cost=1)
    candidate = report(groups={"grounded_math": {"pass_rate": 1.0}}, p95=125, cost=1.5)
    candidate["cases"] = [{"calculation_count": 1}]

    passed = gate.compare("grounded_math", baseline, candidate)
    bad_provenance = report(groups={"grounded_math": {"pass_rate": 1.0}}, p95=125, cost=1.5)
    bad_provenance["cases"] = [{"calculation_count": 1}]
    bad_provenance["grounded_math_evaluation"]["check_totals"]["provenance"]["passed"] = 0
    failed = gate.compare("grounded_math", baseline, bad_provenance)

    assert passed["passed"] is True
    assert passed["checks"]["grounded_math_improved"] is True
    assert failed["checks"]["provenance_exact"] is False
    assert failed["passed"] is False


def test_grounded_math_gate_rejects_unsupported_numbers_and_regressions():
    gate = _module()
    baseline = report(groups={"grounded_math": {"pass_rate": 0.5}}, p95=100, cost=1)
    candidate = report(groups={"grounded_math": {"pass_rate": 1.0}}, p95=126, cost=1.51)
    candidate["cases"] = [{"calculation_count": 1}]
    candidate["grounded_math_evaluation"]["unsupported_number_count"] = 1
    candidate["citation_evaluation"]["citation_accuracy"]["value"] = 0.9

    result = gate.compare("grounded_math", baseline, candidate)

    assert result["checks"]["unsupported_numbers_zero"] is False
    assert result["checks"]["citation_accuracy_complete"] is False
    assert result["checks"]["latency_within_budget"] is False
    assert result["checks"]["cost_within_budget"] is False
