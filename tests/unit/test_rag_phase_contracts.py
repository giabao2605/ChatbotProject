from types import SimpleNamespace

from langchain_core.documents import Document


def test_rerank_phase_passes_bootstrap_client_to_late_interaction(monkeypatch):
    from mech_chatbot.rag import late_interaction
    from mech_chatbot.rag.phases import retrieval_rerank

    client = object()
    document = Document(page_content="bearing", metadata={"doc_id": 7})
    observed = []

    monkeypatch.setattr(retrieval_rerank, "client", client)
    monkeypatch.setattr(
        retrieval_rerank,
        "env_bool",
        lambda name, default=False: name == "RAG_LATE_INTERACTION_ENABLED",
    )
    monkeypatch.setattr(late_interaction, "enabled", lambda: True)

    def attempt(documents, query, received_client, *, top_n):
        observed.append((documents, query, received_client, top_n))
        return SimpleNamespace(
            documents=tuple(documents),
            used_shadow=True,
            total_latency_ms=1,
            encode_latency_ms=1,
            query_latency_ms=0,
            candidate_count=len(documents),
            shadow_hits=len(documents),
            coverage=1.0,
            fallback_reason=None,
        )

    monkeypatch.setattr(late_interaction, "attempt_shadow_rerank", attempt)
    monkeypatch.setattr(retrieval_rerank, "hydrate_parent_context", lambda docs, **_kwargs: docs)
    monkeypatch.setattr(retrieval_rerank, "parent_context_max_workers", lambda: 1)
    monkeypatch.setattr(retrieval_rerank, "long_context_reorder", lambda docs: docs)

    request = SimpleNamespace(
        trace_id="late-phase-contract",
        user_question="bearing",
        response_language="vi",
        user_department="Technical",
        user_roles=("viewer",),
        started_at=0.0,
    )
    decision = SimpleNamespace(
        request=request,
        effective_question="bearing",
        intent_data={},
    )
    enrichment = SimpleNamespace(
        new_part_ids=(),
        documents=(document,),
        graph_documents=(),
        served_graph_documents=(),
        community_documents=(),
        base_k=5,
        retrieval_mode="hybrid",
        has_active_filter=False,
    )

    result = retrieval_rerank.rerank_retrieval(decision, enrichment, object())

    assert result.documents == (document,)
    assert result.reason_code == "reranked"
    assert observed == [(([document]), "bearing", client, 8)]
