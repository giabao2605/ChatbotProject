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
    split_query_intents,
)
from langchain_core.documents import Document


pytestmark = pytest.mark.unit


@pytest.mark.parametrize('question', [
    'Mình mới vào công ty, muốn xin nghỉ phép thì làm thế nào, cần báo ai và trước bao lâu?',
    'Nếu tài khoản bị khóa, cần báo ai, gửi thông tin gì và kiểm tra ở đâu?',
    'Giấy tờ, chứng từ, hóa đơn và thông tin nhà cung cấp đã được xác nhận chưa?',
])
def test_context_and_subject_lists_are_not_independent_requests(question):
    calls = []
    plan = compile_query_plan(question, {}, planner=lambda q: calls.append(q) or {})
    assert not plan.intent_overflow
    assert plan.subqueries == (question,)
    assert calls == [question]


def test_semantic_split_retains_leave_topic_and_uses_existing_planner_once():
    question = 'Mình mới vào công ty, muốn xin nghỉ phép thì làm thế nào, cần báo ai và trước bao lâu?'
    queries = ['Xin nghỉ phép thì làm thế nào?', 'Xin nghỉ phép cần báo ai?', 'Xin nghỉ phép cần báo trước bao lâu?']
    calls = []
    plan = compile_query_plan(question, {}, planner=lambda q: calls.append(q) or {'subqueries': queries})
    assert calls == [question]
    assert plan.subqueries == tuple(queries)
    assert not plan.used_fallback and not plan.intent_overflow


def test_semantic_split_cannot_silently_truncate_more_than_three_requests():
    question = 'Mình cần hỗ trợ, tìm BOM, nêu vật liệu, kiểm tra dung sai và liệt kê bước hàn'
    queries = ['tìm BOM', 'nêu vật liệu', 'kiểm tra dung sai', 'liệt kê bước hàn']
    plan = compile_query_plan(question, {}, planner=lambda _: {'subqueries': queries})
    assert plan.intent_overflow and plan.subqueries == ()
    assert len(plan.intents) == 4
    assert all(actual.endswith(expected) for actual, expected in zip(plan.intents, queries))


def test_dependent_questions_without_introduction_keep_their_shared_topic():
    question = 'Xin nghỉ phép thế nào, cần báo ai và trước bao lâu?'
    calls = []
    plan = compile_query_plan(question, {}, planner=lambda q: calls.append(q) or {
        'subqueries': ['Xin nghỉ phép thế nào?']})
    assert calls == [question]
    assert plan.used_fallback and plan.subqueries == (question,)


@pytest.mark.parametrize('prefix', ['', 'Theo nội quy công ty, '])
def test_semantic_branches_cannot_lose_shared_topic(prefix):
    question = prefix + 'Xin nghỉ phép thế nào, cần báo ai và trước bao lâu?'
    plan = compile_query_plan(question, {}, planner=lambda _: {
        'subqueries': ['Xin nghỉ phép thế nào?', 'Cần báo ai?', 'Trước bao lâu?']})
    assert plan.used_fallback and plan.subqueries == (question,)


@pytest.mark.parametrize('question', [
    'Xin nghỉ phép thế nào; cần báo ai; trước bao lâu?',
    'Xin nghỉ phép thế nào? Cần báo ai? Trước bao lâu?',
    'Theo nội quy công ty, xin nghỉ phép thế nào, cần báo ai và trước bao lâu?',
])
def test_dependent_questions_keep_topic_across_punctuation_and_scope(question):
    plan = compile_query_plan(question, {})
    assert plan.subqueries == (question,)
    assert not plan.intent_overflow


def test_intro_does_not_hide_four_clear_requests_when_planner_unavailable():
    question = 'Mình cần hỗ trợ, tìm BOM, nêu vật liệu, kiểm tra dung sai và liệt kê bước hàn'
    plan = compile_query_plan(question, {})
    assert plan.intent_overflow and not plan.subqueries and len(plan.intents) == 4


@pytest.mark.parametrize('planner', [None, lambda _: {}, lambda q: {'subqueries': [q]},
    lambda q: {'subqueries': ['Hãy cho biết ' + q]},
    lambda q: {'subqueries': [', '.join(q.split(', ')[:2]), ', '.join(q.split(', ')[2:])]}])
