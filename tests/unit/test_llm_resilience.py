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


def test_evaluator_marks_missing_coverage_ambiguous_only_when_correction_enabled(monkeypatch):
    monkeypatch.setenv("RAG_CRAG_ENABLED", "true")

    decision = evidence_gate.evaluate_answerability(
        "Chi phí gia công là bao nhiêu?",
        "Tài liệu chỉ mô tả quy trình gồm ba bước chuẩn bị, gia công và kiểm tra.",
    )

    assert decision.state is evidence_gate.EvidenceState.AMBIGUOUS
    assert decision.stage == "heuristic"
    assert decision.telemetry_status == "heuristic_block"


def test_evaluator_reports_verifier_disabled(monkeypatch):
    monkeypatch.delenv("LLM_EVIDENCE_VERIFIER_ENABLED", raising=False)

    decision = evidence_gate.evaluate_answerability(
        "Nội quy lao động quy định gì?",
        "Nội quy lao động quy định giờ làm việc.",
    )

    assert decision.state is evidence_gate.EvidenceState.SUFFICIENT
    assert decision.telemetry_status == "verifier_disabled"
