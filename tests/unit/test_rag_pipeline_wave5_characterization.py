import json
from functools import partial
from types import SimpleNamespace

import pytest

from mech_chatbot.rag.execution import (
    AccessScope,
    DefaultRagExecutor,
    RagCitation,
    RagCompleted,
    RagInvocation,
    RagPrepared,
    RagRequest,
    RagToken,
)


pytestmark = pytest.mark.unit


def _offline_executor(*, semantic_cache_enabled=False):
    from mech_chatbot.rag.pipeline_steps import _retrieve

    vectorstore = SimpleNamespace(
        embeddings=SimpleNamespace(embed_query=lambda _question: [1.0, 0.0]),
        sparse_embeddings=object(),
    )
    runtime = SimpleNamespace(
        client=object(),
        vectorstore=vectorstore,
        collection_name="test-knowledge",
        semantic_cache_enabled=semantic_cache_enabled,
        semantic_cache_sim_threshold=0.93,
        semantic_cache_ttl_hours=24.0,
        semantic_cache_environment={},
        semantic_router_enabled=False,
        hyde_enabled=False,
        query_decomposition_enabled=False,
        graph_retrieval_enabled=False,
        community_summaries_enabled=False,
        crag_enabled=False,
        grounded_math_enabled=False,
        evidence_verifier_enabled=False,
        voyage_enabled=False,
        parent_context_enabled=False,
        late_interaction_config=None,
        strict_answer_mode=True,
    )
    runtime.retrieve = partial(
        _retrieve,
        vectorstore=vectorstore,
        client=runtime.client,
        collection_name=runtime.collection_name,
    )
    provider = SimpleNamespace(
        invoke=lambda *_args, **_kwargs: SimpleNamespace(content="")
    )
    return DefaultRagExecutor(
        retrieval_adapter=runtime,
        provider_adapter=provider,
    ), runtime


@pytest.fixture
def offline_boundaries(monkeypatch):
    """Run the real executor while replacing SQL, Qdrant, and provider boundaries."""
    from mech_chatbot.db import repository

    documents = []
    qdrant_calls = []

    class OfflineQdrantVectorStore:
        def __init__(self, **kwargs):
            qdrant_calls.append(kwargs.get("retrieval_mode"))

        def similarity_search(self, *_args, **_kwargs):
            return list(documents)

    monkeypatch.setattr("langchain_qdrant.QdrantVectorStore", OfflineQdrantVectorStore)
    monkeypatch.setattr(repository, "get_app_setting_int", lambda *_args: 5)
    monkeypatch.setattr(repository, "get_active_glossary", lambda *_args: [])
    monkeypatch.setattr(repository, "get_common_metadata_for_rag", lambda *_args: {})
    monkeypatch.setattr(repository, "get_technical_attributes_for_rag", lambda *_args: [])
    monkeypatch.setattr(repository, "find_golden_answer", lambda *_args: None)

    executor, runtime = _offline_executor()
    return documents, qdrant_calls, executor, runtime


def _run(
    question,
    *,
    trace_id,
    history=(),
    max_security_level="internal",
    executor=None,
):
    if executor is None:
        executor, _runtime = _offline_executor()
    return list(
        executor.run(
            RagRequest(
                question,
                AccessScope(
                    department="HR",
                    roles=frozenset({"viewer"}),
                    allowed_departments=frozenset({"HR"}),
                    max_security_level=max_security_level,
                    allowed_sites=frozenset({"HCM"}),
                ),
                history=tuple(history),
            ),
            RagInvocation(trace_id=trace_id, mode="test"),
        )
    )


def _answer(events):
    return "".join(event.text for event in events if isinstance(event, RagToken))


