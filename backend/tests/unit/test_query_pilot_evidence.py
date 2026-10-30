"""Metadata-only per-request evidence for the Query Decomposition pilot."""

import pytest


def test_query_provenance_diagnostic_identifies_missing_branch_without_content():
    from mech_chatbot.rag.pilot_evidence import query_provenance_diagnostics
    from mech_chatbot.rag.query_decomposition import audit_decomposition_stream

    sources = [
        {"doc_id": 7, "trang": 1, "version_no": 1, "source_id": "D7P1"},
        {"doc_id": 8, "trang": 2, "version_no": 1, "source_id": "D8P2"},
    ]
    branches = [{"outcome": "full_answer", "citations": [s]} for s in sources]
    answer = "".join(audit_decomposition_stream(iter(["Private text [SourceID D7P1]."]), branches))
    result = query_provenance_diagnostics({
        "citation_docs": sources, "decomposition_branches": branches,
    }, answer)
    assert result == {
        "citation_structure_passed": True, "provenance_passed": False,
        "full_answer_branch_count": 2, "invalid_rendered_branch_count": 1,
        "branch_citation_mismatch_count": 0, "branch_answer_mismatch_count": 0,
        "answer_outside_branch_citations": False,
        "answer_outside_branch_rendered": False,
    }
    assert all(type(value) in (bool, int) for value in result.values())

from mech_chatbot.rag.execution import RequestBudgetLedger, RequestBudgetLimits
from mech_chatbot.rag.evidence_gate import make_insufficient_evidence_message
from mech_chatbot.rag.pilot_evidence import pilot_request_event_fields


@pytest.mark.parametrize("label", ["Source-ID D8P2", "[SRC: D8P2]", "Source\tID D8P2"])
def test_query_provenance_uses_stream_citation_parser(label):
    from mech_chatbot.rag.pilot_evidence import _query_answer_contract
    from mech_chatbot.rag.query_decomposition import audit_decomposition_stream

    citations = [
        {"doc_id": 7, "trang": 1, "version_no": 1, "source_id": "D7P1"},
        {"doc_id": 8, "trang": 2, "version_no": 1, "source_id": "D8P2"},
    ]
    branches = [{"outcome": "full_answer", "citations": [item]} for item in citations]
    answer = "".join(audit_decomposition_stream(
        iter([f"First [SourceID D7P1]. Second [{label}]."]), branches,
    ))
    assert _query_answer_contract({
        "citation_docs": citations, "decomposition_branches": branches,
    }, answer) == (True, True)


def _usage(branch_count: int = 2) -> dict:
    return {
        "schema": "rag-decomposition-usage-v1",
        "planner": {
            "calls": 0,
            "input_tokens": 0,
            "output_tokens": 0,
            "estimated_cost": 0.0,
        },
        "branches": [
            {
                "branch_id": f"branch-{index}",
                "retrieval": {
                    "latency_ms": 10,
                    "latency_scope": "shared_batch",
                    "document_count": 1,
                    "estimated_input_tokens": 20,
                    "estimated_cost": None,
                    "cost_status": "unpriced",
                },
                "correction": {
                    "attempted": False,
                    "input_tokens": 0,
                    "output_tokens": 0,
                    "estimated_cost": 0.0,
                },
            }
            for index in range(1, branch_count + 1)
        ],
        "retrieval_batch": {
            "latency_ms": 10,
            "branch_count": branch_count,
            "shared": True,
        },
        "final_context": {
            "estimated_input_tokens": 40,
            "estimated_input_cost": 0.0001,
            "included_in_final_generation": True,
        },
        "final_generation": {
            "calls": 1,
            "input_tokens": 40,
            "output_tokens": 20,
            "estimated_cost": 0.0002,
        },
    }


def _diagnostics(**updates) -> dict:
    source = {
        "doc_id": 7,
        "trang": 1,
        "source_id": "D7P1",
        "version_no": 1,
    }
    return {
        "decomposition_usage": _usage(),
        "decomposition_intent_count": 2,
        "decomposition_intent_coverage": [True, True],
        "decomposition_used_fallback": True,
        "decomposition_intent_overflow": False,
        "generation_metrics": {"estimated_cost": 0.0003},
        "citation_docs": [source],
        "decomposition_branches": [
            {
                "outcome": "full_answer",
                "citations": [source],
                "rendered_source_ids": ["D7P1"],
            },
            {
                "outcome": "full_answer",
                "citations": [source],
                "rendered_source_ids": ["D7P1"],
            },
        ],
        "pilot_request_validation": {
            "access_scope_passed": True,
            "citation_structure_passed": True,
            "provenance_passed": True,
            "leakage_passed": True,
        },
        **updates,
    }


