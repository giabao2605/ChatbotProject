from __future__ import annotations

import importlib.util
import json
from pathlib import Path

import pytest

from mech_chatbot.config import logging as trace_logging


pytestmark = pytest.mark.unit


def _load_snapshot_module():
    script = Path(__file__).resolve().parents[2] / "scripts/eval/rag_trace_snapshot.py"
    spec = importlib.util.spec_from_file_location("rag_trace_snapshot", script)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_log_trace_adds_execution_context_and_normalizes_rag_end_reason(monkeypatch):
    emitted: list[dict] = []
    monkeypatch.setenv("RAG_EXECUTION_CONTEXT", "evaluation")
    monkeypatch.setattr(
        trace_logging.trace_logger,
        "info",
        lambda message: emitted.append(json.loads(message)),
    )
    monkeypatch.setattr(trace_logging, "_persist_rag_trace", lambda *_args: None)

    trace_logging.log_trace(
        "rag_end",
        "trace-eval",
        refusal=True,
        reason="no_confident_candidate",
    )

    assert emitted == [
        {
            "ts": emitted[0]["ts"],
            "event": "rag_end",
            "trace_id": "trace-eval",
            "execution_context": "evaluation",
            "refusal": True,
            "refusal_reason": "no_confident_candidate",
        }
    ]


def test_replay_trace_context_removes_raw_query_fields(monkeypatch):
    emitted: list[dict] = []
    monkeypatch.setattr(
        trace_logging.trace_logger,
        "info",
        lambda message: emitted.append(json.loads(message)),
    )

    with trace_logging.redact_sensitive_trace_fields(True):
        trace_logging.log_trace(
            "context_analysis",
            "replay-trace",
            original_question="raw question",
            standalone_question="rewritten raw question",
            corrected_query="raw corrected query",
            latency_ms=10,
        )

    assert emitted[0]["latency_ms"] == 10
    assert "original_question" not in emitted[0]
    assert "standalone_question" not in emitted[0]
    assert "corrected_query" not in emitted[0]


def test_snapshot_excludes_test_cancelled_and_empty_reason_by_default(tmp_path):
    snapshot = _load_snapshot_module()
    path = tmp_path / "trace.jsonl"
    events = [
        {"ts": "2026-07-13T00:00:00+00:00", "event": "llm_retry", "trace_id": "p1", "execution_context": "production", "attempt": 1},
        {"ts": "2026-07-13T00:00:00+00:00", "event": "rag_end", "trace_id": "p1", "execution_context": "production", "refusal": True, "refusal_reason": "post_check_numbers"},
        {"ts": "2026-07-13T00:01:00+00:00", "event": "rag_end", "trace_id": "p2", "execution_context": "production", "refusal": True, "refusal_reason": "client_cancelled"},
        {"ts": "2026-07-13T00:02:00+00:00", "event": "rag_end", "trace_id": "p3", "execution_context": "production", "refusal": True, "refusal_reason": None},
        {"ts": "2026-07-13T00:03:00+00:00", "event": "rag_end", "trace_id": "t1", "execution_context": "test", "refusal": True, "refusal_reason": "evidence_gate"},
    ]
    path.write_text("\n".join(json.dumps(item) for item in events), encoding="utf-8")

    report = snapshot.build_snapshot(path, execution_contexts={"production"})

    assert report["denominator"] == 1
    assert report["refusal_reasons"] == {"post_check_numbers": 1}
    assert report["filters"]["excluded_reasons"] == ["client_cancelled"]
    assert report["source"]["path"] == str(path.resolve())
    assert report["system_metrics"]["retry_rate"] == pytest.approx(1 / 3)


