import pytest
import threading
import time

from mech_chatbot.rag.query_decomposition import (
    BranchPlan,
    BranchRetrievalResult,
    CorrectionBudget,
    audit_decomposition_stream,
    build_plan,
    build_decomposition_instruction,
    reconcile_grounded_calculation_branch,
    codes_in_query,
    compile_query_plan,
    execute_plan,
    sufficient_branch_documents,
)
from langchain_core.documents import Document


pytestmark = pytest.mark.unit


def test_decomposition_stream_marks_partial_and_audits_rendered_branch_sources():
    branches = [
        {
            "outcome": "full_answer",
            "citations": [{"source_id": "D198P1"}, {"source_id": "D201P1"}],
        },
        {"outcome": "access_denied", "citations": []},
    ]
    rendered = "".join(audit_decomposition_stream(iter(["Evidence [SRC:D198P1]"]), branches))

    assert rendered == "Trả lời được một phần: Evidence [SRC:D198P1]"
    assert branches[0]["rendered_source_ids"] == ["D198P1"]
    assert branches[1]["rendered_source_ids"] == []


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


def test_compile_query_plan_falls_back_when_planner_repeats_whole_question():
    question = (
        "Cho biết số lượng DEMO-PART-A, đồng thời nêu vật liệu DEMO-PART-B "
        "và quy trình bảo trì DEMO-PART-C?"
    )

    plan = compile_query_plan(
        question,
        {"allowed_departments": ("Technical",), "allowed_sites": ("HQ",)},
        planner=lambda original: {"subqueries": [original]},
    )

    assert isinstance(plan, BranchPlan)
    assert plan.is_complex is True
    assert len(plan.intents) == 3
    assert len(plan.subqueries) == 3
    assert plan.used_fallback is True
    assert plan.intent_coverage == (True, True, True)
    assert set(code for query in plan.subqueries for code in codes_in_query(query)) == {
        "demo-part-a", "demo-part-b", "demo-part-c",
    }


def test_compile_query_plan_fallback_keeps_three_comma_separated_intents():
    question = (
        "Cho biết giá trị CRAG-EVAL-NUM-001, chu kỳ mắt cú xanh "
        "và quy trình lắp CRAG-EVAL-PART-C?"
    )

    plan = compile_query_plan(
        question,
        {},
        planner=lambda original: {"subqueries": [original]},
    )

    assert len(plan.subqueries) == 3
    assert plan.intent_coverage == (True, True, True)


def test_compile_query_plan_keeps_complete_bounded_planner_output():
    question = "Cho biết BOM MA-100 và quy trình bảo trì MA-200?"
    plan = compile_query_plan(
        question,
        {},
        planner=lambda _original: {
            "subqueries": ["BOM MA-100", "quy trình bảo trì MA-200"]
        },
    )

    assert plan.subqueries == ("BOM MA-100", "quy trình bảo trì MA-200")
    assert plan.intent_coverage == (True, True)
    assert plan.used_fallback is False


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
    instruction = build_decomposition_instruction([
        {"outcome": "full_answer"},
        {"outcome": "insufficient_evidence"},
        {"outcome": "access_denied", "restricted_source": "secret-payroll.md"},
    ])

    assert "1 nhánh chưa có đủ bằng chứng" in instruction
    assert "1 nhánh không thể truy cập" in instruction
    assert "secret-payroll.md" not in instruction


def test_full_decomposition_instruction_limits_each_answer_to_the_asked_fact():
    instruction = build_decomposition_instruction([
        {"outcome": "full_answer"},
        {"outcome": "full_answer"},
    ])

    assert "chỉ trả lời đúng thông tin được hỏi" in instruction.lower()
    assert "không thêm thuộc tính khác" in instruction.lower()
    assert "không suy diễn" in instruction.lower()


def test_common_prompt_limits_answers_to_the_requested_attribute():
    from mech_chatbot.rag.prompt import _COMMON_RULES_VI

    assert "chỉ nêu đúng thông tin được hỏi" in _COMMON_RULES_VI.lower()
    assert "không thêm mã, vật liệu hoặc thông số" in _COMMON_RULES_VI.lower()


def test_grounded_negative_branch_is_reported_as_missing():
    instruction = build_decomposition_instruction([
        {"outcome": "insufficient_evidence", "grounded_negative": True},
    ])
    assert "1 nhánh chưa có đủ bằng chứng" in instruction


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
    extra = Document(page_content="extra", metadata={"doc_id": 9, "trang_so": 1})
    ambiguous = Document(page_content="unproven", metadata={"doc_id": 2, "trang_so": 1})
    results = [
        BranchRetrievalResult([sufficient, extra], 5, "strict", 1.0, None),
        BranchRetrievalResult([ambiguous], 5, "broad", 1.0, None),
    ]

    selected = sufficient_branch_documents(results, [
        {"outcome": "full_answer"},
        {"outcome": "insufficient_evidence"},
    ])

    assert selected == [sufficient, extra]


def test_general_branch_limits_generation_to_top_evidence():
    relevant = Document(page_content="relevant", metadata={"doc_id": 1, "trang_so": 1})
    unrelated = Document(page_content="unrelated", metadata={"doc_id": 2, "trang_so": 1})
    results = [
        BranchRetrievalResult(
            [relevant, unrelated],
            5,
            "general:explicit_dense_bm25_rrf",
            1.0,
            None,
        ),
    ]

    selected = sufficient_branch_documents(results, [{"outcome": "full_answer"}])

    assert selected == [relevant]


def test_grounded_negative_branch_documents_do_not_reach_final_generation():
    negative = Document(page_content="Không có trường đơn giá.", metadata={"doc_id": 3, "trang_so": 1})
    results = [BranchRetrievalResult([negative], 5, "strict", 1.0, None)]
    selected = sufficient_branch_documents(results, [
        {"outcome": "insufficient_evidence", "grounded_negative": True},
    ])
    assert selected == []


def test_grounded_calculation_reconciles_one_bom_branch_without_mutation():
    branches = (
        {
            "branch_id": "branch-1",
            "outcome": "insufficient_evidence",
            "grounded_negative": True,
            "bom_lookup": True,
            "citations": [],
        },
        {
            "branch_id": "branch-2",
            "outcome": "full_answer",
            "grounded_negative": False,
            "bom_lookup": False,
            "citations": [{"source_id": "D70P1"}],
        },
    )

    reconciled = reconcile_grounded_calculation_branch(
        branches,
        [{"source_id": "D43P1"}],
    )

    assert reconciled[0] == {
        **branches[0],
        "outcome": "full_answer",
        "grounded_negative": False,
        "citations": [{"source_id": "D43P1"}],
    }
    assert reconciled[1] == branches[1]
    assert branches[0]["outcome"] == "insufficient_evidence"