def _budget(
    *, subqueries: int = 2, final_generations: int = 1,
) -> RequestBudgetLedger:
    return RequestBudgetLedger(
        limits=RequestBudgetLimits(provider_retries=0),
        started_monotonic=0.0,
        subqueries=subqueries,
        final_generations=final_generations,
    )


def test_query_route_emits_complete_metadata_only_pilot_evidence():
    evidence = pilot_request_event_fields(
        _diagnostics(),
        "SECRET ANSWER. SourceID: D7P1",
        _budget(),
        final_latency_ms=250,
        completion_outcome="answered",
        refusal_reason=None,
    )

    assert evidence == {
        "route": "query_decomposition",
        "query_result_status": "valid",
        "completion_outcome": "answered",
        "refusal_reason_code": None,
        "refusal_template_passed": False,
        "owner_review_required": False,
        "security_passed": True,
        "citation_structure_passed": True,
        "provenance_passed": True,
        "leakage_detected": False,
        "planner_calls": 0,
        "subquery_count": 2,
        "correction_count": 0,
        "intent_count": 2,
        "intent_coverage_complete": True,
        "deterministic_split_used": True,
        "intent_overflow": False,
        "final_latency_ms": 250,
        "request_deadline_ms": 120000,
        "estimated_cost": 0.0003,
        "provider_retries": 0,
        "final_generations": 1,
    }
    serialized = str(evidence)
    assert "SECRET ANSWER" not in serialized
    assert "question" not in serialized
    assert "subquery" not in serialized.replace("subquery_count", "")


def test_query_answer_reconciles_final_rendered_sources_after_streaming():
    first = {
        "doc_id": 7,
        "trang": 1,
        "source_id": "D7P1",
        "version_no": 1,
    }
    second = {
        "doc_id": 8,
        "trang": 2,
        "source_id": "D8P2",
        "version_no": 3,
    }
    evidence = pilot_request_event_fields(
        _diagnostics(
            citation_docs=[first, second],
            decomposition_branches=[
                {
                    "outcome": "full_answer",
                    "citations": [first],
                    "rendered_source_ids": ["D7P1"],
                },
                {
                    "outcome": "full_answer",
                    "citations": [second],
                    "rendered_source_ids": ["D8P2"],
                },
            ],
            pilot_request_validation={
                "access_scope_passed": True,
                "leakage_passed": True,
            },
        ),
        "Nguồn: tài liệu A, trang 1, version 1, SourceID: D7P1. "
        "Nguồn: tài liệu B, trang 2, version 3, SourceID: D8P2.",
        _budget(),
        final_latency_ms=250,
        completion_outcome="answered",
        refusal_reason=None,
    )

    assert evidence["query_result_status"] == "valid"
    assert evidence["citation_structure_passed"] is True
    assert evidence["provenance_passed"] is True


def test_query_answer_rejects_rendered_source_missing_from_citation_docs():
    source = {
        "doc_id": 7,
        "trang": 1,
        "source_id": "D7P1",
        "version_no": 1,
    }
    evidence = pilot_request_event_fields(
        _diagnostics(
            citation_docs=[source],
            decomposition_branches=[
                {
                    "outcome": "full_answer",
                    "citations": [source],
                    "rendered_source_ids": ["D7P1"],
                }
            ],
        ),
        "SourceID: D99P1",
        _budget(),
        final_latency_ms=250,
        completion_outcome="answered",
        refusal_reason=None,
    )

    assert evidence["query_result_status"] == "invalid"
    assert evidence["citation_structure_passed"] is False


def test_query_answer_rejects_full_branch_without_rendered_provenance():
    source = {
        "doc_id": 7,
        "trang": 1,
        "source_id": "D7P1",
        "version_no": 1,
    }
    evidence = pilot_request_event_fields(
        _diagnostics(
            citation_docs=[source],
            decomposition_branches=[
                {
                    "outcome": "full_answer",
                    "citations": [source],
                    "rendered_source_ids": [],
                }
            ],
        ),
        "SourceID: D7P1",
        _budget(),
        final_latency_ms=250,
        completion_outcome="answered",
        refusal_reason=None,
    )

    assert evidence["query_result_status"] == "invalid"
    assert evidence["citation_structure_passed"] is True
    assert evidence["provenance_passed"] is False


