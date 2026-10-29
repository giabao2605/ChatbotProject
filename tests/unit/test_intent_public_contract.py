from __future__ import annotations

import concurrent.futures
from types import SimpleNamespace

import pytest

from mech_chatbot.rag import intent
from mech_chatbot.llm.external_ai import ExternalProcessingDenied


pytestmark = pytest.mark.unit


class _Future:
    def __init__(self, *, value=None, error=None):
        self.value = value
        self.error = error
        self.cancelled = False

    def result(self, timeout=None):
        if self.error is not None:
            raise self.error
        return self.value

    def cancel(self):
        self.cancelled = True
        return True


class _ImmediateExecutor:
    def __init__(self, *, error=None):
        self.error = error
        self.future = None

    def submit(self, function, *args):
        if self.error is not None:
            self.future = _Future(error=self.error)
        else:
            try:
                self.future = _Future(value=function(*args))
            except Exception as exc:
                self.future = _Future(error=exc)
        return self.future


def _runtime(executor=None, *, rewrite_enabled=True):
    return intent.IntentRuntime(
        executor=executor if executor is not None else _ImmediateExecutor(),
        intent_timeout=6.0,
        context_timeout=5.0,
        query_rewrite_enabled=rewrite_enabled,
    )


@pytest.mark.parametrize('question', ['Quy trình đó thế nào?', 'Việc này cần báo ai?', 'How does that process work?'])
def test_reference_without_history_requests_clarification_without_provider(question):
    result = intent.analyze_context(question, runtime=_runtime())
    assert result['clarification_required'] is True
    assert result['standalone_question'] == question


def test_reference_resolution_uses_existing_context_call_and_keeps_topic():
    calls = []
    def provider(*args, **kwargs):
        calls.append(kwargs)
        return SimpleNamespace(content='{"context_action":"continue","standalone_question":"Quy trình xin nghỉ phép thế nào?"}')
    result = intent.analyze_context('Quy trình đó thế nào?',
        chat_history=[{'role': 'user', 'content': 'Tôi muốn xin nghỉ phép'}],
        invoke_provider=provider, runtime=_runtime())
    assert len(calls) == 1 and not result.get('clarification_required', False)
    assert 'xin nghỉ phép' in result['standalone_question']


@pytest.mark.parametrize('question', [
    'Tôi muốn xin nghỉ phép, quy trình đó thế nào?',
    'Quy trình này về xin nghỉ phép thực hiện thế nào?',
])
def test_reference_can_resolve_subject_from_current_sentence(question):
    calls = []
    def provider(*args, **kwargs):
        calls.append(kwargs)
        return SimpleNamespace(content='{"context_action":"continue","standalone_question":"Quy trình xin nghỉ phép thế nào?"}')
    result = intent.analyze_context(question, invoke_provider=provider, runtime=_runtime())
    assert len(calls) == 1
    assert not result.get('clarification_required', False)
    assert result['standalone_question'] == 'Quy trình xin nghỉ phép thế nào?'


def test_unresolved_reference_does_not_guess_after_context_failure():
    result = intent.analyze_context('Quy trình đó thế nào?',
        chat_history=[{'role': 'user', 'content': 'Xin chào'}],
        runtime=_runtime(_ImmediateExecutor(error=ValueError('invalid JSON'))))
    assert result['clarification_required'] is True


@pytest.mark.parametrize('reference_chain', [False, True])
def test_reference_uses_single_previous_user_request_without_external_processing(reference_chain):
    from mech_chatbot.rag.query_decomposition import split_query_intents

    previous = 'Công ty quy định thời gian xử lý yêu cầu cấp quyền hệ thống là bao lâu?'
    question = 'Quy trình đó thực hiện như thế nào?'
    history = [
        {'role': 'user', 'content': previous},
        {'role': 'assistant', 'content': 'SLA là 999 giờ. Một chủ đề khác là nghỉ phép.'},
    ]
    if reference_chain:
        history += [
            {'role': 'user', 'content': 'Quy trình đó cần báo ai?'},
            {'role': 'assistant', 'content': 'Liên hệ người trong câu trả lời này.'},
        ]
    calls = []

    def denied_provider(*args, **kwargs):
        calls.append(kwargs)
        raise ExternalProcessingDenied('internal_only')

    result = intent.analyze_context(question, chat_history=history,
        invoke_provider=denied_provider, runtime=_runtime())

    assert calls == []
    assert not result.get('clarification_required', False)
    assert previous.rstrip('?') in result['standalone_question']
    assert question in result['standalone_question']
    assert '999' not in result['standalone_question']
    assert 'nghỉ phép' not in result['standalone_question']
    assert result['llm_resolved'] is False
    requests, overflow = split_query_intents(result['standalone_question'])
    assert len(requests) == 1 and overflow is False


