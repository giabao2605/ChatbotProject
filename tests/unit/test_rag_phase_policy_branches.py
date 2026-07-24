from __future__ import annotations

import time
from types import SimpleNamespace

import pytest
from langchain_core.documents import Document

from mech_chatbot.rag.answer_policy import (
    AnswerDecision,
    AnswerOutcome,
)
from mech_chatbot.rag.evidence_gate import EvidenceDecision, EvidenceState
from mech_chatbot.rag.phases.evidence import EvidenceOutcome
from mech_chatbot.rag.phases.preparation import PreparedRequest
from mech_chatbot.rag.phases.retrieval import PrimaryRetrievalOutcome
from mech_chatbot.rag.phases.retrieval_enrichment import EnrichmentOutcome
from mech_chatbot.rag.phases.retrieval_rerank import RerankOutcome
from mech_chatbot.rag.phases.routing import RouteDecision


pytestmark = pytest.mark.unit


def _request(*, cache_eligible=False, history_summary=None, summary_covered=None):
    return PreparedRequest(
        user_question="Thông số P-1 là gì?",
        image_path=None,
        chat_history=(),
        current_part_ids=("P-1",),
        user_department="Technical",
        user_roles=("viewer",),
        allowed_departments=("Technical",),
        max_security_level="internal",
        allowed_sites=("HQ",),
        response_language="vi",
        conversation_context=None,
        trace_id="phase-policy-contract",
        started_at=time.time(),
        cache_scope=None,
        cache_eligible=cache_eligible,
        history_text="history",
        history_summary=history_summary,
        summary_covered=summary_covered,
        image_analysis=None,
    )


def _decision(request=None, **changes):
    values = {
        "request": request or _request(),
        "effective_question": "Thông số P-1 là gì?",
        "new_part_ids": ("P-1",),
        "is_inherited": False,
        "is_bom_query": False,
        "intent_data": {"version_policy": "current_only"},
        "strict_filter": object(),
        "broad_filter": object(),
        "rbac_filter": object(),
        "skip_hyde_anchor": False,
        "hyde_eligible": False,
        "query_to_search": "thong so p-1",
        "cache_query_embedding": None,
        "cache_scope": None,
        "crag_enabled": False,
    }
    values.update(changes)
    return RouteDecision(**values)


def _primary(*, branches=()):
    return PrimaryRetrievalOutcome(
        documents=(), base_k=5, retrieval_mode="hybrid", started_at=time.time(),
        active_filter=object(), has_active_filter=True, decomposition_notice="",
        decomposition_states=(), decomposition_branches=tuple(branches),
        decomposition_intents=("intent",) if branches else (),
        decomposition_intent_coverage=(True,) if branches else (),
        decomposition_used_fallback=False, decomposition_intent_overflow=False,
        auxiliary_input_tokens=2, auxiliary_output_tokens=1,
        planner_estimated_cost=0.001, correction_estimated_cost=0.0,
    )


def _enrichment(documents=(), **changes):
    values = {
        "documents": tuple(documents),
        "new_part_ids": ("P-1",),
        "base_k": 5,
        "retrieval_mode": "hybrid",
        "active_filter": object(),
        "has_active_filter": True,
        "served_graph_documents": (),
        "graph_documents": (),
        "graph_routed": False,
        "graph_edge_count": 0,
        "graph_max_hops": 0,
        "community_documents": (),
        "community_summary_used": False,
        "community_summary_count": 0,
        "community_fallback_reason": "disabled",
        "grounded_math_enabled": False,
        "auxiliary_input_tokens": 2,
        "auxiliary_output_tokens": 1,
        "correction_estimated_cost": 0.0,
    }
    values.update(changes)
    return EnrichmentOutcome(**values)


def _state():
    budget = SimpleNamespace(
        corrections=0,
        planners=0,
        subqueries=0,
        final_generations=0,
        provider_retries=0,
        deadline_exceeded=False,
        deadline_monotonic=time.monotonic() + 60,
        limits=SimpleNamespace(corrections=1),
        record=lambda field, value, **_kwargs: setattr(budget, field, value),
    )
    return SimpleNamespace(
        budget=budget,
        retrieval_adapter=SimpleNamespace(
            semantic_cache_enabled=True,
            semantic_cache_ttl_hours=24.0,
            semantic_cache_sim_threshold=0.93,
            semantic_cache_environment={},
            conversation_state_enabled=True,
            citation_max_sources=5,
            bom_citation_max_sources=3,
            strict_answer_mode=True,
            crag_enabled=False,
            evidence_verifier_enabled=False,
            hyde_enabled=True,
            glossary_cache_ttl=60.0,
            voyage_enabled=False,
            voyage_runtime=None,
            parent_context_enabled=False,
            late_interaction_config=None,
        ),
        provider_adapter=SimpleNamespace(
            client=object(),
            settings=SimpleNamespace(model_name="test-model", base_url="test"),
        ),
        invocation=SimpleNamespace(mode="test"),
        cancellation=object(),
        transition=lambda _phase: None,
        checkpoint=lambda _phase: None,
        invoke_provider=lambda *_args, **_kwargs: None,
        bind_generation=lambda outcome: None,
        refuse=lambda reason: None,
        prepared=lambda values: values,
    )


