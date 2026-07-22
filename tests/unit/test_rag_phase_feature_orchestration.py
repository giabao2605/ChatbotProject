from __future__ import annotations

import time
from dataclasses import replace
from types import SimpleNamespace

import pytest
from langchain_core.documents import Document

from mech_chatbot.rag.execution import (
    AccessScope,
    DefaultRagExecutor,
    RagCompleted,
    RagInvocation,
    RagRequest,
)
from mech_chatbot.rag.phases.preparation import PreparedRequest
from mech_chatbot.rag.phases.retrieval import PrimaryRetrievalOutcome
from mech_chatbot.rag.phases.routing import RouteDecision


pytestmark = pytest.mark.unit


def _prepared_request(*, question: str, image_analysis: str | None = None) -> PreparedRequest:
    return PreparedRequest(
        user_question=question,
        image_path=None,
        chat_history=(),
        current_part_ids=(),
        user_department="Technical",
        user_roles=("viewer",),
        allowed_departments=("Technical",),
        max_security_level="internal",
        allowed_sites=("HQ",),
        response_language="vi",
        conversation_context=None,
        trace_id="phase-feature-contract",
        started_at=time.time(),
        cache_scope=None,
        cache_eligible=False,
        history_text="",
        history_summary=None,
        summary_covered=None,
        image_analysis=image_analysis,
    )


def _route_decision(
    request: PreparedRequest,
    *,
    part_ids=("P-1",),
    crag_enabled=False,
) -> RouteDecision:
    return RouteDecision(
        request=request,
        effective_question=request.user_question,
        new_part_ids=tuple(part_ids),
        is_inherited=False,
        is_bom_query=False,
        intent_data={"version_policy": "current_only"},
        strict_filter=SimpleNamespace(must=()),
        broad_filter=SimpleNamespace(must=()),
        rbac_filter=SimpleNamespace(must=()),
        skip_hyde_anchor=False,
        hyde_eligible=False,
        query_to_search=request.user_question,
        cache_query_embedding=None,
        cache_scope=None,
        crag_enabled=crag_enabled,
    )


def _run_phase(callback, *, retrieval, provider):
    observed = {}

    def execute_pipeline(state):
        observed["outcome"] = callback(state)
        return state.prepared((iter(("done",)), "", (), (), {}))

    events = list(
        DefaultRagExecutor(
            execute_pipeline=execute_pipeline,
            retrieval_adapter=retrieval,
            provider_adapter=provider,
        ).run(
            RagRequest("phase feature contract", AccessScope()),
            RagInvocation(trace_id="phase-feature-contract", mode="test"),
        )
    )

    assert isinstance(events[-1], RagCompleted)
    return observed["outcome"]


def test_executor_runs_complex_decomposition_with_typed_branch_handoffs(monkeypatch):
    from mech_chatbot.rag.evidence_gate import EvidenceDecision, EvidenceState
    from mech_chatbot.rag.phases import retrieval as retrieval_phase

    request = _prepared_request(question="So sánh P-1 và P-2")
    decision = _route_decision(request, part_ids=("P-1", "P-2"))
    retrieval_calls = []
    provider_calls = []

    def retrieve(**kwargs):
        retrieval_calls.append(kwargs)
        query = kwargs["query_to_search"]
        return (
            [
                Document(
                    page_content=f"Approved evidence for {query}",
                    metadata={
                        "doc_id": len(retrieval_calls),
                        "trang_so": 1,
                        "file_goc": "comparison.pdf",
                        "version_no": 1,
                    },
                )
            ],
            5,
            "hybrid",
            time.time(),
            SimpleNamespace(kind="active"),
        )

    def invoke(*_args, **kwargs):
        provider_calls.append(kwargs)
        return SimpleNamespace(
            content='{"subqueries":["So sánh P-1","P-2"]}'
        )

    monkeypatch.setattr(
        retrieval_phase,
        "env_bool",
        lambda name, default=False: name == "RAG_QUERY_DECOMPOSITION_ENABLED",
    )
    monkeypatch.setattr(retrieval_phase, "tokenize_cached", lambda value: str(value))
    monkeypatch.setattr(
        retrieval_phase,
        "_assemble_context",
        lambda docs, _query: "\n".join(doc.page_content for doc in docs),
    )
    monkeypatch.setattr(
        retrieval_phase,
        "evaluate_answerability",
        lambda *_args, **_kwargs: EvidenceDecision(
            EvidenceState.SUFFICIENT,
            reason="covered",
        ),
    )

    outcome = _run_phase(
        lambda state: retrieval_phase.retrieve_primary(decision, state),
        retrieval=SimpleNamespace(retrieve=retrieve),
        provider=SimpleNamespace(invoke=invoke),
    )

    assert len(retrieval_calls) == 2
    assert [call["surface"] for call in provider_calls] == ["query_decomposition"]
    assert len(outcome.decomposition_branches) == 2
    assert outcome.decomposition_used_fallback is False
    assert outcome.documents
    assert outcome.reason_code == "retrieved"