def test_unresolved_multi_subject_request_cannot_bypass_limit_as_one_query(planner):
    question = 'Nghỉ phép được bao lâu, mua hàng thực hiện thế nào, bảo trì do ai phụ trách, hỗ trợ hệ thống gửi ở đâu?'
    plan = compile_query_plan(question, {}, planner=planner)
    assert plan.clarification_required and not plan.intent_overflow
    assert not plan.subqueries
    assert execute_plan(plan, lambda *_: pytest.fail('Unresolved requests must not retrieve'), {}) == []


def test_unbounded_planner_output_cannot_bypass_intent_limit():
    question = 'Mình cần hỗ trợ, tìm hồ sơ và báo ai?'
    plan = compile_query_plan(question, {}, planner=lambda _: {
        'subqueries': [f'tìm hồ sơ {i}' for i in range(9)]})
    assert plan.intent_overflow and not plan.subqueries


@pytest.mark.parametrize("question, expected", [
    ("Theo POLICY-HR-41 và POLICY-IT-72, HR gửi yêu cầu trước bao lâu và IT cấp quyền trong bao lâu?",
     ("Theo POLICY-HR-41 và POLICY-IT-72, HR gửi yêu cầu trước bao lâu",
      "Theo POLICY-HR-41 và POLICY-IT-72, IT cấp quyền trong bao lâu")),
    ("Theo POLICY-HR-41 và POLICY-ACC-25, hồ sơ thiếu giấy tờ và chứng từ thiếu hóa đơn phải ở trạng thái gì?",
     ("Theo POLICY-HR-41 và POLICY-ACC-25, hồ sơ thiếu giấy tờ và chứng từ thiếu hóa đơn phải ở trạng thái gì",)),
    ("Theo STOCK-WH-15, nêu mã vị trí, tồn tham chiếu và cách xử lý sai lệch.",
     ("Theo STOCK-WH-15, nêu mã vị trí", "Theo STOCK-WH-15, tồn tham chiếu",
      "Theo STOCK-WH-15, cách xử lý sai lệch.")),
    ("According to POLICY-A-41 and POLICY-B-72, what is the deadline? Also check the owner?",
     ("According to POLICY-A-41 and POLICY-B-72, what is the deadline",
      "According to POLICY-A-41 and POLICY-B-72, check the owner")),
])
def test_document_scope_is_shared_without_becoming_an_intent(question, expected):
    assert split_query_intents(question) == (expected, False)
    plan = compile_query_plan(question, {})
    assert plan.intent_overflow is False
    assert plan.original_query == question


def test_shared_document_scope_does_not_hide_four_real_requests():
    question = "Theo MANUAL-A-41 và MANUAL-B-72, tìm BOM; tìm vật liệu; tìm dung sai; tìm hướng dẫn hàn"
    calls = []
    plan = compile_query_plan(question, {}, planner=lambda _: calls.append("planner"))
    assert plan.intent_overflow is True
    assert len(plan.intents) == 4
    assert all("MANUAL-A-41 và MANUAL-B-72" in intent for intent in plan.intents)
    assert execute_plan(plan, lambda *_: calls.append("retrieval"), {}) == []
    assert calls == []


@pytest.mark.parametrize("body", [
    "thông số nhiệt, áp và thời gian cụ thể theo mã sản phẩm và máy đã được xác định chưa?",
    "giấy tờ, chứng từ và thông tin theo đơn hàng và nhà cung cấp đã được xác nhận chưa?",
])
def test_long_subject_list_with_one_shared_predicate_is_one_request(body):
    question = "Theo tài liệu MANUAL-QA-82, " + body
    assert split_query_intents(question) == ((question.rstrip('?'),), False)


def test_comma_separated_independent_requests_keep_the_limit():
    question = "Theo MANUAL-QA-82, tìm BOM, nêu vật liệu, tìm dung sai và liệt kê hướng dẫn hàn"
    assert split_query_intents(question)[1] is True


@pytest.mark.parametrize("body", [
    "tóm tắt BOM, so sánh vật liệu, giải thích dung sai và đánh giá hướng dẫn hàn",
    "tóm tắt BOM, so sánh vật liệu, giải thích dung sai và kiểm tra hồ sơ đã được xác nhận chưa?",
])
def test_long_independent_commands_are_not_a_shared_subject_list(body):
    assert split_query_intents("Theo MANUAL-QA-82, " + body)[1] is True


