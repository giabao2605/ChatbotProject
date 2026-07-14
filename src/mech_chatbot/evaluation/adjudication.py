"""Deterministic two-reviewer plus tie-break adjudication protocol."""

from __future__ import annotations

from mech_chatbot.evaluation.outcomes import VALID_OUTCOMES


def _validate_review(review: dict) -> None:
    if not str(review.get("reviewer_id") or "").strip():
        raise ValueError("reviewer_id is required")
    if review.get("role") not in {"reviewer", "adjudicator"}:
        raise ValueError("role must be reviewer or adjudicator")
    if review.get("outcome_label") not in VALID_OUTCOMES:
        raise ValueError("invalid outcome_label")
    if not isinstance(review.get("answer_correct"), bool):
        raise ValueError("answer_correct must be boolean")
    if not isinstance(review.get("citation_correct"), bool):
        raise ValueError("citation_correct must be boolean")
    if not str(review.get("reason_code") or "").strip():
        raise ValueError("reason_code is required")


def _label(review: dict) -> tuple:
    return (
        review["outcome_label"],
        review["answer_correct"],
        review["citation_correct"],
    )


def resolve_case_reviews(case_id: str, reviews: list[dict]) -> dict:
    """Resolve two independent labels, requiring a third reviewer on disagreement."""
    if not str(case_id or "").strip():
        raise ValueError("case_id is required")
    for review in reviews:
        _validate_review(review)
    reviewer_ids = [str(review["reviewer_id"]) for review in reviews]
    if len(set(reviewer_ids)) != len(reviewer_ids):
        raise ValueError("reviewers must be unique")

    independent = [review for review in reviews if review["role"] == "reviewer"]
    adjudicators = [review for review in reviews if review["role"] == "adjudicator"]
    if len(independent) != 2:
        raise ValueError("exactly two independent reviewers are required")
    disagreement = _label(independent[0]) != _label(independent[1])
    if disagreement:
        if len(adjudicators) != 1:
            raise ValueError("a third adjudicator is required when labels disagree")
        resolved = adjudicators[0]
        resolved_by = str(resolved["reviewer_id"])
    else:
        if adjudicators:
            raise ValueError("adjudicator is only allowed when labels disagree")
        resolved = independent[0]
        resolved_by = "consensus"

    return {
        "schema": "evaluation-adjudication-v1",
        "case_id": str(case_id),
        "disagreement": disagreement,
        "resolved_by": resolved_by,
        "reviewer_ids": reviewer_ids,
        "outcome_label": resolved["outcome_label"],
        "answer_correct": resolved["answer_correct"],
        "citation_correct": resolved["citation_correct"],
        "reason_codes": [str(review["reason_code"]) for review in reviews],
    }
