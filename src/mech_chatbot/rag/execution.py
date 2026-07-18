"""Typed public seam for executing one complete RAG request.

The orchestration remains synchronous because the RAG server already owns the
thread/async transport boundary.  Callers observe a small event stream instead
of the legacy five-value tuple and its post-consumption mutation rules.
"""

from __future__ import annotations

from contextlib import ExitStack
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
import re
import uuid
from typing import Any, Iterator, Literal, Mapping, Protocol


ExecutionMode = Literal["production", "evaluation", "pilot_replay"]
CompletionOutcome = Literal["answered", "refused"]
RagDiagnostics = Mapping[str, Any]


@dataclass(frozen=True, slots=True)
class AccessScope:
    department: str | None = None
    roles: frozenset[str] = frozenset()
    allowed_departments: frozenset[str] = frozenset()
    max_security_level: str = "public"
    allowed_sites: frozenset[str] = frozenset()


@dataclass(frozen=True, slots=True)
class RagRequest:
    question: str
    access: AccessScope
    image_path: Path | str | None = None
    history: tuple[Mapping[str, Any], ...] = ()
    current_part_ids: tuple[str, ...] = ()
    response_language: str = "vi"
    conversation_context: Mapping[str, Any] | None = None


@dataclass(frozen=True, slots=True)
class RagInvocation:
    trace_id: str
    mode: ExecutionMode = "production"

    def __post_init__(self) -> None:
        if self.mode not in {"production", "evaluation", "pilot_replay"}:
            raise ValueError(f"Unsupported RAG execution mode: {self.mode}")


class CancellationSignal(Protocol):
    def is_set(self) -> bool: ...


class _NeverCancelled:
    def is_set(self) -> bool:
        return False


NEVER_CANCELLED: CancellationSignal = _NeverCancelled()


class RagExecutor(Protocol):
    def run(
        self,
        request: RagRequest,
        invocation: RagInvocation,
        cancellation: CancellationSignal = NEVER_CANCELLED,
    ) -> Iterator["RagEvent"]: ...


@dataclass(frozen=True, slots=True)
class RagPrepared:
    ref_text: str
    ref_images: tuple[str, ...]
    new_part_ids: tuple[str, ...]
    diagnostics: RagDiagnostics


@dataclass(frozen=True, slots=True)
class RagToken:
    text: str


@dataclass(frozen=True, slots=True)
class RagCitation:
    citation: Mapping[str, Any]


@dataclass(frozen=True, slots=True)
class RagCompleted:
    outcome: CompletionOutcome
    trace_id: str
    diagnostics: RagDiagnostics
    refusal_reason: str | None = None


@dataclass(frozen=True, slots=True)
class RagFailed:
    code: str
    message: str
    retryable: bool
    cause: BaseException = field(repr=False, compare=False)


@dataclass(frozen=True, slots=True)
class RagCancelled:
    reason: str
    cause: BaseException | None = field(default=None, repr=False, compare=False)


RagEvent = RagPrepared | RagToken | RagCitation | RagCompleted | RagFailed | RagCancelled


@dataclass(frozen=True, slots=True)
class RagCollectedResult:
    answer: str
    ref_text: str
    ref_images: tuple[str, ...]
    new_part_ids: tuple[str, ...]
    citations: tuple[Mapping[str, Any], ...]
    outcome: CompletionOutcome
    trace_id: str
    diagnostics: RagDiagnostics
    refusal_reason: str | None


def collect_rag_events(events: Iterator[RagEvent]) -> RagCollectedResult:
    """Fold a complete event stream for sync, worker and evaluation callers."""
    from mech_chatbot.llm.external_ai import ExternalAICallCancelled

    prepared: RagPrepared | None = None
    completed: RagCompleted | None = None
    answer_parts: list[str] = []
    citations: list[Mapping[str, Any]] = []
    try:
        for event in events:
            if completed is not None:
                raise RuntimeError("RAG executor emitted an event after completion")
            if isinstance(event, RagPrepared):
                if prepared is not None:
                    raise RuntimeError("RAG executor emitted RagPrepared more than once")
                prepared = event
            elif isinstance(event, RagToken):
                if prepared is None:
                    raise RuntimeError("RAG executor emitted RagToken before RagPrepared")
                answer_parts.append(event.text)
            elif isinstance(event, RagCitation):
                if prepared is None:
                    raise RuntimeError("RAG executor emitted RagCitation before RagPrepared")
                citations.append(dict(event.citation))
            elif isinstance(event, RagCompleted):
                if prepared is None:
                    raise RuntimeError("RAG executor completed before RagPrepared")
                completed = event
            elif isinstance(event, RagFailed):
                raise event.cause
            elif isinstance(event, RagCancelled):
                if event.cause is not None:
                    raise event.cause
                raise ExternalAICallCancelled(event.reason)
    finally:
        close = getattr(events, "close", None)
        if callable(close):
            close()

    if prepared is None:
        raise RuntimeError("RAG executor ended without RagPrepared")
    if completed is None:
        raise RuntimeError("RAG executor ended without RagCompleted")
    return RagCollectedResult(
        answer="".join(answer_parts),
        ref_text=prepared.ref_text,
        ref_images=prepared.ref_images,
        new_part_ids=prepared.new_part_ids,
        citations=tuple(citations),
        outcome=completed.outcome,
        trace_id=completed.trace_id,
        diagnostics=completed.diagnostics,
        refusal_reason=completed.refusal_reason,
    )


