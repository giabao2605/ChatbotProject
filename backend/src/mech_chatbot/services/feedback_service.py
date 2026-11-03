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


class EvidenceCertificationError(PermissionError):
    """A knowledge source could not be certified before generation."""


def pin_generation_evidence(documents, actor, *, checkpoint=None, deadline=None, image_analysis=None):
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
            # Retrieval page numbers may be strings; pin and history must share the same integer locator.
            for key in ('trang', 'page_no', 'trang_so'):
                page = metadata.get(key)
                if isinstance(page, str) and page.isascii() and page.isdecimal():
                    metadata[key] = int(page)
            if (image_analysis and metadata.get('doc_id') is None
                    and metadata.get('loai_du_lieu') == 'image_summary'
                    and metadata.get('file_goc') == 'Anh dinh kem tu nguoi dung'
                    and document.page_content == f'Phan tich noi dung anh nguoi dung tai len: {image_analysis}'):
                result.append(copy(document))
                continue
            try:
                pinned = pin_generation_sources(conn,[{**metadata,'text':metadata.get('noi_dung_goc') or document.page_content}],effective)[0]
                metadata = {**metadata,**({'evidence_pin':pinned['evidence_pin']} if 'evidence_pin' in pinned else {})}
            except PermissionError:
                # Synthetic title metadata has no page proof; exclude it before generation.
                if metadata.get('loai_du_lieu') == 'title_block':
                    continue
                raise EvidenceCertificationError('Source evidence could not be certified') from None
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
