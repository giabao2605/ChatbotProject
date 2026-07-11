from types import SimpleNamespace

import pytest

from mech_chatbot.rag.rerank import diversify_candidates


pytestmark = pytest.mark.unit


def _doc(doc_id, text):
    return SimpleNamespace(page_content=text, metadata={"doc_id": doc_id})


def test_diversify_deduplicates_and_caps_per_document():
    docs = [
        _doc(1, "same"),
        _doc(1, "same"),
        _doc(1, "second"),
        _doc(1, "third"),
        _doc(2, "other"),
    ]

    result = diversify_candidates(docs, max_per_document=2, cap=10)

    assert [(d.metadata["doc_id"], d.page_content) for d in result] == [
        (1, "same"),
        (1, "second"),
        (2, "other"),
    ]


def test_diversify_respects_global_cap():
    docs = [_doc(index, f"text-{index}") for index in range(10)]
    assert len(diversify_candidates(docs, max_per_document=4, cap=3)) == 3
