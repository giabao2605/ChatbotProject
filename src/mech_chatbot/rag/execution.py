"""Typed public seam for executing one complete RAG request.

The orchestration remains synchronous because the RAG server already owns the
thread/async transport boundary.  Callers observe a small event stream instead
of the legacy five-value tuple and its post-consumption mutation rules.
"""

from __future__ import annotations

from contextlib import ExitStack
from contextvars import Context, ContextVar, copy_context
from dataclasses import dataclass, field
from datetime import datetime
import math
import os
from pathlib import Path
import re
import threading
import time
import uuid
from collections.abc import Mapping as MappingABC
from typing import Any, Callable, Iterator, Literal, Mapping, Protocol


ExecutionMode = Literal["production", "evaluation", "pilot_replay", "test"]
CompletionOutcome = Literal["answered", "refused"]
CONTROLLED_DEMO_REQUEST_DEADLINE_SECONDS = 120.0
_EXECUTION_CONTEXT: ContextVar[str | None] = ContextVar(
    "rag_execution_context",
    default=None,
)
_REQUEST_BUDGET: ContextVar["RequestBudgetLedger | None"] = ContextVar(
    "rag_request_budget",
    default=None,
)


def current_execution_context() -> str:
    """Return the request-local trace/evaluation context.

    The environment remains the compatibility fallback for scripts that have
    not migrated to :class:`RagInvocation`.  A ContextVar keeps concurrent
    production and evaluation requests from changing each other's mode.
    """

    value = _EXECUTION_CONTEXT.get()
    if value is None:
        value = os.getenv("RAG_EXECUTION_CONTEXT", "production")
    normalized = str(value).strip().lower()
    return normalized if normalized in {
        "production", "evaluation", "pilot_replay", "test",
    } else "production"


def current_request_budget() -> "RequestBudgetLedger | None":
    return _REQUEST_BUDGET.get()


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
        if self.mode not in {"production", "evaluation", "pilot_replay", "test"}:
            raise ValueError(f"Unsupported RAG execution mode: {self.mode}")


@dataclass(frozen=True, slots=True)
class RagRuntimeContract:
    """Canonical process-level settings reported by health and pilot evidence."""

    execution_context: ExecutionMode
    evaluation_force_ambiguous: bool
    request_deadline_seconds: float

    @classmethod
    def from_mapping(
        cls,
        value: Mapping[str, Any] | "RagRuntimeContract",
    ) -> "RagRuntimeContract":
        if isinstance(value, cls):
            return value
        if not isinstance(value, MappingABC):
            raise ValueError("runtime contract must be a mapping")
        context = str(value.get("execution_context") or "").strip().lower()
        if context not in {"production", "evaluation", "pilot_replay", "test"}:
            raise ValueError("runtime contract has an unsupported execution context")
        force_ambiguous = value.get("evaluation_force_ambiguous")
        if not isinstance(force_ambiguous, bool):
            raise ValueError("runtime contract requires a boolean evaluation override")
        raw_deadline = value.get("request_deadline_seconds")
        if isinstance(raw_deadline, bool):
            raise ValueError("runtime contract requires a numeric deadline")
        try:
            deadline = float(raw_deadline)
        except (TypeError, ValueError) as exc:
            raise ValueError("runtime contract requires a numeric deadline") from exc
        if not math.isfinite(deadline) or deadline <= 0:
            raise ValueError("runtime contract deadline must be finite and positive")
        return cls(
            execution_context=context,
            evaluation_force_ambiguous=force_ambiguous,
            request_deadline_seconds=deadline,
        )

    @classmethod
    def from_environment(cls) -> "RagRuntimeContract":
        raw_override = os.getenv("RAG_EVAL_FORCE_AMBIGUOUS", "false").strip().lower()
        if raw_override not in {
            "1", "true", "yes", "y", "on",
            "0", "false", "no", "n", "off",
        }:
            raise ValueError("RAG_EVAL_FORCE_AMBIGUOUS must be a boolean")
        return cls.from_mapping(
            {
                "execution_context": os.getenv("RAG_EXECUTION_CONTEXT", "production"),
                "evaluation_force_ambiguous": raw_override
                in {"1", "true", "yes", "y", "on"},
                "request_deadline_seconds": os.getenv(
                    "RAG_REQUEST_DEADLINE_SECONDS", "120"
                ),
            }
        )

    @property
    def is_controlled_demo(self) -> bool:
        return (
            self.execution_context == "production"
            and self.evaluation_force_ambiguous is False
            and self.request_deadline_seconds
            == CONTROLLED_DEMO_REQUEST_DEADLINE_SECONDS
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "execution_context": self.execution_context,
            "evaluation_force_ambiguous": self.evaluation_force_ambiguous,
            "request_deadline_seconds": self.request_deadline_seconds,
        }


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
class EvidenceDiagnostics:
    outcome: str | None = None
    state: str | None = None
    stage: str | None = None
    quotes: tuple[str, ...] = ()
    correction_allowed: bool = False


