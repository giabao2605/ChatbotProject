import pytest

from mech_chatbot.rag import intent


pytestmark = pytest.mark.unit


@pytest.mark.parametrize(
    "question",
    [
        "nội quy lao động",
        "quy trình nghỉ phép",
        "chính sách mua hàng",
        "doanh thu tháng này",
    ],
)
def test_generic_department_queries_skip_llm(question, monkeypatch):
    monkeypatch.setattr(
        intent,
        "cohere_invoke",
        lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("LLM called")),
    )

    _, _, _, _, _, data = intent.extract_search_intent(
        question,
        user_department="HR",
        user_roles=["viewer"],
        allowed_departments=["HR"],
        max_security_level="internal",
        allowed_sites=["VP_NHAN_SU"],
    )

    assert data["version_policy"] == "current_only"
    assert data["query_type"] == "general_lookup"


def test_provider_intent_and_context_calls_keep_request_trace(monkeypatch):
    calls = []

    class Response:
        def __init__(self, content):
            self.content = content

    def fake_invoke(_messages, **kwargs):
        calls.append(kwargs)
        if kwargs["surface"] == "intent_routing":
            return Response(
                '{"base_codes":[],"detected_versions":[1,2],"variant_codes":[],'
                '"version_policy":"compare_versions","query_type":"general_lookup"}'
            )
        return Response(
            '{"context_action":"continue","standalone_question":"so sanh v1 va v2"}'
        )

    monkeypatch.setattr(intent, "cohere_invoke", fake_invoke)

    intent.extract_search_intent("so sánh version v1 và v2", trace_id="trace-intent")
    intent.analyze_context(
        "còn bản trước?",
        chat_history=[{"role": "user", "content": "xem tài liệu"}],
        current_part_ids=["DOC-001"],
        trace_id="trace-context",
    )

    assert calls[0]["surface"] == "intent_routing"
    assert calls[0]["trace_id"] == "trace-intent"
    assert calls[1]["surface"] == "query_disambiguation"
    assert calls[1]["trace_id"] == "trace-context"
