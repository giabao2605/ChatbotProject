from __future__ import annotations

import pytest

from mech_chatbot.rag.answer_policy import (
    AnswerOutcome,
    PolicyEvidence,
    decide_answer_policy,
    decide_terminal_policy,
)
from mech_chatbot.rag.answer_checks import source_id_for_evidence_quote
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


def test_negative_evidence_source_id_comes_from_document_containing_quote():
    documents = [
        type("Doc", (), {
            "page_content": "Quy trình lắp ráp.",
            "metadata": {"doc_id": 10, "trang_so": 1},
        })(),
        type("Doc", (), {
            "page_content": "Không có trường đơn giá trong BOM này.",
            "metadata": {"doc_id": 73, "trang_so": 3},
        })(),
    ]

    assert source_id_for_evidence_quote(
        "Không có trường đơn giá trong BOM này.", documents
    ) == "D73P3"
    assert source_id_for_evidence_quote("Nội dung không tồn tại.", documents) == ""


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


@pytest.mark.parametrize(
    ("reason", "access_denied", "outcome"),
    [
        ("no_retrieved_docs", False, AnswerOutcome.INSUFFICIENT_EVIDENCE),
        ("no_docs_for_exact_code", False, AnswerOutcome.INSUFFICIENT_EVIDENCE),
        ("access_denied", True, AnswerOutcome.ACCESS_DENIED),
    ],
)
def test_terminal_outcomes_use_the_same_answer_policy_seam(reason, access_denied, outcome):
    decision = decide_terminal_policy(
        "Câu hỏi cần dữ liệu nguồn",
        reason=reason,
        access_denied=access_denied,
    )

    assert decision.outcome is outcome
    assert decision.evidence_state is EvidenceState.INSUFFICIENT
    assert decision.reason == ("access_denied" if access_denied else reason)
    assert decision.correction_allowed is False


def test_verifier_failure_cannot_be_overridden_by_sufficient_branches():
    decision = decide_answer_policy("Quy định gì?", PolicyEvidence(
        decision=EvidenceDecision(EvidenceState.INSUFFICIENT, reason="verifier_unavailable",
            stage="verifier", telemetry_status="verifier_unavailable"),
        has_retrieved_evidence=True, sufficient_branch_count=2, total_branch_count=2,
    ), {})
    assert not decision.allows_answer_generation
    assert decision.reason == "verifier_unavailable"
