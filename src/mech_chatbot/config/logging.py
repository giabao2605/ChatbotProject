"""Process logging configured explicitly at a composition root.

Importing this module is side-effect free: no directory, file handler,
database callback, RAG callback, or environment read is created at import.
"""

from __future__ import annotations

from collections.abc import Callable
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass, field
from datetime import datetime, timezone
import json
import logging
from logging.handlers import RotatingFileHandler
from pathlib import Path
from threading import RLock
from typing import Any


class _WindowsSafeRotatingFileHandler(RotatingFileHandler):
    """Keep logging best-effort when another Windows process holds a log."""

    def doRollover(self):  # noqa: N802 - stdlib hook name
        try:
            super().doRollover()
        except PermissionError:
            if self.stream is None:
                self.stream = self._open()


@dataclass(frozen=True, slots=True)
class LoggingConfig:
    system_log_file: Path
    trace_log_file: Path
    level: int = logging.INFO

    def __post_init__(self) -> None:
        object.__setattr__(self, "system_log_file", Path(self.system_log_file))
        object.__setattr__(self, "trace_log_file", Path(self.trace_log_file))

    @classmethod
    def from_settings(cls, settings: object) -> "LoggingConfig":
        """Project the canonical settings snapshot without retaining it."""

        configured_trace = getattr(settings, "RAG_TRACE_LOG_FILE", None)
        return cls(
            system_log_file=log_file,
            trace_log_file=(
                Path(configured_trace)
                if configured_trace
                else trace_log_file
            ),
        )


@dataclass(frozen=True, slots=True)
class TraceRuntime:
    """Narrow callbacks supplied by the process composition root."""

    persist: Callable[[str, dict[str, Any]], None] | None = None
    execution_context: Callable[[], str] | None = None


@dataclass(slots=True)
class _TraceState:
    accumulators: dict[str, dict[str, Any]] = field(default_factory=dict)
    stage_metrics: dict[str, dict[str, dict[str, int]]] = field(
        default_factory=dict
    )
    lock: RLock = field(default_factory=RLock)


_PROJECT_ROOT = Path(__file__).resolve().parents[3]
log_dir = _PROJECT_ROOT / "logs"
log_file = log_dir / "chatbot_system.log"
trace_log_file = log_dir / "rag_trace.jsonl"

logger = logging.getLogger("MechChatbot")
trace_logger = logging.getLogger("RagTrace")


def _close_handlers(target: logging.Logger) -> None:
    for handler in tuple(target.handlers):
        target.removeHandler(handler)
        handler.close()


def configure_logging(config: LoggingConfig) -> None:
    """Install process-owned handlers after settings have been validated."""

    config.system_log_file.parent.mkdir(parents=True, exist_ok=True)
    config.trace_log_file.parent.mkdir(parents=True, exist_ok=True)
    _close_handlers(logger)
    _close_handlers(trace_logger)
    logger.setLevel(config.level)
    trace_logger.setLevel(config.level)

    system_handler = _WindowsSafeRotatingFileHandler(
        config.system_log_file,
        maxBytes=10 * 1024 * 1024,
        backupCount=5,
        encoding="utf-8",
    )
    system_handler.setFormatter(
        logging.Formatter(
            "%(asctime)s - %(name)s - %(levelname)s - %(message)s"
        )
    )
    console_handler = logging.StreamHandler()
    console_handler.setFormatter(logging.Formatter("%(levelname)s: %(message)s"))
    logger.addHandler(system_handler)
    logger.addHandler(console_handler)

    trace_handler = _WindowsSafeRotatingFileHandler(
        config.trace_log_file,
        maxBytes=20 * 1024 * 1024,
        backupCount=5,
        encoding="utf-8",
    )
    trace_handler.setFormatter(logging.Formatter("%(message)s"))
    trace_logger.addHandler(trace_handler)


_TRACE_RUNTIME = ContextVar("trace_runtime", default=TraceRuntime())
_TRACE_STATE: ContextVar[_TraceState | None] = ContextVar(
    "trace_state", default=None
)