@pytest.mark.parametrize('previous', [
    'Nghỉ phép được bao lâu, mua hàng thực hiện thế nào?',
    'Quy trình này về mua hàng thực hiện thế nào?',
])
def test_local_reference_scope_stops_at_ambiguous_or_new_subject(previous):
    result = intent.analyze_context('Quy trình đó thực hiện như thế nào?', chat_history=[
        {'role': 'user', 'content': 'Thời gian xử lý yêu cầu cấp quyền là bao lâu?'},
        {'role': 'user', 'content': previous},
    ], runtime=_runtime(_ImmediateExecutor(error=ValueError('provider unavailable'))))
    assert result['clarification_required'] is True


def test_local_reference_scope_does_not_resolve_pending_candidate_choice():
    result = intent.analyze_context('Quy trình đó thực hiện như thế nào?',
        chat_history=[{'role': 'user', 'content': 'Thời gian xử lý yêu cầu cấp quyền là bao lâu?'}],
        pending_candidates=[{'key': 'DOC-A'}, {'key': 'DOC-B'}],
        runtime=_runtime(_ImmediateExecutor(error=ValueError('provider unavailable'))))
    assert result['clarification_required'] is True


@pytest.mark.parametrize('question', ['Nghỉ phép?', 'Quy trình xin nghỉ phép thế nào?', 'Doanh thu tháng này'])
def test_short_clear_questions_do_not_need_reference_clarification(question):
    assert not intent.analyze_context(question, runtime=_runtime()).get('clarification_required', False)


@pytest.mark.parametrize(
    ("question", "expected_policy", "expected_versions"),
    [
        ("so sánh v2 và rev 4", "compare_versions", [2, 4]),
        ("lịch sử version 3", "version_history", [3]),
        ("xem bản cũ v1", "include_archived", [1]),
        ("mở version 7", "specific_version", [7]),
        ("quy trình hiện hành", None, []),
        ("So sánh hai phiên bản giúp mình.", "compare_versions", []),
        ("Compare the two versions, please.", "compare_versions", []),
    ],
)
def test_version_intent_is_deterministic(question, expected_policy, expected_versions):
    assert intent.deterministic_version_intent(question) == (expected_policy, expected_versions)


def test_mechanical_code_extraction_deduplicates_and_sorts_supported_formats():
    assert intent.extract_mechanical_codes(
        "Tra 9.3.03844, AB-120 va 123-456; lap lai AB-120"
    ) == ["123-456", "9.3.03844", "AB-120"]


def test_business_document_intent_recognizes_types_aliases_and_references():
    result = intent.deterministic_business_document_intent(
        "So sánh PO-2024, hợp đồng HD/998 và biểu mẫu BM-77"
    )

    assert result["document_types"] == ["purchase_order", "contract", "form"]
    assert result["document_type_values"] == [
        "purchase_order",
        "po",
        "purchase order",
        "đơn đặt hàng",
        "don dat hang",
        "contract",
        "hợp đồng",
        "hop dong",
        "form",
        "biểu mẫu",
        "bieu mau",
        "mẫu đơn",
        "mau don",
    ]
    assert result["document_references"] == ["PO-2024", "HD/998", "BM-77"]


def test_search_intent_uses_regex_code_without_calling_llm(monkeypatch):
    monkeypatch.setattr(
        intent,
        "cohere_invoke",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("LLM called")),
    )

    strict_filter, broad_filter, part_ids, inherited, is_bom, data = intent.extract_search_intent(
        "BOM của AB-120 gồm vật tư nào?",
        user_department="TECH",
        user_roles=["viewer"],
        allowed_departments=["TECH"],
        max_security_level="internal",
        allowed_sites=["HQ"],
    )

    assert strict_filter is not None
    assert broad_filter is not None
    assert part_ids == ["ab-120"]
    assert inherited is False
    assert is_bom is True
    assert data["query_type"] == "general_lookup"


