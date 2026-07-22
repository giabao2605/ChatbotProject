"""Private diagnostic builders shared by RAG phases."""

from __future__ import annotations

from typing import Any

from mech_chatbot.rag.answer_policy import decide_terminal_policy


def serialize_debug_documents(docs=None) -> list[dict[str, Any]]:
    return [
        {
            "file_goc": doc.metadata.get("file_goc"),
            "doc_id": doc.metadata.get("doc_id"),
            "version_no": doc.metadata.get("version_no"),
            "variant_code": doc.metadata.get("variant_code"),
            "is_current": doc.metadata.get("is_current"),
            "lifecycle_status": doc.metadata.get("lifecycle_status"),
            "review_status": doc.metadata.get("review_status"),
            "trang": doc.metadata.get("trang_so"),
            "source_id": (
                f"D{doc.metadata.get('doc_id')}P{doc.metadata.get('trang_so')}"
                if doc.metadata.get("doc_id") is not None
                and doc.metadata.get("trang_so") is not None
                else None
            ),
            "vision_used": bool(doc.metadata.get("vision_used", False)),
            "score": doc.metadata.get("relevance_score"),
            "security_level": doc.metadata.get("security_level"),
            "graph_edge_id": doc.metadata.get("graph_edge_id"),
            "graph_relation_type": doc.metadata.get("graph_relation_type"),
            "graph_source_key": doc.metadata.get("graph_source_key"),
            "graph_target_key": doc.metadata.get("graph_target_key"),
            "text": str(
                doc.metadata.get("noi_dung_goc")
                or getattr(doc, "page_content", "")
                or ""
            )[:800],
        }
        for doc in docs or []
    ]


def make_debug_info(docs=None) -> dict[str, Any]:
    try:
        from mech_chatbot.rag.semantic_cache import pipeline_namespace

        namespace = pipeline_namespace()
    except Exception:
        namespace = "unknown"
    return {
        "pipeline_namespace": namespace,
        "retrieved_docs": serialize_debug_documents(docs),
    }


def make_terminal_debug(
    question: str,
    reason: str,
    docs=None,
    *,
    access_denied: bool = False,
) -> dict[str, Any]:
    decision = decide_terminal_policy(
        question,
        reason=reason,
        access_denied=access_denied,
    )
    debug = make_debug_info(docs)
    debug.update(
        {
            "answer_outcome": decision.outcome.value,
            "evidence_state": decision.evidence_state.value,
            "evidence_stage": "terminal",
            "correction_allowed": False,
            "evidence_quotes": list(decision.evidence_quotes),
        }
    )
    return debug


def make_source_snapshot(docs=None) -> list[dict[str, Any]]:
    snapshots = []
    for doc in docs or []:
        metadata = getattr(doc, "metadata", {}) or {}
        doc_id = metadata.get("doc_id")
        page_no = metadata.get("trang_so")
        try:
            normalized_doc_id = int(doc_id) if doc_id is not None else None
        except (TypeError, ValueError):
            normalized_doc_id = None
        try:
            normalized_page_no = int(page_no) if page_no is not None else None
        except (TypeError, ValueError):
            normalized_page_no = None
        if normalized_doc_id is None:
            continue
        snapshots.append(
            {
                "file_goc": metadata.get("file_goc"),
                "doc_id": normalized_doc_id,
                "version_no": metadata.get("version_no"),
                "variant_code": metadata.get("variant_code"),
                "is_current": metadata.get("is_current"),
                "lifecycle_status": metadata.get("lifecycle_status"),
                "review_status": metadata.get("review_status"),
                "trang": normalized_page_no,
                "source_id": (
                    f"D{normalized_doc_id}P{normalized_page_no}"
                    if normalized_page_no is not None
                    else None
                ),
                "score": metadata.get("relevance_score"),
                "security_level": metadata.get("security_level"),
            }
        )
    return snapshots


__all__ = [
    "make_debug_info",
    "make_source_snapshot",
    "make_terminal_debug",
    "serialize_debug_documents",
]
