from __future__ import annotations

import json

import pytest

from scripts.eval.crag_latency_breakdown import build_latency_breakdown


pytestmark = pytest.mark.unit


def _write_trace(path, rows):
    path.write_text(
        "\n".join(json.dumps(row, ensure_ascii=False) for row in rows) + "\n",
        encoding="utf-8",
    )


def test_latency_breakdown_groups_stages_and_hashes_trace_ids(tmp_path):
    trace = tmp_path / "rag_trace.jsonl"
    base = {"ts": "2026-07-18T01:00:00+00:00", "execution_context": "evaluation"}
    _write_trace(
        trace,
        [
            {**base, "event": "retrieval", "trace_id": "raw-trace", "latency_ms": 100, "question": "secret"},
            {**base, "event": "corrective_retrieval", "trace_id": "raw-trace", "latency_ms": 50},
            {**base, "event": "rerank", "trace_id": "raw-trace", "latency_ms": 20},
            {**base, "event": "parent_context", "trace_id": "raw-trace", "latency_ms": 30},
            {**base, "event": "llm_generation", "trace_id": "raw-trace", "latency_ms": 200},
            {**base, "event": "claim_repair", "trace_id": "raw-trace", "latency_ms": 70},
            {**base, "event": "rag_end", "trace_id": "raw-trace", "final_latency_ms": 500},
        ],
    )

    report = build_latency_breakdown(trace, execution_contexts={"evaluation"})

    assert report["schema"] == "crag-latency-breakdown-v1"
    assert report["query_count"] == 1
    row = report["traces"][0]
    assert row["trace_id_sha256"] != "raw-trace"
    assert len(row["trace_id_sha256"]) == 64
    assert row["stages_ms"] == {
        "retrieval": 100,
        "parent_context": 30,
        "rerank": 20,
        "generation": 200,
        "correction": 50,
        "claim_repair": 70,
        "total": 500,
    }
    serialized = json.dumps(report, ensure_ascii=False)
    assert "raw-trace" not in serialized
    assert "secret" not in serialized
    assert str(tmp_path) not in serialized


def test_latency_breakdown_filters_utc_window_and_sums_repeated_stage_events(tmp_path):
    trace = tmp_path / "rag_trace.jsonl"
    _write_trace(
        trace,
        [
            {"ts": "2026-07-18T00:59:59Z", "execution_context": "evaluation", "event": "rag_end", "trace_id": "before", "final_latency_ms": 999},
            {"ts": "2026-07-18T01:00:01Z", "execution_context": "evaluation", "event": "retrieval", "trace_id": "inside", "latency_ms": 40},
            {"ts": "2026-07-18T01:00:02Z", "execution_context": "evaluation", "event": "retrieval", "trace_id": "inside", "latency_ms": 60},
            {"ts": "2026-07-18T01:00:03Z", "execution_context": "evaluation", "event": "rag_end", "trace_id": "inside", "final_latency_ms": 150},
        ],
    )

    report = build_latency_breakdown(
        trace,
        start="2026-07-18T01:00:00Z",
        end="2026-07-18T01:01:00Z",
        execution_contexts={"evaluation"},
    )

    assert report["query_count"] == 1
    assert report["traces"][0]["stages_ms"]["retrieval"] == 100
    assert report["stage_summary"]["retrieval"]["latency_p95_ms"] == 100
