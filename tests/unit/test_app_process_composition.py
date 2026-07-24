"""App-process composition contracts for the browser-facing API."""

from __future__ import annotations

import pytest

from mech_chatbot.application.chat_turn import (
    ChatActor,
    ChatTurnCommand,
    RagStreamDone,
    RagStreamRequest,
)
from mech_chatbot.config.settings import Settings


pytestmark = pytest.mark.unit


def test_create_app_captures_typed_process_settings_without_starting_resources():
    from mech_chatbot.api.app_server import create_app

    application = create_app(
        Settings.from_env(
            {
                "APP_THREAD_LIMIT": "7",
                "RAG_SERVER_URL": "http://rag.internal/",
                "RAG_SERVICE_TOKEN": "service-secret",
                "APP_RAG_CHAT_TIMEOUT_SECONDS": "45",
                "APP_SESSION_SECRET": "session-secret",
                "CRAG_PILOT_REPLAY_WORKERS": "3",
                "CRAG_PILOT_REPLAY_QUEUE_SIZE": "5",
                "CRAG_PILOT_REPLAY_TIMEOUT_SECONDS": "90",
            }
        )
    )

    process = application.state.process_settings
    assert process.thread_limit == 7
    assert process.rag_base_url == "http://rag.internal"
    assert process.rag_service_token == "service-secret"
    assert process.rag_chat_timeout_seconds == 45
    assert process.pilot_replay_workers == 3
    assert process.pilot_replay_queue_size == 5
    assert process.pilot_replay_timeout_seconds == 90
    assert application.state.runtime.settings is process
    assert application.state.pilot_replays.executor is None
    assert application.state.pilot_replays.capacity is None
    paths = application.openapi()["paths"]
    assert "/api/health" in paths
    assert "/api/chat/message" in paths


class _SseResponse:
    ok = True

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def iter_lines(self, decode_unicode=True):
        yield "event: done"
        yield 'data: {"trace_id": "trace-captured"}'
        yield ""


def _request() -> RagStreamRequest:
    return RagStreamRequest(
        command=ChatTurnCommand(
            request_id="request-1",
            session_id="session-1",
            question="question",
            image_path=None,
            history=(),
            current_part_ids=(),
            conversation_context=None,
        ),
        actor=ChatActor(
            user_id=1,
            username="alice",
            roles=frozenset({"viewer"}),
            department="CoKhi",
            allowed_departments=frozenset({"CoKhi"}),
            allowed_sites=frozenset({"HN"}),
            max_security_level="internal",
            response_language="vi",
        ),
    )


def test_create_app_builds_rag_transport_from_the_captured_snapshot(monkeypatch):
    from mech_chatbot.api.app_server import create_app

    calls = []

    def post(url, **kwargs):
        calls.append((url, kwargs))
        return _SseResponse()

    application = create_app(
        Settings.from_env(
            {
                "RAG_SERVER_URL": "http://captured-rag/",
                "RAG_SERVICE_TOKEN": "captured-token",
                "APP_SESSION_SECRET": "session-secret",
            }
        ),
        post=post,
    )
    monkeypatch.setenv("RAG_SERVER_URL", "http://later-rag")
    monkeypatch.setenv("RAG_SERVICE_TOKEN", "later-token")

    events = tuple(application.state.runtime.rag_stream.stream(_request(), None))

    assert isinstance(events[-1], RagStreamDone)
    assert events[-1].trace_id == "trace-captured"
    assert calls[0][0] == "http://captured-rag/chat/stream"
    assert calls[0][1]["headers"] == {
        "Content-Type": "application/json",
        "X-RAG-Service-Token": "captured-token",
    }
