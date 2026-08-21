from __future__ import annotations

import json
import threading
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
from mech_chatbot.rag.phases.diagnostics import make_decomposition_usage
from mech_chatbot.rag.phases.retrieval import PrimaryRetrievalOutcome
from mech_chatbot.rag.phases.routing import RouteDecision


pytestmark = pytest.mark.unit


def test_decomposition_usage_debug_boundary_strips_unsafe_branch_fields():
    primary = SimpleNamespace(
        decomposition_usage={
            "planner": {"calls": 0},
            "branches": [
                {
                    "branch_id": "branch-1",
                    "subquery": "must not escape",
                    "retrieval": {
                        "latency_ms": 4,
                        "document_count": 1,
                        "estimated_input_tokens": 7,
                        "estimated_cost": None,
                        "cost_status": "unpriced",
                        "document_text": "must not escape",
                    },
                    "correction": {
                        "attempted": False,
                        "input_tokens": 0,
                        "output_tokens": 0,
                        "estimated_cost": 0.0,
                        "prompt": "must not escape",
                    },
                }
            ],
        }
    )

    usage = make_decomposition_usage(primary, "approved context")

    assert set(usage["branches"][0]) == {"branch_id", "retrieval", "correction"}
    assert set(usage["branches"][0]["retrieval"]) == {
        "latency_ms",
        "latency_scope",
        "document_count",
        "estimated_input_tokens",
        "estimated_cost",
        "cost_status",
    }
    assert set(usage["branches"][0]["correction"]) == {
        "attempted",
        "input_tokens",
        "output_tokens",
        "estimated_cost",
    }
    assert usage["retrieval_batch"] == {
        "latency_ms": 0,
        "branch_count": 0,
        "shared": False,
    }


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
    usage = outcome.decomposition_usage
    assert usage["schema"] == "rag-decomposition-usage-v1"
    assert usage["planner"] == {
        "calls": 0,
        "input_tokens": 0,
        "output_tokens": 0,
        "estimated_cost": 0.0,
    }
    assert [branch["branch_id"] for branch in usage["branches"]] == [
        "branch-1",
        "branch-2",
    ]
    assert all(
        branch["retrieval"]["document_count"] == 1
        and branch["retrieval"]["estimated_input_tokens"] > 0
        and branch["retrieval"]["estimated_cost"] is None
        and branch["retrieval"]["cost_status"] == "unpriced"
        and branch["correction"]["attempted"] is False
        for branch in usage["branches"]
    )
    serialized = json.dumps(usage).casefold()
    assert not any(
        forbidden in serialized
        for forbidden in ("subquery", "question", "prompt", "document_text", "answer")
    )


def test_query_decomposition_batches_shared_qdrant_reads(monkeypatch):
    from mech_chatbot.rag.evidence_gate import EvidenceDecision, EvidenceState
    from mech_chatbot.rag.phases import retrieval as retrieval_phase

    request = _prepared_request(
        question=(
            "Đối chiếu định mức CRAG-EVAL-NUM-001, tổng BOM "
            "CRAG-EVAL-BOM-001 và quy trình lắp CRAG-EVAL-PART-C."
        )
    )
    decision = replace(
        _route_decision(
            request,
            part_ids=(
                "CRAG-EVAL-NUM-001",
                "CRAG-EVAL-BOM-001",
                "CRAG-EVAL-PART-C",
            ),
        ),
        is_bom_query=True,
    )
    batch_calls = []

    def retrieve(**_kwargs):
        raise AssertionError("decomposed retrieval must use the batch boundary")

    def retrieve_many(requests, *, deadline_monotonic=None):
        assert deadline_monotonic is not None
        batch_calls.append(tuple(requests))
        return tuple(
            (
                [
                    Document(
                        page_content=(
                            f"Approved evidence for {request['query_to_search']}"
                        ),
                        metadata={
                            "doc_id": request["query_to_search"],
                            "trang_so": 1,
                        },
                    )
                ],
                5,
                "strict_exact:explicit_dense_bm25_rrf",
                time.time(),
                object(),
            )
            for request in requests
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
            retrieve_many=retrieve_many,
            query_decomposition_enabled=True,
        ),
        provider=SimpleNamespace(
            invoke=lambda *_args, **_kwargs: SimpleNamespace(
                content=(
                    '{"subqueries":["định mức CRAG-EVAL-NUM-001",'
                    '"tổng BOM CRAG-EVAL-BOM-001",'
                    '"quy trình lắp CRAG-EVAL-PART-C"]}'
                )
            )
        ),
    )

    assert len(batch_calls) == 1
    assert len(batch_calls[0]) == 3
    assert [
        code in request["query_to_search"]
        for code, request in zip(
            (
                "CRAG-EVAL-NUM-001",
                "CRAG-EVAL-BOM-001",
                "CRAG-EVAL-PART-C",
            ),
            batch_calls[0],
            strict=True,
        )
    ] == [True, True, True]
    assert len(outcome.decomposition_branches) == 3
    assert outcome.decomposition_usage["retrieval_batch"]["branch_count"] == 3
    assert outcome.decomposition_usage["retrieval_batch"]["shared"] is True
    assert all(
        branch["retrieval"]["latency_scope"] == "shared_batch"
        for branch in outcome.decomposition_usage["branches"]
    )


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


