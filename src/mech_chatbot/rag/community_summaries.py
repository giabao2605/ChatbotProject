"""RAG integration and compatibility exports for community summaries."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Callable

from langchain_core.documents import Document
from qdrant_client import models

from mech_chatbot.domain.community_policy import (
    SummaryServingDecision,
    build_pending_summary,
    detect_communities,
    evaluate_summary_serving,
)


@dataclass(frozen=True)
class CommunityContextResult:
    documents: tuple[Document, ...]
    used: bool
    reason: str
    summary_count: int = 0


_GLOBAL_QUERY_TERMS = (
    "tổng hợp",
    "tổng quan",
    "toàn bộ tài liệu",
    "giữa các tài liệu",
    "xuyên tài liệu",
    "xu hướng chung",
    "bức tranh chung",
    "across documents",
    "across all documents",
    "overall summary",
    "global overview",
    "summarize all",
    "cross-document",
)


def enabled(value: bool = False) -> bool:
    return bool(value)


def is_global_query(question: str) -> bool:
    normalized = " ".join(str(question or "").casefold().split())
    return bool(normalized) and any(
        term in normalized for term in _GLOBAL_QUERY_TERMS
    )


def _hydrate_summary_documents(summary: dict, client, collection_name: str):
    hydrated = []
    sources = list(summary.get("source_provenance") or ())
    for source in sources:
        try:
            doc_id = int(source["doc_id"])
            page = int(source["page"])
            version = int(source["version"])
        except (KeyError, TypeError, ValueError):
            return []
        offset = None
        matched = None
        while True:
            batch, offset = client.scroll(
                collection_name=collection_name,
                scroll_filter=models.Filter(must=[
                    models.FieldCondition(
                        key="metadata.doc_id",
                        match=models.MatchValue(value=doc_id),
                    ),
                ]),
                offset=offset,
                limit=100,
                with_payload=True,
                with_vectors=False,
            )
            for point in batch:
                payload = getattr(point, "payload", {}) or {}
                metadata = dict(payload.get("metadata") or {})
                try:
                    exact = (
                        int(metadata.get("doc_id")) == doc_id
                        and int(metadata.get("trang_so")) == page
                        and int(metadata.get("version_no")) == version
                    )
                except (TypeError, ValueError):
                    exact = False
                content = str(
                    payload.get("page_content")
                    or metadata.get("noi_dung_goc")
                    or ""
                ).strip()
                if exact and content:
                    matched = (metadata, content)
                    break
            if matched is not None or offset is None:
                break
        if matched is None:
            return []
        metadata, source_content = matched
        summary_text = str(summary.get("summary_text") or "").strip()
        metadata.update({
            "doc_id": doc_id,
            "trang_so": page,
            "version_no": version,
            "department": source.get("department"),
            "site": source.get("site"),
            "security_level": source.get("security_level"),
            "loai_du_lieu": "community_summary_source",
            "community_summary_used": True,
            "community_summary_id": summary.get("summary_id"),
            "community_key": summary.get("community_key"),
            "community_summary_sha256": summary.get("summary_sha256"),
        })
        combined = (
            "Tóm tắt cộng đồng tri thức đã được duyệt:\n"
            f"{summary_text}\n\nNguồn gốc đã được xác minh:\n{source_content}"
        )
        metadata["noi_dung_goc"] = combined
        hydrated.append(Document(page_content=combined, metadata=metadata))
    return hydrated


def load_community_context(
    question: str,
    *,
    graph_enabled: bool,
    community_enabled: bool,
    access_context: dict,
    seed_keys,
    serving_epoch: str,
    graph_fingerprint: str,
    client,
    collection_name: str,
    loader: Callable | None = None,
    max_documents: int = 10,
) -> CommunityContextResult:
    """Load reviewed global context while retaining exact source-page citations."""
    if not graph_enabled:
        return CommunityContextResult((), False, "graph_retrieval_disabled")
    if not community_enabled:
        return CommunityContextResult(
            (), False, "community_summaries_disabled",
        )
    if not is_global_query(question):
        return CommunityContextResult((), False, "query_not_global")
    if not str(serving_epoch or "").strip() or not str(
        graph_fingerprint or "",
    ).strip():
        return CommunityContextResult((), False, "community_identity_missing")
    if loader is None:
        from mech_chatbot.db.repositories.community_summaries import (
            load_servable_community_summaries,
        )

        loader = load_servable_community_summaries
    try:
        summaries = loader(
            node_keys=list(seed_keys or ()),
            access_context=dict(access_context or {}),
            serving_epoch=str(serving_epoch),
            graph_fingerprint=str(graph_fingerprint),
            limit=5,
        )
    except Exception:
        return CommunityContextResult(
            (), False, "community_repository_unavailable",
        )
    if not summaries:
        return CommunityContextResult((), False, "no_approved_summary")
    documents = []
    used_summaries = 0
    for summary in summaries:
        hydrated = _hydrate_summary_documents(
            summary, client, collection_name,
        )
        if not hydrated:
            continue
        used_summaries += 1
        documents.extend(hydrated)
        if len(documents) >= max(1, int(max_documents)):
            documents = documents[:max(1, int(max_documents))]
            break
    if not documents:
        return CommunityContextResult(
            (), False, "summary_sources_not_hydrated",
        )
    return CommunityContextResult(
        tuple(documents), True, "approved_summary_used", used_summaries,
    )


__all__ = [
    "CommunityContextResult",
    "SummaryServingDecision",
    "build_pending_summary",
    "detect_communities",
    "enabled",
    "evaluate_summary_serving",
    "is_global_query",
    "load_community_context",
]
