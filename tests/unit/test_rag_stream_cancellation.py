import asyncio
from concurrent.futures import ThreadPoolExecutor
import json
import threading

import pytest

from mech_chatbot.api import rag_server
from mech_chatbot.llm.external_ai import ExternalAICallCancelled


pytestmark = pytest.mark.unit


def test_legacy_admin_global_read_audit_contains_no_raw_prompt(monkeypatch):
    from mech_chatbot import services

    writes = []
    monkeypatch.setattr(services, "write_audit_log", lambda *args, **kwargs: writes.append((args, kwargs)))
    profile = {"username": "legacy-admin", "user_id": 9, "roles": ["admin"]}

    rag_server._audit_admin_query(
        profile,
        "trace-admin",
        "chat",
        outcome="completed",
        debug_info={"retrieved_docs": [{"doc_id": 4, "security_level": "confidential"}]},
    )

    payload = writes[0][0][4]
    assert payload == {
        "trace_id": "trace-admin",
        "surface": "chat",
        "outcome": "completed",
        "doc_ids": [4],
        "security_levels": ["confidential"],
    }
    assert "question" not in payload


def test_client_disconnect_cancels_stream_and_releases_rag_permit(monkeypatch):
    """The SSE generator must signal the worker, then its done callback frees capacity."""
    observed_cancel = threading.Event()
    audit_outcomes = []
    executor = ThreadPoolExecutor(max_workers=1)
    semaphore = asyncio.Semaphore(1)

    monkeypatch.setattr(rag_server, "_rag_ready", True)
    monkeypatch.setattr(rag_server, "_rag_executor", executor)
    monkeypatch.setattr(rag_server, "_rag_semaphore", semaphore)
    monkeypatch.setattr(
        rag_server,
        "resolve_user_profile",
        lambda _req: {
            "username": "admin-test",
            "user_id": 1,
            "roles": ["admin"],
            "department": "Technical",
            "allowed_departments": ["Technical"],
            "allowed_sites": ["HQ"],
            "max_security_level": "confidential",
        },
    )
    monkeypatch.setattr(
        rag_server,
        "_audit_admin_query",
        lambda _profile, _trace, _surface, *, outcome, debug_info=None: audit_outcomes.append(outcome),
    )

    def fake_open(_req, _profile, trace_id=None, cancel_event=None):
        del trace_id

        def stream():
            yield "first token"
            assert cancel_event is not None
            if not cancel_event.wait(timeout=2):
                raise AssertionError("client disconnect was not forwarded to worker")
            observed_cancel.set()
            raise ExternalAICallCancelled("cancelled by test client")

        return stream(), "", [], [], {"citation_docs": []}

    monkeypatch.setattr(rag_server, "_open_rag_stream", fake_open)

    async def scenario():
        response = await rag_server.chat_stream_endpoint(
            rag_server.ChatRequest(user_question="test disconnect", username="admin-test")
        )
        iterator = response.body_iterator
        seen_token = False
        while not seen_token:
            event = await anext(iterator)
            seen_token = "event: token" in event
        await iterator.aclose()

        for _ in range(100):
            if observed_cancel.is_set() and getattr(semaphore, "_value", 0) == 1:
                return
            await asyncio.sleep(0.01)
        raise AssertionError("cancelled worker did not release RAG semaphore")

    try:
        asyncio.run(scenario())
    finally:
        executor.shutdown(wait=True)

    assert observed_cancel.is_set()
    assert audit_outcomes == ["cancelled"]


def test_stream_done_exposes_numeric_trace_stages_for_benchmark(monkeypatch):
    executor = ThreadPoolExecutor(max_workers=1)
    semaphore = asyncio.Semaphore(1)
    monkeypatch.setattr(rag_server, "_rag_ready", True)
    monkeypatch.setattr(rag_server, "_rag_executor", executor)
    monkeypatch.setattr(rag_server, "_rag_semaphore", semaphore)
    monkeypatch.setattr(
        rag_server,
        "resolve_user_profile",
        lambda _req: {
            "username": "viewer-test",
            "user_id": 2,
            "roles": ["viewer"],
            "department": "Technical",
            "allowed_departments": ["Technical"],
            "allowed_sites": ["HQ"],
            "max_security_level": "internal",
        },
    )
    monkeypatch.setattr(
        rag_server,
        "_open_rag_stream",
        lambda *_args, **_kwargs: (iter(["safe answer"]), "", [], [], {"citation_docs": []}),
    )
    monkeypatch.setattr(
        rag_server,
        "pop_trace_stage_metrics",
        lambda _trace: {
            "dense_retrieval": {"latency_ms": 12},
            "rrf_grouping": {"latency_ms": 2},
        },
    )

    async def scenario():
        response = await rag_server.chat_stream_endpoint(
            rag_server.ChatRequest(user_question="test trace", username="viewer-test")
        )
        done = None
        async for event in response.body_iterator:
            if "event: done" in event:
                done = event
        assert done is not None
        payload = json.loads(done.split("data: ", 1)[1])
        return payload

    try:
        payload = asyncio.run(scenario())
    finally:
        executor.shutdown(wait=True)

    assert payload["trace_id"].startswith("rag_")
    assert payload["trace_stages"]["dense_retrieval"] == {"latency_ms": 12}
    assert payload["trace_stages"]["rrf_grouping"] == {"latency_ms": 2}
    assert payload["trace_stages"]["first_token"]["latency_ms"] >= 0
    assert payload["trace_stages"]["completion"]["latency_ms"] >= 0
