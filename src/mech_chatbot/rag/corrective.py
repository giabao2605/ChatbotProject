"""Bounded corrective-retrieval policy and deterministic merge helpers."""

from __future__ import annotations

from mech_chatbot.rag.evidence_gate import EvidenceDecision, EvidenceState


MAX_CORRECTION_PASSES = 1


def should_attempt_correction(
    decision: EvidenceDecision,
    *,
    attempts: int,
    enabled: bool,
) -> bool:
    return bool(
        enabled
        and attempts < MAX_CORRECTION_PASSES
        and decision.state is EvidenceState.AMBIGUOUS
    )


def _document_key(document):
    metadata = getattr(document, "metadata", {}) or {}
    doc_id = metadata.get("doc_id")
    page = metadata.get("trang_so") or metadata.get("page_no")
    if doc_id is not None and page is not None:
        return ("source", str(doc_id), str(page))
    return ("content", str(getattr(document, "page_content", ""))[:500])


def merge_corrected_documents(original, corrected):
    merged = []
    seen = set()
    for document in list(original or []) + list(corrected or []):
        key = _document_key(document)
        if key in seen:
            continue
        seen.add(key)
        merged.append(document)
    return merged


def run_corrected_retrieval(
    retrieve,
    *,
    corrected_query,
    new_part_ids,
    strict_filter,
    broad_filter,
    rbac_filter,
    is_bom_query,
    trace_id,
):
    """Invoke the normal retrieval boundary without rebuilding any filter."""
    return retrieve(
        new_part_ids=new_part_ids,
        strict_filter=strict_filter,
        broad_filter=broad_filter,
        is_bom_query=is_bom_query,
        query_to_search=corrected_query,
        rbac_filter=rbac_filter,
        trace_id=trace_id,
    )