def test_executor_runs_graph_bom_image_and_corrective_enrichment(monkeypatch):
    from mech_chatbot.rag import community_summaries, graph_retrieval, grounded_math
    from mech_chatbot.rag.evidence_gate import EvidenceDecision, EvidenceState
    from mech_chatbot.rag.phases import retrieval_enrichment as enrichment_phase

    request = _prepared_request(
        question="Tổng số lượng P-1 là bao nhiêu?",
        image_analysis="bearing diagram",
    )
    decision = _route_decision(request, crag_enabled=True)
    base_document = Document(
        page_content="Mechanical assembly evidence",
        metadata={
            "doc_id": 7,
            "trang_so": 2,
            "file_goc": "assembly.pdf",
            "version_no": 1,
            "domain": "mechanical",
        },
    )
    graph_document = Document(
        page_content="Graph relationship",
        metadata={"doc_id": 8, "trang_so": 1, "file_goc": "graph.pdf"},
    )
    community_document = Document(
        page_content="Community summary",
        metadata={"doc_id": 9, "trang_so": 1, "file_goc": "community.pdf"},
    )
    corrected_document = Document(
        page_content="Corrected evidence",
        metadata={"doc_id": 10, "trang_so": 1, "file_goc": "corrected.pdf"},
    )
    primary = PrimaryRetrievalOutcome(
        documents=(base_document,),
        base_k=5,
        retrieval_mode="hybrid",
        started_at=time.time(),
        active_filter=SimpleNamespace(kind="active"),
        has_active_filter=True,
        decomposition_notice="",
        decomposition_states=(),
        decomposition_branches=(),
        decomposition_intents=(),
        decomposition_intent_coverage=(),
        decomposition_used_fallback=False,
        decomposition_intent_overflow=False,
        auxiliary_input_tokens=0,
        auxiliary_output_tokens=0,
        planner_estimated_cost=0.0,
        correction_estimated_cost=0.0,
    )

    monkeypatch.setattr(
        enrichment_phase,
        "env_bool",
        lambda name, default=False: name
        in {
            "RAG_GRAPH_RETRIEVAL_ENABLED",
            "RAG_GRAPH_COMMUNITY_SUMMARIES_ENABLED",
            "RAG_GROUNDED_MATH_ENABLED",
        },
    )
    monkeypatch.setattr(graph_retrieval, "select_graph_seeds", lambda *_args: ["P-1"])
    monkeypatch.setattr(graph_retrieval, "should_attempt_graph", lambda *_args: True)
    monkeypatch.setattr(enrichment_phase, "traverse_knowledge_graph", lambda *_args, **_kwargs: [object()])
    monkeypatch.setattr(graph_retrieval, "filter_servable_edges", lambda edges, _access: edges)
    monkeypatch.setattr(graph_retrieval, "hydrate_graph_edges", lambda *_args, **_kwargs: [graph_document])
    monkeypatch.setattr(
        community_summaries,
        "load_community_context",
        lambda *_args, **_kwargs: SimpleNamespace(
            documents=(community_document,),
            used=True,
            summary_count=1,
            reason="used",
        ),
    )
    monkeypatch.setattr(
        enrichment_phase,
        "_disambiguate",
        lambda **kwargs: (None, kwargs["retrieved_docs"]),
    )
    monkeypatch.setattr(enrichment_phase, "_context_is_mechanical", lambda *_args: True)
    monkeypatch.setattr(
        enrichment_phase,
        "search_bom_facts",
        lambda **_kwargs: [
            SimpleNamespace(
                doc_id=None,
                page=None,
                document="invalid.pdf",
                version=1,
                security_level="internal",
                site="HQ",
                external_processing_policy="internal_only",
                part_code="INVALID",
                description="invalid",
                material="steel",
                quantity=1,
                unit="pcs",
                note="",
                source_row_id="invalid",
            ),
            SimpleNamespace(
                doc_id=7,
                page=2,
                document="assembly.pdf",
                version=1,
                security_level="internal",
                site="HQ",
                external_processing_policy="internal_only",
                part_code="P-1",
                description="bearing",
                material="steel",
                quantity=2,
                unit="pcs",
                note="approved",
                source_row_id="bom-7-2",
            ),
        ],
    )
    monkeypatch.setattr(
        grounded_math,
        "solve_grounded_calculation",
        lambda _question, facts: SimpleNamespace(
            plan=SimpleNamespace(operands=tuple(facts)),
            claim=SimpleNamespace(
                status="valid",
                approximate=False,
                display_value="2",
                unit="pcs",
                formula="2",
            ),
        ),
    )
    monkeypatch.setattr(
        grounded_math,
        "make_calculation_provenance",
        lambda _plan, claim: {"status": claim.status},
    )
    monkeypatch.setattr(
        enrichment_phase,
        "_assemble_context",
        lambda docs, _query: "\n".join(doc.page_content for doc in docs),
    )
    monkeypatch.setattr(
        enrichment_phase,
        "evaluate_answerability",
        lambda *_args, **_kwargs: EvidenceDecision(
            EvidenceState.AMBIGUOUS,
            reason="missing coverage",
        ),
    )
    monkeypatch.setattr(enrichment_phase, "should_attempt_correction", lambda *_args, **_kwargs: True)
    monkeypatch.setattr(
        enrichment_phase,
        "run_corrected_retrieval",
        lambda *_args, **_kwargs: (
            [corrected_document],
            5,
            "corrected",
            time.time(),
            object(),
        ),
    )

    outcome = _run_phase(
        lambda state: enrichment_phase.enrich_retrieval(decision, primary, state),
        retrieval=SimpleNamespace(retrieve=lambda **_kwargs: ()),
        provider=SimpleNamespace(
            invoke=lambda *_args, **_kwargs: SimpleNamespace(content="P-1 quantity")
        ),
    )

    assert outcome.graph_routed is True
    assert outcome.graph_edge_count == 1
    assert outcome.community_summary_used is True
    assert outcome.community_summary_count == 1
    assert outcome.grounded_math_enabled is True
    assert outcome.correction_estimated_cost > 0
    assert any(doc.metadata.get("loai_du_lieu") == "sql_bom" for doc in outcome.documents)
    assert any(doc.metadata.get("loai_du_lieu") == "image_summary" for doc in outcome.documents)
    assert corrected_document in outcome.documents


