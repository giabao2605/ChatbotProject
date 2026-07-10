# -*- coding: utf-8 -*-
"""Reranking qua Voyage API va cac helper sap xep context."""

import os

import requests
import underthesea
from functools import lru_cache


_VOYAGE_RERANK_URL = "https://api.voyageai.com/v1/rerank"


@lru_cache(maxsize=4096)
def tokenize_cached(text):
    return underthesea.word_tokenize(text, format="text")


def voyage_rerank_documents(documents, query, top_n=10):
    """Rerank candidate documents bang Voyage ``rerank-2.5-lite``.

    Voyage tra ve index theo danh sach document dau vao; giu nguyen Document
    va metadata cua project, chi them ``relevance_score`` de trace/debug.
    """
    docs = list(documents or [])
    if not docs:
        return []

    api_key = (os.getenv("VOYAGE_API_KEY") or "").strip()
    if not api_key:
        raise RuntimeError("VOYAGE_API_KEY chua duoc cau hinh")

    top_n = max(1, min(int(top_n or 10), len(docs)))
    model = (os.getenv("VOYAGE_RERANK_MODEL") or "rerank-2.5-lite").strip()
    timeout_seconds = float(os.getenv("VOYAGE_RERANK_TIMEOUT_SECONDS", "15"))
    texts = [
        str((getattr(doc, "metadata", {}) or {}).get("noi_dung_goc")
            or getattr(doc, "page_content", "") or "")
        for doc in docs
    ]

    response = requests.post(
        _VOYAGE_RERANK_URL,
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
    results = payload.get("data", payload.get("results", [])) if isinstance(payload, dict) else []
    if not isinstance(results, list):
        raise ValueError("Voyage rerank response khong co danh sach ket qua hop le")

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
    'tokenize_cached',
    'voyage_rerank_documents',
    'rerank_docs',
    'long_context_reorder',
]