def _current_trace_state() -> _TraceState:
    state = _TRACE_STATE.get()
    if state is None:
        state = _TraceState()
        _TRACE_STATE.set(state)
    return state


@contextmanager
def bind_trace_runtime(runtime: TraceRuntime):
    """Bind callbacks to the current request/task without global mutation."""

    runtime_token = _TRACE_RUNTIME.set(runtime)
    state_token = _TRACE_STATE.set(_TraceState())
    try:
        yield
    finally:
        _TRACE_STATE.reset(state_token)
        _TRACE_RUNTIME.reset(runtime_token)


_BENCHMARK_STAGE_EVENTS = {
    "route",
    "cache",
    "embed",
    "dense_retrieval",
    "bm25_retrieval",
    "rrf_grouping",
    "rerank",
    "parent_context",
    "corrective_retrieval",
    "claim_repair",
}
_STEP_COL = {
    "context_analysis": "context_ms",
    "intent": "intent_ms",
    "hyde": "hyde_ms",
    "glossary_expansion": "glossary_ms",
    "retrieval": "retrieval_ms",
    "rerank": "rerank_ms",
    "evidence_gate": "gate_ms",
    "llm_generation": "llm_ms",
    "image_analysis": "image_ms",
    "sql_bom": "sqlbom_ms",
    "corrective_retrieval": "corrective_retrieval_ms",
    "claim_repair": "claim_repair_ms",
}
_REDACT_SENSITIVE_TRACE_FIELDS = ContextVar(
    "redact_sensitive_trace_fields", default=False
)
_SENSITIVE_TRACE_KEY_PARTS = ("question", "prompt", "query", "content")
_VALID_EXECUTION_CONTEXTS = {
    "production",
    "evaluation",
    "pilot_replay",
    "test",
}


@contextmanager
def redact_sensitive_trace_fields(enabled=True):
    """Remove prompt-like values from trace events in replay contexts."""

    token = _REDACT_SENSITIVE_TRACE_FIELDS.set(bool(enabled))
    try:
        yield
    finally:
        _REDACT_SENSITIVE_TRACE_FIELDS.reset(token)


def _persist_rag_trace(trace_id, acc):
    runtime = _TRACE_RUNTIME.get()
    if runtime.persist is None:
        return
    try:
        runtime.persist(trace_id, acc)
    except Exception as error:
        logger.error(f"Loi persist rag trace summary: {error}")


def pop_trace_stage_metrics(trace_id):
    """Return numeric per-stage latency for one completed browser stream."""

    if not trace_id:
        return {}
    state = _current_trace_state()
    with state.lock:
        values = state.stage_metrics.pop(trace_id, {})
    return {
        stage: {"latency_ms": int(item.get("latency_ms", 0))}
        for stage, item in values.items()
        if isinstance(item, dict) and item.get("latency_ms") is not None
    }


def _record_benchmark_stage(event_name, trace_id, kwargs):
    if event_name not in _BENCHMARK_STAGE_EVENTS or not trace_id:
        return
    latency = kwargs.get("latency_ms")
    if latency is None:
        return
    try:
        latency = max(0, int(latency))
    except (TypeError, ValueError):
        return
    state = _current_trace_state()
    with state.lock:
        stages = state.stage_metrics.setdefault(trace_id, {})
        current = stages.get(event_name, {"latency_ms": 0})
        if event_name == "cache":
            latency += int(current.get("latency_ms", 0) or 0)
        elif event_name == "embed":
            latency = max(latency, int(current.get("latency_ms", 0) or 0))
        stages[event_name] = {"latency_ms": latency}
        if len(state.stage_metrics) > 1000:
            state.stage_metrics.clear()


def _execution_context(explicit: Any) -> str:
    value = explicit
    if value is None:
        provider = _TRACE_RUNTIME.get().execution_context
        value = provider() if provider is not None else "production"
    normalized = str(value).strip().lower()
    return (
        normalized
        if normalized in _VALID_EXECUTION_CONTEXTS
        else "production"
    )