@dataclass(frozen=True, slots=True)
class BudgetDiagnostics:
    planners: int = 0
    subqueries: int = 0
    corrections: int = 0
    repairs: int = 0
    calculations: int = 0
    graph_edges: int = 0
    provider_retries: int = 0
    final_generations: int = 0
    deadline_exceeded: bool = False


@dataclass(frozen=True, slots=True)
class GenerationDiagnostics:
    input_tokens: int = 0
    output_tokens: int = 0
    estimated_cost: float = 0.0
    repair_count: int = 0
    provider_retries: int = 0


@dataclass(frozen=True, slots=True, eq=False)
class RagDiagnostics(MappingABC[str, Any]):
    """Typed view over the legacy diagnostics payload.

    ``extra`` preserves every unmodelled key, while the Mapping interface keeps
    existing JSON/SSE and compatibility-adapter serialization byte-for-byte in
    shape.
    """

    retrieved_documents: tuple[Mapping[str, Any], ...] = ()
    citations: tuple[Mapping[str, Any], ...] = ()
    evidence: EvidenceDiagnostics = EvidenceDiagnostics()
    budget: BudgetDiagnostics = BudgetDiagnostics()
    generation: GenerationDiagnostics = GenerationDiagnostics()
    conversation_delta: Mapping[str, Any] | None = None
    extra: Mapping[str, Any] = field(default_factory=dict)
    _raw: Mapping[str, Any] = field(default_factory=dict, repr=False)

    @classmethod
    def from_mapping(
        cls,
        value: Mapping[str, Any] | "RagDiagnostics" | None,
        *,
        ledger: "RequestBudgetLedger | None" = None,
    ) -> "RagDiagnostics":
        if isinstance(value, cls) and ledger is None:
            return value
        raw = dict(value or {})
        generation_raw = dict(raw.get("generation_metrics") or {})

        def as_int(item: Any) -> int:
            try:
                return int(item or 0)
            except (TypeError, ValueError):
                return 0

        def as_float(item: Any) -> float:
            try:
                return float(item or 0.0)
            except (TypeError, ValueError):
                return 0.0

        budget = BudgetDiagnostics(
            planners=as_int(raw.get("planner_count") or getattr(ledger, "planners", 0)),
            subqueries=as_int(raw.get("subquery_count") or getattr(ledger, "subqueries", 0)),
            corrections=as_int(raw.get("correction_count") or getattr(ledger, "corrections", 0)),
            repairs=as_int(generation_raw.get("repair_count") or getattr(ledger, "repairs", 0)),
            calculations=as_int(generation_raw.get("calculation_count") or getattr(ledger, "calculations", 0)),
            graph_edges=as_int(raw.get("graph_edge_count") or getattr(ledger, "graph_edges", 0)),
            provider_retries=as_int(generation_raw.get("provider_retries") or getattr(ledger, "provider_retries", 0)),
            final_generations=as_int(raw.get("final_generation_count") or getattr(ledger, "final_generations", 0)),
            deadline_exceeded=bool(raw.get("deadline_exceeded") or getattr(ledger, "deadline_exceeded", False)),
        )
        modeled = {
            "retrieved_docs", "citation_docs", "answer_outcome", "evidence_state",
            "evidence_stage", "evidence_quotes", "correction_allowed",
            "generation_metrics", "conversation_context", "planner_count",
            "subquery_count", "correction_count", "final_generation_count",
            "deadline_exceeded", "graph_edge_count",
        }
        return cls(
            retrieved_documents=tuple(raw.get("retrieved_docs") or ()),
            citations=tuple(raw.get("citation_docs") or ()),
            evidence=EvidenceDiagnostics(
                outcome=raw.get("answer_outcome"),
                state=raw.get("evidence_state"),
                stage=raw.get("evidence_stage"),
                quotes=tuple(raw.get("evidence_quotes") or ()),
                correction_allowed=bool(raw.get("correction_allowed", False)),
            ),
            budget=budget,
            generation=GenerationDiagnostics(
                input_tokens=as_int(generation_raw.get("input_tokens")),
                output_tokens=as_int(generation_raw.get("output_tokens")),
                estimated_cost=as_float(generation_raw.get("estimated_cost")),
                repair_count=as_int(generation_raw.get("repair_count")),
                provider_retries=as_int(generation_raw.get("provider_retries")),
            ),
            conversation_delta=raw.get("conversation_context"),
            extra={key: item for key, item in raw.items() if key not in modeled},
            _raw=raw,
        )

    def __getitem__(self, key: str) -> Any:
        return self._raw[key]

    def __iter__(self):
        return iter(self._raw)

    def __len__(self) -> int:
        return len(self._raw)

    def __eq__(self, other: object) -> bool:
        if isinstance(other, MappingABC):
            return dict(self) == dict(other)
        return NotImplemented


