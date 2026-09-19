import asyncio

import pytest

from mech_chatbot.config import logging as trace_logging


pytestmark = pytest.mark.unit


def test_trace_stage_metrics_aggregate_cache_and_do_not_double_count_embed():
    trace_id = "test-benchmark-stage"
    with trace_logging.bind_trace_runtime(trace_logging.TraceRuntime()):
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


def test_concurrent_trace_contexts_do_not_share_accumulators():
    async def collect(latency):
        with trace_logging.bind_trace_runtime(trace_logging.TraceRuntime()):
            trace_logging.log_trace(
                "dense_retrieval",
                "same-trace-id",
                latency_ms=latency,
            )
            await asyncio.sleep(0)
            return trace_logging.pop_trace_stage_metrics("same-trace-id")

    async def run_concurrently():
        return await asyncio.gather(collect(11), collect(29))

    first, second = asyncio.run(run_concurrently())

    assert first == {"dense_retrieval": {"latency_ms": 11}}
    assert second == {"dense_retrieval": {"latency_ms": 29}}
