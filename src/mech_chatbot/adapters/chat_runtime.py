"""Production adapters for the application-owned chat turn ports.

The adapters deliberately keep framework and persistence details at the
boundary.  Every side effect is injectable, which makes the HTTP/SQL seams
deterministic in unit tests and lets the API composition root retain its
existing monkeypatch points.
"""

from __future__ import annotations

import json
from collections.abc import Callable, Iterable, Iterator, Mapping
from typing import Any

from mech_chatbot.application.chat_turn import (
    ChatActor,
    ChatPersistence,
    ChatTurnCommand,
    ChatAuditRecord,
    PilotReplayInput,
    RagCitationCandidate,
    RagDelta,
    RagMetadata,
    RagStreamDone,
    RagStreamError,
    RagStreamEvent,
    RagStreamRequest,
)


def _default_post() -> Callable[..., Any]:
    # Import lazily so importing the application runtime does not initialise
    # an HTTP client (and so tests need not install requests just to use SQL
    # or pilot adapters).
    import requests

    return requests.post


def _default_base_url() -> str:
    return "http://127.0.0.1:8100"


def _default_headers() -> dict[str, str]:
    return {"Content-Type": "application/json"}


def _sse_events(response: Any) -> Iterator[tuple[str, Any]]:
    """Parse SSE records using the same tolerant rules as the legacy router."""

    event_name = "message"
    data_lines: list[str] = []
    for raw_line in response.iter_lines(decode_unicode=True):
        line = raw_line.decode("utf-8") if isinstance(raw_line, bytes) else str(raw_line or "")
        if not line:
            if data_lines:
                yield event_name, _decode_sse_payload("\n".join(data_lines))
            event_name = "message"
            data_lines = []
            continue
        if line.startswith(":"):
            continue
        if line.startswith("event:"):
            event_name = line[6:].strip() or "message"
        elif line.startswith("data:"):
            data_lines.append(line[5:].lstrip())
    if data_lines:
        yield event_name, _decode_sse_payload("\n".join(data_lines))


def _decode_sse_payload(raw_data: str) -> Any:
    try:
        return json.loads(raw_data)
    except (TypeError, ValueError):
        return {"message": raw_data}


def _as_mapping(value: Any) -> Mapping[str, Any]:
    return value if isinstance(value, Mapping) else {}


def _default_payload(request: RagStreamRequest) -> dict[str, Any]:
    """Build the RAG request body from the application command.

    Both command data and the server-resolved actor are application-owned
    values, so the adapter never trusts browser-supplied RBAC fields.
    """

    command = request.command
    actor = request.actor
    return {
        "user_id": actor.user_id,
        "username": actor.username,
        "user_question": command.question,
        "image_path": str(command.image_path) if command.image_path is not None else None,
        "chat_history": [dict(item) for item in command.history],
        "current_part_ids": list(command.current_part_ids),
        "response_language": actor.response_language,
        "conversation_context": (
            dict(command.conversation_context)
            if isinstance(command.conversation_context, Mapping)
            else command.conversation_context
        ),
    }


class HttpRagStreamAdapter:
    """Translate the internal RAG HTTP/SSE stream to typed application events."""

    def __init__(
        self,
        *,
        post: Callable[..., Any] | None = None,
        base_url: str | Callable[[], str] | None = None,
        headers: Mapping[str, str] | Callable[[], Mapping[str, str]] | None = None,
        timeout: Any | Callable[[], Any] = (10, 300),
        payload_builder: Callable[[RagStreamRequest, object | None], Mapping[str, Any]] | None = None,
    ) -> None:
        self._post = post or _default_post()
        self._base_url = base_url or _default_base_url
        self._headers = headers or _default_headers
        self._timeout = timeout
        self._payload_builder = payload_builder or (lambda request, _route: _default_payload(request))

    def stream(
        self,
        request: RagStreamRequest,
        route: object | None,
    ) -> Iterable[RagStreamEvent]:
        payload = dict(self._payload_builder(request, route))
        base_url = self._base_url() if callable(self._base_url) else str(self._base_url)
        target_url = str(getattr(route, "deployment_url", "") or base_url).rstrip("/")
        header_value = self._headers() if callable(self._headers) else self._headers
        request_headers = dict(header_value)
        request_timeout = self._timeout() if callable(self._timeout) else self._timeout
        try:
            response = self._post(
                f"{target_url}/chat/stream",
                headers=request_headers,
                json=payload,
                timeout=request_timeout,
                stream=True,
            )
            with response as stream_response:
                ok = bool(getattr(stream_response, "ok", True))
                if not ok:
                    status_code = _status_code(stream_response)
                    message = (
                        "RAG server busy"
                        if status_code == 503
                        else f"RAG server error HTTP {status_code}"
                    )
                    yield RagStreamError(
                        message=message,
                        detail=getattr(stream_response, "text", None),
                        http_status=status_code,
                    )
                    return
                for event_name, raw_payload in _sse_events(stream_response):
                    yield from _map_sse_event(event_name, raw_payload)
        except Exception as exc:
            # Transport exceptions are typed at this boundary.  The runner
            # then applies the same no-persistence/error behavior as provider
            # error SSE events without exposing a raw traceback to the client.
            yield RagStreamError(message="Không kết nối được RAG server")


def _status_code(response: Any) -> int | None:
    try:
        return int(response.status_code)
    except (AttributeError, TypeError, ValueError):
        return None