def test_general_branch_publishes_only_its_served_top_source(monkeypatch):
    from mech_chatbot.rag.evidence_gate import EvidenceDecision, EvidenceState
    from mech_chatbot.rag.phases import retrieval as retrieval_phase

    request = _prepared_request(
        question=(
            "Mắt cú xanh kiểm tra theo chu kỳ nào và "
            "giá trị CRAG-EVAL-NUM-001 là bao nhiêu?"
        )
    )
    decision = _route_decision(request, part_ids=("CRAG-EVAL-NUM-001",))

    def document(doc_id, name):
        return Document(
            page_content=name,
            metadata={
                "doc_id": doc_id,
                "trang_so": 1,
                "file_goc": f"{name}.md",
                "version_no": 1,
            },
        )

    def retrieve(**kwargs):
        if "mắt cú xanh" in kwargs["query_to_search"].lower():
            return (
                [document(1, "alias"), document(2, "unrelated")],
                5,
                "general:explicit_dense_bm25_rrf",
                time.time(),
                object(),
            )
        return (
            [document(3, "number")],
            5,
            "strict_exact:explicit_dense_bm25_rrf",
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
                    '{"subqueries":["Mắt cú xanh kiểm tra theo chu kỳ nào",'
                    '"giá trị CRAG-EVAL-NUM-001"]}'
                )
            )
        ),
    )

    assert [
        citation["doc_id"]
        for citation in outcome.decomposition_branches[0]["citations"]
    ] == [1]
    assert [document.metadata["doc_id"] for document in outcome.documents] == [1, 3]
    assert outcome.decomposition_usage["branches"][0]["retrieval"][
        "document_count"
    ] == 2


