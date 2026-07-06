"""P2.3 — Feedback/quality/regression service (L6). Pass-through toi db repositories."""
from mech_chatbot.db.repository import (
    add_regression_question,
    cleanup_dangling_records,
    ensure_regression_question,
    get_doc_quality_ranking,
    get_regression_runs,
    list_regression_questions,
    recompute_doc_quality_scores,
    set_regression_question_active,
    upsert_golden_answer,
)

__all__ = [
    "add_regression_question",
    "cleanup_dangling_records",
    "ensure_regression_question",
    "get_doc_quality_ranking",
    "get_regression_runs",
    "list_regression_questions",
    "recompute_doc_quality_scores",
    "set_regression_question_active",
    "upsert_golden_answer",
]