class _NormalizesRagDiagnostics:
    __slots__ = ()
    diagnostics: RagDiagnostics

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "diagnostics",
            RagDiagnostics.from_mapping(self.diagnostics),
        )


@dataclass(frozen=True, slots=True)
class RagPrepared(_NormalizesRagDiagnostics):
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
class RagCompleted(_NormalizesRagDiagnostics):
    outcome: CompletionOutcome
    trace_id: str
    diagnostics: RagDiagnostics
    refusal_reason: str | None = None

@dataclass(frozen=True, slots=True)
class RagFailed(_NormalizesRagDiagnostics):
    code: str
    message: str
    retryable: bool
    cause: BaseException = field(repr=False, compare=False)
    diagnostics: RagDiagnostics = field(default_factory=RagDiagnostics)

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


@dataclass(frozen=True, slots=True)
class RequestBudgetLimits:
    planners: int = 1
    subqueries: int = 3
    corrections: int = 1
    repairs: int = 1
    calculations: int = 1
    graph_edges: int = 50
    provider_retries: int = 2
    final_generations: int = 1
    deadline_seconds: float = 120.0

    @classmethod
    def from_environment(cls) -> "RequestBudgetLimits":
        try:
            deadline = max(0.1, float(os.getenv("RAG_REQUEST_DEADLINE_SECONDS", "120")))
        except (TypeError, ValueError):
            deadline = 120.0
        return cls(deadline_seconds=deadline)


class RequestBudgetExceeded(RuntimeError):
    """Internal control-flow error for a request-wide budget violation."""


@dataclass(slots=True)
class RequestBudgetLedger:
    limits: RequestBudgetLimits
    started_monotonic: float
    planners: int = 0
    subqueries: int = 0
    corrections: int = 0
    repairs: int = 0
    calculations: int = 0
    graph_edges: int = 0
    provider_retries: int = 0
    final_generations: int = 0
    deadline_exceeded: bool = False
    _lock: threading.Lock = field(
        default_factory=threading.Lock,
        repr=False,
        compare=False,
    )

    @property
    def deadline_monotonic(self) -> float:
        return self.started_monotonic + self.limits.deadline_seconds

    def record(self, field_name: str, value: int, *, cumulative: bool = False) -> None:
        if field_name not in {
            "planners", "subqueries", "corrections", "repairs", "calculations",
            "graph_edges", "provider_retries", "final_generations",
        }:
            raise ValueError(f"Unknown request budget field: {field_name}")
        normalized = max(0, int(value))
        with self._lock:
            current = int(getattr(self, field_name))
            proposed = current + normalized if cumulative else normalized
            if proposed > getattr(self.limits, field_name):
                raise RequestBudgetExceeded(
                    f"RAG request budget exceeded: {field_name}"
                )
            setattr(self, field_name, proposed)

    def consume_provider_retry(self) -> None:
        self.record("provider_retries", 1, cumulative=True)