def test_general_branch_answerability_uses_only_its_served_top_source(monkeypatch):
    from mech_chatbot.rag.evidence_gate import EvidenceDecision, EvidenceState
    from mech_chatbot.rag.phases import retrieval as retrieval_phase

    request = _prepared_request(
        question=(
            "Mắt cú xanh kiểm tra theo chu kỳ nào và "
            "giá trị CRAG-EVAL-NUM-001 là bao nhiêu?"
        )
    )
    decision = _route_decision(request, part_ids=("CRAG-EVAL-NUM-001",))

    def document(doc_id, content):
        return Document(
            page_content=content,
            metadata={"doc_id": doc_id, "trang_so": 1},
        )

    def retrieve(**kwargs):
        if "mắt cú xanh" in kwargs["query_to_search"].lower():
            return (
                [document(1, "unrelated"), document(2, "alias evidence")],
                5,
                "general:explicit_dense_bm25_rrf",
                time.time(),
                object(),
            )
        return (
            [document(3, "number evidence")],
            5,
            "strict_exact:explicit_dense_bm25_rrf",
            time.time(),
            object(),
        )

    def answerability(_query, _context, *, docs, **_kwargs):
        documents = docs
        content = "\n".join(document.page_content for document in documents)
        state = (
            EvidenceState.SUFFICIENT
            if "evidence" in content
            else EvidenceState.INSUFFICIENT
        )
        return EvidenceDecision(
            state,
            reason=(
                "covered" if state is EvidenceState.SUFFICIENT else "missing"
            ),
        )

    monkeypatch.setattr(retrieval_phase, "tokenize_cached", lambda value: str(value))
    monkeypatch.setattr(
        retrieval_phase,
        "_assemble_context",
        lambda docs, _query: "\n".join(doc.page_content for doc in docs),
    )
    monkeypatch.setattr(retrieval_phase, "evaluate_answerability", answerability)

    outcome = _run_phase(
        lambda state: retrieval_phase.retrieve_primary(decision, state),
        retrieval=_retrieval_adapter(
            retrieve=retrieve,
            query_decomposition_enabled=True,
        ),
        provider=SimpleNamespace(
            invoke=lambda *_args, **_kwargs: SimpleNamespace(
                content=(
                    '{"subqueries":["Mắt cú xanh kiểm tra theo chu kỳ nào",'
                    '"giá trị CRAG-EVAL-NUM-001"]}'
                )
            )
        ),
    )

    assert [branch["outcome"] for branch in outcome.decomposition_branches] == [
        "insufficient_evidence",
        "full_answer",
    ]
    assert outcome.decomposition_branches[0]["citations"] == []
    assert [document.metadata["doc_id"] for document in outcome.documents] == [3]
    assert outcome.decomposition_usage["branches"][0]["retrieval"][
        "document_count"
    ] == 2


