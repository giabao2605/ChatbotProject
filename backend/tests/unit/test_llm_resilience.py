import pytest
from types import SimpleNamespace

from mech_chatbot.llm.llm_client import _is_gpt_rate_limit
from mech_chatbot.rag import evidence_gate


pytestmark = pytest.mark.unit


@pytest.mark.parametrize(
    "message",
    [
        "Error code: 503 service_unavailable no_capacity",
        "Error code: 502 Bad Gateway",
        "429 too many requests",
        "provider temporarily unavailable",
        "Service unavailable",
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


def test_evaluator_marks_missing_coverage_ambiguous_only_when_correction_enabled():
    decision = evidence_gate.evaluate_answerability(
        "Chi phí gia công là bao nhiêu?",
        "Tài liệu chỉ mô tả quy trình gồm ba bước chuẩn bị, gia công và kiểm tra.",
        crag_enabled=True,
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


def test_evaluator_accepts_ambiguous_state_from_verifier(monkeypatch):
    calls = []
    monkeypatch.setattr(
        evidence_gate,
        "cohere_invoke",
        lambda *_args, **kwargs: calls.append(kwargs) or SimpleNamespace(
            content='{"state":"AMBIGUOUS","reason":"coverage gap","evidence_quotes":[]}'
        ),
    )

    decision = evidence_gate.evaluate_answerability(
        "Quy định gì?",
        "Có một phần quy định.",
        docs=[SimpleNamespace(metadata={})],
        verifier_enabled=True,
    )

    assert decision.state is evidence_gate.EvidenceState.AMBIGUOUS
    assert decision.telemetry_status == "verifier_block"
    assert calls[0]["policies"] == ["internal_only"]


@pytest.mark.parametrize("response", ["not-json", "[]", "{}", '{"answerable":"false"}', '{"state":"UNKNOWN","answerable":true}', '{"state":"SUFFICIENT","evidence_quotes":[4]}'])
def test_evaluator_does_not_approve_invalid_verifier_output(monkeypatch, response):
    monkeypatch.setattr(
        evidence_gate, "cohere_invoke",
        lambda *_args, **_kwargs: SimpleNamespace(content=response),
    )

    decision = evidence_gate.evaluate_answerability(
        "Quy định gì?", "Có một phần quy định.", verifier_enabled=True,
    )

    assert decision.state is evidence_gate.EvidenceState.INSUFFICIENT
    assert decision.reason == "verifier_unavailable"
    assert decision.telemetry_status == "verifier_unavailable"


@pytest.mark.parametrize("error", [TimeoutError("timeout"), RuntimeError("unavailable")])
def test_verifier_transport_error_is_not_answerable(monkeypatch, error):
    def fail(*args, **kwargs):
        raise error
    monkeypatch.setattr(evidence_gate, "cohere_invoke", fail)
    decision = evidence_gate.evaluate_answerability("Quy định gì?", "Quy định nội bộ.", verifier_enabled=True)
    assert not decision.answerable
    assert decision.telemetry_status == "verifier_unavailable"


@pytest.mark.parametrize("value, expected", [(True, True), (False, False)])
def test_verifier_accepts_real_legacy_booleans(monkeypatch, value, expected):
    import json
    monkeypatch.setattr(evidence_gate, "cohere_invoke", lambda *a, **k: SimpleNamespace(content=json.dumps({"answerable": value})))
    assert evidence_gate.evaluate_answerability("Quy định gì?", "Quy định nội bộ.", verifier_enabled=True).answerable is expected


@pytest.mark.parametrize("lang", ["vi", "en"])
def test_refusal_does_not_expose_internal_reason_or_irrelevant_examples(lang):
    message = evidence_gate.make_insufficient_evidence_message("IT onboarding?", "private_internal_reason", lang)
    assert "private_internal_reason" not in message
    assert "gia công" not in message and "machining" not in message


@pytest.mark.parametrize("kind", ["cancel", "budget"])
def test_verifier_preserves_request_cancellation_and_budget(monkeypatch, kind):
    from mech_chatbot.llm.external_ai import ExternalAICallCancelled
    from mech_chatbot.rag.execution import RequestBudgetExceeded
    error = ExternalAICallCancelled("cancel") if kind == "cancel" else RequestBudgetExceeded("budget")
    def fail(*args, **kwargs):
        raise error
    monkeypatch.setattr(evidence_gate, "cohere_invoke", fail)
    with pytest.raises(type(error)):
        evidence_gate.evaluate_answerability("Quy định gì?", "Quy định nội bộ.", verifier_enabled=True)
