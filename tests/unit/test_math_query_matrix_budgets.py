"""Matrix request telemetry must obey the actual arm, without weakening old gates."""
import pytest

from scripts.integrated_eval.math_query_matrix import evaluate_arm_budgets


def _case(**changes):
    return {"id": "interaction-1", "combination_id": "math_query",
            "planner_count": 1, "subquery_count": 2, "calculation_count": 1,
            "correction_count": 0, "repair_count": 0, "graph_edge_count": 0,
            "provider_retries": 0, "final_generation_count": 1,
            "deadline_exceeded": False, **changes}


def test_interaction_candidate_allows_planning_and_calculation_but_never_retry():
    report = evaluate_arm_budgets([_case()], row="math_query", label="candidate")
    assert report["passed"] is True
    assert report["limits"]["provider_retries"] == 0
    rejected = evaluate_arm_budgets(
        [_case(provider_retries=1)], row="math_query", label="candidate")
    assert rejected["passed"] is False
    assert any(item["field"] == "provider_retries" for item in rejected["violations"])


@pytest.mark.parametrize("row,planner,calculation", [
    ("math_only", 0, 1), ("query_only", 1, 0), ("math_query", 1, 1),
])
@pytest.mark.parametrize("label", ["baseline", "candidate"])
def test_each_arm_enforces_its_actual_active_features(row, planner, calculation, label):
    active = label == "candidate"
    case = _case(combination_id=row, planner_count=planner if active else 0,
                 subquery_count=2 if active and planner else 0,
                 calculation_count=calculation if active else 0)
    assert evaluate_arm_budgets([case], row=row, label=label)["passed"] is True
    disabled = ["correction_count", "repair_count", "graph_edge_count"]
    if not active or not planner:
        disabled += ["planner_count", "subquery_count"]
    if not active or not calculation:
        disabled += ["calculation_count"]
    for field in disabled:
        report = evaluate_arm_budgets([{**case, field: 1}], row=row, label=label)
        assert report["passed"] is False, field


@pytest.mark.parametrize("value", [None, False, "0", 0.0, -1, 1])
def test_zero_retry_requires_explicit_integer_zero(value):
    assert evaluate_arm_budgets(
        [_case(provider_retries=value)], row="math_query", label="candidate"
    )["passed"] is False


@pytest.mark.parametrize("cases", [[], [_case(combination_id="query_decomposition")]])
def test_empty_or_different_combination_cannot_satisfy_arm(cases):
    assert evaluate_arm_budgets(cases, row="math_query", label="candidate")["passed"] is False


@pytest.mark.parametrize("row,label", [("other", "candidate"), ("math_query", "other")])
def test_unknown_arm_is_rejected(row, label):
    with pytest.raises(ValueError, match="unknown matrix arm"):
        evaluate_arm_budgets([_case()], row=row, label=label)


def test_existing_gate_keeps_its_five_combinations_and_two_retry_ceiling():
    from mech_chatbot.evaluation.integrated_hardening import evaluate_request_budgets

    old_case = _case(combination_id="query_decomposition", calculation_count=0,
                     provider_retries=2)
    assert evaluate_request_budgets([old_case])["passed"] is True
    assert evaluate_request_budgets([_case()])["passed"] is False
    assert evaluate_request_budgets([old_case])["limits"]["provider_retries"] == 2


@pytest.mark.parametrize("value", [3, -1, False, 0.0, "0", None])
def test_shared_budget_cannot_raise_or_coerce_retry_ceiling(value):
    from mech_chatbot.evaluation.integrated_hardening import evaluate_request_budgets

    with pytest.raises(ValueError, match="retry ceiling"):
        evaluate_request_budgets([_case()], maximum_provider_retries=value)


@pytest.mark.parametrize("contract", [{}, {"": set()}, {"math_query": {"UNKNOWN"}},
                                      {"math_query": ["RAG_GROUNDED_MATH_ENABLED"]}])
def test_shared_budget_rejects_invalid_feature_contract(contract):
    from mech_chatbot.evaluation.integrated_hardening import evaluate_request_budgets

    with pytest.raises(ValueError, match="budget combinations"):
        evaluate_request_budgets([_case()], combinations=contract)
