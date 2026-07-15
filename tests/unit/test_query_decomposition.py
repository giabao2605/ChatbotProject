import pytest
import threading
import time

from mech_chatbot.rag.query_decomposition import (
    BranchRetrievalResult,
    CorrectionBudget,
    build_plan,
    build_partial_answer_instruction,
    codes_in_query,
    execute_plan,
    sufficient_branch_documents,
)
from langchain_core.documents import Document


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

    deadline = time.monotonic() + 1.0

    def retrieve(query, access_context, correction_budget, deadline_monotonic):
        seen.append((query, access_context, correction_budget.claim(), deadline_monotonic))
        return [query]

    plan = build_plan(
        "Cho biết BOM và quy trình bảo trì?",
        planner=lambda _: {"subqueries": ["BOM", "quy trình bảo trì"]},
    )
    results = execute_plan(
        plan,
        retrieve,
        context,
        correction_budget=CorrectionBudget(1),
        deadline_monotonic=deadline,
    )

    assert len(results) == 2
    assert all(item[1] is context for item in seen)
    assert sum(item[2] for item in seen) == 1
    assert all(item[3] == deadline for item in seen)


def test_partial_answer_instruction_counts_missing_and_denied_without_source_names():
    instruction = build_partial_answer_instruction([
        {"outcome": "full_answer"},
        {"outcome": "insufficient_evidence"},
        {"outcome": "access_denied", "restricted_source": "secret-payroll.md"},
    ])

    assert "1 nhánh chưa có đủ bằng chứng" in instruction
    assert "1 nhánh không thể truy cập" in instruction
    assert "secret-payroll.md" not in instruction


def test_execute_plan_returns_at_deadline_without_waiting_for_slow_branch():
    release = threading.Event()
    plan = build_plan(
        "Cho biết BOM và quy trình bảo trì?",
        planner=lambda _: {"subqueries": ["BOM", "quy trình bảo trì"]},
    )

    def retrieve(query, *_args):
        if query == "BOM":
            return [query]
        release.wait(1)
        return [query]

    started = time.monotonic()
    try:
        results = execute_plan(
            plan, retrieve, {}, deadline_monotonic=started + 0.05,
            on_timeout=lambda query: [f"timeout:{query}"],
        )
    finally:
        release.set()

    assert time.monotonic() - started < 0.5
    assert results == [["BOM"], ["timeout:quy trình bảo trì"]]


def test_only_sufficient_branch_documents_reach_final_generation():
    sufficient = Document(page_content="approved", metadata={"doc_id": 1, "trang_so": 1})
    ambiguous = Document(page_content="unproven", metadata={"doc_id": 2, "trang_so": 1})
    results = [
        BranchRetrievalResult([sufficient], 5, "strict", 1.0, None),
        BranchRetrievalResult([ambiguous], 5, "broad", 1.0, None),
    ]

    selected = sufficient_branch_documents(results, [
        {"outcome": "full_answer"},
        {"outcome": "insufficient_evidence"},
    ])

    assert selected == [sufficient]
