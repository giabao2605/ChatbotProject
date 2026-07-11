# -*- coding: utf-8 -*-
"""Reranking qua Voyage API va cac helper sap xep context."""

import os
import hashlib
import unicodedata
from dataclasses import dataclass

import requests
from functools import lru_cache
from mech_chatbot.llm.external_ai import (
    audited_external_call,
    get_provider_runtime,
    normalize_rerank_result,
)


_VOYAGE_RERANK_URL = "https://api.voyageai.com/v1/rerank"


def _voyage_runtime():
    return get_provider_runtime(
        "voyage",
        fallback_endpoint="https://api.voyageai.com/v1",
        fallback_model=os.getenv("VOYAGE_RERANK_MODEL", "rerank-2.5-lite"),
        fallback_secret_envs=("VOYAGE_API_KEY",),
    )


def _voyage_rerank_url(endpoint: str) -> str:
    base = str(endpoint or _VOYAGE_RERANK_URL).rstrip("/")
    return base if base.endswith("/rerank") else base + "/rerank"


@dataclass(frozen=True)
class RerankPolicy:
    """Choose one backend for the complete candidate set.

    Risk acceptance for this deployment permits all security levels to use the
    configured external provider.  The policy intentionally never reranks two
    subsets separately because scores from separate requests are not a common
    ranking scale.
    """

    voyage_provider: str = "voyage"

    def select_backend(self, candidates, user_context=None, data_policy=None) -> str:
        del user_context, data_policy
        policies = {
            str((getattr(doc, "metadata", {}) or {}).get("external_processing_policy") or "all_external").strip().lower()
            for doc in (candidates or [])
        }
        if policies and policies != {"all_external"}:
            return "local_fusion"
        enabled = os.getenv("USE_VOYAGE_RERANK", "true").strip().lower() in {"1", "true", "yes", "on"}
        if enabled:
            try:
                if _voyage_runtime().api_key:
                    return self.voyage_provider
            except Exception:
                pass
        return "local_fusion"


@lru_cache(maxsize=4096)
def tokenize_cached(text):
    from underthesea import word_tokenize

    return word_tokenize(text, format="text")


def voyage_rerank_documents(documents, query, top_n=10, trace_id=None):
    """Rerank candidate documents bang Voyage ``rerank-2.5-lite``.

    Voyage tra ve index theo danh sach document dau vao; giu nguyen Document
    va metadata cua project, chi them ``relevance_score`` de trace/debug.
    """
    docs = list(documents or [])
    if not docs:
        return []

    runtime = _voyage_runtime()
    api_key = (runtime.api_key or "").strip()
    if not api_key:
        raise RuntimeError("VOYAGE_API_KEY chua resolve duoc tu secret reference cua Voyage")

    top_n = max(1, min(int(top_n or 10), len(docs)))
    model = runtime.model
    rerank_url = _voyage_rerank_url(runtime.endpoint)
    timeout_seconds = float(os.getenv("VOYAGE_RERANK_TIMEOUT_SECONDS", "15"))
    texts = [
        str((getattr(doc, "metadata", {}) or {}).get("noi_dung_goc")
            or getattr(doc, "page_content", "") or "")
        for doc in docs
    ]
    doc_ids = [(getattr(doc, "metadata", {}) or {}).get("doc_id") for doc in docs]
    security_levels = [
        (getattr(doc, "metadata", {}) or {}).get("security_level") for doc in docs
    ]
    policies = [
        (getattr(doc, "metadata", {}) or {}).get("external_processing_policy")
        or "all_external"
        for doc in docs
    ]

    with audited_external_call(
        provider="voyage",
        model=model,
        endpoint=runtime.endpoint,
        surface="reranking",
        trace_id=trace_id,
        doc_ids=doc_ids,
        security_levels=security_levels,
        policies=policies,
        input_chars=len(str(query or "")) + sum(len(value) for value in texts),
        input_bytes=len(str(query or "").encode("utf-8")) + sum(len(value.encode("utf-8")) for value in texts),
    ):
        response = requests.post(
            rerank_url,
            headers={
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json",
            },
            json={
                "query": str(query or ""),
                "documents": texts,
                "model": model,
                "top_k": top_n,
                "return_documents": False,
                "truncation": True,
            },
            timeout=timeout_seconds,
        )
        response.raise_for_status()
        payload = response.json()
    results = normalize_rerank_result(
        payload,
        provider="voyage",
        model=model,
    ).items

    ranked = []
    seen = set()
    for item in results:
        if not isinstance(item, dict):
            continue
        try:
            index = int(item["index"])
        except (KeyError, TypeError, ValueError):
            continue
        if index < 0 or index >= len(docs) or index in seen:
            continue
        seen.add(index)
        doc = docs[index]
        try:
            doc.metadata["relevance_score"] = float(item.get("relevance_score", 0.0))
        except (AttributeError, TypeError, ValueError):
            pass
        ranked.append(doc)
        if len(ranked) == top_n:
            break

    if not ranked:
        raise ValueError("Voyage rerank response khong co index document hop le")
    return ranked


