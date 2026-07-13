import pytest

from mech_chatbot.rag.query_decomposition import (
    CorrectionBudget,
    build_plan,
    codes_in_query,
    execute_plan,
)


pytestmark = pytest.mark.unit


def test_branch_code_extraction_is_normalized_and_does_not_inherit_other_codes():
    assert codes_in_query("BOM MA-100") == ("ma-100",)


def test_simple_query_does_not_call_planner():
    calls = []
    plan = build_plan("Quy trình bảo trì là gì?", planner=lambda _: calls.append(True))

    assert plan.is_complex is False
    assert plan.subqueries == ()
    assert calls == []


def test_complex_query_is_limited_to_three_subqueries_and_drops_invented_codes():
    plan = build_plan(
        "So sánh BOM MA-100 và quy trình bảo trì của MA-200, đồng thời cho biết vật liệu?",
        planner=lambda _: {
            "subqueries": [
                "BOM MA-100",
                "Bảo trì MA-200",
                "Vật liệu MA-100",
                "Chi phí ZZ-999",
            ]
        },
    )

    assert plan.is_complex is True
    assert len(plan.subqueries) == 3
    assert all("ZZ-999" not in query for query in plan.subqueries)


def test_decomposed_retrieval_reuses_access_context_and_one_shared_correction():
    context = {"allowed_departments": ["Technical"], "allowed_sites": ["HQ"]}
    seen = []

    def retrieve(query, access_context, correction_budget):
        seen.append((query, access_context, correction_budget.claim()))
        return [query]

    plan = build_plan(
        "Cho biết BOM và quy trình bảo trì?",
        planner=lambda _: {"subqueries": ["BOM", "quy trình bảo trì"]},
    )
    results = execute_plan(plan, retrieve, context, correction_budget=CorrectionBudget(1))

    assert len(results) == 2
    assert all(item[1] is context for item in seen)
    assert sum(item[2] for item in seen) == 1
