"""Composition-root contracts for the browser-chat application runtime."""

from __future__ import annotations

from dataclasses import FrozenInstanceError

import pytest

from mech_chatbot.application.chat_turn import (
    ChatActor,
    ChatDone,
    ChatTurnCommand,
    RagDelta,
    RagStreamDone,
    RagStreamRequest,
)
from mech_chatbot.composition.app_runtime import (
    AppRuntime,
    build_app_runtime,
    build_default_app_runtime,
)
from mech_chatbot.config.settings import AppProcessSettings, Settings


pytestmark = pytest.mark.unit


class ScriptedRag:
    def stream(self, request: RagStreamRequest, route: object | None):
        yield RagDelta("answer")
        yield RagStreamDone(trace_id="trace-1")


class Store:
    def persist(self, record):
        return 41


class Audit:
    def record(self, record) -> None:
        return None


class Pilot:
    def assign(self, actor, command):
        return None

    def schedule_replay(self, route, replay_input):
        return True


class SseResponse:
    ok = True
    status_code = 200

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def iter_lines(self, decode_unicode=True):
        yield "event: token"
        yield 'data: {"text": "answer"}'
        yield ""
        yield "event: done"
        yield 'data: {"trace_id": "trace-1"}'
        yield ""


def _command() -> ChatTurnCommand:
    return ChatTurnCommand(
        request_id="request-1",
        session_id="session-1",
        question="question",
        image_path=None,
        history=(),
        current_part_ids=(),
        conversation_context=None,
    )


def _actor() -> ChatActor:
    return ChatActor(
        user_id=1,
        username="alice",
        roles=frozenset({"viewer"}),
        department="CoKhi",
        allowed_departments=frozenset({"CoKhi"}),
        allowed_sites=frozenset({"HN"}),
        max_security_level="internal",
        response_language="vi",
    )


def _settings() -> AppProcessSettings:
    return AppProcessSettings.from_settings(Settings.from_env({}))


def test_build_app_runtime_keeps_explicit_dependencies_and_streams_with_runner() -> None:
    settings = _settings()
    rag_stream = ScriptedRag()
    chat_store = Store()
    audit_sink = Audit()
    pilot_experiments = Pilot()

    runtime = build_app_runtime(
        settings,
        rag_stream=rag_stream,
        chat_store=chat_store,
        audit_sink=audit_sink,
        pilot_experiments=pilot_experiments,
        citation_resolver=lambda docs, candidates, answer: ((), ""),
    )

    assert isinstance(runtime, AppRuntime)
    assert runtime.settings is settings
    events = tuple(runtime.chat_turn_runner.stream(_command(), _actor()))
    assert isinstance(events[-1], ChatDone)
    assert events[-1].chat_id == 41
    assert runtime.rag_stream is rag_stream
    assert runtime.chat_store is chat_store
    assert runtime.audit_sink is audit_sink
    assert runtime.pilot_experiments is pilot_experiments
    events = tuple(runtime.chat_turn_runner.stream(_command(), _actor()))
    assert isinstance(events[-1], ChatDone)
    assert events[-1].chat_id == 41


def test_app_runtime_is_frozen() -> None:
    runtime = build_app_runtime(
        _settings(),
        rag_stream=ScriptedRag(),
        chat_store=Store(),
        audit_sink=Audit(),
        pilot_experiments=Pilot(),
        citation_resolver=lambda docs, candidates, answer: ((), ""),
    )

    with pytest.raises(FrozenInstanceError):
        runtime.settings = object()


def test_build_default_app_runtime_owns_production_adapter_wiring() -> None:
    settings = _settings()

    runtime = build_default_app_runtime(
        settings,
        post=lambda *_args, **_kwargs: SseResponse(),
        base_url=lambda: "http://rag",
        headers=lambda: {"X-RAG-Service-Token": "token"},
        timeout=lambda: (10, 300),
        save_chat_history=lambda **_kwargs: 41,
        save_answer_evidence=lambda *_args: None,
        save_answer_sources=lambda *_args: None,
        write_audit_log=lambda **_kwargs: None,
        load_pilot_config=lambda: None,
        assign_pilot_route=lambda *_args, **_kwargs: None,
        pilot_outcome=lambda *_args, **_kwargs: {},
        schedule_pilot_replay=lambda *_args: True,
        citation_resolver=lambda docs, candidates, answer: ((), ""),
    )

    assert isinstance(runtime, AppRuntime)
    assert runtime.settings is settings