def test_planner_subqueries_cannot_drop_shared_document_scope():
    question = "Theo POLICY-A-41 và POLICY-B-72, thời hạn là bao lâu và ai chịu trách nhiệm?"
    plan = compile_query_plan(question, {}, planner=lambda _: {
        "subqueries": ["thời hạn là bao lâu", "ai chịu trách nhiệm"]})
    assert plan.used_fallback is False
    assert plan.subqueries == (
        "Theo POLICY-A-41 và POLICY-B-72, thời hạn là bao lâu",
        "Theo POLICY-A-41 và POLICY-B-72, ai chịu trách nhiệm",
    )
    assert plan.intent_coverage == (True, True)


@pytest.mark.parametrize('question, expected', [
    ('So sánh tổng doanh thu kỳ A và kỳ B, chênh lệch B trừ A và tỷ lệ thay đổi so với kỳ A.',
        ('So sánh tổng doanh thu kỳ A và kỳ B', 'chênh lệch B trừ A', 'tỷ lệ thay đổi so với kỳ A.')),
    ('Compare revenue for period OLD and period NEW, difference, percentage change.',
        ('Compare revenue for period OLD and period NEW', 'difference', 'percentage change.')),
    ('So sánh tồn kho tháng 09 và tháng 10; tìm BOM; xem vật liệu; kiểm dung sai',
        ('So sánh tồn kho tháng 09 và tháng 10', 'tìm BOM', 'xem vật liệu', 'kiểm dung sai')),
])
def test_comparison_scope_operands_stay_together_without_hiding_other_intents(question, expected):
    assert split_query_intents(question) == (expected, len(expected) > 3)


def test_overflow_keeps_all_explicit_intents_and_never_calls_planner_or_retrieval():
    question = "Tìm BOM; tìm vật liệu; tìm dung sai; tìm hướng dẫn hàn"
    expected = ("Tìm BOM", "tìm vật liệu", "tìm dung sai", "tìm hướng dẫn hàn")
    calls = []
    assert split_query_intents(question) == (expected, True)
    plan = compile_query_plan(question, {}, planner=lambda _: calls.append("planner"))
    assert plan.intent_overflow is True
    assert plan.intents == expected
    assert plan.subqueries == ()
    assert plan.intent_coverage == (False,) * 4
    assert execute_plan(plan, lambda *_: calls.append("retrieval"), {}) == []
    assert calls == []


def test_execute_plan_checks_request_before_each_queued_branch():
    from mech_chatbot.llm.external_ai import ExternalAICallCancelled

    calls = []
    cancelled = threading.Event()
    plan = build_plan("BOM và vật liệu và dung sai")

    def checkpoint():
        if cancelled.is_set():
            raise ExternalAICallCancelled("cancelled")

    def retrieve(query, *_):
        calls.append(query)
        cancelled.set()
        return [query]

    with pytest.raises(ExternalAICallCancelled):
        execute_plan(plan, retrieve, {}, max_workers=1, checkpoint=checkpoint)
    assert calls == ["BOM"]


def test_execute_plan_does_not_start_retrieval_after_deadline():
    calls = []
    plan = build_plan("BOM và vật liệu")
    results = execute_plan(
        plan, lambda query, *_: calls.append(query), {},
        deadline_monotonic=time.monotonic() - 1,
        on_timeout=lambda query: "timeout:" + query,
    )
    assert calls == []
    assert results == ["timeout:BOM", "timeout:vật liệu"]


def test_question_mark_separated_overflow_preserves_each_question():
    question = "BOM là gì? Vật liệu gì? Dung sai gì? Hàn thế nào?"
    plan = build_plan(question)
    assert plan.intent_overflow is True
    assert plan.intents == ("BOM là gì", "Vật liệu gì", "Dung sai gì", "Hàn thế nào")


