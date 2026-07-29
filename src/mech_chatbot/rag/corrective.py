"""Bounded corrective-retrieval policy and deterministic merge helpers."""

from __future__ import annotations

import re
import unicodedata

from qdrant_client import models

from mech_chatbot.domain.serving_state import is_currently_servable
from mech_chatbot.rag.answer_policy import AnswerDecision
from mech_chatbot.rag.context_builders import _payload_document


MAX_CORRECTION_PASSES = 1
_SAFE_CORRECTION_CODE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._/-]{1,127}$")


def correction_enabled(enabled: bool = False) -> bool:
    """Return the process-provided CRAG switch."""
    return bool(enabled)


def should_attempt_correction(
    decision: AnswerDecision,
    *,
    attempts: int,
    enabled: bool,
) -> bool:
    return bool(
        enabled
        and attempts < MAX_CORRECTION_PASSES
        and decision.correction_allowed
    )


def _normalized_terms(value):
    normalized = unicodedata.normalize("NFKD", str(value or "").casefold())
    ascii_text = "".join(
        char for char in normalized if not unicodedata.combining(char)
    )
    return re.findall(r"[a-z0-9]+", ascii_text)


def _shares_query_phrase(question, document):
    query_terms = _normalized_terms(question)
    document_text = " ".join(
        _normalized_terms(getattr(document, "page_content", ""))
    )
    return any(
        " ".join(query_terms[index:index + 3]) in document_text
        for index in range(max(0, len(query_terms) - 2))
    )


def metadata_correction_code(question, documents):
    """Return the top governed result's safe, query-relevant document code."""
    docs = list(documents or [])
    if not docs:
        return None
    metadata = getattr(docs[0], "metadata", {}) or {}
    code = str(metadata.get("base_code") or "").strip()
    if (
        not _SAFE_CORRECTION_CODE.fullmatch(code)
        or code.casefold() in str(question or "").casefold()
        or not _shares_query_phrase(question, docs[0])
    ):
        return None
    return code


def metadata_correction_query(question, documents):
    """Expand a query with the top governed result's stable document code."""
    code = metadata_correction_code(question, documents)
    if code is None:
        return None
    return f"{str(question or '').strip()} {code}".strip()


def load_metadata_corrected_documents(
    *,
    client,
    collection_name,
    strict_filter,
    base_code,
):
    """Load a bounded, payload-only correction without relaxing governance."""
    code = str(base_code or "").strip()
    if not _SAFE_CORRECTION_CODE.fullmatch(code):
        raise ValueError("invalid corrective base code")
    if client is None or not str(collection_name or "").strip():
        raise RuntimeError("corrective Qdrant runtime is not configured")
    if strict_filter is None:
        raise RuntimeError("corrective strict filter is not configured")

    def sort_number(value):
        try:
            return int(value or 0)
        except (TypeError, ValueError):
            return 0

    points, _ = client.scroll(
        collection_name=collection_name,
        scroll_filter=models.Filter(
            must=[
                strict_filter,
                models.FieldCondition(
                    key="metadata.base_code",
                    match=models.MatchValue(value=code),
                ),
            ]
        ),
        limit=30,
        with_payload=True,
        with_vectors=False,
        timeout=3,
    )
    documents = []
    for point in points or ():
        payload = getattr(point, "payload", None) or {}
        metadata = dict(payload.get("metadata") or {})
        if not is_currently_servable(metadata, require_current=True):
            continue
        document = _payload_document(payload)
        if document is not None:
            documents.append((
                (
                    str(metadata.get("doc_id") or ""),
                    sort_number(
                        metadata.get("trang_so") or metadata.get("page_no")
                    ),
                    sort_number(metadata.get("chunk_index")),
                    str(getattr(point, "id", "")),
                ),
                document,
            ))
    documents.sort(key=lambda item: item[0])
    return [document for _, document in documents]


def _document_key(document):
    metadata = getattr(document, "metadata", {}) or {}
    doc_id = metadata.get("doc_id")
    page = metadata.get("trang_so") or metadata.get("page_no")
    if doc_id is not None and page is not None:
        chunk = metadata.get("chunk_index")
        if chunk is not None:
            return ("chunk", str(doc_id), str(page), str(chunk))
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