def test_query_answer_rejects_source_not_rendered_by_any_branch():
    first = {
        "doc_id": 7,
        "trang": 1,
        "source_id": "D7P1",
        "version_no": 1,
    }
    second = {
        "doc_id": 8,
        "trang": 2,
        "source_id": "D8P2",
        "version_no": 3,
    }
    evidence = pilot_request_event_fields(
        _diagnostics(
            citation_docs=[first, second],
            decomposition_branches=[
                {
                    "outcome": "full_answer",
                    "citations": [first],
                    "rendered_source_ids": ["D7P1"],
                },
                {
                    "outcome": "full_answer",
                    "citations": [first, second],
                    "rendered_source_ids": ["D7P1"],
                },
            ],
        ),
        "SourceID: D7P1 SourceID: D8P2",
        _budget(),
        final_latency_ms=250,
        completion_outcome="answered",
        refusal_reason=None,
    )

    assert evidence["query_result_status"] == "invalid"
    assert evidence["citation_structure_passed"] is True
    assert evidence["provenance_passed"] is False


def test_query_answer_rejects_bool_source_metadata():
    bool_source = {
        "doc_id": True,
        "trang": True,
        "source_id": "D1P1",
        "version_no": True,
    }
    evidence = pilot_request_event_fields(
        _diagnostics(
            citation_docs=[bool_source],
            decomposition_branches=[
                {
                    "outcome": "full_answer",
                    "citations": [bool_source],
                    "rendered_source_ids": ["D1P1"],
                },
                {
                    "outcome": "full_answer",
                    "citations": [bool_source],
                    "rendered_source_ids": ["D1P1"],
                },
            ],
        ),
        "SourceID: D1P1",
        _budget(),
        final_latency_ms=250,
        completion_outcome="answered",
        refusal_reason=None,
    )

    assert evidence["query_result_status"] == "invalid"
    assert evidence["citation_structure_passed"] is False
    assert evidence["provenance_passed"] is False


def test_query_pilot_evidence_fails_closed_on_intent_overflow():
    evidence = pilot_request_event_fields(
        _diagnostics(decomposition_intent_overflow=True),
        "answer",
        _budget(),
        final_latency_ms=250,
        completion_outcome="answered",
        refusal_reason=None,
    )

    assert evidence["query_result_status"] == "invalid"
    assert evidence["owner_review_required"] is True


def test_query_pilot_evidence_fails_closed_on_malformed_intent_count():
    evidence = pilot_request_event_fields(
        _diagnostics(decomposition_intent_count="not-an-integer"),
        "SECRET ANSWER",
        _budget(),
        final_latency_ms=250,
        completion_outcome="answered",
        refusal_reason=None,
    )

    assert evidence["query_result_status"] == "invalid"
    assert evidence["intent_count"] == 0
    assert "SECRET ANSWER" not in str(evidence)


def test_query_terminal_evidence_gate_refusal_is_reviewable_safe_evidence():
    evidence = pilot_request_event_fields(
        _diagnostics(
            answer_outcome="insufficient_evidence",
            evidence_stage="terminal",
            pilot_request_validation={
                "access_scope_passed": True,
                "leakage_passed": True,
            },
        ),
        make_insufficient_evidence_message(
            "SECRET QUESTION", "missing source", "vi"
        ),
        _budget(final_generations=0),
        final_latency_ms=250,
        completion_outcome="refused",
        refusal_reason="evidence_gate",
    )

    assert evidence["query_result_status"] == "safe_refusal"
    assert evidence["completion_outcome"] == "refused"
    assert evidence["refusal_reason_code"] == "evidence_gate"
    assert evidence["refusal_template_passed"] is True
    assert evidence["owner_review_required"] is True


def test_simple_request_does_not_count_as_query_pilot_evidence():
    assert pilot_request_event_fields(
        _diagnostics(
            decomposition_usage=_usage(branch_count=1),
            decomposition_intent_count=1,
            decomposition_intent_coverage=[True],
        ),
        "answer",
        _budget(subqueries=1),
        final_latency_ms=250,
        completion_outcome="answered",
        refusal_reason=None,
    ) is None