@dataclass(slots=True)
class _ExecutionState:
    """Composition root and mutable lifecycle for exactly one RAG request."""

    request: RagRequest
    invocation: RagInvocation
    cancellation: CancellationSignal
    trace_id: str
    budget: RequestBudgetLedger
    retrieval_adapter: Any | None = field(default=None, repr=False)
    provider_adapter: Any | None = field(default=None, repr=False)
    refusal_reason: str | None = None
    generation_outcome: Any | None = field(default=None, repr=False)
    phase: str = "created"

    @classmethod
    def create(
        cls,
        request: RagRequest,
        invocation: RagInvocation,
        cancellation: CancellationSignal,
        trace_id: str,
        retrieval_adapter: Any | None = None,
        provider_adapter: Any | None = None,
    ) -> "_ExecutionState":
        started = time.monotonic()
        return cls(
            request=request,
            invocation=invocation,
            cancellation=cancellation,
            trace_id=trace_id,
            retrieval_adapter=retrieval_adapter,
            provider_adapter=provider_adapter,
            budget=RequestBudgetLedger(
                limits=RequestBudgetLimits.from_environment(),
                started_monotonic=started,
            ),
        )

    def retrieve(self, **kwargs: Any) -> Any:
        if self.retrieval_adapter is not None:
            return self.retrieval_adapter.retrieve(**kwargs)
        from mech_chatbot.rag.pipeline_steps import _retrieve

        return _retrieve(**kwargs)

    def invoke_provider(self, *args: Any, **kwargs: Any) -> Any:
        if self.provider_adapter is not None:
            return self.provider_adapter.invoke(*args, **kwargs)
        from mech_chatbot.llm.llm_client import cohere_invoke

        return cohere_invoke(*args, **kwargs)

    def transition(self, phase: str) -> None:
        self.checkpoint(phase)
        self.phase = str(phase)

    def checkpoint(self, stage: str) -> None:
        from mech_chatbot.llm.external_ai import ExternalAICallCancelled

        if self.cancellation.is_set():
            raise ExternalAICallCancelled(f"RAG request cancelled during {stage}")
        if time.monotonic() >= self.budget.deadline_monotonic:
            self.budget.deadline_exceeded = True
            raise TimeoutError(f"RAG request deadline exceeded during {stage}")

    def refuse(self, reason: str) -> None:
        if not self.refusal_reason:
            self.refusal_reason = str(reason or "refused")

    def bind_generation(self, outcome: Any) -> None:
        self.generation_outcome = outcome

    def completion(self) -> tuple[CompletionOutcome, str | None]:
        generated_reason = getattr(self.generation_outcome, "refusal_reason", None)
        reason = str(generated_reason or self.refusal_reason or "").strip() or None
        return ("refused", reason) if reason else ("answered", None)

    def prepared(self, values: tuple[Any, Any, Any, Any, Any]) -> "_PreparedExecution":
        stream, ref_text, ref_images, new_part_ids, diagnostics = values
        self.phase = "prepared"
        return _PreparedExecution(
            stream=stream,
            ref_text=str(ref_text or ""),
            ref_images=tuple(ref_images or ()),
            new_part_ids=tuple(new_part_ids or ()),
            diagnostics=dict(diagnostics or {}),
        )

    def capture_budget(self, diagnostics: Mapping[str, Any]) -> None:
        metrics = dict(diagnostics.get("generation_metrics") or {})
        self.budget.record("repairs", int(metrics.get("repair_count") or 0))
        self.budget.record("calculations", int(metrics.get("calculation_count") or 0))


@dataclass(frozen=True, slots=True)
class _PreparedExecution:
    """Private handoff from pipeline stages to the event lifecycle."""

    stream: Iterator[str]
    ref_text: str
    ref_images: tuple[str, ...]
    new_part_ids: tuple[str, ...]
    diagnostics: dict[str, Any]


