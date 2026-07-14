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


def test_pilot_replay_header_disables_cache_inside_worker(monkeypatch):
    from mech_chatbot.rag import semantic_cache
    from mech_chatbot.evaluation.crag_pilot import (
        PilotConfig,
        assign_pilot_route,
        build_replay_request,
    )

    executor = ThreadPoolExecutor(max_workers=1)
    semaphore = asyncio.Semaphore(1)
    observed = []
    monkeypatch.setenv("SEMANTIC_CACHE_ENABLED", "true")
    monkeypatch.setenv("CRAG_PILOT_ASSIGNMENT_SALT", "pilot-test-salt")
    monkeypatch.setenv("RAG_DEPLOYMENT_ID", "candidate-1")
    monkeypatch.setattr(rag_server, "_rag_ready", True)
    monkeypatch.setattr(rag_server, "_rag_executor", executor)
    monkeypatch.setattr(rag_server, "_rag_semaphore", semaphore)
    monkeypatch.setattr(
        rag_server,
        "resolve_user_profile",
        lambda _req: {
            "username": "viewer-test", "user_id": 2, "roles": ["viewer"],
            "department": "Technical", "allowed_departments": ["Technical"],
            "allowed_sites": ["HQ"], "max_security_level": "internal",
        },
    )

    def fake_open(*_args, **_kwargs):
        observed.append(semantic_cache.enabled())
        return iter(["replay answer"]), "", [], [], {"citation_docs": []}

    monkeypatch.setattr(rag_server, "_open_rag_stream", fake_open)
    route = assign_pilot_route(
        PilotConfig(
            experiment_id="exp-1",
            assignment_salt="pilot-test-salt",
            eligible_department="Technical",
            cohort_sha256="cohort-v1",
            control_url="http://control",
            candidate_url="http://candidate",
            control_deployment_id="control-1",
            candidate_deployment_id="candidate-1",
            snapshot_fingerprint="snapshot-v1",
        ),
        user_id="2",
        department="Technical",
        request_id="request-1",
    )
    if route.opposite_deployment_id != "candidate-1":
        monkeypatch.setenv("RAG_DEPLOYMENT_ID", route.opposite_deployment_id)
    replay = build_replay_request(
        route,
        {"user_question": "replay", "username": "viewer-test"},
        original_trace_id="rag-original",
    )

    async def scenario():
        response = await rag_server.chat_stream_endpoint(
            rag_server.ChatRequest(user_question="replay", username="viewer-test"),
            x_rag_pilot_replay="true",
            x_rag_pilot_experiment_id="exp-1",
            x_rag_matched_pair_id=route.matched_pair_id,
            x_rag_original_trace_id="rag-original",
            x_rag_assigned_arm=route.arm,
            x_rag_pilot_payload_sha256=replay.headers["X-RAG-Pilot-Payload-SHA256"],
            x_rag_pilot_replay_nonce=replay.headers["X-RAG-Pilot-Replay-Nonce"],
            x_rag_pilot_replay_expires=replay.headers["X-RAG-Pilot-Replay-Expires"],
            x_rag_pilot_replay_signature=replay.headers["X-RAG-Pilot-Replay-Signature"],
        )
        async for _event in response.body_iterator:
            pass

    try:
        asyncio.run(scenario())
    finally:
        executor.shutdown(wait=True)

    assert observed == [False]
    assert semantic_cache.enabled() is True
