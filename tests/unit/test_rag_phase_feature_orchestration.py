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


def _retrieval_adapter(*, retrieve=lambda **_kwargs: (), **overrides):
    values = {
        "retrieve": retrieve,
        "query_decomposition_enabled": False,
        "hyde_enabled": False,
        "graph_retrieval_enabled": False,
        "community_summaries_enabled": False,
        "grounded_math_enabled": False,
        "strict_answer_mode": True,
        "evidence_verifier_enabled": False,
        "evaluation_force_ambiguous": False,
        "client": None,
        "collection_name": "test-knowledge",
        "vectorstore": SimpleNamespace(),
    }
    values.update(overrides)
    return SimpleNamespace(**values)


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
    strict_filter=None,
    broad_filter=None,
    rbac_filter=None,
) -> RouteDecision:
    return RouteDecision(
        request=request,
        effective_question=request.user_question,
        new_part_ids=tuple(part_ids),
        is_inherited=False,
        is_bom_query=False,
        intent_data={"version_policy": "current_only"},
        strict_filter=strict_filter or SimpleNamespace(must=()),
        broad_filter=broad_filter or SimpleNamespace(must=()),
        rbac_filter=rbac_filter or SimpleNamespace(must=()),
        skip_hyde_anchor=False,
        hyde_eligible=False,
        query_to_search=request.user_question,
        cache_query_embedding=None,
        cache_scope=None,
        crag_enabled=crag_enabled,
    )


def _run_phase(callback, *, retrieval, provider, invocation=None):
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
            invocation or RagInvocation(trace_id="phase-feature-contract", mode="test"),
        )
    )

    assert isinstance(events[-1], RagCompleted)
    return observed["outcome"]


def test_executor_runs_complex_decomposition_with_typed_branch_handoffs(monkeypatch):
    from mech_chatbot.rag.evidence_gate import EvidenceDecision, EvidenceState
    from mech_chatbot.rag.phases import retrieval as retrieval_phase

    request = _prepared_request(question="Tổng BOM P-1 và phiên bản P-2?")
    decision = replace(
        _route_decision(request, part_ids=("P-1", "P-2")),
        is_bom_query=True,
    )
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
            content='{"subqueries":["Tổng BOM P-1","phiên bản P-2"]}'
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
        retrieval=_retrieval_adapter(
            retrieve=retrieve,
            query_decomposition_enabled=True,
        ),
        provider=SimpleNamespace(invoke=invoke),
    )

    assert len(retrieval_calls) == 2
    assert {
        call["query_to_search"]: call["is_bom_query"]
        for call in retrieval_calls
    } == {
        "Tổng BOM P-1": True,
        "phiên bản P-2": False,
    }
    assert provider_calls == []
    assert len(outcome.decomposition_branches) == 2
    assert outcome.decomposition_used_fallback is True
    assert outcome.documents
    assert outcome.reason_code == "retrieved"


