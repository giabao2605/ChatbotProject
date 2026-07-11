from contextlib import contextmanager
from types import SimpleNamespace

import pytest

pytestmark = pytest.mark.unit

from mech_chatbot.rag import rerank  # noqa: E402


class _Response:
    def __init__(self, payload):
        self.payload = payload

    def raise_for_status(self):
        return None

    def json(self):
        return self.payload


def _doc(text):
    return SimpleNamespace(page_content=text, metadata={"noi_dung_goc": text})


def test_voyage_rerank_preserves_documents_and_order(monkeypatch):
    posted = {}
    audit = {}

    def fake_post(url, **kwargs):
        posted["url"] = url
        posted.update(kwargs)
        return _Response({
            "data": [
                {"index": 2, "relevance_score": 0.91},
                {"index": 0, "relevance_score": 0.62},
            ]
        })

    @contextmanager
    def fake_audit(**kwargs):
        audit.update(kwargs)
        yield

    monkeypatch.setenv("VOYAGE_API_KEY", "test-key")
    monkeypatch.setenv("VOYAGE_RERANK_MODEL", "rerank-2.5-lite")
    monkeypatch.setattr(rerank.requests, "post", fake_post)
    monkeypatch.setattr(rerank, "audited_external_call", fake_audit)
    docs = [_doc("zero"), _doc("one"), _doc("two")]

    result = rerank.voyage_rerank_documents(docs, "cau hoi", top_n=2)

    assert result == [docs[2], docs[0]]
    assert docs[2].metadata["relevance_score"] == 0.91
    assert posted["url"] == "https://api.voyageai.com/v1/rerank"
    assert posted["headers"]["Authorization"] == "Bearer test-key"
    assert posted["json"] == {
        "query": "cau hoi",
        "documents": ["zero", "one", "two"],
        "model": "rerank-2.5-lite",
        "top_k": 2,
        "return_documents": False,
        "truncation": True,
    }
    assert audit["provider"] == "voyage"
    assert audit["surface"] == "reranking"
    assert audit["policies"] == ["all_external", "all_external", "all_external"]


def test_voyage_rerank_requires_api_key(monkeypatch):
    monkeypatch.delenv("VOYAGE_API_KEY", raising=False)

    with pytest.raises(RuntimeError, match="VOYAGE_API_KEY"):
        rerank.voyage_rerank_documents([_doc("text")], "cau hoi")