def _event_payload(event_name, trace_id, execution_context, kwargs):
    if event_name == "rag_end":
        legacy_reason = kwargs.pop("reason", None)
        if not kwargs.get("refusal_reason") and legacy_reason:
            kwargs["refusal_reason"] = legacy_reason
    return {
        "ts": datetime.now(timezone.utc).isoformat(),
        "event": event_name,
        "trace_id": trace_id,
        "execution_context": execution_context,
        **kwargs,
    }


def _redacted_kwargs(kwargs):
    if not _REDACT_SENSITIVE_TRACE_FIELDS.get():
        return kwargs
    return {
        key: value
        for key, value in kwargs.items()
        if not any(
            part in str(key).casefold()
            for part in _SENSITIVE_TRACE_KEY_PARTS
        )
    }


def _update_trace_accumulator(event_name, trace_id, execution_context, kwargs):
    if not trace_id:
        return
    _record_benchmark_stage(event_name, trace_id, kwargs)
    state = _current_trace_state()
    with state.lock:
        acc = state.accumulators.setdefault(trace_id, {})
        acc["execution_context"] = execution_context
        if event_name == "rag_start":
            acc["question"] = str(kwargs.get("question", ""))[:490]
            acc["model"] = kwargs.get("model")
            acc["department"] = kwargs.get("department")
            acc["roles"] = kwargs.get("role")
        if event_name in _STEP_COL and kwargs.get("latency_ms") is not None:
            acc[_STEP_COL[event_name]] = kwargs.get("latency_ms")
        if event_name == "llm_generation":
            _record_llm_summary(acc, kwargs)
        if event_name == "rag_end":
            _complete_trace(trace_id, acc, kwargs)
            state.accumulators.pop(trace_id, None)
        if len(state.accumulators) > 1000:
            state.accumulators.clear()


def _record_llm_summary(acc, kwargs):
    if kwargs.get("input_tokens") is not None:
        acc["tokens_in"] = kwargs.get("input_tokens")
    if kwargs.get("output_tokens") is not None:
        acc["tokens_out"] = kwargs.get("output_tokens")
    if kwargs.get("estimated_cost") is not None:
        acc["cost"] = kwargs.get("estimated_cost")
    if kwargs.get("model"):
        acc["model"] = kwargs.get("model")


def _complete_trace(trace_id, acc, kwargs):
    acc["final_latency_ms"] = kwargs.get("final_latency_ms")
    acc["refusal"] = 1 if kwargs.get("refusal") else 0
    acc["refusal_reason"] = kwargs.get("refusal_reason") or kwargs.get("reason")
    acc["docs_count"] = kwargs.get("docs_count")
    if kwargs.get("user_department"):
        acc["department"] = kwargs.get("user_department")
    roles = kwargs.get("user_roles")
    if roles:
        acc["roles"] = (
            ",".join(roles)
            if isinstance(roles, (list, tuple))
            else str(roles)
        )
    if kwargs.get("retrieval_mode"):
        acc["retrieval_mode"] = kwargs.get("retrieval_mode")
    _persist_rag_trace(trace_id, dict(acc))


def log_trace(event_name, trace_id, **kwargs):
    """Write one trace event and update its best-effort summary."""

    kwargs = _redacted_kwargs(kwargs)
    execution_context = _execution_context(
        kwargs.pop("execution_context", None)
    )
    event = _event_payload(
        event_name,
        trace_id,
        execution_context,
        kwargs,
    )
    try:
        trace_logger.info(json.dumps(event, ensure_ascii=False))
    except Exception as error:
        logger.error(f"Loi khi ghi trace log {event_name}: {error}")
    try:
        _update_trace_accumulator(
            event_name,
            trace_id,
            execution_context,
            kwargs,
        )
    except Exception as error:
        logger.error(f"Loi accumulate rag trace {event_name}: {error}")


__all__ = [
    "LoggingConfig",
    "TraceRuntime",
    "bind_trace_runtime",
    "configure_logging",
    "log_trace",
    "logger",
    "pop_trace_stage_metrics",
    "redact_sensitive_trace_fields",
    "trace_logger",
]