@pytest.mark.parametrize("exception_type", ["cancel", "budget"])
def test_terminal_branch_failure_propagates_without_waiting_for_slow_sibling(exception_type):
    from mech_chatbot.llm.external_ai import ExternalAICallCancelled
    from mech_chatbot.rag.execution import RequestBudgetExceeded

    failure = ExternalAICallCancelled if exception_type == "cancel" else RequestBudgetExceeded
    release = threading.Event()
    entered = threading.Event()
    plan = build_plan("BOM và vật liệu")

    def retrieve(query, *_):
        if query == "BOM":
            entered.wait(1)
            raise failure("terminal")
        entered.set()
        release.wait(1)
        return [query]

    started = time.monotonic()
    try:
        with pytest.raises(failure):
            execute_plan(plan, retrieve, {})
    finally:
        release.set()
    assert time.monotonic() - started < 0.5


def test_decomposition_stream_marks_partial_and_audits_rendered_branch_sources():
    branches = [
        {
            "outcome": "full_answer",
            "citations": [{"source_id": "D198P1"}, {"source_id": "D201P1"}],
        },
        {"outcome": "access_denied", "citations": []},
    ]
    rendered = "".join(audit_decomposition_stream(iter(["Evidence [SRC:D198P1]"]), branches))

    assert rendered == (
        "Trả lời được một phần: Evidence [SRC:D198P1]\n"
        "Phần bị chặn chưa thể trả lời do không có nguồn được phép truy cập."
    )
    assert branches[0]["rendered_source_ids"] == ["D198P1"]
    assert branches[1]["rendered_source_ids"] == []


def test_decomposition_stream_appends_canonical_missing_evidence_notice():
    branches = [
        {"outcome": "full_answer", "citations": [{"source_id": "D198P1"}]},
        {"outcome": "insufficient_evidence", "citations": []},
    ]

    rendered = "".join(
        audit_decomposition_stream(iter(["Evidence [SRC:D198P1]"]), branches)
    )

    assert rendered.endswith(
        "\nPhần chưa có đủ bằng chứng chưa thể trả lời "
        "từ tài liệu nội bộ hiện có."
    )


def test_decomposition_stream_does_not_duplicate_canonical_notice():
    notice = "Phần bị chặn chưa thể trả lời do không có nguồn được phép truy cập."
    branches = [
        {"outcome": "full_answer", "citations": [{"source_id": "D198P1"}]},
        {"outcome": "access_denied", "citations": []},
    ]

    rendered = "".join(
        audit_decomposition_stream(
            iter([f"Evidence [SRC:D198P1]\n{notice}"]),
            branches,
        )
    )

    assert rendered.count(notice) == 1


def test_branch_code_extraction_is_normalized_and_does_not_inherit_other_codes():
    assert codes_in_query("BOM MA-100") == ("ma-100",)


def test_numeric_drawing_codes_keep_the_branch_source_filter():
    from types import SimpleNamespace
    from qdrant_client import models
    from mech_chatbot.rag.phases.retrieval import _branch_filters
    from mech_chatbot.rag.rbac import compose_retrieval_filters

    question = "Theo 7.3.04218(867-234)-ver02-Model5.pdf, dung sai?"
    codes = ["7.3.04218", "867-234"]
    access = models.FieldCondition(key="metadata.site", match=models.MatchValue(value="QA"))
    strict, _ = compose_retrieval_filters([access], codes)
    context = SimpleNamespace(new_part_ids=tuple(codes), decision=SimpleNamespace(strict_filter=strict))

    assert set(codes_in_query(question)) == set(codes)
    branch_ids, branch_strict, branch_broad = _branch_filters(context, question)
    assert branch_ids == codes
    for branch_filter in (branch_strict, branch_broad):
        assert access in branch_filter.must
        assert any(getattr(condition, "should", None) for condition in branch_filter.must)


def test_planner_cannot_drop_or_invent_numeric_drawing_scope():
    from mech_chatbot.rag.query_decomposition import _query_covers_intent

    assert not _query_covers_intent("dung sai", "dung sai 7.3.04218")
    assert not _query_covers_intent("dung sai 7.3.99999", "dung sai 7.3.04218")
    assert _query_covers_intent("dung sai 7.3.04218", "dung sai 7.3.04218")