def test_exact_cache_outage_falls_back_to_a_fail_closed_retrieval(
    monkeypatch,
    offline_boundaries,
):
    from mech_chatbot.db import repository

    _documents, qdrant_calls, executor, runtime = offline_boundaries
    runtime.semantic_cache_enabled = True
    monkeypatch.setattr(
        repository,
        "sc_get_exact",
        lambda *_args: (_ for _ in ()).throw(RuntimeError("cache unavailable")),
    )
    monkeypatch.setattr(repository, "sc_get_candidates", lambda *_args: [])
    monkeypatch.setattr(repository, "sc_record_lookup", lambda *_args: None)

    events = _run(
        "Quy trình phê duyệt nội bộ hiện hành là gì?",
        trace_id="wave5-exact-cache-outage",
        executor=executor,
    )

    assert len(qdrant_calls) == 2
    assert isinstance(events[-1], RagCompleted)
    assert events[-1].outcome == "refused"
    assert events[-1].refusal_reason == "no_retrieved_docs"


def test_semantic_cache_hit_preserves_attribution_and_skips_qdrant(
    monkeypatch,
    offline_boundaries,
):
    from mech_chatbot.db import repository

    _documents, qdrant_calls, executor, runtime = offline_boundaries
    source = {
        "file_goc": "approved-procedure.pdf",
        "doc_id": 31,
        "version_no": 2,
        "trang": 4,
        "source_id": "D31P4",
        "security_level": "internal",
    }
    cache_row = {
        "cache_id": 52,
        "answer": "Thực hiện theo quy trình đã phê duyệt. [SRC:D31P4]",
        "ref_text": "Nguồn semantic cache",
        "ref_images": "[]",
        "source_doc_ids": "[31]",
        "citation_snapshot": json.dumps([source]),
        "evidence_snapshot": json.dumps([source]),
        "embedding": "[1.0, 0.0]",
        "est_cost": "0.003",
    }

    runtime.semantic_cache_enabled = True
    runtime.semantic_cache_sim_threshold = 0.90
    monkeypatch.setattr(repository, "sc_get_exact", lambda *_args: None)
    monkeypatch.setattr(repository, "sc_get_candidates", lambda *_args: [cache_row])
    monkeypatch.setattr(repository, "sc_docs_all_current", lambda _ids: True)
    monkeypatch.setattr(repository, "sc_record_hit", lambda *_args: None)
    monkeypatch.setattr(repository, "sc_record_lookup", lambda *_args: None)
    monkeypatch.setattr(repository, "sc_delete", lambda *_args: None)

    events = _run(
        "Hãy nhắc lại quy trình phê duyệt nội bộ.",
        trace_id="wave5-semantic-cache-hit",
        executor=executor,
    )

    assert qdrant_calls == []
    assert _answer(events) == cache_row["answer"]
    assert isinstance(events[0], RagPrepared)
    assert events[0].ref_text == cache_row["ref_text"]
    citation_ids = [
        event.citation["source_id"]
        for event in events
        if isinstance(event, RagCitation)
    ]
    assert citation_ids == ["D31P4"]
    assert isinstance(events[-1], RagCompleted)
    assert events[-1].diagnostics["cache_hit"] is True


def test_conversation_history_bypasses_cache_entries_from_an_unrelated_turn(
    monkeypatch,
    offline_boundaries,
):
    from mech_chatbot.db import repository

    _documents, qdrant_calls, executor, runtime = offline_boundaries
    runtime.semantic_cache_enabled = True

    def unexpected_cache_read(*_args, **_kwargs):
        pytest.fail("a contextual turn must not reuse a context-free cache entry")

    monkeypatch.setattr(repository, "sc_get_exact", unexpected_cache_read)
    monkeypatch.setattr(repository, "sc_get_candidates", unexpected_cache_read)

    events = _run(
        "Trình bày quy trình phê duyệt nội bộ hiện hành của phòng nhân sự.",
        history=({"role": "user", "content": "Cho tôi xem quy trình phê duyệt."},),
        trace_id="wave5-context-bypasses-cache",
        executor=executor,
    )

    assert len(qdrant_calls) == 2
    assert isinstance(events[-1], RagCompleted)
    assert events[-1].outcome == "refused"