def _normalized_metadata_text(value):
    text = unicodedata.normalize("NFD", str(value or "").lower())
    text = "".join(char for char in text if unicodedata.category(char) != "Mn")
    return text


def prioritize_document_types(documents, document_type_hints=None):
    """Apply deterministic metadata preference before candidate grouping."""
    hints = {str(item).strip().lower() for item in (document_type_hints or []) if str(item).strip()}
    if not hints:
        return list(documents or [])
    aliases = {
        "purchase_order": ("purchase_order", "purchase order", "po", "don dat hang"),
        "contract": ("contract", "hop dong"),
        "form": ("form", "bieu mau", "mau don"),
    }
    wanted = tuple(alias for hint in hints for alias in aliases.get(hint, (hint,)))

    def rank(doc):
        metadata = getattr(doc, "metadata", {}) or {}
        values = (
            metadata.get("document_type_family"),
            metadata.get("document_type"),
            metadata.get("doc_type"),
            metadata.get("loai_tai_lieu"),
        )
        haystack = " ".join(_normalized_metadata_text(value) for value in values)
        matched = any(alias in haystack for alias in wanted)
        if isinstance(metadata, dict):
            metadata["document_type_rule_match"] = matched
        return 0 if matched else 1

    return sorted(list(documents or []), key=rank)


def diversify_candidates(documents, max_per_document=4, max_per_section=1, cap=40):
    """Group by document/section before reranking.

    The representative child chunk preserves retrieval precision while the
    parent-context stage later restores its neighboring text.  This keeps a
    large document from consuming the entire Voyage candidate budget.
    """
    docs = list(documents or [])
    max_per_document = max(1, int(max_per_document or 1))
    max_per_section = max(1, int(max_per_section or 1))
    cap = max(1, int(cap or len(docs) or 1))
    selected = []
    per_document = {}
    per_section = {}
    seen_content = set()
    for doc in docs:
        metadata = getattr(doc, "metadata", {}) or {}
        document_key = (
            metadata.get("doc_id")
            or metadata.get("file_goc")
            or metadata.get("source")
            or "unknown"
        )
        parent_section = str(metadata.get("parent_section") or "").strip()
        parent_page = metadata.get("parent_page") or metadata.get("trang_so")
        # A chunk with no section/page provenance is not known to share a
        # section with another chunk.  Do not collapse every such chunk into
        # one synthetic ``page:`` bucket; only apply the per-section cap when
        # the ingestion metadata actually identifies a section or page.
        section_key = (
            (document_key, parent_section or f"page:{parent_page}")
            if parent_section or parent_page not in (None, "")
            else None
        )
        text_value = str(
            metadata.get("noi_dung_goc") or getattr(doc, "page_content", "") or ""
        ).strip()
        content_key = hashlib.sha1(text_value.encode("utf-8")).hexdigest()
        if content_key in seen_content:
            continue
        count = per_document.get(document_key, 0)
        if count >= max_per_document:
            continue
        if section_key is not None and per_section.get(section_key, 0) >= max_per_section:
            continue
        selected.append(doc)
        seen_content.add(content_key)
        per_document[document_key] = count + 1
        if section_key is not None:
            per_section[section_key] = per_section.get(section_key, 0) + 1
        if len(selected) >= cap:
            break
    return selected


def rerank_docs(docs):
    priority = {
        "title_block": 0,
        "bang_ke_vat_tu": 1,
        "yckt": 2,
        "hdcv": 3,
        "text": 4,
        "image_summary": 5,
    }
    return sorted(docs, key=lambda d: priority.get(d.metadata.get("loai_du_lieu", "text"), 4))


def long_context_reorder(docs):
    """
    Sap xep lai tai lieu de chong 'Lost in the Middle' cua LLaMA.
    Gia dinh docs da duoc sort theo do uu tien (tu cao xuong thap).
    Mang reorder se xen ke: Rank 1 o dau, Rank 2 o cuoi, Rank 3 o sat dau, Rank 4 o sat cuoi...
    """
    if len(docs) <= 2:
        return docs
 
    reordered = [None] * len(docs)
    left = 0
    right = len(docs) - 1
    for i, doc in enumerate(docs):
        if i % 2 == 0:
            reordered[left] = doc
            left += 1
        else:
            reordered[right] = doc
            right -= 1
    return reordered

__all__ = [
    'RerankPolicy',
    'tokenize_cached',
    'voyage_rerank_documents',
    'prioritize_document_types',
    'diversify_candidates',
    'rerank_docs',
    'long_context_reorder',
]