def test_search_intent_success_merges_llm_fields_and_deterministic_version(monkeypatch):
    response = SimpleNamespace(
        content=(
            "```json\n"
            '{"base_codes": ["CHITCHAT", "AB-120"], "detected_versions": ["bad", 9], '
            '"variant_codes": ["A"], "version_policy": "current_only", '
            '"query_type": "bom_lookup", "product_names": ["Khung"], '
            '"materials": ["SUS304"], "dimensions": ["20x30"], "models": ["B"], '
            '"query_scope": "compare_candidates", "need_disambiguation": true}\n```'
        )
    )
    monkeypatch.setattr(intent, "cohere_invoke", lambda *_args, **_kwargs: response)
    executor = _ImmediateExecutor()

    _, _, part_ids, inherited, is_bom, data = intent.extract_search_intent(
        "so sánh version 2 và v3 của model B",
        trace_id="trace-intent-contract",
        runtime=_runtime(executor),
    )

    assert part_ids == ["ab-120"]
    assert inherited is False
    assert is_bom is True
    assert data["detected_versions"] == [2, 3]
    assert data["version_policy"] == "compare_versions"
    assert data["variant_codes"] == ["A", "B"]
    assert data["product_names"] == ["Khung"]
    assert data["materials"] == ["SUS304"]
    assert data["dimensions"] == ["20x30"]
    assert data["query_scope"] == "compare_candidates"
    assert data["need_disambiguation"] is True
    assert data["is_chitchat"] is True
    assert "CHITCHAT" not in data["base_codes"]


@pytest.mark.parametrize(
    "error",
    [ValueError("invalid JSON"), concurrent.futures.TimeoutError()],
)
def test_search_intent_provider_failure_falls_back_to_current_state(monkeypatch, error):
    executor = _ImmediateExecutor(error=error)

    strict_filter, broad_filter, part_ids, inherited, is_bom, data = intent.extract_search_intent(
        "model Zeta mới nhất",
        current_part_ids=["OLD-01"],
        runtime=_runtime(executor),
    )

    assert strict_filter is not None
    assert broad_filter is not None
    assert part_ids == ["OLD-01"]
    assert inherited is True
    assert is_bom is False
    assert data["version_policy"] == "current_only"
    assert data["base_codes"] == []
    if isinstance(error, concurrent.futures.TimeoutError):
        assert executor.future.cancelled is True


@pytest.mark.parametrize(
    "error",
    [ValueError("invalid JSON"), concurrent.futures.TimeoutError()],
)
def test_search_intent_provider_failure_keeps_explicit_codes_and_version(error):
    executor = _ImmediateExecutor(error=error)

    _, _, part_ids, inherited, is_bom, data = intent.extract_search_intent(
        "BOM Version 2 của DEMO-ASM-001, DEMO-PART-C có gì?",
        runtime=_runtime(executor),
    )

    assert part_ids == ["demo-asm-001", "demo-part-c"]
    assert inherited is False
    assert is_bom is True
    assert data["detected_versions"] == [2]
    assert data["version_policy"] == "specific_version"


def test_search_intent_broad_question_drops_inherited_part_ids(monkeypatch):
    monkeypatch.setattr(
        intent,
        "cohere_invoke",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("LLM called")),
    )

    _, _, part_ids, inherited, is_bom, _data = intent.extract_search_intent(
        "danh sách tất cả sản phẩm",
        current_part_ids=["OLD-01"],
    )

    assert part_ids == []
    assert inherited is False
    assert is_bom is False


def test_search_intent_records_business_document_preference_alongside_reference_code(monkeypatch):
    monkeypatch.setattr(
        intent,
        "cohere_invoke",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("LLM called")),
    )

    strict_filter, broad_filter, part_ids, inherited, _is_bom, data = intent.extract_search_intent(
        "tra cứu PO-2024",
    )

    assert strict_filter is not None
    assert broad_filter is not None
    assert part_ids == ["po-2024"]
    assert inherited is False
    assert data["document_type_hints"] == ["purchase_order"]
    assert data["document_references"] == ["PO-2024"]
    assert data["metadata_document_type_preference"] == ["purchase_order"]


@pytest.mark.parametrize(
    ("rewrite_enabled", "history", "question"),
    [
        (False, [{"role": "user", "content": "x"}], "còn nó?"),
        (True, None, "còn nó?"),
        (
            True,
            [{"role": "user", "content": "x"}],
            "Quy trình nghỉ phép áp dụng cho nhân viên thử việc tại chi nhánh nào",
        ),
    ],
)
def test_context_analysis_skips_llm_when_rewrite_is_disabled_or_unnecessary(
    monkeypatch, rewrite_enabled, history, question
):
    monkeypatch.setattr(
        intent,
        "cohere_invoke",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("LLM called")),
    )

    assert intent.analyze_context(
        question,
        chat_history=history,
        runtime=_runtime(rewrite_enabled=rewrite_enabled),
    ) == {
        "context_action": "continue",
        "standalone_question": question,
        "llm_resolved": False,
        **({'clarification_required': True} if question == 'còn nó?' else {}),
    }


