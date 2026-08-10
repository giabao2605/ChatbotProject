"""Per-request metadata-only telemetry for the Graph production pilot."""

from contextlib import nullcontext
import json

import pytest

from mech_chatbot.rag.evidence_gate import make_insufficient_evidence_message
from mech_chatbot.rag.execution import (
    AccessScope,
    DefaultRagExecutor,
    RagCompleted,
    RagInvocation,
    RagRequest,
)


pytestmark = pytest.mark.unit


def _graph_diagnostics(**updates):
    base = {
        "answer_outcome": "full_answer",
        "graph_routed": True,
        "graph_edge_count": 1,
        "graph_max_hops": 2,
        "graph_evidence": [{
            "doc_id": 17,
            "trang": 3,
            "version_no": 4,
            "source_id": "D17P3",
            "graph_edge_id": 99,
            "graph_relation_type": "SUPERSEDES",
            "graph_source_key": "SECRET SOURCE KEY",
            "graph_target_key": "SECRET TARGET KEY",
            "graph_confidence": 0.9,
            "text": "SECRET GRAPH CONTENT",
        }],
        "citation_docs": [{
            "doc_id": 17,
            "trang": 3,
            "version_no": 4,
            "source_id": "D17P3",
            "file_goc": "SECRET FILE NAME",
        }],
        "generation_metrics": {"estimated_cost": 0.125},
        "pilot_request_validation": {
            "access_scope_passed": True,
            "leakage_passed": True,
        },
    }
    return {**base, **updates}


def _run_graph(
    monkeypatch,
    pipeline,
    *,
    trace_id: str,
    mode: str = "test",
    runtime_identity: str | None = None,
    clock=None,
):
    from mech_chatbot.config import logging as trace_logging
    from mech_chatbot.rag import execution

    trace_messages = []
    monkeypatch.setattr(trace_logging.trace_logger, "info", trace_messages.append)
    if clock is not None:
        monkeypatch.setattr(execution.time, "monotonic", lambda: next(clock))
    binding = (
        trace_logging.bind_trace_runtime(
            trace_logging.TraceRuntime(runtime_identity_sha256=runtime_identity)
        )
        if runtime_identity else nullcontext()
    )
    with binding:
        events = list(
            DefaultRagExecutor(execute_pipeline=pipeline).run(
                RagRequest("SECRET RELATIONAL QUESTION", AccessScope()),
                RagInvocation(trace_id=trace_id, mode=mode),
            )
        )
    evidence = [
        json.loads(message) for message in trace_messages
        if json.loads(message)["event"] == "pilot_request_evidence"
    ]
    return events, evidence


def _assert_absent(payload: dict, *secrets: str) -> None:
    serialized = json.dumps(payload)
    assert all(secret not in serialized for secret in secrets)


def _expected_graph_event(timestamp: str) -> dict:
    return {
        "ts": timestamp,
        "event": "pilot_request_evidence",
        "trace_id": "graph-pilot",
        "execution_context": "test",
        "route": "graph_relational",
        "graph_result_status": "valid",
        "completion_outcome": "answered",
        "refusal_reason_code": None,
        "refusal_template_passed": False,
        "low_confidence": False,
        "owner_review_required": False,
        "security_passed": True,
        "citation_structure_passed": True,
        "provenance_passed": True,
        "leakage_detected": False,
        "graph_edges": 1,
        "graph_max_hops": 2,
        "graph_evidence_count": 1,
        "rendered_citation_count": 1,
        "graph_citation_count": 1,
        "final_latency_ms": 250,
        "request_deadline_ms": 120000,
        "estimated_cost": 0.125,
        "provider_retries": 0,
        "final_generations": 1,
    }


def test_successful_graph_route_emits_metadata_only_pilot_event(monkeypatch):
    def pipeline(state):
        state.budget.record("graph_edges", 1)
        state.budget.record("final_generations", 1)
        return state.prepared((
            iter(["Quan hệ đã được kiểm chứng [SRC:D17P3]"]),
            "", [], [], _graph_diagnostics(),
        ))

    events, evidence = _run_graph(
        monkeypatch,
        pipeline,
        trace_id="graph-pilot",
        clock=iter([100.0, 100.1, 100.2, 100.25]),
    )

    assert isinstance(events[-1], RagCompleted)
    assert evidence == [_expected_graph_event(evidence[0]["ts"])]
    _assert_absent(
        evidence[0],
        "SECRET RELATIONAL QUESTION",
        "Quan hệ đã được kiểm chứng",
        "SECRET SOURCE KEY",
        "SECRET TARGET KEY",
        "SECRET GRAPH CONTENT",
        "SECRET FILE NAME",
    )


def test_graph_pilot_event_fails_closed_on_unbound_citation_and_provenance(
    monkeypatch,
):
    graph_evidence = [{
        **_graph_diagnostics()["graph_evidence"][0],
        "graph_edge_id": None,
    }]

    def pipeline(state):
        state.budget.record("graph_edges", 1)
        diagnostics = _graph_diagnostics(graph_evidence=graph_evidence)
        return state.prepared((
            iter(["Unsupported relation [SRC:D999P1]"]),
            "", [], [], diagnostics,
        ))

    _, evidence = _run_graph(monkeypatch, pipeline, trace_id="graph-invalid")

    assert evidence[0]["graph_result_status"] == "invalid"
    assert evidence[0]["citation_structure_passed"] is False
    assert evidence[0]["provenance_passed"] is False
    assert evidence[0]["graph_citation_count"] == 0


