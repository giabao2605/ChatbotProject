"""Contract tests for Phase 1 browser-chat system adapters."""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from mech_chatbot.adapters.chat_runtime import (
    HttpRagStreamAdapter,
    PilotExperimentAdapter,
    RepositoryAuditSink,
    RepositoryChatStore,
)
from mech_chatbot.application.chat_turn import (
    ChatActor,
    ChatAuditRecord,
    ChatPersistence,
    ChatTurnCommand,
    PilotReplayInput,
    RagCitationCandidate,
    RagDelta,
    RagMetadata,
    RagStreamDone,
    RagStreamError,
    RagStreamRequest,
)


pytestmark = pytest.mark.unit


class Response:
    def __init__(self, *, status=200, events=(), text=""):
        self.status_code = status
        self.ok = 200 <= status < 400
        self.events = tuple(events)
        self.text = text
        self.closed = False

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        self.closed = True
        return False

    def iter_lines(self, decode_unicode=True):
        for event, payload in self.events:
            yield f"event: {event}"
            yield "data: " + json.dumps(payload)
            yield ""


def actor() -> ChatActor:
    return ChatActor(
        user_id=7,
        username="alice",
        roles=frozenset({"viewer"}),
        department="Technical",
        allowed_departments=frozenset({"Technical"}),
        allowed_sites=frozenset({"HQ"}),
        max_security_level="internal",
        response_language="vi",
    )


def command() -> ChatTurnCommand:
    return ChatTurnCommand(
        request_id="session-1|request-1",
        session_id="session-1",
        question="Approved procedure?",
        image_path=Path("image.png"),
        history=({"role": "user", "content": "previous"},),
        current_part_ids=("P1",),
        conversation_context={"topic": "procedure"},
    )


def test_http_adapter_maps_payload_and_sse_to_typed_events():
    calls = []

    def post(url, **kwargs):
        calls.append((url, kwargs))
        return Response(
            events=(
                ("accepted", {"ok": True}),
                (
                    "metadata",
                    {
                        "ref_text": "upstream",
                        "ref_images": ["page.png"],
                        "new_part_ids": ["P2"],
                        "debug_info": {"route": "technical"},
                    },
                ),
                ("token", {"text": "answer"}),
                ("citation", {"source_id": "D1P1"}),
                ("done", {"trace_id": "trace-1", "elapsed_ms": 12}),
            )
        )

    adapter = HttpRagStreamAdapter(
        post=post,
        base_url="http://rag",
        headers={"X-RAG-Service-Token": "service-token"},
        timeout=(10, 300),
    )
    events = list(adapter.stream(RagStreamRequest(command(), actor()), None))

    assert calls[0][0] == "http://rag/chat/stream"
    assert calls[0][1]["json"]["user_id"] == 7
    assert calls[0][1]["json"]["username"] == "alice"
    assert calls[0][1]["json"]["image_path"] == "image.png"
    assert [type(event) for event in events] == [
        RagMetadata,
        RagDelta,
        RagCitationCandidate,
        RagStreamDone,
    ]
    assert events[-1].trace_id == "trace-1"


def test_http_adapter_maps_busy_without_starting_persistence_contract():
    response = Response(status=503, text="busy")
    adapter = HttpRagStreamAdapter(
        post=lambda *_args, **_kwargs: response,
        base_url="http://rag",
    )

    events = list(adapter.stream(RagStreamRequest(command(), actor()), None))

    assert events == [
        RagStreamError(
            message="RAG server busy",
            detail="busy",
            http_status=503,
        )
    ]
    assert response.closed is True


def test_http_adapter_hides_transport_exception_details():
    adapter = HttpRagStreamAdapter(
        post=lambda *_args, **_kwargs: (_ for _ in ()).throw(
            RuntimeError("internal proxy host")
        ),
        base_url="http://rag",
    )

    events = list(adapter.stream(RagStreamRequest(command(), actor()), None))

    assert events == [RagStreamError(message="Không kết nối được RAG server")]


def test_repository_store_persists_turn_evidence_and_final_sources():
    calls = []
    store = RepositoryChatStore(
        save_chat_history=lambda **kwargs: calls.append(("chat", kwargs)) or 91,
        save_answer_evidence=lambda *args: calls.append(("evidence", args)),
        save_answer_sources=lambda *args: calls.append(("sources", args)),
    )
    citation = {
        "doc_id": 1,
        "file_name": "manual.pdf",
        "version_no": 2,
        "page_no": 3,
        "score": 0.9,
        "source_id": "D1P3",
    }
    record = ChatPersistence(
        command=command(),
        actor=actor(),
        answer="answer",
        ref_text=" refs",
        ref_images=("page.png",),
        evidence=({"doc_id": 1},),
        citations=(citation,),
    )

    assert store.persist(record) == 91
    assert calls[0][1]["bot_msg"] == "answer refs"
    assert calls[1] == ("evidence", (91, [{"doc_id": 1}]))
    assert calls[2][1][1][0]["source_id"] == "D1P3"


def test_audit_adapter_preserves_typed_record_fields():
    calls = []
    sink = RepositoryAuditSink(
        write_audit_log=lambda **kwargs: calls.append(kwargs)
    )

    sink.record(
        ChatAuditRecord(
            action="chat_query",
            username="alice",
            entity_type="LichSuChat",
            entity_id=91,
            details={"session_id": "session-1"},
        )
    )

    assert calls == [
        {
            "username": "alice",
            "action": "chat_query",
            "entity_type": "LichSuChat",
            "entity_id": 91,
            "details": {"session_id": "session-1"},
        }
    ]


def test_pilot_adapter_assigns_and_schedules_from_server_actor():
    route = SimpleNamespace(arm="candidate")
    calls = []
    adapter = PilotExperimentAdapter(
        load_config=lambda: "config",
        assign_route=lambda config, **kwargs: calls.append(
            ("assign", config, kwargs)
        )
        or route,
        outcome=lambda answer, debug, **kwargs: {
            "answer": answer,
            "provider_error": kwargs["provider_error"],
        },
        schedule_replay=lambda *args: calls.append(("replay", args)) or True,
    )

    assigned = adapter.assign(actor(), command())
    scheduled = adapter.schedule_replay(
        route,
        PilotReplayInput(
            command=command(),
            actor=actor(),
            answer="answer",
            debug_info={"route": "technical"},
            trace_id="trace-1",
        ),
    )

    assert assigned is route
    assert calls[0][2]["user_id"] == "7"
    assert calls[0][2]["sites"] == ("HQ",)
    assert scheduled is True
    replay_payload = calls[1][1][1]
    assert replay_payload["username"] == "alice"
    assert replay_payload["response_language"] == "vi"
