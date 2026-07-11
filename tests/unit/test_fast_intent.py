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