class _ContextBoundRagIterator:
    """Run one lazy event source inside an iterator-owned context."""

    def __init__(self, source_factory):
        self._context: Context = copy_context()
        self._source_factory = source_factory
        self._source: Iterator[RagEvent] | None = None
        self._closed = False
        self._setup_terminal: RagFailed | RagCancelled | None = None

    def __iter__(self) -> "_ContextBoundRagIterator":
        return self

    def __next__(self) -> RagEvent:
        if self._closed:
            raise StopIteration
        return self._context.run(self._next_in_context)

    def _next_in_context(self) -> RagEvent:
        if self._source is None:
            self._source = iter(self._source_factory(self))
        try:
            return next(self._source)
        except StopIteration:
            self._closed = True
            raise

    def note_setup_terminal(self, terminal: RagFailed | RagCancelled) -> None:
        self._setup_terminal = terminal

    def prepare_for_legacy(self) -> RagEvent:
        """Consume preparation only and restore legacy synchronous failures."""
        first = next(self)
        terminal = self._setup_terminal
        if terminal is None:
            return first
        self.close()
        if isinstance(terminal, RagFailed):
            raise terminal.cause
        if terminal.cause is not None:
            raise terminal.cause
        from mech_chatbot.llm.external_ai import ExternalAICallCancelled

        raise ExternalAICallCancelled(terminal.reason)

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        source = self._source
        if source is None:
            return
        close = getattr(source, "close", None)
        if callable(close):
            self._context.run(close)


def _prepare_legacy_events(events: Iterator[RagEvent]) -> RagEvent | None:
    prepare = getattr(events, "prepare_for_legacy", None)
    if callable(prepare):
        return prepare()
    return next(events, None)


def consume_rag_events(
    events: Iterator[RagEvent],
) -> RagCollectedResult | RagFailed | RagCancelled:
    """Consume one event stream while preserving a typed terminal failure."""
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
                return event
            elif isinstance(event, RagCancelled):
                return event
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


def collect_rag_events(events: Iterator[RagEvent]) -> RagCollectedResult:
    """Collect a successful stream and preserve legacy exception behavior."""
    terminal = consume_rag_events(events)
    if isinstance(terminal, RagFailed):
        raise terminal.cause
    if isinstance(terminal, RagCancelled):
        if terminal.cause is not None:
            raise terminal.cause
        from mech_chatbot.llm.external_ai import ExternalAICallCancelled

        raise ExternalAICallCancelled(terminal.reason)
    return terminal


_SOURCE_ID_RE = re.compile(
    r"(?:source[_ ]?id\s*[:#]?\s*|\[SRC:)(?P<source_id>D\d+P\d+)",
    re.IGNORECASE,
)
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