def test_citation_policy_selects_bom_sources_and_renders_all_metadata(monkeypatch):
    from mech_chatbot.rag.phases import citations

    monkeypatch.setattr(citations.os.path, "exists", lambda _path: True)
    bom = Document(
        page_content="bom",
        metadata={
            "doc_id": 1, "trang_so": 2, "file_goc": "bom.pdf",
            "cong_doan": "assembly", "loai_du_lieu": "sql_bom",
            "phong_ban_quyen": ["R&D"], "site": "HQ", "version_no": 3,
        },
    )
    same_page = Document(page_content="duplicate", metadata=dict(bom.metadata))
    code_match = Document(
        page_content="code",
        metadata={"doc_id": 2, "parent_page": 4, "file_goc": "drawing.pdf", "ma_vat_tu": ["P-1"]},
    )
    image = Document(
        page_content="image",
        metadata={
            "file_goc": "Anh dinh kem tu nguoi dung",
            "trang_so": 1,
            "loai_du_lieu": "image_summary",
            "phong_ban_quyen": [],
        },
    )

    selected = citations.select_citation_docs(
        [bom, same_page, code_match, image],
        question="Liệt kê BOM P-1",
        part_ids=["P-1"],
        limit=3,
    )
    reference_text, images = citations.build_source_citations([*selected, image])

    assert selected == [bom, code_match]
    assert "R&D | khu HQ | v3 | DocID 1" in reference_text
    assert "phan tich hinh anh" in reference_text
    assert len(images) == 2
    assert citations.select_citation_docs([], question="BOM") == []
    assert citations.select_citation_docs([image], question="BOM") == [image]
    assert citations.build_source_citations([]) == ("", [])


def test_diagnostic_builders_tolerate_invalid_identifiers_and_namespace_failure(monkeypatch):
    from mech_chatbot.rag import semantic_cache
    from mech_chatbot.rag.phases import diagnostics

    monkeypatch.setattr(
        semantic_cache,
        "pipeline_namespace",
        lambda: (_ for _ in ()).throw(RuntimeError("unavailable")),
    )
    docs = [
        Document(page_content="invalid", metadata={"doc_id": "bad", "trang_so": "bad"}),
        Document(page_content="valid", metadata={"doc_id": "7", "trang_so": None}),
    ]

    debug = diagnostics.make_debug_info(docs)
    snapshots = diagnostics.make_source_snapshot(docs)

    assert debug["pipeline_namespace"] == "unknown"
    assert snapshots == [
        {
            "file_goc": None, "doc_id": 7, "version_no": None,
            "variant_code": None, "is_current": None,
            "lifecycle_status": None, "review_status": None,
            "trang": None, "source_id": None, "score": None,
            "security_level": None,
        }
    ]


class _RoutingState:
    def __init__(self):
        self.refusal_reason = None
        self.retrieval_adapter = SimpleNamespace(
            semantic_cache_enabled=True,
            semantic_cache_ttl_hours=24.0,
            semantic_cache_sim_threshold=0.93,
            semantic_cache_environment={},
            hyde_enabled=True,
            glossary_cache_ttl=60.0,
            crag_enabled=False,
        )
        self.invocation = SimpleNamespace(mode="test")

    def transition(self, _phase):
        return None

    def checkpoint(self, _phase):
        return None

    def invoke_provider(self, *_args, **_kwargs):
        return None

    def refuse(self, reason):
        self.refusal_reason = reason


def _install_route_entry(monkeypatch, routing):
    monkeypatch.setattr(
        routing,
        "_route",
        lambda **_kwargs: (
            None,
            {"mock_stream": lambda: iter(("hello",)), "_embed_cached": lambda _q: [1.0]},
        ),
    )


