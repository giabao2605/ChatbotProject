"""Governance checks and bounded traversal helpers for SQL GraphRAG."""

from __future__ import annotations

import os
import re

from langchain_core.documents import Document
from qdrant_client import models


_LEVELS = {"public": 0, "internal": 1, "confidential": 2}
_RELATIONAL_TERMS = (
    "supersede", "thay thế", "contains", "chứa", "thuộc", "uses material",
    "dùng vật liệu", "sử dụng vật liệu", "liên kết", "mối quan hệ", "quan hệ",
    "phiên bản hiện hành", "current version",
)


def enabled() -> bool:
    return os.getenv("RAG_GRAPH_RETRIEVAL_ENABLED", "false").strip().lower() in {
        "1", "true", "yes", "y", "on"
    }


def should_attempt_graph(question: str) -> bool:
    """Route only explicit relational/global questions to the graph seam."""
    normalized = " ".join(str(question or "").casefold().split())
    return bool(normalized) and any(term in normalized for term in _RELATIONAL_TERMS)


def select_graph_seeds(question: str, candidate_ids=()):
    """Keep governed retrieval entities while always retaining explicit query codes."""
    values = {str(item).strip() for item in candidate_ids or () if str(item).strip()}
    values.update(re.findall(
        r"\b[A-Z]{1,10}[-_][A-Z0-9][A-Z0-9._-]*\b",
        str(question or ""), flags=re.IGNORECASE,
    ))
    return sorted(values, key=str.casefold)


def expand_seed_keys(seed_keys):
    """Expand user-visible identifiers to deterministic canonical graph keys."""
    expanded = set()
    for item in seed_keys or ():
        value = str(item or "").strip().lower()
        if not value:
            continue
        expanded.update((value, f"part:{value}", f"material:{value}"))
    return sorted(expanded)


def filter_servable_edges(edges, access_context):
    """Fail closed over graph evidence returned by any storage adapter."""
    context = access_context or {}
    roles = {str(item).strip().lower() for item in context.get("roles", ())}
    is_admin = "admin" in roles
    departments = {str(item).strip() for item in context.get("allowed_departments", ())}
    sites = {str(item).strip() for item in context.get("allowed_sites", ())}
    max_level = _LEVELS.get(str(context.get("max_security_level") or "public").lower(), 0)
    accepted = []
    for edge in edges or ():
        if str(edge.get("serving_status") or "").lower() != "approved":
            continue
        if not all((
            bool(edge.get("servable")),
            bool(edge.get("is_current")),
            str(edge.get("publication_state") or "").lower() == "published",
            str(edge.get("lifecycle_status") or "").lower() == "published",
            str(edge.get("review_status") or "").lower() == "approved",
        )):
            continue
        if not is_admin:
            if str(edge.get("department") or "") not in departments:
                continue
            if not edge.get("site") or str(edge.get("site")) not in sites:
                continue
            level = _LEVELS.get(str(edge.get("security_level") or "confidential").lower(), 2)
            if level > max_level:
                continue
        accepted.append(edge)
    return accepted


def hydrate_graph_edges(edges, client, collection_name):
    """Hydrate approved SQL edges from the exact Qdrant document page or drop them."""
    hydrated = []
    points_by_doc = {}
    for edge in edges or ():
        try:
            doc_id = int(edge["doc_id"])
            page = int(edge["page"])
            version = int(edge["version"])
        except (KeyError, TypeError, ValueError):
            continue
        if page <= 0:
            continue
        if doc_id not in points_by_doc:
            doc_points = []
            offset = None
            while True:
                batch, offset = client.scroll(
                    collection_name=collection_name,
                    scroll_filter=models.Filter(must=[
                        models.FieldCondition(key="metadata.doc_id", match=models.MatchValue(value=doc_id)),
                    ]),
                    offset=offset,
                    limit=100,
                    with_payload=True,
                    with_vectors=False,
                )
                doc_points.extend(batch)
                if offset is None:
                    break
            points_by_doc[doc_id] = doc_points
        points = points_by_doc[doc_id]
        for point in points:
            payload = getattr(point, "payload", {}) or {}
            metadata = dict(payload.get("metadata") or {})
            try:
                if (
                    int(metadata.get("doc_id")) != doc_id
                    or int(metadata.get("trang_so")) != page
                    or int(metadata.get("version_no")) != version
                ):
                    continue
            except (TypeError, ValueError):
                continue
            content = str(payload.get("page_content") or metadata.get("noi_dung_goc") or "").strip()
            if not content:
                continue
            relation = (
                f"Quan he duoc duyet: {edge.get('source_name') or edge.get('source_key')} "
                f"--{edge.get('relation_type')}--> {edge.get('target_name') or edge.get('target_key')}"
            )
            metadata.update({
                "doc_id": doc_id, "trang_so": page, "version_no": version,
                "file_goc": edge.get("file_goc") or metadata.get("file_goc"),
                "security_level": edge.get("security_level"), "site": edge.get("site"),
                "loai_du_lieu": "knowledge_graph", "doc_status": "published",
                "graph_edge_id": edge.get("edge_id"),
                "graph_relation_type": edge.get("relation_type"),
                "graph_source_key": edge.get("source_key"),
                "graph_target_key": edge.get("target_key"),
            })
            hydrated.append(Document(page_content=relation + "\n\n" + content, metadata=metadata))
            break
    return hydrated


def _document_version_key(document):
    metadata = document.metadata or {}
    return (
        str(metadata.get("doc_id")),
        str(metadata.get("trang_so")),
        str(metadata.get("version_no")),
    )


def attach_served_graph_context(served_documents, graph_documents):
    """Attach relation statements only to exact pages surviving selection.

    The returned evidence list is the auditable subset that reached generation,
    not every edge encountered by the traversal.
    """
    served_documents = list(served_documents or [])
    graph_documents = list(graph_documents or [])
    relations_by_page = {}
    served_keys = {_document_version_key(doc) for doc in served_documents}
    served_evidence = []
    for document in graph_documents:
        key = _document_version_key(document)
        relation = str(document.page_content or "").split("\n\n", 1)[0].strip()
        if key not in served_keys:
            continue
        served_evidence.append(document)
        if relation:
            relations_by_page.setdefault(key, []).append(relation)

    merged = []
    for document in served_documents:
        key = _document_version_key(document)
        relations = list(dict.fromkeys(relations_by_page.get(key, ())))
        if not relations:
            merged.append(document)
            continue
        relation_context = "\n".join(relations)
        metadata = dict(document.metadata or {})
        original_content = str(
            metadata.get("noi_dung_goc") or document.page_content or ""
        )
        metadata["noi_dung_goc"] = relation_context + "\n\n" + original_content
        merged.append(Document(
            page_content=relation_context + "\n\n" + str(document.page_content or ""),
            metadata=metadata,
        ))
    return merged, served_evidence