def test_decomposition_scopes_only_router_validated_branch_codes(
    monkeypatch,
):
    from mech_chatbot.rag.evidence_gate import EvidenceDecision, EvidenceState
    from mech_chatbot.rag.phases import retrieval as retrieval_phase
    from qdrant_client import models

    request = _prepared_request(
        question=(
            "Giá trị CRAG-EVAL-NUM-001 là bao nhiêu và "
            "mắt cú xanh kiểm tra theo chu kỳ nào và "
            "quy trình lắp CRAG-EVAL-PART-C?"
        )
    )
    inherited_part_filter = models.Filter(
        should=[
            models.FieldCondition(
                key="metadata.base_code",
                match=models.MatchAny(any=["CRAG-EVAL-NUM-001"]),
            )
        ]
    )
    governance_filter = models.FieldCondition(
        key="metadata.servable",
        match=models.MatchValue(value=True),
    )
    decision = _route_decision(
        request,
        part_ids=("CRAG-EVAL-NUM-001",),
        strict_filter=models.Filter(
            must=[inherited_part_filter, governance_filter]
        ),
    )
    retrieval_calls = []

    def retrieve(**kwargs):
        retrieval_calls.append(kwargs)
        documents = [
            Document(
                page_content=f"Approved evidence for {kwargs['query_to_search']}",
                metadata={"doc_id": len(retrieval_calls), "trang_so": 1},
            )
        ]
        return (
            documents,
            5,
            "hybrid",
            time.time(),
            object(),
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
        retrieval=_retrieval_adapter(
            retrieve=retrieve,
            query_decomposition_enabled=True,
        ),
        provider=SimpleNamespace(
            invoke=lambda *_args, **_kwargs: SimpleNamespace(
                content=(
                    '{"subqueries":["Giá trị CRAG-EVAL-NUM-001",'
                    '"mắt cú xanh kiểm tra theo chu kỳ nào",'
                    '"quy trình lắp CRAG-EVAL-PART-C"]}'
                )
            )
        ),
    )

    part_ids_by_query = {
        call["query_to_search"]: call["new_part_ids"]
        for call in retrieval_calls
    }
    assert part_ids_by_query["Giá trị CRAG-EVAL-NUM-001 là bao nhiêu"] == [
        "CRAG-EVAL-NUM-001"
    ]
    assert part_ids_by_query["mắt cú xanh kiểm tra theo chu kỳ nào"] == []
    assert part_ids_by_query["quy trình lắp CRAG-EVAL-PART-C"] == []
    assert all(
        governance_filter in call["strict_filter"].must
        and governance_filter in call["broad_filter"].must
        for call in retrieval_calls
    )