def test_executor_runs_one_governed_correction_across_decomposition_branches(monkeypatch):
    from mech_chatbot.rag.answer_policy import AnswerDecision, AnswerOutcome
    from mech_chatbot.rag.evidence_gate import EvidenceDecision, EvidenceState
    from mech_chatbot.rag.phases import retrieval as retrieval_phase

    request = _prepared_request(question="So sánh P-1 và P-2")
    decision = _route_decision(
        request,
        part_ids=("P-1", "P-2"),
        crag_enabled=True,
    )
    correction_calls = []

    monkeypatch.setattr(
        retrieval_phase,
        "env_bool",
        lambda name, default=False: name == "RAG_QUERY_DECOMPOSITION_ENABLED",
    )
    monkeypatch.setattr(retrieval_phase, "tokenize_cached", lambda value: str(value))
    monkeypatch.setattr(retrieval_phase, "_assemble_context", lambda *_args: "")
    monkeypatch.setattr(
        retrieval_phase,
        "evaluate_answerability",
        lambda *_args, **_kwargs: EvidenceDecision(
            EvidenceState.AMBIGUOUS,
            reason="missing coverage",
        ),
    )
    monkeypatch.setattr(
        retrieval_phase,
        "decide_answer_policy",
        lambda *_args, **_kwargs: AnswerDecision(
            AnswerOutcome.INSUFFICIENT_EVIDENCE,
            EvidenceState.AMBIGUOUS,
            reason="missing coverage",
            correction_allowed=True,
        ),
    )
    monkeypatch.setattr(
        retrieval_phase,
        "run_corrected_retrieval",
        lambda *_args, **kwargs: correction_calls.append(kwargs) or (
            [
                Document(
                    page_content="corrected branch evidence",
                    metadata={"doc_id": 21, "trang_so": 1},
                )
            ],
            5,
            "corrected",
            time.time(),
            object(),
        ),
    )
    monkeypatch.setattr(
        retrieval_phase,
        "probe_restricted_access",
        lambda *_args, **_kwargs: (False, None),
    )

    def invoke(*_args, **kwargs):
        if kwargs["surface"] == "query_decomposition":
            return SimpleNamespace(
                content='{"subqueries":["So sánh P-1","P-2"]}'
            )
        return SimpleNamespace(content="rewritten branch")

    outcome = _run_phase(
        lambda state: retrieval_phase.retrieve_primary(decision, state),
        retrieval=SimpleNamespace(
            retrieve=lambda **_kwargs: ([], 5, "hybrid", time.time(), object())
        ),
        provider=SimpleNamespace(invoke=invoke),
    )

    assert len(correction_calls) == 1
    assert outcome.correction_estimated_cost > 0
    assert sum(
        branch["correction_attempted"]
        for branch in outcome.decomposition_branches
    ) == 1


