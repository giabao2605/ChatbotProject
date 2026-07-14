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
