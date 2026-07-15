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

    result = gate.compare("query_decomposition", baseline, candidate, {"simple_planner_calls": 0})

    assert result["passed"] is True


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
