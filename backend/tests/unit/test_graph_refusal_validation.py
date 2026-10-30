"""Security metadata emitted by terminal Graph evidence refusals."""

from __future__ import annotations

from types import SimpleNamespace

import pytest
from langchain_core.documents import Document

from mech_chatbot.rag.answer_policy import AnswerDecision, AnswerOutcome
from mech_chatbot.rag.evidence_gate import EvidenceDecision, EvidenceState
from mech_chatbot.rag.phases.contracts import PhaseTerminal


pytestmark = pytest.mark.unit


def _document(**updates) -> Document:
    metadata = {
        "doc_id": 17,
        "trang_so": 3,
        "version_no": 4,
        "file_goc": "graph.md",
        "phong_ban_quyen": ["Technical"],
        "security_level": "internal",
        "site": "HQ",
        "servable": True,
        "is_current": True,
        "publication_state": "published",
        "lifecycle_status": "published",
        "review_status": "approved",
        "effective_status": "effective",
    }
    return Document(page_content="insufficient graph evidence", metadata={
        **metadata, **updates,
    })


def _request() -> SimpleNamespace:
    return SimpleNamespace(
        user_question="Quan hệ hiện hành là gì?",
        response_language="vi",
        trace_id="graph-refusal-phase",
        started_at=0.0,
        user_department="Technical",
        user_roles=("viewer",),
        allowed_departments=("Technical",),
        max_security_level="internal",
        allowed_sites=("HQ",),
    )


def _phase_inputs(document: Document):
    primary = SimpleNamespace(
        decomposition_notice="",
        decomposition_branches=(),
        decomposition_intents=(),
        decomposition_intent_coverage=(),
        decomposition_used_fallback=False,
        decomposition_intent_overflow=False,
        decomposition_usage={},
        planner_estimated_cost=0.0,
    )
    enrichment = SimpleNamespace(
        new_part_ids=(),
        correction_estimated_cost=0.0,
        auxiliary_input_tokens=0,
        auxiliary_output_tokens=0,
        active_filter=None,
        has_active_filter=False,
        base_k=5,
        retrieval_mode="hybrid",
        graph_routed=True,
        graph_edge_count=1,
        graph_max_hops=2,
    )
    reranked = SimpleNamespace(
        documents=(document,), served_graph_documents=(),
    )
    return primary, enrichment, reranked


def _state() -> SimpleNamespace:
    budget = SimpleNamespace(
        corrections=0,
        planners=0,
        subqueries=0,
        final_generations=0,
        provider_retries=0,
        deadline_exceeded=False,
    )
    return SimpleNamespace(
        budget=budget,
        transition=lambda _phase: None,
        refuse=lambda _reason: None,
        prepared=lambda values: values,
    )


def _install_terminal_policy(monkeypatch, evidence) -> None:
    decision = EvidenceDecision(
        EvidenceState.INSUFFICIENT, reason="missing fact", stage="terminal"
    )
    policy = AnswerDecision(
        AnswerOutcome.INSUFFICIENT_EVIDENCE,
        EvidenceState.INSUFFICIENT,
        "missing fact",
    )
    monkeypatch.setattr(evidence, "_assemble_context", lambda *_args: "context")
    monkeypatch.setattr(evidence, "_select_citations", lambda *_args: ("", []))
    monkeypatch.setattr(evidence, "_audit_confidential_access", lambda *_args: None)
    monkeypatch.setattr(evidence, "_trace_evidence_policy", lambda *_args: None)
    monkeypatch.setattr(evidence, "_trace_refusal", lambda *_args: None)
    monkeypatch.setattr(
        evidence,
        "_decide_evidence_policy",
        lambda *_args: (policy, decision, (), False, 0.0),
    )


@pytest.mark.parametrize(
    ("metadata_drift", "expected"),
    [
        ({}, {"access_scope_passed": True, "leakage_passed": True}),
        (
            {"phong_ban_quyen": ["Finance"]},
            {"access_scope_passed": False, "leakage_passed": False},
        ),
        (
            {"is_current": False},
            {"access_scope_passed": True, "leakage_passed": False},
        ),
    ],
)
def test_evidence_refusal_emits_explicit_request_validation(
    monkeypatch, metadata_drift, expected
):
    from mech_chatbot.rag.phases import evidence

    _install_terminal_policy(monkeypatch, evidence)
    document = _document(**metadata_drift)
    primary, enrichment, reranked = _phase_inputs(document)
    outcome = evidence.evaluate_evidence(
        SimpleNamespace(request=_request(), intent_data={}),
        primary,
        enrichment,
        reranked,
        _state(),
    )

    assert isinstance(outcome, PhaseTerminal)
    assert outcome.prepared[4]["pilot_request_validation"] == expected
