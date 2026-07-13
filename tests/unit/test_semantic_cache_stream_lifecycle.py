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


def test_pipeline_namespace_isolated_by_feature_and_version(monkeypatch):
    for name in (
        "RAG_CRAG_ENABLED",
        "RAG_CLAIM_REPAIR_ENABLED",
        "RAG_GROUNDED_MATH_ENABLED",
        "RAG_LATE_INTERACTION_ENABLED",
        "RAG_QUERY_DECOMPOSITION_ENABLED",
        "RAG_GRAPH_RETRIEVAL_ENABLED",
    ):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("RAG_PLANNER_VERSION", "planner-v1")
    monkeypatch.setenv("RAG_LATE_INDEX_VERSION", "late-v1")
    monkeypatch.setenv("RAG_GRAPH_SERVING_EPOCH", "graph-v1")

    baseline = semantic_cache.scope_signature(
        "Technical", ["Technical"], "internal", ["HQ"], ["viewer"]
    )
    monkeypatch.setenv("RAG_LATE_INTERACTION_ENABLED", "true")
    late = semantic_cache.scope_signature(
        "Technical", ["Technical"], "internal", ["HQ"], ["viewer"]
    )
    monkeypatch.setenv("RAG_LATE_INDEX_VERSION", "late-v2")
    reindexed = semantic_cache.scope_signature(
        "Technical", ["Technical"], "internal", ["HQ"], ["viewer"]
    )

    assert "pipe=" in baseline
    assert baseline != late
    assert late != reindexed


def test_evaluation_disables_semantic_cache_by_default(monkeypatch):
    monkeypatch.delenv("SEMANTIC_CACHE_ENABLED", raising=False)
    monkeypatch.setenv("RAG_EXECUTION_CONTEXT", "evaluation")

    assert semantic_cache.enabled() is False
