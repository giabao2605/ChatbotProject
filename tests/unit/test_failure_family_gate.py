from __future__ import annotations

from scripts.eval.failure_family_gate import compare_failure_family_pair


def _report(label, *, wrong=0, leakage=0, provider_failures=0, family_decision="accepted"):
    return {
        "schema": "rag-labeled-eval-v4",
        "run_label": label,
        "git_sha": "a" * 40,
        "manifest_sha256s": ["b" * 64],
        "snapshot_fingerprint": "c" * 64,
        "provider_configuration_sha256": "d" * 64,
        "governance_scope_sha256": "e" * 64,
        "benchmark_concurrency": 1,
        "collection": "TaiLieuKyThuat_v2",
        "execution_context": "evaluation",
        "outcome_confusion": {"wrong_answer": wrong, "leakage": leakage},
        "provider_retries": 0,
        "failure_family_evaluation": {
            "schema": "failure-family-eval-v1",
            "applicable": True,
            "decision": family_decision,
            "provider_failure_count": provider_failures,
            "leakage_count": leakage,
            "families": {
                "EVIDENCE_POLICY_ERROR": {
                    "decision": family_decision,
                    "seed_passed": family_decision == "accepted",
                    "sample_complete": True,
                    "common_pass_rate": 1.0,
                    "holdout_pass_rate": 1.0,
                    "leakage_count": leakage,
                    "provider_failure_count": provider_failures,
                }
            },
        },
        "cases": [{
            "correction_count": 1,
            "repair_count": 1,
            "planner_count": 1,
            "calculation_count": 1,
            "graph_max_hops": 2,
            "graph_edge_count": 50,
        }],
    }


def test_failure_family_gate_accepts_complete_pair_and_rollback():
    result = compare_failure_family_pair(
        _report("baseline", wrong=1),
        _report("candidate", wrong=0),
        rollback={"schema": "feature-rollback-verification-v1", "passed": True},
    )

    assert result["decision"] == "accepted"
    assert result["passed"] is True
    assert all(result["checks"].values())


def test_failure_family_gate_marks_provider_failure_inconclusive():
    candidate = _report(
        "candidate", provider_failures=1, family_decision="inconclusive"
    )

    result = compare_failure_family_pair(
        _report("baseline"),
        candidate,
        rollback={"schema": "feature-rollback-verification-v1", "passed": True},
    )

    assert result["decision"] == "inconclusive"
    assert result["passed"] is False
    assert result["reason"] == "provider_failure"


def test_failure_family_gate_rejects_safety_or_quality_regression():
    candidate = _report("candidate", wrong=2, leakage=1, family_decision="rejected")

    result = compare_failure_family_pair(
        _report("baseline", wrong=1),
        candidate,
        rollback={"schema": "feature-rollback-verification-v1", "passed": True},
    )

    assert result["decision"] == "rejected"
    assert result["checks"]["leakage_zero"] is False
    assert result["checks"]["wrong_answer_not_increased"] is False


def test_failure_family_gate_rejects_wrong_refusal_regression():
    baseline = _report("baseline")
    candidate = _report("candidate")
    baseline["outcome_confusion"]["wrong_refusal"] = 0
    candidate["outcome_confusion"]["wrong_refusal"] = 1
    result = compare_failure_family_pair(
        baseline,
        candidate,
        rollback={"schema": "feature-rollback-verification-v1", "passed": True},
    )
    assert result["checks"]["wrong_refusal_not_increased"] is False
    assert result["decision"] == "rejected"


def test_failure_family_gate_is_inconclusive_for_unmatched_pair_or_missing_rollback():
    candidate = _report("candidate")
    candidate["snapshot_fingerprint"] = "f" * 64

    result = compare_failure_family_pair(_report("baseline"), candidate, rollback={})

    assert result["decision"] == "inconclusive"
    assert result["checks"]["benchmark_conditions_match"] is False
    assert result["checks"]["rollback_verified"] is False


def test_failure_family_gate_rejects_any_request_budget_overrun():
    candidate = _report("candidate")
    candidate["cases"][0]["planner_count"] = 2

    result = compare_failure_family_pair(
        _report("baseline"),
        candidate,
        rollback={"schema": "feature-rollback-verification-v1", "passed": True},
    )

    assert result["decision"] == "rejected"
    assert result["checks"]["request_budgets_respected"] is False


def test_failure_family_gate_rejects_complete_sample_when_regression_seed_fails():
    candidate = _report("candidate", family_decision="rejected")

    result = compare_failure_family_pair(
        _report("baseline"),
        candidate,
        rollback={"schema": "feature-rollback-verification-v1", "passed": True},
    )

    assert result["checks"]["family_contract_complete"] is True
    assert result["checks"]["all_families_accepted"] is False
    assert result["decision"] == "rejected"