def test_executor_runs_hyde_once_after_empty_primary_retrieval(monkeypatch):
    from mech_chatbot.rag.phases import retrieval as retrieval_phase

    request = _prepared_request(question="Thông số ổ bi là gì?")
    decision = replace(
        _route_decision(request, part_ids=()),
        hyde_eligible=True,
    )
    document = Document(
        page_content="HyDE evidence",
        metadata={"doc_id": 31, "trang_so": 2},
    )
    calls = []

    monkeypatch.setattr(retrieval_phase, "env_bool", lambda *_args, **_kwargs: False)
    monkeypatch.setattr(retrieval_phase, "tokenize_cached", lambda value: str(value))

    def retrieve(**kwargs):
        calls.append(kwargs)
        documents = [] if len(calls) == 1 else [document]
        return documents, 5, "hybrid", time.time(), object()

    outcome = _run_phase(
        lambda state: retrieval_phase.retrieve_primary(decision, state),
        retrieval=SimpleNamespace(retrieve=retrieve),
        provider=SimpleNamespace(
            invoke=lambda *_args, **_kwargs: SimpleNamespace(
                content="hypothetical bearing specification"
            )
        ),
    )

    assert len(calls) == 2
    assert calls[1]["query_to_search"] == "hypothetical bearing specification"
    assert outcome.documents == (document,)
    assert outcome.retrieval_mode == "hybrid_hyde_fallback"


