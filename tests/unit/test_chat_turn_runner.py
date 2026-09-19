"""Contract tests for the Phase 1 browser-chat application seam."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from typing import Any, Iterable, Mapping

import pytest

from mech_chatbot.application.chat_turn import (
    ChatActor,
    ChatCitation,
    ChatDelta,
    ChatDone,
    ChatError,
    ChatThinking,
    ChatTurnCommand,
    ChatTurnRunner,
    ChatWarning,
    RagCitationCandidate,
    RagDelta,
    RagMetadata,
    RagStreamDone,
    RagStreamError,
    RagStreamRequest,
)


pytestmark = pytest.mark.unit


class ScriptedRagStream:
    def __init__(self, events: Iterable[object]) -> None:
        self._events = tuple(events)
        self.requests: list[tuple[RagStreamRequest, object | None]] = []

    def stream(
        self, request: RagStreamRequest, route: object | None
    ) -> Iterable[object]:
        self.requests.append((request, route))
        yield from self._events


class RecordingChatStore:
    def __init__(self, *, chat_id: int | None = 123, error: Exception | None = None):
        self.chat_id = chat_id
        self.error = error
        self.records: list[object] = []

    def persist(self, record: object) -> int | None:
        self.records.append(record)
        if self.error is not None:
            raise self.error
        return self.chat_id


class RecordingAuditSink:
    def __init__(self) -> None:
        self.records: list[object] = []

    def record(self, record: object) -> None:
        self.records.append(record)


class ScriptedPilotExperiments:
    def __init__(self, route: object | None = None, *, scheduled: bool = True):
        self.route = route
        self.scheduled = scheduled
        self.assignments: list[tuple[ChatActor, ChatTurnCommand]] = []
        self.replays: list[tuple[object, object]] = []

    def assign(self, actor: ChatActor, command: ChatTurnCommand) -> object | None:
        self.assignments.append((actor, command))
        return self.route

    def schedule_replay(self, route: object, replay_input: object) -> bool:
        self.replays.append((route, replay_input))
        return self.scheduled


class ScriptedCitationResolver:
    def __init__(
        self,
        citations: tuple[Mapping[str, Any], ...] = (),
        ref_text: str = "",
    ) -> None:
        self.result = (citations, ref_text)
        self.requests: list[
            tuple[
                tuple[Mapping[str, Any], ...],
                tuple[Mapping[str, Any], ...],
                str,
            ]
        ] = []

    def __call__(
        self,
        citation_docs: Iterable[Mapping[str, Any]],
        streamed_candidates: Iterable[Mapping[str, Any]],
        answer: str,
    ) -> tuple[tuple[Mapping[str, Any], ...], str]:
        self.requests.append(
            (tuple(citation_docs), tuple(streamed_candidates), answer)
        )
        return self.result


def _actor() -> ChatActor:
    return ChatActor(
        user_id=7,
        username="alice",
        roles=frozenset({"viewer"}),
        department="CoKhi",
        allowed_departments=frozenset({"CoKhi"}),
        allowed_sites=frozenset({"HN"}),
        max_security_level="confidential",
        response_language="vi",
    )


def _command() -> ChatTurnCommand:
    return ChatTurnCommand(
        request_id="session-1|request-1",
        session_id="session-1",
        question="Quy trình BOM?",
        image_path=Path("chat-image.png"),
        history=({"role": "user", "content": "Câu trước"},),
        current_part_ids=("OLD",),
        conversation_context={"previous": True},
    )


def _runner(
    events: Iterable[object],
    *,
    store: RecordingChatStore | None = None,
    audit: RecordingAuditSink | None = None,
    pilot: ScriptedPilotExperiments | None = None,
    citations: ScriptedCitationResolver | None = None,
) -> tuple[
    ChatTurnRunner,
    ScriptedRagStream,
    RecordingChatStore,
    RecordingAuditSink,
    ScriptedPilotExperiments,
    ScriptedCitationResolver,
]:
    rag = ScriptedRagStream(events)
    store = store or RecordingChatStore()
    audit = audit or RecordingAuditSink()
    pilot = pilot or ScriptedPilotExperiments()
    citations = citations or ScriptedCitationResolver()
    runner = ChatTurnRunner(
        rag_stream=rag,
        chat_store=store,
        audit_sink=audit,
        pilot_experiments=pilot,
        citation_resolver=citations,
    )
    return runner, rag, store, audit, pilot, citations


def _success_events(*, debug_info: Mapping[str, Any] | None = None) -> tuple[object, ...]:
    return (
        RagMetadata(
            ref_text="untrusted upstream refs",
            ref_images=("preview.png",),
            debug_info=debug_info or {},
            new_part_ids=("P123",),
        ),
        RagDelta("Câu trả lời "),
        RagDelta("đã kiểm chứng [SourceID D42P3]"),
        RagStreamDone(trace_id="rag-trace-1", elapsed_ms=25),
    )


def test_success_orders_events_and_aggregates_the_answer() -> None:
    runner, _rag, store, _audit, _pilot, _citations = _runner(_success_events())

    events = list(runner.stream(_command(), _actor()))

    assert [type(event) for event in events] == [
        ChatThinking,
        ChatDelta,
        ChatDelta,
        ChatDone,
    ]
    assert [event.text for event in events if isinstance(event, ChatDelta)] == [
        "Câu trả lời ",
        "đã kiểm chứng [SourceID D42P3]",
    ]
    assert store.records[0].answer == (
        "Câu trả lời đã kiểm chứng [SourceID D42P3]"
    )
    assert events[-1].elapsed_ms == 25
    assert events[-1].new_part_ids == ("P123",)


def test_final_citations_and_ref_text_are_attributed_from_the_complete_answer() -> None:
    citation_doc = {
        "doc_id": 42,
        "trang": 3,
        "file_goc": "bom.pdf",
        "source_id": "D42P3",
    }
    streamed = {"doc_id": 99, "page_no": 1, "source_id": "D99P1"}
    attributed = {
        "doc_id": 42,
        "page_no": 3,
        "file_name": "bom.pdf",
        "source_id": "D42P3",
    }
    resolver = ScriptedCitationResolver((attributed,), "\nNguồn: bom.pdf, Trang 3")
    events = (
        RagMetadata(debug_info={"citation_docs": [citation_doc]}),
        RagCitationCandidate(streamed),
        RagDelta("Đáp án [SourceID D42P3]"),
        RagStreamDone(elapsed_ms=9),
    )
    runner, _rag, _store, _audit, _pilot, resolver = _runner(
        events, citations=resolver
    )

    output = list(runner.stream(_command(), _actor()))

    assert [type(event) for event in output] == [
        ChatThinking,
        ChatDelta,
        ChatCitation,
        ChatDone,
    ]
    assert resolver.requests == [
        ((citation_doc,), (streamed,), "Đáp án [SourceID D42P3]")
    ]
    assert [event.citation for event in output if isinstance(event, ChatCitation)] == [
        attributed
    ]
    done = output[-1]
    assert isinstance(done, ChatDone)
    assert done.ref_text == "\nNguồn: bom.pdf, Trang 3"
    assert done.citations == (attributed,)


def test_success_persists_evidence_sources_and_confidential_access_audit() -> None:
    retrieved = {
        "doc_id": 42,
        "trang": 3,
        "file_goc": "bom.pdf",
        "security_level": "confidential",
    }
    citation = {
        "doc_id": 42,
        "page_no": 3,
        "file_name": "bom.pdf",
        "source_id": "D42P3",
    }
    resolver = ScriptedCitationResolver((citation,), "\nNguồn: bom.pdf, Trang 3")
    runner, _rag, store, audit, _pilot, _resolver = _runner(
        _success_events(debug_info={"retrieved_docs": [retrieved]}),
        citations=resolver,
    )

    output = list(runner.stream(_command(), _actor()))

    assert len(store.records) == 1
    persisted = store.records[0]
    assert persisted.answer == "Câu trả lời đã kiểm chứng [SourceID D42P3]"
    assert persisted.ref_text == "\nNguồn: bom.pdf, Trang 3"
    assert persisted.evidence == (retrieved,)
    assert persisted.citations == (citation,)
    assert [record.action for record in audit.records] == [
        "chat_query",
        "read_confidential",
    ]
    assert isinstance(output[-1], ChatDone)
    assert output[-1].chat_id == 123


def test_unknown_evidence_security_level_is_audited_as_confidential() -> None:
    retrieved = {"doc_id": 42, "trang": 3, "file_goc": "bom.pdf"}
    runner, _rag, _store, audit, _pilot, _resolver = _runner(
        _success_events(debug_info={"retrieved_docs": [retrieved]})
    )

    list(runner.stream(_command(), _actor()))

    assert [record.action for record in audit.records] == [
        "chat_query",
        "read_confidential",
    ]


def test_rag_busy_fails_closed_without_persistence_or_audit() -> None:
    runner, _rag, store, audit, _pilot, _citations = _runner(
        (
            RagStreamError(
                message="RAG server busy",
                detail="provider detail must not become a test contract",
                http_status=503,
            ),
        )
    )

    output = list(runner.stream(_command(), _actor()))

    assert [type(event) for event in output] == [ChatThinking, ChatError]
    assert output[-1].http_status == 503
    assert output[-1].retryable is True
    assert store.records == []
    assert audit.records == []


def test_stream_without_done_fails_closed_after_visible_deltas() -> None:
    runner, _rag, store, audit, _pilot, _citations = _runner(
        (RagDelta("partial answer"),)
    )

    output = list(runner.stream(_command(), _actor()))

    assert [type(event) for event in output] == [
        ChatThinking,
        ChatDelta,
        ChatError,
    ]
    assert output[-1].code == "rag_incomplete_stream"
    assert not any(isinstance(event, ChatDone) for event in output)
    assert store.records == []
    assert audit.records == []


def test_persistence_failure_warns_then_completes_the_user_response() -> None:
    store = RecordingChatStore(error=RuntimeError("database host and SQL details"))
    runner, _rag, store, _audit, _pilot, _citations = _runner(
        _success_events(), store=store
    )

    output = list(runner.stream(_command(), _actor()))

    assert [type(event) for event in output] == [
        ChatThinking,
        ChatDelta,
        ChatDelta,
        ChatWarning,
        ChatDone,
    ]
    warning = output[-2]
    assert warning.code == "chat_persistence_failed"
    assert warning.message == "Không lưu được lịch sử chat"
    assert warning.detail is None
    assert output[-1].chat_id is None


@pytest.mark.parametrize("scheduled", [True, False], ids=["scheduled", "dropped"])
def test_pilot_assignment_and_replay_do_not_change_the_user_stream(
    scheduled: bool,
) -> None:
    route = SimpleNamespace(arm="candidate", matched_pair_id="pair-1")
    pilot = ScriptedPilotExperiments(route, scheduled=scheduled)
    runner, rag, store, _audit, pilot, _citations = _runner(
        _success_events(debug_info={"route": "technical"}), pilot=pilot
    )

    output = list(runner.stream(_command(), _actor()))

    assert pilot.assignments == [(_actor(), _command())]
    assert rag.requests == [(RagStreamRequest(_command(), _actor()), route)]
    assert len(pilot.replays) == 1
    assert pilot.replays[0][0] is route
    assert pilot.replays[0][1].trace_id == "rag-trace-1"
    assert [type(event) for event in output] == [
        ChatThinking,
        ChatDelta,
        ChatDelta,
        ChatDone,
    ]
    assert len(store.records) == 1