def test_decomposition_branch_filter_preserves_rbac_when_part_filter_is_not_last(
    monkeypatch,
):
    from qdrant_client import models

    from mech_chatbot.rag.evidence_gate import EvidenceDecision, EvidenceState
    from mech_chatbot.rag.phases import retrieval as retrieval_phase
    from mech_chatbot.rag.rbac import create_rbac_filter

    request = _prepared_request(question="Mắt cú xanh và CRAG-EVAL-NUM-001?")
    rbac_filter = create_rbac_filter(
        "Technical",
        ["viewer"],
        allowed_departments=["Technical"],
        max_security_level="internal",
        allowed_sites=["HQ"],
    )
    part_filter = models.Filter(should=[
        models.FieldCondition(
            key="metadata.base_code",
            match=models.MatchAny(any=["CRAG-EVAL-NUM-001"]),
        )
    ])
    lifecycle_filter = models.FieldCondition(
        key="metadata.servable",
        match=models.MatchValue(value=True),
    )
    decision = _route_decision(
        request,
        part_ids=("CRAG-EVAL-NUM-001",),
        strict_filter=models.Filter(must=[part_filter, rbac_filter, lifecycle_filter]),
        rbac_filter=rbac_filter,
    )
    filters_by_query = {}

    def retrieve(**kwargs):
        filters_by_query[kwargs["query_to_search"]] = kwargs["strict_filter"]
        return (
            [
                Document(
                    page_content=f"Approved evidence for {kwargs['query_to_search']}",
                    metadata={"doc_id": len(filters_by_query), "trang_so": 1},
                )
            ],
            5,
            "hybrid",
            time.time(),
            object(),
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

    _run_phase(
        lambda state: retrieval_phase.retrieve_primary(decision, state),
        retrieval=_retrieval_adapter(
            retrieve=retrieve,
            query_decomposition_enabled=True,
        ),
        provider=SimpleNamespace(
            invoke=lambda *_args, **_kwargs: SimpleNamespace(
                content='{"subqueries":["mắt cú xanh","CRAG-EVAL-NUM-001"]}'
            )
        ),
    )

    no_code_must = filters_by_query["Mắt cú xanh"].must
    assert rbac_filter in no_code_must
    assert lifecycle_filter in no_code_must
    assert part_filter not in no_code_must


def test_decomposition_missing_branch_does_not_publish_citations(monkeypatch):
    from mech_chatbot.rag.evidence_gate import EvidenceDecision, EvidenceState
    from mech_chatbot.rag.phases import retrieval as retrieval_phase

    request = _prepared_request(
        question=(
            "Giá trị CRAG-EVAL-NUM-001 là bao nhiêu và "
            "chi phí CRAG-EVAL-PART-C là bao nhiêu?"
        )
    )
    decision = _route_decision(
        request,
        part_ids=("CRAG-EVAL-NUM-001", "CRAG-EVAL-PART-C"),
    )

    def retrieve(**kwargs):
        query = kwargs["query_to_search"]
        content = (
            "Không có trường đơn giá."
            if "chi phí" in query
            else "CRAG-EVAL-NUM-001 có giá trị 1,500."
        )
        return (
            [
                Document(
                    page_content=content,
                    metadata={"doc_id": 1 if "chi phí" not in query else 2, "trang_so": 1},
                )
            ],
            5,
            "hybrid",
            time.time(),
            object(),
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
        retrieval=_retrieval_adapter(
            retrieve=retrieve,
            query_decomposition_enabled=True,
        ),
        provider=SimpleNamespace(
            invoke=lambda *_args, **_kwargs: SimpleNamespace(
                content=(
                    '{"subqueries":["Giá trị CRAG-EVAL-NUM-001",'
                    '"chi phí CRAG-EVAL-PART-C"]}'
                )
            )
        ),
    )

    assert [branch["outcome"] for branch in outcome.decomposition_branches] == [
        "full_answer",
        "insufficient_evidence",
    ]
    assert outcome.decomposition_branches[1]["citations"] == []
    assert "1 nhánh chưa có đủ bằng chứng" in outcome.decomposition_notice
    assert [document.metadata["doc_id"] for document in outcome.documents] == [1]
    assert [
        document.metadata["doc_id"] for document in outcome.lookup_documents
    ] == [1, 2]


def test_crag_force_ambiguous_override_is_request_local(monkeypatch):
    from mech_chatbot.rag.evidence_gate import EvidenceDecision, EvidenceState
    from mech_chatbot.rag.phases import retrieval_enrichment as enrichment_phase

    monkeypatch.setattr(
        enrichment_phase,
        "evaluate_answerability",
        lambda *_args, **_kwargs: EvidenceDecision(
            EvidenceState.SUFFICIENT,
            reason="normally covered",
        ),
    )
    document = Document(page_content="Approved evidence", metadata={"doc_id": 1})
    decision = _run_phase(
        lambda state: enrichment_phase._coverage_policy(
            SimpleNamespace(
                user_question="Alias này là gì?",
                trace_id="phase-feature-contract",
            ),
            [document],
            state,
        )[0],
        retrieval=_retrieval_adapter(
            crag_enabled=True,
            evaluation_force_ambiguous=False,
        ),
        provider=SimpleNamespace(invoke=lambda *_args, **_kwargs: None),
        invocation=RagInvocation(
            trace_id="phase-feature-contract",
            mode="evaluation",
            evaluation_force_ambiguous=True,
        ),
    )

    assert decision.state is EvidenceState.AMBIGUOUS
    assert decision.reason == "controlled_evaluation_correction_fixture"


def test_crag_uses_local_metadata_correction_without_provider_round_trip(monkeypatch):
    from mech_chatbot.rag.evidence_gate import EvidenceDecision, EvidenceState
    from mech_chatbot.rag.phases import retrieval_enrichment as enrichment_phase

    request = _prepared_request(question="Mắt cú xanh kiểm tra khi nào?")
    decision = _route_decision(request, part_ids=(), crag_enabled=True)
    base_document = Document(
        page_content="Mắt cú xanh là alias đã phê duyệt.",
        metadata={
            "doc_id": 7,
            "trang_so": 1,
            "base_code": "crag-eval-alias-001",
        },
    )
    corrected_document = Document(
        page_content="Chu kỳ kiểm tra là 90 ngày.",
        metadata={
            "doc_id": 8,
            "trang_so": 1,
            "base_code": "crag-eval-alias-001",
        },
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
    retrieval_calls = []

    monkeypatch.setattr(
        enrichment_phase,
        "_disambiguate",
        lambda **kwargs: (None, kwargs["retrieved_docs"]),
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

    outcome = _run_phase(
        lambda state: enrichment_phase.enrich_retrieval(decision, primary, state),
        retrieval=_retrieval_adapter(
            retrieve=lambda **kwargs: retrieval_calls.append(kwargs) or (
                [corrected_document],
                5,
                "corrected",
                time.time(),
                object(),
            ),
            crag_enabled=True,
        ),
        provider=SimpleNamespace(
            invoke=lambda *_args, **_kwargs: pytest.fail(
                "local metadata correction must not call the provider"
            )
        ),
    )

    assert corrected_document in outcome.documents
    assert outcome.correction_estimated_cost == 0
    assert len(retrieval_calls) == 1
    assert retrieval_calls[0]["query_to_search"].endswith(
        "crag-eval-alias-001"
    )
    assert retrieval_calls[0]["strict_filter"] is decision.strict_filter
    assert retrieval_calls[0]["broad_filter"] is decision.broad_filter
    assert retrieval_calls[0]["rbac_filter"] is decision.rbac_filter

    trace_events = []
    failed_retrieval_calls = []
    failed_correction_counts = []
    monkeypatch.setattr(
        enrichment_phase,
        "log_trace",
        lambda event, trace_id, **fields: trace_events.append(
            {"event": event, "trace_id": trace_id, **fields}
        ),
    )
    def run_failed_correction(state):
        outcome = enrichment_phase.enrich_retrieval(
            decision,
            primary,
            state,
        )
        failed_correction_counts.append(state.budget.corrections)
        return outcome

    failed_outcome = _run_phase(
        run_failed_correction,
        retrieval=_retrieval_adapter(
            retrieve=lambda **kwargs: failed_retrieval_calls.append(kwargs)
            or ([], 5, "corrected", time.time(), object()),
            crag_enabled=True,
        ),
        provider=SimpleNamespace(
            invoke=lambda *_args, **_kwargs: pytest.fail(
                "failed local metadata correction must not call the provider"
            )
        ),
    )

    correction_events = [
        event
        for event in trace_events
        if event["event"] == "corrective_retrieval"
    ]
    assert failed_outcome.documents == (base_document,)
    assert len(failed_retrieval_calls) == 1
    assert failed_correction_counts == [1]
    assert correction_events == [
        {
            "event": "corrective_retrieval",
            "trace_id": "phase-feature-contract",
            "latency_ms": correction_events[0]["latency_ms"],
            "strategy": "metadata_expansion",
            "attempt": 1,
            "before_docs": 1,
            "after_docs": 1,
            "evaluator_state": "AMBIGUOUS",
            "error": "ValueError",
        }
    ]


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
    outcome = _run_phase(
        lambda state: enrichment_phase.enrich_retrieval(decision, primary, state),
        retrieval=_retrieval_adapter(
            retrieve=lambda **_kwargs: (
                [corrected_document],
                5,
                "corrected",
                time.time(),
                object(),
            ),
            graph_retrieval_enabled=True,
            community_summaries_enabled=True,
            grounded_math_enabled=True,
        ),
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


def test_bom_lookup_falls_back_to_the_governed_document_scope():
    from mech_chatbot.rag.phases.retrieval_enrichment_support import (
        _search_bom_rows,
    )

    calls = []

    def search_bom_facts(**kwargs):
        calls.append(kwargs)
        return [] if kwargs["part_codes"] else ["document-row"]

    context = SimpleNamespace(
        decision=SimpleNamespace(
            request=SimpleNamespace(
                user_department="Technical",
                max_security_level="internal",
            ),
            intent_data={"version_policy": "current_only"},
        ),
        user_roles=("viewer",),
        allowed_departments=("Technical",),
        allowed_sites=("HQ",),
    )

    rows = _search_bom_rows(
        context,
        ["CRAG-EVAL-BOM-001"],
        [43],
        search_bom_facts,
    )

    assert rows == ["document-row"]
    assert [call["part_codes"] for call in calls] == [
        ["CRAG-EVAL-BOM-001"],
        [],
    ]
    assert all(call["document_ids"] == [43] for call in calls)


def test_bom_lookup_resolves_the_retrieved_document_even_when_a_code_was_parsed(
    monkeypatch,
):
    from mech_chatbot.rag.phases import retrieval_enrichment_support as support

    document = Document(
        page_content="BOM CRAG-EVAL-BOM-001",
        metadata={"doc_id": 43},
    )
    observed = []
    context = SimpleNamespace(
        decision=SimpleNamespace(
            request=SimpleNamespace(
                user_department="Technical",
                max_security_level="internal",
            ),
            intent_data={"version_policy": "current_only"},
        ),
        trace_id="bom-document-scope",
        user_question="Tổng BOM CRAG-EVAL-BOM-001 là bao nhiêu?",
        user_roles=("viewer",),
        allowed_departments=("Technical",),
        allowed_sites=("HQ",),
    )
    monkeypatch.setattr(
        support,
        "_search_bom_rows",
        lambda _context, part_ids, document_ids, _search: (
            observed.append((list(part_ids), list(document_ids))) or []
        ),
    )

    documents, _ = support.inject_bom(
        context,
        [],
        ["CRAG-EVAL-BOM-001"],
        env_bool=lambda *_args: True,
        context_is_mechanical=lambda *_args: True,
        search_bom_facts=lambda **_kwargs: [],
        lookup_documents=[document],
    )

    assert observed == [(["CRAG-EVAL-BOM-001"], [43])]
    assert documents == ()


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
    retrieval_calls = []

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
        retrieval=_retrieval_adapter(
            retrieve=lambda **kwargs: retrieval_calls.append(kwargs) or (
                [
                    Document(
                        page_content="corrected branch evidence",
                        metadata={"doc_id": 21, "trang_so": 1},
                    )
                ]
                if kwargs["query_to_search"] == "rewritten branch"
                else [],
                5,
                "hybrid",
                time.time(),
                object(),
            ),
            query_decomposition_enabled=True,
        ),
        provider=SimpleNamespace(invoke=invoke),
    )

    assert sum(
        call["query_to_search"] == "rewritten branch"
        for call in retrieval_calls
    ) == 1
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

    monkeypatch.setattr(retrieval_phase, "tokenize_cached", lambda value: str(value))

    def retrieve(**kwargs):
        calls.append(kwargs)
        documents = [] if len(calls) == 1 else [document]
        return documents, 5, "hybrid", time.time(), object()

    outcome = _run_phase(
        lambda state: retrieval_phase.retrieve_primary(decision, state),
        retrieval=_retrieval_adapter(retrieve=retrieve, hyde_enabled=True),
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
        retrieval=_retrieval_adapter(),
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

    monkeypatch.setattr(
        "mech_chatbot.rag.community_summaries.load_community_context",
        lambda *_args, **_kwargs: SimpleNamespace(
            documents=(), used=False, summary_count=0, reason="disabled"
        ),
    )
    vectorstore = SimpleNamespace(
        as_retriever=lambda **_kwargs: SimpleNamespace(
            invoke=lambda _query: [fallback_document]
        )
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
        retrieval=_retrieval_adapter(vectorstore=vectorstore),
        provider=SimpleNamespace(invoke=lambda *_args, **_kwargs: None),
    )

    assert outcome.new_part_ids == ()
    assert outcome.documents == (fallback_document,)
    assert outcome.retrieval_mode == "general_after_inherit_miss"
