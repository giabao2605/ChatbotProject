# -*- coding: utf-8 -*-
"""Auto-split tu rag/service.py (P1.2 refactor). Giu nguyen logic goc; chi tach file + import."""

import underthesea
from tenacity import retry, retry_if_exception_type, retry_if_exception, wait_exponential, stop_after_attempt
from mech_chatbot.llm.llm_client import cohere_invoke, get_cohere_llm, _is_cohere_rate_limit, gpt_rerank_documents, get_llm_model_name
from functools import lru_cache


@lru_cache(maxsize=4096)
def tokenize_cached(text):
    return underthesea.word_tokenize(text, format="text")


@retry(
    retry=retry_if_exception(_is_cohere_rate_limit),
    wait=wait_exponential(multiplier=2, min=2, max=30),
    stop=stop_after_attempt(4),
)
def cohere_rerank(compressor, documents, query, top_n=10):
    # Ten ham cu de tuong thich nguoc; nay rerank bang GPT-5.4.
    return gpt_rerank_documents(documents, query, top_n=top_n)


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
    'cohere_rerank',
    'rerank_docs',
    'long_context_reorder',
]