def test_explicit_filename_scope_keeps_multiple_named_sources_before_branch_selection():
    from mech_chatbot.rag.phases.retrieval import _documents_in_question

    docs = [Document(page_content="source", metadata={"file_goc": filename}) for filename in (
        "7.3.04218-ver02-Model4.pdf", "7.3.04218-ver02-Model5.pdf", "instructions.docx",
    )]
    assert _documents_in_question(docs, "Theo 7.3.04218-ver02-Model5.pdf, dung sai?") == [docs[1]]
    assert _documents_in_question(docs, "Compare 7.3.04218-ver02-model5.PDF and instructions.docx") == docs[1:]
    assert _documents_in_question(docs, "Dung sai cac ban ve?") == docs
    suffix_docs = [Document(page_content="source", metadata={"file_goc": filename})
                   for filename in ("a.pdf", "ba.pdf")]
    assert _documents_in_question(suffix_docs, "Theo ba.pdf, dung sai?") == suffix_docs[1:]


def test_simple_query_does_not_call_planner():
    calls = []
    plan = build_plan("Quy trình bảo trì là gì?", planner=lambda _: calls.append(True))

    assert plan.is_complex is False
    assert plan.subqueries == ()
    assert calls == []


def test_simple_question_with_introductory_comma_does_not_call_planner():
    calls = []
    plan = build_plan("Theo tài liệu, quy trình bảo trì là gì?", planner=lambda _: calls.append(True))
    assert plan.is_complex is False
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


def test_planner_fourth_branch_is_not_silently_discarded():
    question = 'Tìm BOM MA-100; tìm vật liệu MA-200; tìm hướng dẫn bảo trì MA-300'
    plan = compile_query_plan(question, {}, planner=lambda _: {'subqueries':[
        'BOM MA-100', 'vật liệu MA-200', 'hướng dẫn bảo trì MA-300', 'kiểm tra thêm BOM MA-100']})
    assert plan.used_fallback is True
    assert plan.subqueries == ('Tìm BOM MA-100', 'tìm vật liệu MA-200', 'tìm hướng dẫn bảo trì MA-300')
    assert plan.intent_coverage == (True, True, True)


@pytest.mark.parametrize('proposed', [None, 3, 'BOM MA-100', {'BOM MA-100':'material'},
                                     [{'query':'BOM MA-100'}, True, 17]])
def test_malformed_planner_branches_keep_the_original_request(proposed):
    plan = compile_query_plan('Tìm BOM MA-100; tìm vật liệu MA-200', {},
                              planner=lambda _: {'subqueries':proposed})
    assert plan.used_fallback and plan.subqueries == ('Tìm BOM MA-100', 'tìm vật liệu MA-200')


def test_executor_rejects_oversized_handbuilt_plan_before_any_source_lookup():
    calls = []
    plan = BranchPlan('Four requested branches', True, ('BOM','materials','tolerance','welding'))
    with pytest.raises(ValueError,match='three'):
        execute_plan(plan,lambda query,*_:calls.append(query),{})
    assert calls == []


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

    assert len(instruction) <= 130
    assert "1 nhánh chưa có đủ bằng chứng" in instruction
    assert "1 nhánh không thể truy cập" in instruction
    assert "không lộ nhánh bị chặn" in instruction.lower()
    assert "secret-payroll.md" not in instruction


def test_full_decomposition_instruction_limits_each_answer_to_the_asked_fact():
    instruction = build_decomposition_instruction([
        {"outcome": "full_answer"},
        {"outcome": "full_answer"},
    ])

    assert len(instruction) <= 80
    assert "từng ý" in instruction.lower()
    assert "trả lời có nguồn" in instruction.lower()
    assert "không suy diễn" in instruction.lower()
    assert "không lộ nhánh bị chặn" in instruction.lower()


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


def test_shared_prefix_is_not_evidence_that_planner_covered_both_requests():
    question = "Theo POLICY-A-41 và POLICY-B-72, thời hạn bao lâu và ai phê duyệt?"
    plan = compile_query_plan(question, {}, planner=lambda _: {
        "subqueries": ["thời hạn bao lâu", "hồ sơ lưu trữ"]})
    assert plan.used_fallback is True
    assert all("ai phê duyệt" in query for query in plan.subqueries[-1:])


def test_shared_scope_does_not_merge_four_short_commands():
    question = "Theo MANUAL-A-41, xem BOM và kiểm vật liệu và xem dung sai và kiểm mối hàn"
    assert split_query_intents(question)[1] is True
