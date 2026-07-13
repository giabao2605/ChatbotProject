import logging
from logging.handlers import RotatingFileHandler
import os


class _WindowsSafeRotatingFileHandler(RotatingFileHandler):
    """Keep logging best-effort when another Windows process holds a log file.

    The demo server and an evaluation worker can legitimately write the same
    observability files.  Windows does not permit the rename used by standard
    rotation while another process has the file open; dropping a rotation is
    preferable to printing a traceback for every RAG event.
    """

    def doRollover(self):  # noqa: N802 - stdlib hook name
        try:
            super().doRollover()
        except PermissionError:
            if self.stream is None:
                self.stream = self._open()

# Đảm bảo thư mục logs tồn tại (project root / logs)
_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
log_dir = os.path.join(_PROJECT_ROOT, 'logs')
os.makedirs(log_dir, exist_ok=True)

log_file = os.path.join(log_dir, 'chatbot_system.log')

# Tạo logger
logger = logging.getLogger("MechChatbot")
logger.setLevel(logging.INFO)

# Tránh việc add handler nhiều lần nếu module được import nhiều nơi
if not logger.handlers:
    # Handler 1: Ghi ra file với Rotating (tối đa 5 file, mỗi file 10MB)
    file_handler = _WindowsSafeRotatingFileHandler(
        log_file, maxBytes=10*1024*1024, backupCount=5, encoding='utf-8'
    )
    file_formatter = logging.Formatter('%(asctime)s - %(name)s - %(levelname)s - %(message)s')
    file_handler.setFormatter(file_formatter)
    
    # Handler 2: Ghi ra Console (Stream)
    console_handler = logging.StreamHandler()
    console_formatter = logging.Formatter('%(levelname)s: %(message)s')
    console_handler.setFormatter(console_formatter)
    
    logger.addHandler(file_handler)
    logger.addHandler(console_handler)

# --- OBSERVABILITY / TRACING ---
import json
from datetime import datetime, timezone

trace_log_file = os.path.join(log_dir, 'rag_trace.jsonl')
trace_logger = logging.getLogger("RagTrace")
trace_logger.setLevel(logging.INFO)

if not trace_logger.handlers:
    trace_handler = _WindowsSafeRotatingFileHandler(
        trace_log_file, maxBytes=20*1024*1024, backupCount=5, encoding='utf-8'
    )
    # Jsonl không cần formatter có asctime text, chỉ ghi message (chứa chuỗi json)
    trace_handler.setFormatter(logging.Formatter('%(message)s'))
    trace_logger.addHandler(trace_handler)
    
# --- P1-4: Observability accumulator (gom event theo trace_id -> 1 dong summary vao SQL) ---
_TRACE_ACC = {}
_TRACE_STAGE_METRICS = {}
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


def _persist_rag_trace(trace_id, acc):
    """Ghi 1 dong summary vao SQL (best-effort, lazy import de tranh circular import)."""
    try:
        from mech_chatbot.db.repository import save_rag_trace_summary
        save_rag_trace_summary(trace_id, acc)
    except Exception as e:
        logger.error(f"Loi persist rag trace summary: {e}")


def pop_trace_stage_metrics(trace_id):
    """Return numeric per-stage latency for one completed browser stream.

    The JSONL log remains the audit source of truth.  This small in-memory copy
    exists only long enough for the SSE ``done`` event, so the benchmark client
    can report P50/P95 without receiving prompts, chunks, or provider payloads.
    """
    if not trace_id:
        return {}
    values = _TRACE_STAGE_METRICS.pop(trace_id, {})
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
    stages = _TRACE_STAGE_METRICS.setdefault(trace_id, {})
    current = stages.get(event_name, {"latency_ms": 0})
    if event_name == "cache":
        # Exact and semantic lookups are consecutive parts of the same stage.
        latency += int(current.get("latency_ms", 0) or 0)
    elif event_name == "embed":
        # The final embed event is an aggregate; never double-count it.
        latency = max(latency, int(current.get("latency_ms", 0) or 0))
    stages[event_name] = {"latency_ms": latency}
    if len(_TRACE_STAGE_METRICS) > 1000:
        _TRACE_STAGE_METRICS.clear()


def log_trace(event_name, trace_id, **kwargs):
    """Ghi 1 event JSONL vao log trace + gom vao summary theo trace_id (P1-4)."""
    execution_context = str(
        kwargs.pop("execution_context", None)
        or os.getenv("RAG_EXECUTION_CONTEXT", "production")
    ).strip().lower()
    if execution_context not in {"production", "evaluation", "test"}:
        execution_context = "production"
    if event_name == "rag_end":
        legacy_reason = kwargs.pop("reason", None)
        if not kwargs.get("refusal_reason") and legacy_reason:
            kwargs["refusal_reason"] = legacy_reason
    try:
        event = {
            "ts": datetime.now(timezone.utc).isoformat(),
            "event": event_name,
            "trace_id": trace_id,
            "execution_context": execution_context,
        }
        event.update(kwargs)
        trace_logger.info(json.dumps(event, ensure_ascii=False))
    except Exception as e:
        logger.error(f"Loi khi ghi trace log {event_name}: {e}")

    # P1-4: gom metric theo trace_id, flush 1 dong summary khi rag_end
    try:
        if not trace_id:
            return
        _record_benchmark_stage(event_name, trace_id, kwargs)
        acc = _TRACE_ACC.setdefault(trace_id, {})
        acc["execution_context"] = execution_context
        if event_name == "rag_start":
            acc["question"] = str(kwargs.get("question", ""))[:490]
            acc["model"] = kwargs.get("model")
            acc["department"] = kwargs.get("department")
            acc["roles"] = kwargs.get("role")
        if event_name in _STEP_COL and kwargs.get("latency_ms") is not None:
            acc[_STEP_COL[event_name]] = kwargs.get("latency_ms")
        if event_name == "llm_generation":
            if kwargs.get("input_tokens") is not None:
                acc["tokens_in"] = kwargs.get("input_tokens")
            if kwargs.get("output_tokens") is not None:
                acc["tokens_out"] = kwargs.get("output_tokens")
            if kwargs.get("estimated_cost") is not None:
                acc["cost"] = kwargs.get("estimated_cost")
            if kwargs.get("model"):
                acc["model"] = kwargs.get("model")
        if event_name == "rag_end":
            acc["final_latency_ms"] = kwargs.get("final_latency_ms")
            acc["refusal"] = 1 if kwargs.get("refusal") else 0
            acc["refusal_reason"] = kwargs.get("refusal_reason") or kwargs.get("reason")
            acc["docs_count"] = kwargs.get("docs_count")
            if kwargs.get("user_department"):
                acc["department"] = kwargs.get("user_department")
            ur = kwargs.get("user_roles")
            if ur:
                acc["roles"] = ",".join(ur) if isinstance(ur, (list, tuple)) else str(ur)
            if kwargs.get("retrieval_mode"):
                acc["retrieval_mode"] = kwargs.get("retrieval_mode")
            _persist_rag_trace(trace_id, dict(acc))
            _TRACE_ACC.pop(trace_id, None)
        if len(_TRACE_ACC) > 1000:
            _TRACE_ACC.clear()
    except Exception as e:
        logger.error(f"Loi accumulate rag trace {event_name}: {e}")
