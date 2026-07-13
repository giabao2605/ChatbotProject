"""Outcome labels and confusion metrics for answer/refusal evaluation."""

from __future__ import annotations

from collections import Counter


ANSWER_OUTCOMES = {"full_answer", "partial_answer", "clarification_required"}
REFUSAL_OUTCOMES = {"insufficient_evidence", "access_denied"}
VALID_OUTCOMES = ANSWER_OUTCOMES | REFUSAL_OUTCOMES


def expected_outcome(case: dict) -> str:
    explicit = case.get("expected_outcome")
    if explicit in VALID_OUTCOMES:
        return explicit
    return "insufficient_evidence" if case.get("should_refuse") else "full_answer"


def classify_outcome(expected: str, actual: str, *, answer_correct: bool, leaked: bool) -> str:
    if leaked:
        return "leakage"
    if expected in ANSWER_OUTCOMES and actual in REFUSAL_OUTCOMES:
        return "wrong_refusal"
    if expected in REFUSAL_OUTCOMES and actual in REFUSAL_OUTCOMES:
        return "correct_refusal" if expected == actual else "wrong_refusal_type"
    if expected in REFUSAL_OUTCOMES and actual in ANSWER_OUTCOMES:
        return "wrong_answer"
    return "correct_answer" if answer_correct else "wrong_answer"


def summarize_outcomes(rows: list[dict]) -> dict[str, int]:
    counts = Counter(
        classify_outcome(
            row["expected"],
            row["actual"],
            answer_correct=bool(row.get("answer_correct")),
            leaked=bool(row.get("leaked")),
        )
        for row in rows
    )
    return dict(sorted(counts.items()))