@pytest.mark.parametrize(
    ("blocked", "expected_reason"),
    [(False, "no_docs_for_exact_code"), (True, "access_denied")],
)
def test_exact_code_miss_returns_a_typed_terminal(monkeypatch, blocked, expected_reason):
    from mech_chatbot.rag.phases import retrieval_enrichment as enrichment_phase
    from mech_chatbot.rag.phases.contracts import PhaseTerminal

    request = _prepared_request(question="Thông số P-1")
    decision = _route_decision(request)
    primary = PrimaryRetrievalOutcome(
        documents=(),
        base_k=5,
        retrieval_mode="hybrid",
        started_at=time.time(),
        active_filter=None,
        has_active_filter=False,
        decomposition_notice="",
        decomposition_states=(),
        decomposition_branches=(),
        decomposition_intents=(),
        decomposition_intent_coverage=(),
        decomposition_used_fallback=False,
        decomposition_intent_overflow=False,
        auxiliary_input_tokens=0,
        auxiliary_output_tokens=0,
        planner_estimated_cost=0.0,
        correction_estimated_cost=0.0,
    )

    monkeypatch.setattr(enrichment_phase, "env_bool", lambda *_args, **_kwargs: False)
    monkeypatch.setattr(
        "mech_chatbot.rag.community_summaries.load_community_context",
        lambda *_args, **_kwargs: SimpleNamespace(
            documents=(), used=False, summary_count=0, reason="disabled"
        ),
    )
    monkeypatch.setattr(
        enrichment_phase,
        "probe_restricted_access",
        lambda *_args, **_kwargs: (blocked, "security_level" if blocked else None),
    )

    outcome = _run_phase(
        lambda state: enrichment_phase.enrich_retrieval(decision, primary, state),
        retrieval=SimpleNamespace(retrieve=lambda **_kwargs: ()),
        provider=SimpleNamespace(invoke=lambda *_args, **_kwargs: None),
    )

    assert isinstance(outcome, PhaseTerminal)
    assert outcome.reason_code == expected_reason


def test_inherited_code_miss_falls_back_to_general_retrieval(monkeypatch):
    from mech_chatbot.rag.phases import retrieval_enrichment as enrichment_phase

    request = _prepared_request(question="Thông số của nó là gì?")
    decision = replace(_route_decision(request), is_inherited=True)
    fallback_document = Document(
        page_content="General governed evidence",
        metadata={"doc_id": 41, "trang_so": 1, "domain": "hr"},
    )
    primary = PrimaryRetrievalOutcome(
        documents=(), base_k=5, retrieval_mode="hybrid", started_at=time.time(),
        active_filter=None, has_active_filter=False, decomposition_notice="",
        decomposition_states=(), decomposition_branches=(), decomposition_intents=(),
        decomposition_intent_coverage=(), decomposition_used_fallback=False,
        decomposition_intent_overflow=False, auxiliary_input_tokens=0,
        auxiliary_output_tokens=0, planner_estimated_cost=0.0,
        correction_estimated_cost=0.0,
    )

    monkeypatch.setattr(enrichment_phase, "env_bool", lambda *_args, **_kwargs: False)
    monkeypatch.setattr(
        "mech_chatbot.rag.community_summaries.load_community_context",
        lambda *_args, **_kwargs: SimpleNamespace(
            documents=(), used=False, summary_count=0, reason="disabled"
        ),
    )
    monkeypatch.setattr(
        enrichment_phase.vectorstore,
        "as_retriever",
        lambda **_kwargs: SimpleNamespace(invoke=lambda _query: [fallback_document]),
    )
    monkeypatch.setattr(
        enrichment_phase,
        "current_published_filter",
        lambda _rbac_filter: SimpleNamespace(kind="general"),
    )
    monkeypatch.setattr(
        enrichment_phase,
        "_disambiguate",
        lambda **kwargs: (None, kwargs["retrieved_docs"]),
    )

    outcome = _run_phase(
        lambda state: enrichment_phase.enrich_retrieval(decision, primary, state),
        retrieval=SimpleNamespace(retrieve=lambda **_kwargs: ()),
        provider=SimpleNamespace(invoke=lambda *_args, **_kwargs: None),
    )

    assert outcome.new_part_ids == ()
    assert outcome.documents == (fallback_document,)
    assert outcome.retrieval_mode == "general_after_inherit_miss"
