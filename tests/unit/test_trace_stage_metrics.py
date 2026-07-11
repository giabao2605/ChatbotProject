import pytest

from mech_chatbot.config import logging as trace_logging


pytestmark = pytest.mark.unit


def test_trace_stage_metrics_aggregate_cache_and_do_not_double_count_embed(monkeypatch):
    trace_id = "test-benchmark-stage"
    monkeypatch.setattr(trace_logging, "_TRACE_ACC", {})
    monkeypatch.setattr(trace_logging, "_TRACE_STAGE_METRICS", {})

    trace_logging.log_trace("cache", trace_id, latency_ms=3)
    trace_logging.log_trace("cache", trace_id, latency_ms=7)
    trace_logging.log_trace("embed", trace_id, latency_ms=4)
    trace_logging.log_trace("embed", trace_id, latency_ms=9)
    trace_logging.log_trace("dense_retrieval", trace_id, latency_ms=12)

    assert trace_logging.pop_trace_stage_metrics(trace_id) == {
        "cache": {"latency_ms": 10},
        "embed": {"latency_ms": 9},
        "dense_retrieval": {"latency_ms": 12},
    }
    assert trace_logging.pop_trace_stage_metrics(trace_id) == {}