def test_snapshot_summarizes_external_ai_latency_by_surface_without_payload(tmp_path):
    snapshot = _load_snapshot_module()
    path = tmp_path / "trace.jsonl"
    events = [
        {
            "ts": "2026-07-13T00:00:00+00:00",
            "event": "external_ai_call",
            "trace_id": "eval:candidate:slow-route",
            "execution_context": "evaluation",
            "surface": "interaction_routing",
            "status": "success",
            "latency_ms": 120000,
        },
        {
            "ts": "2026-07-13T00:00:01+00:00",
            "event": "external_ai_call",
            "trace_id": "eval:candidate:slow-route",
            "execution_context": "evaluation",
            "surface": "generation",
            "status": "success",
            "latency_ms": 4000,
        },
        {
            "ts": "2026-07-13T00:00:02+00:00",
            "event": "rag_end",
            "trace_id": "eval:candidate:slow-route",
            "execution_context": "evaluation",
            "refusal": False,
            "final_latency_ms": 125000,
        },
    ]
    path.write_text("\n".join(json.dumps(item) for item in events), encoding="utf-8")

    report = snapshot.build_snapshot(path, execution_contexts={"evaluation"})

    assert report["external_ai_latency"]["interaction_routing"] == {
        "call_count": 1,
        "success_count": 1,
        "error_count": 0,
        "cancelled_count": 0,
        "unknown_count": 0,
        "latency_p50_ms": 120000.0,
        "latency_p95_ms": 120000.0,
        "latency_max_ms": 120000.0,
    }
    assert report["external_ai_latency"]["generation"]["latency_p95_ms"] == 4000.0
    assert "prompt" not in json.dumps(report).lower()
    markdown = snapshot.render_markdown(report)
    assert "interaction_routing" in markdown
    assert "120000.0" in markdown


def test_snapshot_reports_voyage_error_and_local_fallback_rate(tmp_path):
    snapshot = _load_snapshot_module()
    path = tmp_path / "trace.jsonl"
    events = [
        {
            "ts": "2026-07-13T00:00:00+00:00", "event": "rerank",
            "trace_id": "p1", "execution_context": "production",
            "backend": "voyage", "status": "success", "fallback": False,
        },
        {
            "ts": "2026-07-13T00:01:00+00:00", "event": "rerank",
            "trace_id": "p2", "execution_context": "production",
            "backend": "voyage", "status": "error", "fallback": True,
            "fallback_backend": "local_fusion", "provider_status_code": 429,
            "retry_attempted": False,
        },
        {
            "ts": "2026-07-13T00:02:00+00:00", "event": "rag_end",
            "trace_id": "p1", "execution_context": "production", "refusal": False,
        },
        {
            "ts": "2026-07-13T00:03:00+00:00", "event": "rag_end",
            "trace_id": "p2", "execution_context": "production", "refusal": False,
        },
    ]
    path.write_text("\n".join(json.dumps(item) for item in events), encoding="utf-8")

    report = snapshot.build_snapshot(path, execution_contexts={"production"})

    assert report["voyage_rerank"] == {
        "call_count": 2,
        "success_count": 1,
        "error_count": 1,
        "fallback_count": 1,
        "error_rate": 0.5,
        "fallback_rate": 0.5,
        "status_codes": {"429": 1},
        "retry_attempt_count": 0,
    }


def test_snapshot_emits_observed_event_and_request_budget_metrics(tmp_path):
    snapshot = _load_snapshot_module()
    path = tmp_path / "trace.jsonl"
    events = [
        {"ts": "2026-07-13T00:00:00+00:00", "event": "query_decomposition",
         "trace_id": "q1", "execution_context": "evaluation",
         "planner_count": 1, "subquery_count": 3},
        {"ts": "2026-07-13T00:00:01+00:00", "event": "graph_retrieval",
         "trace_id": "q1", "execution_context": "evaluation", "edge_count": 12},
        {"ts": "2026-07-13T00:00:02+00:00", "event": "grounded_math_generation",
         "trace_id": "q1", "execution_context": "evaluation", "calculations": 1},
        {"ts": "2026-07-13T00:00:03+00:00", "event": "llm_retry",
         "trace_id": "q1", "execution_context": "evaluation"},
        {"ts": "2026-07-13T00:00:04+00:00", "event": "rag_end",
         "trace_id": "q1", "execution_context": "evaluation", "refusal": False},
    ]
    path.write_text("\n".join(json.dumps(item) for item in events), encoding="utf-8")

    report = snapshot.build_snapshot(path, execution_contexts={"evaluation"})

    assert report["event_counts"]["rag_end"] == 1
    assert report["observed_range"] == {
        "first": "2026-07-13T00:00:04+00:00",
        "last": "2026-07-13T00:00:04+00:00",
    }
    assert report["observed_budget_metrics"] == {
        "max_planner_count": 1, "max_subquery_count": 3,
        "max_correction_count": 0, "max_repair_count": 0,
        "max_calculation_count": 1, "max_graph_edge_count": 12,
        "max_provider_retries": 1,
    }
