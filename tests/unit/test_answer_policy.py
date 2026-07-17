from __future__ import annotations

import pytest

from mech_chatbot.rag.answer_policy import (
    AnswerOutcome,
    PolicyEvidence,
    decide_answer_policy,
    has_explicit_negative_evidence,
    explicit_negative_evidence_quote,
)
from mech_chatbot.rag.evidence_gate import EvidenceDecision, EvidenceState


pytestmark = pytest.mark.unit


def _evidence(state=EvidenceState.SUFFICIENT, **overrides):
    value = {
        "decision": EvidenceDecision(
            state,
            reason="evidence reason",
            evidence_quotes=("direct quote",),
        ),
        "has_retrieved_evidence": True,
    }
    value.update(overrides)
    return PolicyEvidence(**value)


def test_access_denial_overrides_retrieval_quality_without_exposing_source_details():
    decision = decide_answer_policy(
        "Chi phí là bao nhiêu?",
        _evidence(),
        {"access_denied": True, "access_reason": "secret-file-name.pdf"},
    )

    assert decision.outcome is AnswerOutcome.ACCESS_DENIED
    assert decision.evidence_state is EvidenceState.INSUFFICIENT
    assert decision.reason == "access_denied"
    assert "secret-file-name.pdf" not in decision.reason
    assert decision.correction_allowed is False
    assert decision.evidence_quotes == ()


def test_explicit_negative_evidence_is_terminal_and_does_not_trigger_correction():
    decision = decide_answer_policy(
        "Tài liệu có công bố đơn giá không?",
        _evidence(
            EvidenceState.INSUFFICIENT,
            negative_evidence=True,
            retrieval_can_improve=True,
        ),
        {},
    )

    assert decision.outcome is AnswerOutcome.INSUFFICIENT_EVIDENCE
    assert decision.evidence_state is EvidenceState.SUFFICIENT
    assert decision.reason == "explicit_negative_evidence"
    assert decision.evidence_quotes == ("direct quote",)
    assert decision.correction_allowed is False
    assert decision.allows_answer_generation is True


def test_negative_evidence_quote_is_preserved_for_audit():
    quote = explicit_negative_evidence_quote(
        "Đơn giá của part là bao nhiêu?",
        "Không có trường đơn giá trong BOM này.",
    )
    decision = decide_answer_policy(
        "Đơn giá của part là bao nhiêu?",
        _evidence(
            EvidenceState.INSUFFICIENT,
            negative_evidence=True,
            negative_evidence_quote=quote,
        ),
        {},
    )
    assert decision.evidence_quotes == (quote,)
    assert "Không có trường đơn giá" in quote


@pytest.mark.parametrize("context", [
    "Tài liệu không công bố chi phí hoặc đơn giá.",
    "Không có trường đơn giá trong BOM này.",
    "The document does not provide unit price or cost.",
])
def test_explicit_negative_evidence_detector_requires_direct_source_statement(context):
    assert has_explicit_negative_evidence(
        "Đơn giá của part là bao nhiêu?", context
    ) is True


def test_negative_detector_does_not_treat_plain_missing_terms_as_explicit_evidence():
    assert has_explicit_negative_evidence(
        "Đơn giá là bao nhiêu?", "Tài liệu mô tả ba bước lắp ráp."
    ) is False


def test_negative_detector_does_not_cross_sentence_into_positive_evidence():
    assert has_explicit_negative_evidence(
        "Chi phí là bao nhiêu?",
        "Không có sự cố nào được ghi nhận. Chi phí là 1.500 USD.",
    ) is False


def test_ambiguous_evidence_only_allows_correction_when_retrieval_can_improve():
    retrievable = decide_answer_policy(
        "Quy định nào áp dụng?",
        _evidence(EvidenceState.AMBIGUOUS, retrieval_can_improve=True),
        {},
    )
    terminal = decide_answer_policy(
        "Quy định nào áp dụng?",
        _evidence(EvidenceState.AMBIGUOUS, retrieval_can_improve=False),
        {},
    )

    assert retrievable.correction_allowed is True
    assert retrievable.outcome is AnswerOutcome.INSUFFICIENT_EVIDENCE
    assert terminal.correction_allowed is False


def test_branch_coverage_produces_partial_or_full_answer_policy():
    partial = decide_answer_policy(
        "Trả lời ba ý",
        _evidence(
            EvidenceState.INSUFFICIENT,
            sufficient_branch_count=2,
            total_branch_count=3,
        ),
        {},
    )
    full = decide_answer_policy(
        "Trả lời ba ý",
        _evidence(
            EvidenceState.INSUFFICIENT,
            sufficient_branch_count=3,
            total_branch_count=3,
        ),
        {},
    )

    assert partial.outcome is AnswerOutcome.PARTIAL_ANSWER
    assert partial.evidence_state is EvidenceState.AMBIGUOUS
    assert full.outcome is AnswerOutcome.FULL_ANSWER
    assert full.evidence_state is EvidenceState.SUFFICIENT


def test_clarification_policy_is_terminal_without_retrieval_correction():
    decision = decide_answer_policy(
        "So sánh phiên bản",
        _evidence(EvidenceState.AMBIGUOUS, clarification_required=True),
        {},
    )

    assert decision.outcome is AnswerOutcome.CLARIFICATION_REQUIRED
    assert decision.correction_allowed is False