def test_routing_returns_semantic_cache_hit(monkeypatch):
    from mech_chatbot.rag import semantic_cache
    from mech_chatbot.rag.phases import routing

    _install_route_entry(monkeypatch, routing)
    monkeypatch.setattr(semantic_cache, "enabled", lambda *_args: True)
    monkeypatch.setattr(
        semantic_cache,
        "scope_signature",
        lambda *_args, **_kwargs: "scope",
    )
    monkeypatch.setattr(
        semantic_cache,
        "lookup",
        lambda *_args, **_kwargs: {
            "answer": "cached", "ref_text": "refs", "ref_images": ["img"],
            "evidence_snapshot": [{"doc_id": 1}], "citation_snapshot": [{"doc_id": 1}],
        },
    )

    outcome = routing.route(_request(cache_eligible=True), _RoutingState())

    assert outcome.reason_code == "semantic_cache_hit"
    assert "".join(outcome.terminal[0]) == "cached"
    assert outcome.terminal[4]["cache_hit"] is True


@pytest.mark.parametrize(
    ("intent", "reason"),
    [
        ({"version_policy": "compare_versions", "detected_versions": []}, "missing_compare_versions"),
        ({"is_chitchat": True}, "chitchat"),
    ],
)
def test_routing_returns_typed_intent_terminals(monkeypatch, intent, reason):
    from mech_chatbot.rag import semantic_cache
    from mech_chatbot.rag.phases import routing

    _install_route_entry(monkeypatch, routing)
    monkeypatch.setattr(semantic_cache, "enabled", lambda *_args: False)
    monkeypatch.setattr(
        routing,
        "_rewrite_and_anchor",
        lambda **_kwargs: (
            "question", [], False, False, intent, object(), object(), object(), False,
        ),
    )
    state = _RoutingState()

    outcome = routing.route(_request(), state)

    assert outcome.reason_code == reason
    if reason == "missing_compare_versions":
        assert state.refusal_reason == "clarification_required"


def test_routing_adds_glossary_and_recovers_glossary_failure(monkeypatch):
    from mech_chatbot.rag import semantic_cache
    from mech_chatbot.rag.phases import routing

    _install_route_entry(monkeypatch, routing)
    monkeypatch.setattr(semantic_cache, "enabled", lambda *_args: False)
    monkeypatch.setattr(
        routing,
        "_rewrite_and_anchor",
        lambda **_kwargs: (
            "bearing", [], False, False, {}, object(), object(), object(), False,
        ),
    )
    monkeypatch.setattr(routing, "tokenize_cached", lambda value: str(value))
    monkeypatch.setattr(
        routing,
        "glossary_expansion_terms",
        lambda *_args, **_kwargs: "ổ bi",
    )

    expanded = routing.route(_request(), _RoutingState())
    monkeypatch.setattr(
        routing,
        "glossary_expansion_terms",
        lambda *_args: (_ for _ in ()).throw(RuntimeError("glossary unavailable")),
    )
    recovered = routing.route(_request(), _RoutingState())

    assert expanded.decision.query_to_search == "bearing ổ bi"
    assert recovered.decision.query_to_search == "bearing"


def test_evidence_prefers_grounded_citations_and_audits_confidential_docs(monkeypatch):
    from mech_chatbot.rag import grounded_math
    from mech_chatbot.rag.phases import evidence

    document = Document(
        page_content="approved calculation evidence",
        metadata={
            "doc_id": 7, "trang_so": 2, "file_goc": "secret.pdf",
            "security_level": "confidential", "version_no": 1,
        },
    )
    decision = _decision()
    primary = _primary(branches=({"outcome": "full_answer", "grounded_negative": False},))
    enrichment = _enrichment([document], grounded_math_enabled=True)
    reranked = RerankOutcome((document,), (), reason_code="reranked")
    monkeypatch.setattr(evidence, "_assemble_context", lambda *_args: document.page_content)
    monkeypatch.setattr(
        evidence,
        "evaluate_answerability",
        lambda *_args, **_kwargs: EvidenceDecision(EvidenceState.SUFFICIENT, reason="covered"),
    )
    monkeypatch.setattr(
        grounded_math,
        "select_grounded_answer_citation_documents",
        lambda _docs, _branches: [document],
    )

    outcome = evidence.evaluate_evidence(decision, primary, enrichment, reranked, _state())

    assert isinstance(outcome, EvidenceOutcome)
    assert "secret.pdf" in outcome.ref_text
    assert outcome.reason_code == "evidence_approved"


