"""P2.3 — Feedback/quality/regression service (L6). Pass-through toi db repositories."""
from mech_chatbot.db.repositories.feedback import (
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
from mech_chatbot.db.repositories.feedback_access import FeedbackSchemaUnavailable


def pin_generation_evidence(documents, actor, *, checkpoint=None, deadline=None):
    from copy import copy
    from dataclasses import asdict
    from mech_chatbot.db.repositories.feedback_access import access_transaction, pin_generation_sources
    from mech_chatbot.application.feedback_access import effective_actor
    if not actor.user_id or not actor.username:
        return documents
    with access_transaction(asdict(actor),schema=False,review=False,checkpoint=checkpoint,deadline=deadline) as (conn,current):
        effective = effective_actor(current,asdict(actor))
        result = []
        for document in documents:
            metadata = {key:value for key,value in document.metadata.items() if key != 'evidence_pin'}
            try:
                pinned = pin_generation_sources(conn,[{**metadata,'text':metadata.get('noi_dung_goc') or document.page_content}],effective)[0]
                metadata = {**metadata,**({'evidence_pin':pinned['evidence_pin']} if 'evidence_pin' in pinned else {})}
            except PermissionError:
                # shortcut: derived text stays unpinned until its complete source proof is available.
                pass
            duplicate = copy(document)
            duplicate.metadata = metadata
            result.append(duplicate)
        return result

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
