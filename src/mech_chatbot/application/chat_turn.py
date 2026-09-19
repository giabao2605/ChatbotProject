"""Deep application module owning one browser chat turn lifecycle."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Iterable, Iterator, Mapping, Protocol, TypeAlias

logger = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class ChatActor:
    user_id: int
    username: str
    roles: frozenset[str]
    department: str | None
    allowed_departments: frozenset[str]
    allowed_sites: frozenset[str]
    max_security_level: str
    response_language: str


@dataclass(frozen=True, slots=True)
class ChatTurnCommand:
    request_id: str
    session_id: str
    question: str
    image_path: Path | None
    history: tuple[Mapping[str, Any], ...]
    current_part_ids: tuple[str, ...]
    conversation_context: Mapping[str, Any] | None


@dataclass(frozen=True, slots=True)
class RagStreamRequest:
    command: ChatTurnCommand
    actor: ChatActor


@dataclass(frozen=True, slots=True)
class ChatThinking:
    message: str


@dataclass(frozen=True, slots=True)
class ChatDelta:
    text: str


@dataclass(frozen=True, slots=True)
class ChatCitation:
    citation: Mapping[str, Any]


@dataclass(frozen=True, slots=True)
class ChatWarning:
    code: str
    message: str
    detail: str | Mapping[str, Any] | None = None


@dataclass(frozen=True, slots=True)
class ChatError:
    code: str
    message: str
    http_status: int | None = None
    retryable: bool = False
    elapsed_ms: int | None = None
    detail: str | Mapping[str, Any] | None = None


@dataclass(frozen=True, slots=True)
class ChatDone:
    chat_id: int | None
    ref_text: str
    citations: tuple[Mapping[str, Any], ...]
    new_part_ids: tuple[str, ...]
    conversation_context: Mapping[str, Any] | None
    elapsed_ms: int | None


ChatTurnEvent: TypeAlias = (
    ChatThinking | ChatDelta | ChatCitation | ChatWarning | ChatError | ChatDone
)


@dataclass(frozen=True, slots=True)
class RagMetadata:
    ref_text: str = ""
    ref_images: tuple[Any, ...] = ()
    debug_info: Mapping[str, Any] | None = None
    new_part_ids: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class RagDelta:
    text: str


@dataclass(frozen=True, slots=True)
class RagCitationCandidate:
    citation: Mapping[str, Any]


@dataclass(frozen=True, slots=True)
class RagStreamError:
    message: str
    detail: str | Mapping[str, Any] | None = None
    http_status: int | None = None
    trace_id: str | None = None
    elapsed_ms: int | None = None


@dataclass(frozen=True, slots=True)
class RagStreamDone:
    trace_id: str | None = None
    elapsed_ms: int | None = None


RagStreamEvent: TypeAlias = (
    RagMetadata | RagDelta | RagCitationCandidate | RagStreamError | RagStreamDone
)


@dataclass(frozen=True, slots=True)
class ChatPersistence:
    command: ChatTurnCommand
    actor: ChatActor
    answer: str
    ref_text: str
    ref_images: tuple[Any, ...]
    evidence: tuple[Mapping[str, Any], ...]
    citations: tuple[Mapping[str, Any], ...]


@dataclass(frozen=True, slots=True)
class ChatAuditRecord:
    action: str
    username: str
    entity_type: str
    entity_id: int | None
    details: Mapping[str, Any]


@dataclass(frozen=True, slots=True)
class PilotReplayInput:
    command: ChatTurnCommand
    actor: ChatActor
    answer: str
    debug_info: Mapping[str, Any]
    trace_id: str
    provider_error: bool = False


class RagStreamPort(Protocol):
    def stream(
        self,
        request: RagStreamRequest,
        route: object | None,
    ) -> Iterable[RagStreamEvent]: ...


class ChatStore(Protocol):
    def persist(self, record: ChatPersistence) -> int | None: ...


class AuditSink(Protocol):
    def record(self, record: ChatAuditRecord) -> None: ...


class PilotExperimentPort(Protocol):
    def assign(self, actor: ChatActor, command: ChatTurnCommand) -> object | None: ...

    def schedule_replay(
        self,
        route: object,
        replay_input: PilotReplayInput,
    ) -> bool: ...


CitationResolver: TypeAlias = Callable[
    [
        Iterable[Mapping[str, Any]],
        Iterable[Mapping[str, Any]],
        str,
    ],
    tuple[tuple[Mapping[str, Any], ...], str],
]


def _mapping_items(value: Any) -> tuple[Mapping[str, Any], ...]:
    if not isinstance(value, (list, tuple)):
        return ()
    return tuple(item for item in value if isinstance(item, Mapping))


def _is_confidential(document: Mapping[str, Any]) -> bool:
    level = str(document.get("security_level") or "").strip().casefold()
    return level not in {"public", "internal"}


class ChatTurnRunner:
    """Own RAG streaming, attribution, persistence, audit and pilot replay."""

    def __init__(
        self,
        *,
        rag_stream: RagStreamPort,
        chat_store: ChatStore,
        audit_sink: AuditSink,
        pilot_experiments: PilotExperimentPort,
        citation_resolver: CitationResolver,
    ) -> None:
        self._rag_stream = rag_stream
        self._chat_store = chat_store
        self._audit_sink = audit_sink
        self._pilot_experiments = pilot_experiments
        self._citation_resolver = citation_resolver

    def stream(
        self,
        command: ChatTurnCommand,
        actor: ChatActor,
    ) -> Iterator[ChatTurnEvent]:
        yield ChatThinking("Đang suy nghĩ")
        try:
            route = self._pilot_experiments.assign(actor, command)
        except ValueError:
            yield ChatError(
                code="pilot_configuration_invalid",
                message="CRAG pilot configuration is invalid",
            )
            return

        answer_parts: list[str] = []
        metadata = RagMetadata()
        streamed_candidates: list[Mapping[str, Any]] = []
        completed: RagStreamDone | None = None

        try:
            rag_request = RagStreamRequest(command=command, actor=actor)
            for event in self._rag_stream.stream(rag_request, route):
                if isinstance(event, RagMetadata):
                    metadata = event
                elif isinstance(event, RagDelta):
                    answer_parts.append(event.text)
                    yield ChatDelta(event.text)
                elif isinstance(event, RagCitationCandidate):
                    streamed_candidates.append(event.citation)
                elif isinstance(event, RagStreamError):
                    self._schedule_replay(
                        route,
                        command,
                        actor,
                        answer="",
                        debug_info=metadata.debug_info or {},
                        trace_id=event.trace_id,
                        provider_error=True,
                    )
                    yield ChatError(
                        code="rag_stream_error",
                        message=event.message,
                        http_status=event.http_status,
                        retryable=event.http_status == 503,
                        elapsed_ms=event.elapsed_ms,
                        detail=event.detail,
                    )
                    return
                elif isinstance(event, RagStreamDone):
                    completed = event

            if completed is None:
                yield ChatError(
                    code="rag_incomplete_stream",
                    message="RAG stream ended without a done event",
                )
                return

            answer = "".join(answer_parts)
            debug_info = (
                metadata.debug_info
                if isinstance(metadata.debug_info, Mapping)
                else {}
            )
            evidence = _mapping_items(debug_info.get("retrieved_docs"))
            citation_docs = _mapping_items(debug_info.get("citation_docs"))
            citations, ref_text = self._citation_resolver(
                citation_docs,
                tuple(streamed_candidates),
                answer,
            )
            citations = tuple(citations)

            self._schedule_replay(
                route,
                command,
                actor,
                answer=answer,
                debug_info=debug_info,
                trace_id=completed.trace_id,
                provider_error=False,
            )
            for citation in citations:
                yield ChatCitation(citation)

            chat_id: int | None = None
            try:
                chat_id = self._chat_store.persist(
                    ChatPersistence(
                        command=command,
                        actor=actor,
                        answer=answer,
                        ref_text=ref_text,
                        ref_images=tuple(metadata.ref_images),
                        evidence=evidence,
                        citations=citations,
                    )
                )
                self._record_audit(command, actor, chat_id, evidence)
            except Exception as exc:
                logger.error("Could not persist chat turn: %s", exc, exc_info=True)
                yield ChatWarning(
                    code="chat_persistence_failed",
                    message="Không lưu được lịch sử chat",
                )

            yield ChatDone(
                chat_id=chat_id,
                ref_text=ref_text,
                citations=citations,
                new_part_ids=tuple(metadata.new_part_ids),
                conversation_context=(
                    debug_info.get("conversation_context")
                    if isinstance(debug_info.get("conversation_context"), Mapping)
                    else None
                ),
                elapsed_ms=completed.elapsed_ms,
            )
        except Exception as exc:
            logger.error("Chat turn failed: %s", exc, exc_info=True)
            yield ChatError(
                code="chat_turn_failed",
                message="Không xử lý được lượt chat",
            )

    def _schedule_replay(
        self,
        route: object | None,
        command: ChatTurnCommand,
        actor: ChatActor,
        *,
        answer: str,
        debug_info: Mapping[str, Any],
        trace_id: str | None,
        provider_error: bool,
    ) -> None:
        if route is None:
            return
        fallback_trace = str(getattr(route, "matched_pair_id", "") or "")
        self._pilot_experiments.schedule_replay(
            route,
            PilotReplayInput(
                command=command,
                actor=actor,
                answer=answer,
                debug_info=debug_info,
                trace_id=str(trace_id or fallback_trace),
                provider_error=provider_error,
            ),
        )

    def _record_audit(
        self,
        command: ChatTurnCommand,
        actor: ChatActor,
        chat_id: int | None,
        evidence: tuple[Mapping[str, Any], ...],
    ) -> None:
        self._audit_sink.record(
            ChatAuditRecord(
                action="chat_query",
                username=actor.username,
                entity_type="LichSuChat",
                entity_id=chat_id,
                details={
                    "prompt": command.question,
                    "session_id": command.session_id,
                },
            )
        )
        confidential_count = sum(_is_confidential(item) for item in evidence)
        if confidential_count:
            self._audit_sink.record(
                ChatAuditRecord(
                    action="read_confidential",
                    username=actor.username,
                    entity_type="LichSuChat",
                    entity_id=chat_id,
                    details={
                        "session_id": command.session_id,
                        "prompt": command.question,
                        "so_tai_lieu_mat": confidential_count,
                    },
                )
            )


__all__ = [
    "AuditSink",
    "ChatActor",
    "ChatAuditRecord",
    "ChatCitation",
    "ChatDelta",
    "ChatDone",
    "ChatError",
    "ChatPersistence",
    "ChatStore",
    "ChatThinking",
    "ChatTurnCommand",
    "ChatTurnEvent",
    "ChatTurnRunner",
    "ChatWarning",
    "PilotExperimentPort",
    "PilotReplayInput",
    "RagCitationCandidate",
    "RagDelta",
    "RagMetadata",
    "RagStreamDone",
    "RagStreamError",
    "RagStreamEvent",
    "RagStreamPort",
    "RagStreamRequest",
]