def test_rerank_voyage_path_keeps_image_graph_and_community_context(monkeypatch):
    from mech_chatbot.rag.phases import retrieval_rerank

    image = Document(
        page_content="image",
        metadata={"loai_du_lieu": "image_summary", "file_goc": "Anh dinh kem tu nguoi dung"},
    )
    real = Document(page_content="real", metadata={"file_goc": "doc.pdf", "trang_so": 1})
    graph = Document(page_content="graph", metadata={"doc_id": 8})
    community = Document(page_content="community", metadata={"doc_id": 9})
    enrichment = _enrichment(
        [image, real],
        graph_documents=(graph,),
        community_documents=(community,),
    )
    monkeypatch.setattr(retrieval_rerank, "prioritize_document_types", lambda docs, _hints: docs)
    monkeypatch.setattr(retrieval_rerank, "diversify_candidates", lambda docs, **_kwargs: docs)
    monkeypatch.setattr(
        retrieval_rerank,
        "RerankPolicy",
        lambda **_kwargs: SimpleNamespace(select_backend=lambda _docs: "voyage"),
    )
    monkeypatch.setattr(
        retrieval_rerank,
        "voyage_rerank_documents",
        lambda docs, *_args, **_kwargs: docs,
    )
    monkeypatch.setattr(
        retrieval_rerank,
        "parent_context_max_workers",
        lambda *_args: 1,
    )
    monkeypatch.setattr(retrieval_rerank, "hydrate_parent_context", lambda docs, **_kwargs: docs)
    monkeypatch.setattr(
        "mech_chatbot.rag.graph_retrieval.attach_served_graph_context",
        lambda docs, graph_docs: (docs, graph_docs),
    )
    monkeypatch.setattr(retrieval_rerank, "long_context_reorder", lambda docs: docs)

    outcome = retrieval_rerank.rerank_retrieval(
        _decision(), enrichment, _state()
    )

    assert outcome.documents[0] is image
    assert community in outcome.documents
    assert outcome.served_graph_documents == (graph,)


def test_generation_updates_context_and_wraps_semantic_cache(monkeypatch):
    from mech_chatbot.rag import conversation_state, semantic_cache
    from mech_chatbot.rag.phases import generation

    document = Document(
        page_content="answer evidence",
        metadata={
            "doc_id": 7, "trang_so": 2, "file_goc": "doc.pdf",
            "rerank_backend": "late_interaction",
            "calculation_provenance": {"status": "valid"},
        },
    )
    request = _request(history_summary="summary", summary_covered=4)
    decision = _decision(
        request,
        cache_query_embedding=[1.0, 0.0],
        cache_scope="scope",
    )
    primary = _primary(
        branches=({"branch_id": "branch-1", "citations": [{"source_id": "D7P2"}]},)
    )
    enrichment = _enrichment([document])
    reranked = RerankOutcome((document,), (), reason_code="reranked")
    answer_policy = AnswerDecision(
        AnswerOutcome.FULL_ANSWER,
        EvidenceState.SUFFICIENT,
        reason="covered",
    )
    evidence = EvidenceOutcome(
        context_text="context",
        ref_text="refs",
        ref_images=(),
        answer_policy=answer_policy,
        evidence_decision=EvidenceDecision(EvidenceState.SUFFICIENT, reason="covered"),
        evidence_quotes=(),
        explicit_negative_answer="",
    )
    cache_calls = []
    monkeypatch.setattr(generation, "generate_answer", lambda *_args, **_kwargs: iter(("answer [SRC:D7P2]",)))
    monkeypatch.setattr(conversation_state, "is_enabled", lambda *_args: True)
    monkeypatch.setattr(conversation_state, "dominant_doc_refs", lambda _docs: ["P-1"])
    monkeypatch.setattr(semantic_cache, "enabled", lambda *_args: True)
    monkeypatch.setattr(
        semantic_cache,
        "teeing_store_stream",
        lambda stream, **kwargs: cache_calls.append(kwargs) or stream,
    )
    monkeypatch.setattr(
        generation,
        "get_llm_model_name",
        lambda *_args: "test-model",
    )

    state = _state()
    result = generation.generate(
        decision, primary, enrichment, reranked, evidence, state
    )
    stream, ref_text, _images, part_ids, debug = result.prepared

    assert "".join(stream) == "answer [SRC:D7P2]"
    assert ref_text == "refs"
    assert part_ids == ["P-1"]
    assert debug["conversation_context"] == {
        "active_doc_refs": ["P-1"],
        "last_intent": "answered",
        "history_summary": "summary",
        "summary_covered": 4,
    }
    assert debug["late_interaction_hits"] == 1
    assert debug["calculation_provenance"] == [{"status": "valid"}]
    assert cache_calls[0]["source_doc_ids"] == [7]