def _map_sse_event(event_name: str, payload: Any) -> Iterable[RagStreamEvent]:
    data = _as_mapping(payload)
    name = str(event_name or "message").strip().casefold()
    if name == "metadata":
        debug_info = data.get("debug_info")
        yield RagMetadata(
            ref_text=str(data.get("ref_text") or ""),
            ref_images=tuple(data.get("ref_images") or ()) if isinstance(data.get("ref_images") or (), (list, tuple)) else (),
            debug_info=debug_info if isinstance(debug_info, Mapping) else None,
            new_part_ids=tuple(str(item) for item in (data.get("new_part_ids") or ()) if item is not None)
            if isinstance(data.get("new_part_ids") or (), (list, tuple))
            else (),
        )
    elif name in {"token", "delta"}:
        yield RagDelta(text=str(data.get("text") or ""))
    elif name == "citation":
        yield RagCitationCandidate(citation=dict(data))
    elif name == "error":
        yield RagStreamError(
            message=str(data.get("message") or "RAG stream error"),
            detail=dict(data),
            http_status=_optional_int(data.get("status", data.get("http_status"))),
            trace_id=_optional_str(data.get("trace_id")),
            elapsed_ms=_optional_int(data.get("elapsed_ms")),
        )
    elif name == "done":
        yield RagStreamDone(
            trace_id=_optional_str(data.get("trace_id")),
            elapsed_ms=_optional_int(data.get("elapsed_ms")),
        )


def _optional_str(value: Any) -> str | None:
    return None if value in (None, "") else str(value)


def _optional_int(value: Any) -> int | None:
    try:
        return None if value in (None, "") else int(value)
    except (TypeError, ValueError):
        return None


class RepositoryChatStore:
    """Persist a completed turn, its full evidence basis, and final sources."""

    def __init__(
        self,
        *,
        save_chat_history: Callable[..., int | None],
        save_answer_evidence: Callable[..., Any],
        save_answer_sources: Callable[..., Any],
    ) -> None:
        self._save_chat_history = save_chat_history
        self._save_answer_evidence = save_answer_evidence
        self._save_answer_sources = save_answer_sources

    def persist(self, record: ChatPersistence) -> int | None:
        chat_id = self._save_chat_history(
            session_id=record.command.session_id,
            user_msg=record.command.question,
            bot_msg=record.answer + record.ref_text,
            image_path=(
                str(record.command.image_path)
                if record.command.image_path is not None
                else None
            ),
            ref_images=list(record.ref_images),
            username=record.actor.username,
        )
        if not chat_id:
            return None
        self._save_answer_evidence(chat_id, list(record.evidence))
        if record.citations:
            persisted_sources = [
                {
                    "doc_id": citation.get("doc_id"),
                    "file_goc": citation.get("file_name"),
                    "version_no": citation.get("version_no"),
                    "trang": citation.get("page_no"),
                    "score": citation.get("score"),
                    "source_id": citation.get("source_id"),
                }
                for citation in record.citations
            ]
            self._save_answer_sources(chat_id, persisted_sources)
        return chat_id


class RepositoryAuditSink:
    """Persist application audit records through the existing repository API."""

    def __init__(self, *, write_audit_log: Callable[..., Any]) -> None:
        self._write_audit_log = write_audit_log

    def record(self, record: ChatAuditRecord) -> None:
        self._write_audit_log(
            username=record.username,
            action=record.action,
            entity_type=record.entity_type,
            entity_id=record.entity_id,
            details=dict(record.details),
        )


class PilotExperimentAdapter:
    """Delegate pilot assignment and bounded replay to control-plane callbacks."""

    def __init__(
        self,
        *,
        load_config: Callable[[], object | None],
        assign_route: Callable[..., object],
        outcome: Callable[..., Mapping[str, Any]],
        schedule_replay: Callable[..., bool],
        payload_builder: Callable[[ChatTurnCommand, ChatActor], Mapping[str, Any]] | None = None,
    ) -> None:
        self._load_config = load_config
        self._assign_route = assign_route
        self._outcome = outcome
        self._schedule_replay = schedule_replay
        self._payload_builder = payload_builder or _pilot_payload

    def assign(self, actor: ChatActor, command: ChatTurnCommand) -> object | None:
        config = self._load_config()
        if config is None:
            return None
        return self._assign_route(
            config,
            user_id=str(actor.user_id),
            department=str(actor.department or ""),
            request_id=command.request_id,
            sites=tuple(actor.allowed_sites),
        )

    def schedule_replay(self, route: object, replay_input: PilotReplayInput) -> bool:
        debug = dict(replay_input.debug_info)
        payload = dict(self._payload_builder(replay_input.command, replay_input.actor))
        outcome = dict(
            self._outcome(
                replay_input.answer,
                debug,
                provider_error=replay_input.provider_error,
            )
        )
        return bool(
            self._schedule_replay(
                route,
                payload,
                outcome,
                replay_input.trace_id,
                replay_input.actor,
            )
        )


def _pilot_payload(command: ChatTurnCommand, actor: ChatActor) -> dict[str, Any]:
    return _default_payload(RagStreamRequest(command=command, actor=actor))


__all__ = [
    "HttpRagStreamAdapter",
    "PilotExperimentAdapter",
    "RepositoryAuditSink",
    "RepositoryChatStore",
]