class DefaultRagExecutor:
    """Own one RAG lifecycle and expose it as a typed event stream."""

    def __init__(
        self,
        *,
        execute_pipeline: Callable[["_ExecutionState"], "_PreparedExecution"] | None = None,
        retrieval_adapter: Any | None = None,
        provider_adapter: Any | None = None,
    ) -> None:
        self._execute_pipeline = execute_pipeline
        self._retrieval_adapter = retrieval_adapter
        self._provider_adapter = provider_adapter

    def run(
        self,
        request: RagRequest,
        invocation: RagInvocation,
        cancellation: CancellationSignal = NEVER_CANCELLED,
    ) -> Iterator[RagEvent]:
        return _ContextBoundRagIterator(
            lambda owner: self._run_bound(
                request,
                invocation,
                cancellation,
                owner,
            )
        )

    def _run_bound(
        self,
        request: RagRequest,
        invocation: RagInvocation,
        cancellation: CancellationSignal,
        owner: _ContextBoundRagIterator,
    ) -> Iterator[RagEvent]:
        from mech_chatbot.config.logging import redact_sensitive_trace_fields
        from mech_chatbot.rag.semantic_cache import replay_cache_disabled

        with ExitStack() as stack:
            context_token = _EXECUTION_CONTEXT.set(invocation.mode)
            stack.callback(_EXECUTION_CONTEXT.reset, context_token)
            if invocation.mode in {"evaluation", "pilot_replay"}:
                stack.enter_context(replay_cache_disabled(True))
            if invocation.mode == "pilot_replay":
                stack.enter_context(redact_sensitive_trace_fields(True))
            yield from self._run_in_context(
                request,
                invocation,
                cancellation,
                owner,
            )

    def _run_in_context(
        self,
        request: RagRequest,
        invocation: RagInvocation,
        cancellation: CancellationSignal,
        owner: _ContextBoundRagIterator,
    ) -> Iterator[RagEvent]:
        from mech_chatbot.llm.external_ai import ExternalAICallCancelled
        trace_id = invocation.trace_id or (
            f"rag_{datetime.now().strftime('%Y%m%d_%H%M%S')}_{uuid.uuid4().hex[:6]}"
        )
        stream = None
        diagnostics: dict[str, Any] = {}
        state = _ExecutionState.create(
            request,
            invocation,
            cancellation,
            trace_id,
            retrieval_adapter=self._retrieval_adapter,
            provider_adapter=self._provider_adapter,
        )
        budget_token = _REQUEST_BUDGET.set(state.budget)
        try:
            execute_pipeline = self._execute_pipeline
            if execute_pipeline is None:
                from mech_chatbot.rag import pipeline

                execute_pipeline = pipeline.execute_pipeline
            prepared = execute_pipeline(state)
            stream = prepared.stream
            diagnostics = prepared.diagnostics
            yield RagPrepared(
                ref_text=prepared.ref_text,
                ref_images=prepared.ref_images,
                new_part_ids=prepared.new_part_ids,
                diagnostics=RagDiagnostics.from_mapping(
                    diagnostics,
                    ledger=state.budget,
                ),
            )

            answer_parts: list[str] = []
            for chunk in stream:
                state.checkpoint("generation")
                text = str(chunk)
                answer_parts.append(text)
                yield RagToken(text=text)

            final_diagnostics = dict(diagnostics or {})
            generation_metrics = final_diagnostics.get("generation_metrics")
            if isinstance(generation_metrics, dict):
                generation_metrics["provider_retries"] = state.budget.provider_retries
            state.capture_budget(final_diagnostics)
            state.checkpoint("completion")
            for citation in attributed_citations(final_diagnostics, "".join(answer_parts)):
                yield RagCitation(citation=citation)

            outcome, refusal_reason = state.completion()
            yield RagCompleted(
                outcome=outcome,
                trace_id=trace_id,
                diagnostics=RagDiagnostics.from_mapping(
                    final_diagnostics,
                    ledger=state.budget,
                ),
                refusal_reason=refusal_reason,
            )
        except ExternalAICallCancelled as exc:
            terminal = RagCancelled(reason=str(exc), cause=exc)
            if state.phase != "prepared":
                owner.note_setup_terminal(terminal)
                yield RagPrepared("", (), (), {})
            yield terminal
        except Exception as exc:
            failure_diagnostics = dict(diagnostics or {})
            generation_metrics = failure_diagnostics.get("generation_metrics")
            if isinstance(generation_metrics, dict):
                generation_metrics["provider_retries"] = state.budget.provider_retries
            terminal = RagFailed(
                code=type(exc).__name__,
                message=str(exc),
                retryable=False,
                cause=exc,
                diagnostics=RagDiagnostics.from_mapping(
                    failure_diagnostics,
                    ledger=state.budget,
                ),
            )
            if state.phase != "prepared":
                owner.note_setup_terminal(terminal)
                yield RagPrepared("", (), (), {})
            yield terminal
        finally:
            close = getattr(stream, "close", None)
            if callable(close):
                close()
            _REQUEST_BUDGET.reset(budget_token)


__all__ = [
    "AccessScope",
    "CancellationSignal",
    "BudgetDiagnostics",
    "DefaultRagExecutor",
    "EvidenceDiagnostics",
    "GenerationDiagnostics",
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
    "RagRuntimeContract",
    "RagDiagnostics",
    "RagToken",
    "RequestBudgetLedger",
    "RequestBudgetLimits",
    "CONTROLLED_DEMO_REQUEST_DEADLINE_SECONDS",
    "attributed_citations",
    "collect_rag_events",
    "consume_rag_events",
    "current_execution_context",
    "current_request_budget",
]
