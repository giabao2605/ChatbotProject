import pytest

from mech_chatbot.llm.llm_client import _is_gpt_rate_limit
from mech_chatbot.rag import evidence_gate


pytestmark = pytest.mark.unit


@pytest.mark.parametrize(
    "message",
    [
        "Error code: 503 service_unavailable no_capacity",
        "429 too many requests",
        "provider temporarily unavailable",
        "request timeout",
    ],
)
def test_transient_provider_errors_are_retryable(message):
    assert _is_gpt_rate_limit(RuntimeError(message)) is True


def test_deterministic_evidence_gate_skips_second_llm_by_default(monkeypatch):
    monkeypatch.delenv("LLM_EVIDENCE_VERIFIER_ENABLED", raising=False)
    monkeypatch.setattr(
        evidence_gate,
        "cohere_invoke",
        lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("LLM called")),
    )

    answerable, reason, quotes = evidence_gate.verify_answerability(
        "Nội quy lao động quy định gì?",
        "Nội quy lao động quy định giờ làm việc và trách nhiệm của nhân viên.",
    )

    assert answerable is True
    assert reason == "deterministic_evidence_gate_passed"
    assert quotes == []
