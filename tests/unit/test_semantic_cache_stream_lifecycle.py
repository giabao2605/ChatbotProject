import pytest

from mech_chatbot.rag import semantic_cache


pytestmark = pytest.mark.unit


def test_partial_or_cancelled_stream_is_never_saved_to_semantic_cache(monkeypatch):
    saved = []
    monkeypatch.setattr(semantic_cache, "store", lambda *args, **kwargs: saved.append((args, kwargs)))

    def inner():
        yield "partial answer"
        raise RuntimeError("client stream cancelled")

    stream = semantic_cache.teeing_store_stream(
        inner(),
        question="q",
        embedding=[0.1],
        scope_sig="scope",
        ref_text="",
        ref_images=[],
        source_doc_ids=[1],
        model="model",
        citation_snapshot=[{"doc_id": 1}],
        evidence_snapshot=[{"doc_id": 1}],
    )

    assert next(stream) == "partial answer"
    with pytest.raises(RuntimeError, match="cancelled"):
        next(stream)
    assert saved == []