def test_graph_safe_refusal_requires_terminal_refused_completion(monkeypatch):
    answer = make_insufficient_evidence_message(
        "SECRET RELATIONAL QUESTION", "missing fact", "vi"
    )

    def pipeline(state):
        state.budget.record("graph_edges", 1)
        state.refuse("evidence_gate")
        diagnostics = {
            "answer_outcome": "insufficient_evidence",
            "evidence_stage": "terminal",
            "graph_routed": True,
            "graph_max_hops": 2,
            "graph_evidence": [],
            "citation_docs": [],
            "generation_metrics": {},
            "pilot_request_validation": {
                "access_scope_passed": True,
                "leakage_passed": True,
            },
        }
        return state.prepared((
            iter([answer]),
            "", [], [], diagnostics,
        ))

    events, evidence = _run_graph(
        monkeypatch, pipeline, trace_id="graph-safe-refusal"
    )

    assert events[-1].outcome == "refused"
    assert evidence[0]["graph_result_status"] == "safe_refusal"
    assert evidence[0]["completion_outcome"] == "refused"
    assert evidence[0]["refusal_reason_code"] == "evidence_gate"
    assert evidence[0]["refusal_template_passed"] is True
    assert evidence[0]["low_confidence"] is True
    assert evidence[0]["owner_review_required"] is True


def test_graph_refusal_with_factual_text_fails_closed(monkeypatch):
    def pipeline(state):
        state.budget.record("graph_edges", 1)
        state.refuse("evidence_gate")
        diagnostics = _graph_diagnostics(
            answer_outcome="insufficient_evidence",
            evidence_stage="terminal",
            graph_evidence=[],
            citation_docs=[],
        )
        return state.prepared((
            iter(["Giá trị nội bộ là 1.500 nhưng tài liệu không đủ."]),
            "", [], [], diagnostics,
        ))

    _, evidence = _run_graph(monkeypatch, pipeline, trace_id="graph-unsafe-refusal")

    assert evidence[0]["graph_result_status"] == "invalid"
    assert evidence[0]["refusal_template_passed"] is False


def test_graph_refusal_requires_explicit_request_security_validation(monkeypatch):
    answer = make_insufficient_evidence_message("question", "missing fact", "en")

    def pipeline(state):
        state.budget.record("graph_edges", 1)
        state.refuse("evidence_gate")
        diagnostics = _graph_diagnostics(
            answer_outcome="insufficient_evidence",
            evidence_stage="terminal",
            graph_evidence=[],
            citation_docs=[],
            pilot_request_validation={},
        )
        return state.prepared((iter([answer]), "", [], [], diagnostics))

    _, evidence = _run_graph(monkeypatch, pipeline, trace_id="graph-unvalidated-refusal")

    assert evidence[0]["graph_result_status"] == "invalid"
    assert evidence[0]["security_passed"] is False
    assert evidence[0]["leakage_detected"] is True


def test_low_confidence_graph_answer_requires_owner_review(monkeypatch):
    graph_evidence = [{
        **_graph_diagnostics()["graph_evidence"][0],
        "graph_confidence": 0.2,
    }]

    def pipeline(state):
        state.budget.record("graph_edges", 1)
        state.budget.record("final_generations", 1)
        diagnostics = _graph_diagnostics(graph_evidence=graph_evidence)
        return state.prepared((
            iter(["Quan hệ có nguồn [SRC:D17P3]"]),
            "", [], [], diagnostics,
        ))

    _, evidence = _run_graph(monkeypatch, pipeline, trace_id="graph-low-confidence")

    assert evidence[0]["graph_result_status"] == "valid"
    assert evidence[0]["low_confidence"] is True
    assert evidence[0]["owner_review_required"] is True


def test_graph_non_refusal_with_uncited_factual_text_fails_closed(monkeypatch):
    def pipeline(state):
        state.budget.record("graph_edges", 1)
        diagnostics = _graph_diagnostics(
            answer_outcome="insufficient_evidence",
            evidence_stage="terminal",
            graph_evidence=[],
            citation_docs=[],
            generation_metrics={},
        )
        return state.prepared((
            iter(["Giá trị bí mật là 1.500 nhưng tài liệu không đủ."]),
            "", [], [], diagnostics,
        ))

    _, evidence = _run_graph(monkeypatch, pipeline, trace_id="graph-not-refused")

    assert evidence[0]["graph_result_status"] == "invalid"
    assert evidence[0]["completion_outcome"] == "answered"
    assert evidence[0]["owner_review_required"] is True


def test_graph_pilot_evidence_keeps_runtime_identity_and_bad_metrics_safe(
    monkeypatch,
):
    def pipeline(state):
        state.budget.record("graph_edges", 1)
        diagnostics = _graph_diagnostics(
            graph_max_hops="not-an-integer",
            graph_evidence=[],
            citation_docs=[],
            generation_metrics={"estimated_cost": "not-a-number"},
        )
        return state.prepared((iter(["Invalid graph response"]), "", [], [], diagnostics))

    events, evidence = _run_graph(
        monkeypatch,
        pipeline,
        trace_id="graph-runtime-bound",
        mode="production",
        runtime_identity="a" * 64,
    )

    assert isinstance(events[-1], RagCompleted)
    assert evidence[0]["runtime_identity_sha256"] == "a" * 64
    assert evidence[0]["graph_result_status"] == "invalid"
    assert evidence[0]["graph_max_hops"] == 0
    assert evidence[0]["estimated_cost"] == 0.0