def test_context_analysis_returns_clean_standalone_question_and_trace(monkeypatch):
    calls = []

    def fake_invoke(messages, **kwargs):
        calls.append((messages[0].content, kwargs))
        return SimpleNamespace(
            content='```json\n{"context_action":"switch_topic","standalone_question":"  Quy trinh B  "}\n```'
        )

    monkeypatch.setattr(intent, "cohere_invoke", fake_invoke)

    result = intent.analyze_context(
        "còn quy trình B?",
        chat_history=[{"role": "assistant", "content": "Quy trinh A"}],
        active_doc_refs=["DOC-A"],
        trace_id="trace-context-contract",
        runtime=_runtime(),
    )

    assert result == {
        "context_action": "switch_topic",
        "standalone_question": "Quy trinh B",
        "llm_resolved": True,
    }
    prompt, kwargs = calls[0]
    assert "tai lieu dang trao doi: ['DOC-A']" in prompt
    assert "Bot: Quy trinh A" in prompt
    assert kwargs == {"surface": "query_disambiguation", "trace_id": "trace-context-contract"}


@pytest.mark.parametrize(
    ("payload", "expected_question"),
    [
        ('{"context_action":"unsupported","standalone_question":"rewritten"}', "còn nó?"),
        ('{"context_action":"broaden","standalone_question":"   "}', "còn nó?"),
        ('{"context_action":"continue","standalone_question":42}', "còn nó?"),
    ],
)
def test_context_analysis_normalizes_invalid_provider_fields(monkeypatch, payload, expected_question):
    monkeypatch.setattr(
        intent,
        "cohere_invoke",
        lambda *_args, **_kwargs: SimpleNamespace(content=payload),
    )

    result = intent.analyze_context(
        "còn nó?",
        chat_history=[{"role": "user", "content": "xem quy trinh"}],
        current_part_ids=["DOC-1"],
        runtime=_runtime(),
    )

    if expected_question == 'còn nó?':
        assert result == {'context_action': 'continue', 'standalone_question': expected_question,
                          'llm_resolved': False, 'clarification_required': True}
        return
    expected_action = "broaden" if '"broaden"' in payload else "continue"
    assert result == {
        "context_action": expected_action,
        "standalone_question": expected_question,
        "llm_resolved": True,
    }


@pytest.mark.parametrize(
    "error",
    [ValueError("invalid JSON"), concurrent.futures.TimeoutError()],
)
def test_context_analysis_provider_failure_returns_safe_fallback(monkeypatch, error):
    executor = _ImmediateExecutor(error=error)

    result = intent.analyze_context(
        "còn nó?",
        chat_history=[{"role": "user", "content": "xem quy trinh"}],
        current_part_ids=["DOC-1"],
        runtime=_runtime(executor),
    )

    assert result == {
        "context_action": "continue",
        "standalone_question": "còn nó?",
        "llm_resolved": False,
        "clarification_required": True,
    }
    if isinstance(error, concurrent.futures.TimeoutError):
        assert executor.future.cancelled is True


def test_runtime_factory_owns_executor_lifecycle(monkeypatch):
    created = []

    class Executor:
        def __init__(self, max_workers):
            created.append(max_workers)
            self.closed = None

        def shutdown(self, *, wait, cancel_futures):
            self.closed = (wait, cancel_futures)

    monkeypatch.setattr(intent, "ThreadPoolExecutor", Executor)

    runtime = intent.build_intent_runtime(
        max_workers=3,
        intent_timeout=1.5,
        context_timeout=2.5,
        query_rewrite_enabled=False,
    )
    runtime.close()

    assert created == [3]
    assert runtime.intent_timeout == 1.5
    assert runtime.context_timeout == 2.5
    assert runtime.query_rewrite_enabled is False
    assert runtime.executor.closed == (False, True)


def test_version_comparison_does_not_accept_unstated_model_versions(monkeypatch):
    monkeypatch.setattr(intent, "cohere_invoke", lambda *a, **k: SimpleNamespace(content=
        '{"base_codes":[],"detected_versions":[1,2],"version_policy":"compare_versions"}'))
    *_, data = intent.extract_search_intent("Compare two versions, please.", runtime=_runtime())
    assert data["version_policy"] == "compare_versions"
    assert data["detected_versions"] == []
