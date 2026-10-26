"""One policy seam for answer, correction, clarification, and refusal outcomes."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Mapping

from mech_chatbot.rag.evidence_gate import EvidenceDecision, EvidenceState


class AnswerOutcome(str, Enum):
    FULL_ANSWER = "full_answer"
    PARTIAL_ANSWER = "partial_answer"
    CLARIFICATION_REQUIRED = "clarification_required"
    INSUFFICIENT_EVIDENCE = "insufficient_evidence"
    ACCESS_DENIED = "access_denied"


@dataclass(frozen=True)
class PolicyEvidence:
    decision: EvidenceDecision
    has_retrieved_evidence: bool
    retrieval_can_improve: bool = False
    clarification_required: bool = False
    sufficient_branch_count: int = 0
    total_branch_count: int = 0


@dataclass(frozen=True)
class AnswerDecision:
    outcome: AnswerOutcome
    evidence_state: EvidenceState
    reason: str
    evidence_quotes: tuple[str, ...] = ()
    correction_allowed: bool = False

    @property
    def allows_answer_generation(self) -> bool:
        return self.outcome in {
            AnswerOutcome.FULL_ANSWER,
            AnswerOutcome.PARTIAL_ANSWER,
        }


def decide_answer_policy(
    question: str,
    evidence: PolicyEvidence,
    access_context: Mapping[str, object] | None,
) -> AnswerDecision:
    """Return the only outcome policy callers need to interpret.

    The question is deliberately accepted at the seam even though the first
    deterministic policy version does not inspect its raw text. Future policy
    revisions can use normalized intent without changing callers.
    """
    del question
    access = access_context or {}
    if bool(access.get("access_denied")):
        return AnswerDecision(
            AnswerOutcome.ACCESS_DENIED,
            EvidenceState.INSUFFICIENT,
            reason="access_denied",
        )

    source_decision = evidence.decision
    quotes = tuple(source_decision.evidence_quotes)
    if evidence.clarification_required:
        return AnswerDecision(
            AnswerOutcome.CLARIFICATION_REQUIRED,
            EvidenceState.AMBIGUOUS,
            reason="clarification_required",
            evidence_quotes=quotes,
        )

    if source_decision.telemetry_status == "verifier_unavailable":
        return AnswerDecision(
            AnswerOutcome.INSUFFICIENT_EVIDENCE,
            EvidenceState.INSUFFICIENT,
            reason=source_decision.reason,
        )

    total_branches = max(0, int(evidence.total_branch_count))
    sufficient_branches = min(
        total_branches,
        max(0, int(evidence.sufficient_branch_count)),
    )
    if total_branches and sufficient_branches:
        if sufficient_branches == total_branches:
            return AnswerDecision(
                AnswerOutcome.FULL_ANSWER,
                EvidenceState.SUFFICIENT,
                reason="all_branches_sufficient",
                evidence_quotes=quotes,
            )
        return AnswerDecision(
            AnswerOutcome.PARTIAL_ANSWER,
            EvidenceState.AMBIGUOUS,
            reason="partial_branch_coverage",
            evidence_quotes=quotes,
        )

    if source_decision.state is EvidenceState.SUFFICIENT:
        return AnswerDecision(
            AnswerOutcome.FULL_ANSWER,
            EvidenceState.SUFFICIENT,
            reason=source_decision.reason or "evidence_sufficient",
            evidence_quotes=quotes,
        )

    correction_allowed = bool(
        source_decision.state is EvidenceState.AMBIGUOUS
        and evidence.has_retrieved_evidence
        and evidence.retrieval_can_improve
    )
    return AnswerDecision(
        AnswerOutcome.INSUFFICIENT_EVIDENCE,
        source_decision.state,
        reason=source_decision.reason or "insufficient_evidence",
        evidence_quotes=quotes,
        correction_allowed=correction_allowed,
    )


def decide_terminal_policy(
    question: str,
    *,
    reason: str,
    access_denied: bool = False,
) -> AnswerDecision:
    """Normalize early terminal paths through the shared policy seam."""
    return decide_answer_policy(
        question,
        PolicyEvidence(
            decision=EvidenceDecision(
                EvidenceState.INSUFFICIENT,
                reason=reason,
                stage="terminal",
                telemetry_status="terminal",
            ),
            has_retrieved_evidence=False,
            retrieval_can_improve=False,
        ),
        {"access_denied": access_denied},
    )


__all__ = [
    "AnswerDecision",
    "AnswerOutcome",
    "PolicyEvidence",
    "decide_answer_policy",
    "decide_terminal_policy",
]
