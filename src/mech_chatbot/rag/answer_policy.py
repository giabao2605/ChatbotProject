"""One policy seam for answer, correction, clarification, and refusal outcomes."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
import re
from typing import Mapping
import unicodedata

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
    negative_evidence: bool = False
    negative_evidence_quote: str = ""
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
        return self.reason == "explicit_negative_evidence" or self.outcome in {
            AnswerOutcome.FULL_ANSWER,
            AnswerOutcome.PARTIAL_ANSWER,
        }


def _fold(value: str) -> str:
    normalized = unicodedata.normalize("NFKD", str(value or "").casefold())
    return "".join(
        char for char in normalized if not unicodedata.combining(char)
    ).replace("đ", "d")


_NEGATIVE_STATEMENT = re.compile(
    r"(?:khong|chua)\s+(?:co|ghi|neu|cung cap|cong bo|de cap|quy dinh)\b[^.!?;\n]{0,140}"
    r"|\bdoes\s+not\s+(?:provide|state|publish|specify|contain)\b[^.!?;\n]{0,140}"
    r"|\bno\s+(?:field|data|information)\b[^.!?;\n]{0,140}",
    re.IGNORECASE,
)
_STOP_WORDS = {
    "bao", "nhieu", "la", "cua", "trong", "tai", "lieu", "co", "khong",
    "what", "which", "the", "does", "document", "provide",
}
_NEGATIVE_TOPIC_GROUPS = (
    ("chi phi", "don gia", "cost", "price"),
    ("thoi gian", "chu ky", "duration", "cycle", "time"),
    ("vat lieu", "material"),
)


def has_explicit_negative_evidence(question: str, context_text: str) -> bool:
    """Return true only for a direct, question-relevant negative source statement."""
    return bool(explicit_negative_evidence_quote(question, context_text))


def explicit_negative_evidence_quote(question: str, context_text: str) -> str:
    """Return the relevant negative source clause, if one is present."""
    question_folded = _fold(question)
    question_tokens = set(re.findall(r"[a-z0-9]+", question_folded)) - _STOP_WORDS
    sentences = re.split(r"(?<=[.!?;])\s+|\r?\n+", str(context_text or ""))
    for original_sentence in sentences:
        context_folded = _fold(original_sentence)
        for match in _NEGATIVE_STATEMENT.finditer(context_folded):
            statement_tokens = set(re.findall(r"[a-z0-9]+", match.group(0)))
            matched_topics = [
                group
                for group in _NEGATIVE_TOPIC_GROUPS
                if any(re.search(rf"\b{re.escape(topic)}\b", context_folded) for topic in group)
            ]
            if matched_topics and any(
                re.search(rf"\b{re.escape(topic)}\b", question_folded)
                for group in matched_topics
                for topic in group
            ):
                return original_sentence.strip()
            if not matched_topics and question_tokens & statement_tokens:
                return original_sentence.strip()
    return ""


def render_explicit_negative_answer(
    quote: str,
    *,
    source_id: str = "",
    language: str = "vi",
) -> str:
    """Render direct negative evidence without another provider round trip."""
    statement = str(quote or "").strip()[:500]
    citation = f" [SRC:{source_id.strip().upper()}]" if source_id else ""
    if str(language or "vi").strip().casefold().startswith("en"):
        return f"The document states directly: “{statement}”{citation}"
    return f"Theo tài liệu, thông tin được nêu rõ: “{statement}”{citation}"


def render_cited_explicit_negative_answer(
    quote: str,
    documents,
    *,
    language: str = "vi",
) -> str:
    """Fail closed to normal generation when the evidence source is unresolved."""
    from mech_chatbot.rag.answer_checks import source_id_for_evidence_quote

    source_id = source_id_for_evidence_quote(quote, documents)
    if not source_id:
        return ""
    return render_explicit_negative_answer(
        quote,
        source_id=source_id,
        language=language,
    )


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

    if evidence.negative_evidence:
        return AnswerDecision(
            AnswerOutcome.INSUFFICIENT_EVIDENCE,
            EvidenceState.SUFFICIENT,
            reason="explicit_negative_evidence",
            evidence_quotes=(
                (evidence.negative_evidence_quote,)
                if evidence.negative_evidence_quote else quotes
            ),
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
    "has_explicit_negative_evidence",
    "explicit_negative_evidence_quote",
    "render_explicit_negative_answer",
    "render_cited_explicit_negative_answer",
]