def test_general_branch_serves_corrected_top_source_and_keeps_raw_count(monkeypatch):
    from mech_chatbot.rag.evidence_gate import EvidenceDecision, EvidenceState
    from mech_chatbot.rag.phases import retrieval as retrieval_phase

    request = _prepared_request(
        question=(
            "Mắt cú xanh kiểm tra theo chu kỳ nào và "
            "giá trị CRAG-EVAL-NUM-001 là bao nhiêu?"
        )
    )
    decision = _route_decision(
        request,
        part_ids=("CRAG-EVAL-NUM-001",),
        crag_enabled=True,
    )

    def document(doc_id, content):
        return Document(
            page_content=content,
            metadata={"doc_id": doc_id, "trang_so": 1},
        )

    def retrieve(**kwargs):
        query = kwargs["query_to_search"].lower()
        if query == "rewritten alias":
            return (
                [document(4, "alias evidence")],
                5,
                "general:explicit_dense_bm25_rrf",
                time.time(),
                object(),
            )
        if "mắt cú xanh" in query:
            return (
                [document(1, "unrelated one"), document(2, "unrelated two")],
                5,
                "general:explicit_dense_bm25_rrf",
                time.time(),
                object(),
            )
        return (
            [document(3, "number evidence")],
            5,
            "strict_exact:explicit_dense_bm25_rrf",
            time.time(),
            object(),
        )

    def answerability(_query, _context, *, docs, **_kwargs):
        content = "\n".join(document.page_content for document in docs)
        if "evidence" in content:
            return EvidenceDecision(EvidenceState.SUFFICIENT, reason="covered")
        return EvidenceDecision(EvidenceState.AMBIGUOUS, reason="missing")

    def invoke(*_args, **kwargs):
        if kwargs["surface"] == "query_decomposition":
            return SimpleNamespace(
                content=(
                    '{"subqueries":["Mắt cú xanh kiểm tra theo chu kỳ nào",'
                    '"giá trị CRAG-EVAL-NUM-001"]}'
                )
            )
        return SimpleNamespace(content="rewritten alias")

    monkeypatch.setattr(retrieval_phase, "tokenize_cached", lambda value: str(value))
    monkeypatch.setattr(
        retrieval_phase,
        "_assemble_context",
        lambda docs, _query: "\n".join(doc.page_content for doc in docs),
    )
    monkeypatch.setattr(retrieval_phase, "evaluate_answerability", answerability)

    outcome = _run_phase(
        lambda state: retrieval_phase.retrieve_primary(decision, state),
        retrieval=_retrieval_adapter(
            retrieve=retrieve,
            query_decomposition_enabled=True,
            crag_enabled=True,
        ),
        provider=SimpleNamespace(invoke=invoke),
    )

    assert [branch["outcome"] for branch in outcome.decomposition_branches] == [
        "full_answer",
        "full_answer",
    ]
    assert [
        citation["doc_id"]
        for citation in outcome.decomposition_branches[0]["citations"]
    ] == [4]
    assert [document.metadata["doc_id"] for document in outcome.documents] == [4, 3]
    assert outcome.decomposition_usage["branches"][0]["retrieval"][
        "document_count"
    ] == 2


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
    from qdrant_client import models

    request = _prepared_request(question="Mắt cú xanh kiểm tra khi nào?")
    governance_filter = models.Filter(
        must=[
            models.FieldCondition(
                key="metadata.site",
                match=models.MatchValue(value="HQ"),
            )
        ]
    )
    decision = _route_decision(
        request,
        part_ids=(),
        crag_enabled=True,
        strict_filter=governance_filter,
    )
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
            "servable": True,
            "publication_state": "published",
            "lifecycle_status": "published",
            "review_status": "approved",
            "is_current": True,
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
    scroll_calls = []
    trace_events = []

    class Client:
        def scroll(self, **kwargs):
            scroll_calls.append(kwargs)
            return [
                SimpleNamespace(
                    id="corrected-point",
                    payload={
                        "page_content": corrected_document.page_content,
                        "metadata": corrected_document.metadata,
                    },
                )
            ], None

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
    monkeypatch.setattr(
        enrichment_phase,
        "log_trace",
        lambda event, trace_id, **fields: trace_events.append(
            {"event": event, "trace_id": trace_id, **fields}
        ),
    )

    outcome = _run_phase(
        lambda state: enrichment_phase.enrich_retrieval(decision, primary, state),
        retrieval=_retrieval_adapter(
            retrieve=lambda **kwargs: retrieval_calls.append(kwargs) or pytest.fail(
                "metadata correction must not run embedding or hybrid retrieval"
            ),
            client=Client(),
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
    assert retrieval_calls == []
    assert len(scroll_calls) == 1
    assert governance_filter in scroll_calls[0]["scroll_filter"].must

    correction_events = [
        event
        for event in trace_events
        if event["event"] == "corrective_retrieval"
    ]
    assert correction_events[0]["backend"] == "metadata_filter"
    assert correction_events[0]["fallback_reason"] is None
    assert not {"query", "base_code", "payload"} & correction_events[0].keys()


@pytest.mark.parametrize("metadata_result", ["empty", "timeout"])
def test_crag_metadata_lookup_falls_back_to_hybrid_once(
    monkeypatch,
    metadata_result,
):
    from mech_chatbot.rag.evidence_gate import EvidenceDecision, EvidenceState
    from mech_chatbot.rag.phases import retrieval_enrichment as enrichment_phase
    from qdrant_client import models

    request = _prepared_request(question="Mắt cú xanh kiểm tra khi nào?")
    decision = _route_decision(
        request,
        part_ids=(),
        crag_enabled=True,
        strict_filter=models.Filter(must=[]),
    )
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
        metadata={"doc_id": 8, "trang_so": 1},
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
    correction_counts = []
    trace_events = []

    class Client:
        def scroll(self, **_kwargs):
            if metadata_result == "timeout":
                raise TimeoutError("qdrant timeout")
            return [], None

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
    monkeypatch.setattr(
        enrichment_phase,
        "log_trace",
        lambda event, trace_id, **fields: trace_events.append(
            {"event": event, "trace_id": trace_id, **fields}
        ),
    )

    def run_correction(state):
        outcome = enrichment_phase.enrich_retrieval(decision, primary, state)
        correction_counts.append(state.budget.corrections)
        return outcome

    outcome = _run_phase(
        run_correction,
        retrieval=_retrieval_adapter(
            retrieve=lambda **kwargs: retrieval_calls.append(kwargs) or (
                [corrected_document],
                5,
                "corrected",
                time.time(),
                object(),
            ),
            client=Client(),
            crag_enabled=True,
        ),
        provider=SimpleNamespace(
            invoke=lambda *_args, **_kwargs: pytest.fail(
                "metadata fallback must not call the provider"
            )
        ),
    )

    assert corrected_document in outcome.documents
    assert len(retrieval_calls) == 1
    assert correction_counts == [1]
    correction_event = next(
        event for event in trace_events if event["event"] == "corrective_retrieval"
    )
    assert correction_event["backend"] == "hybrid_fallback"
    assert correction_event["fallback_reason"] == (
        "metadata_empty"
        if metadata_result == "empty"
        else "metadata_unavailable"
    )
    assert not {"query", "base_code", "payload"} & correction_event.keys()


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


@pytest.mark.parametrize(
    ("part_ids", "expected_codes"),
    [
        (("HCP7235-STK",), []),
        (("HCP7235-STK", "PART-A"), ["PART-A"]),
    ],
)
def test_grounded_math_exact_bom_filename_selects_one_variant(
    monkeypatch, part_ids, expected_codes,
):
    from mech_chatbot.rag.phases import retrieval_enrichment as enrichment_phase
    from mech_chatbot.rag.phases.contracts import PhaseTerminal

    request = _prepared_request(
        question=(
            "Tính tổng BOM của 9.3.03951(HCP7235-STK)-ver03-Model8.pdf "
            "và nêu rõ phép tính."
        )
    )
    decision = replace(
        _route_decision(request, part_ids=part_ids),
        is_bom_query=True,
    )
    documents = tuple(
        Document(
            page_content=f"Bản vẽ cơ khí {variant}",
            metadata={
                "doc_id": str(doc_id),
                "trang_so": 1,
                "file_goc": f"9.3.03951(HCP7235-STK)-ver03-{variant}.pdf",
                "base_code": "9.3.03951",
                "variant_code": variant,
                "version_no": 3,
                "domain": "mechanical",
            },
        )
        for doc_id, variant in ((68, "Model7"), (69, "Model8"))
    )
    primary = PrimaryRetrievalOutcome(
        documents=documents,
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
        lookup_documents=documents,
    )
    bom_lookups = []

    monkeypatch.setattr(
        "mech_chatbot.rag.community_summaries.load_community_context",
        lambda *_args, **_kwargs: SimpleNamespace(
            documents=(), used=False, summary_count=0, reason="disabled"
        ),
    )
    monkeypatch.setattr(
        enrichment_phase,
        "search_bom_facts",
        lambda **kwargs: bom_lookups.append(kwargs) or [],
    )

    outcome = _run_phase(
        lambda state: enrichment_phase.enrich_retrieval(decision, primary, state),
        retrieval=_retrieval_adapter(grounded_math_enabled=True),
        provider=SimpleNamespace(invoke=lambda *_args, **_kwargs: None),
    )

    assert not isinstance(outcome, PhaseTerminal)
    assert [document.metadata["doc_id"] for document in outcome.documents] == ["69"]
    assert [
        (lookup["part_codes"], lookup["document_ids"])
        for lookup in bom_lookups
    ] == [(expected_codes, [69])]


def test_exact_document_operand_miss_does_not_fall_back_to_all_document_rows():
    from mech_chatbot.rag.phases.retrieval_enrichment_support import (
        _search_bom_rows,
    )

    calls = []

    def search_bom_facts(**kwargs):
        calls.append(kwargs)
        return []

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

    assert _search_bom_rows(
        context,
        ["PART-NOT-IN-DOCUMENT"],
        [69],
        search_bom_facts,
        exact_document=True,
    ) == []
    assert [
        (call["part_codes"], call["document_ids"])
        for call in calls
    ] == [(["PART-NOT-IN-DOCUMENT"], [69])]


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
    assert [call["document_ids"] for call in calls] == [[], [43]]


def test_bom_aggregate_uses_only_the_resolved_document_scope():
    from mech_chatbot.rag.phases.retrieval_enrichment_support import (
        _search_bom_rows,
    )

    calls = []

    def search_bom_facts(**kwargs):
        calls.append(kwargs)
        return ["scoped-row"] if kwargs["document_ids"] == [136] else [
            "row-from-136",
            "row-from-137",
        ]

    context = SimpleNamespace(
        decision=SimpleNamespace(
            request=SimpleNamespace(
                user_department="Technical",
                max_security_level="internal",
            ),
            intent_data={"version_policy": "current_only"},
        ),
        user_question=(
            "Tính tổng số lượng toàn bộ các dòng BOM của "
            "9.3.04068 ver01 Model3."
        ),
        user_roles=("viewer",),
        allowed_departments=("Technical",),
        allowed_sites=("HQ",),
    )

    rows = _search_bom_rows(
        context,
        ["9.3.04068"],
        [136],
        search_bom_facts,
    )

    assert rows == ["scoped-row"]
    assert [
        (call["part_codes"], call["document_ids"])
        for call in calls
    ] == [([], [136])]


def test_bom_documents_use_an_internal_repository_scope_marker():
    from mech_chatbot.rag.phases.retrieval_enrichment_support import (
        _build_bom_documents,
        is_governed_sql_bom_document,
    )

    documents = _build_bom_documents(
        {
            (7, 2): {
                "file_goc": "assembly.pdf",
                "version_no": 3,
                "security_level": "internal",
                "site": "HQ",
                "external_processing_policy": "internal_only",
                "lines": ["- Ma: P-1"],
            }
        },
        {},
    )

    assert is_governed_sql_bom_document(documents[0]) is True
    assert "access_scope_serving_predicates_applied" not in documents[0].metadata
    assert "version_policy_serving_predicates_applied" not in documents[0].metadata


def test_bom_lookup_does_not_scope_explicit_operands_to_one_document():
    from mech_chatbot.rag.phases.retrieval_enrichment_support import (
        _search_bom_rows,
    )

    calls = []

    def search_bom_facts(**kwargs):
        calls.append(kwargs)
        return ["row-a", "row-b"] if not kwargs["document_ids"] else ["row-b"]

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
        ["PART-A-100", "OTHER-A-700"],
        [49],
        search_bom_facts,
    )

    assert rows == ["row-a", "row-b"]
    assert calls[0]["document_ids"] == []


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
    corrected = [
        branch
        for branch in outcome.decomposition_usage["branches"]
        if branch["correction"]["attempted"]
    ]
    assert len(corrected) == 1
    assert corrected[0]["correction"]["input_tokens"] > 0
    assert corrected[0]["correction"]["output_tokens"] > 0
    assert corrected[0]["correction"]["estimated_cost"] > 0


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

    trace_events = []
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
    monkeypatch.setattr(
        enrichment_phase,
        "log_trace",
        lambda event, _trace_id, **fields: trace_events.append((event, fields)),
    )

    outcome = _run_phase(
        lambda state: enrichment_phase.enrich_retrieval(decision, primary, state),
        retrieval=_retrieval_adapter(),
        provider=SimpleNamespace(invoke=lambda *_args, **_kwargs: None),
    )

    assert isinstance(outcome, PhaseTerminal)
    assert outcome.reason_code == expected_reason
    retrieval_events = [
        fields for event, fields in trace_events if event == "retrieval"
    ]
    assert len(retrieval_events) == 1
    assert retrieval_events[0]["mode"] == "hybrid"
    assert retrieval_events[0]["docs_count"] == 0
    event_names = [event for event, _fields in trace_events]
    assert event_names.index("retrieval") < event_names.index("rag_end")


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