_SOURCE_ID_RE = re.compile(
    r"(?:source[_ ]?id\s*[:#]?\s*|\[SRC:)(?P<source_id>D\d+P\d+)",
    re.IGNORECASE,
)
_ANSWERED_OUTCOMES = {"full_answer", "partial_answer"}
_REFUSED_OUTCOMES = {
    "access_denied",
    "clarification_required",
    "insufficient_evidence",
}


def attributed_citations(
    diagnostics: Mapping[str, Any] | None,
    answer: str,
) -> tuple[Mapping[str, Any], ...]:
    """Return source cards whose stable SourceID occurs in the final answer."""
    source_ids = {
        match.group("source_id").upper()
        for match in _SOURCE_ID_RE.finditer(str(answer or ""))
    }
    if not source_ids:
        return ()

    result: list[Mapping[str, Any]] = []
    seen: set[str] = set()
    for item in (diagnostics or {}).get("citation_docs") or []:
        if not isinstance(item, Mapping):
            continue
        try:
            doc_id = int(item.get("doc_id"))
            page_no = int(
                item.get("trang") or item.get("trang_so") or item.get("page_no")
            )
        except (TypeError, ValueError):
            continue
        source_id = str(item.get("source_id") or f"D{doc_id}P{page_no}").upper()
        if source_id not in source_ids or source_id in seen:
            continue
        seen.add(source_id)
        result.append(
            {
                "doc_id": doc_id,
                "page_no": page_no,
                "file_name": item.get("file_goc") or item.get("file_name"),
                "file_goc": item.get("file_goc") or item.get("file_name"),
                "version_no": item.get("version_no"),
                "score": item.get("score"),
                "trang": page_no,
                "source_id": source_id,
            }
        )
    return tuple(result)


def _completion_outcome(
    diagnostics: Mapping[str, Any],
    *,
    safety_blocked: bool,
) -> CompletionOutcome:
    if safety_blocked:
        return "refused"
    answer_outcome = str(diagnostics.get("answer_outcome") or "")
    if answer_outcome in _REFUSED_OUTCOMES:
        return "refused"
    if answer_outcome in _ANSWERED_OUTCOMES:
        return "answered"
    return "answered"


class DefaultRagExecutor:
    """Production event facade over the current RAG orchestration."""

    def run(
        self,
        request: RagRequest,
        invocation: RagInvocation,
        cancellation: CancellationSignal = NEVER_CANCELLED,
    ) -> Iterator[RagEvent]:
        from mech_chatbot.config.logging import redact_sensitive_trace_fields
        from mech_chatbot.rag.semantic_cache import replay_cache_disabled

        with ExitStack() as stack:
            if invocation.mode in {"evaluation", "pilot_replay"}:
                stack.enter_context(replay_cache_disabled(True))
            if invocation.mode == "pilot_replay":
                stack.enter_context(redact_sensitive_trace_fields(True))
            yield from self._run_in_context(request, invocation, cancellation)

    def _run_in_context(
        self,
        request: RagRequest,
        invocation: RagInvocation,
        cancellation: CancellationSignal,
    ) -> Iterator[RagEvent]:
        from mech_chatbot.llm.external_ai import ExternalAICallCancelled
        from mech_chatbot.rag import pipeline, route_safety

        trace_id = invocation.trace_id or (
            f"rag_{datetime.now().strftime('%Y%m%d_%H%M%S')}_{uuid.uuid4().hex[:6]}"
        )
        stream = None
        diagnostics: dict[str, Any] = {}
        safety_blocked = bool(
            route_safety.enabled() and route_safety.detect(request.question)
        )
        try:
            stream, ref_text, ref_images, new_part_ids, diagnostics = pipeline._execute_rag_request(
                request,
                trace_id=trace_id,
                cancel_event=cancellation,
            )
            yield RagPrepared(
                ref_text=ref_text or "",
                ref_images=tuple(ref_images or ()),
                new_part_ids=tuple(new_part_ids or ()),
                diagnostics=dict(diagnostics or {}),
            )

            answer_parts: list[str] = []
            for chunk in stream:
                text = str(chunk)
                answer_parts.append(text)
                yield RagToken(text=text)

            final_diagnostics = dict(diagnostics or {})
            for citation in attributed_citations(final_diagnostics, "".join(answer_parts)):
                yield RagCitation(citation=citation)

            outcome = _completion_outcome(
                final_diagnostics,
                safety_blocked=safety_blocked,
            )
            refusal_reason = None
            if outcome == "refused":
                refusal_reason = (
                    "safety_block"
                    if safety_blocked
                    else str(final_diagnostics.get("answer_outcome") or "refused")
                )
            yield RagCompleted(
                outcome=outcome,
                trace_id=trace_id,
                diagnostics=final_diagnostics,
                refusal_reason=refusal_reason,
            )
        except ExternalAICallCancelled as exc:
            yield RagCancelled(reason=str(exc), cause=exc)
        except Exception as exc:
            yield RagFailed(
                code=type(exc).__name__,
                message=str(exc),
                retryable=False,
                cause=exc,
            )
        finally:
            close = getattr(stream, "close", None)
            if callable(close):
                close()


__all__ = [
    "AccessScope",
    "CancellationSignal",
    "DefaultRagExecutor",
    "NEVER_CANCELLED",
    "RagCancelled",
    "RagCitation",
    "RagCollectedResult",
    "RagCompleted",
    "RagEvent",
    "RagExecutor",
    "RagFailed",
    "RagInvocation",
    "RagPrepared",
    "RagRequest",
    "RagDiagnostics",
    "RagToken",
    "attributed_citations",
    "collect_rag_events",
]
