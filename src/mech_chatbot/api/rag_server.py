"""
RAG FastAPI Server — persistent service that loads RAG models once at startup.

Usage:
    python rag_server.py          # default port 8100
    uvicorn rag_server:app --host 0.0.0.0 --port 8100

Environment variables:
    RAG_SERVER_PORT        — port to listen on (default 8100)
    MAX_CONCURRENT_RAG     — max concurrent /chat requests (default 2)
    RAG_SERVER_HOST        — bind address (default 0.0.0.0)
"""

import asyncio
import json
import re
import secrets
import threading
import time
from contextlib import asynccontextmanager
from dataclasses import dataclass, replace
from pathlib import Path
from collections.abc import Callable

from fastapi import APIRouter, Depends, FastAPI, Header, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field
from typing import List, Optional, Dict, Any

from mech_chatbot.config.logging import (
    TraceRuntime,
    bind_trace_runtime,
    logger,
    log_trace,
    pop_trace_stage_metrics,
    redact_sensitive_trace_fields,
)
from mech_chatbot.llm.external_ai import ExternalAICallCancelled, external_processing_context
from mech_chatbot.config.settings import RagProcessSettings, Settings, load_settings
from mech_chatbot.governance.feature_activation import ActivationStatus


# ---------------------------------------------------------------------------
# Lifespan: load RAG system once at startup, clean up at shutdown
# ---------------------------------------------------------------------------
@dataclass(frozen=True, slots=True)
class RagServerState:
    settings: Settings
    process_settings: RagProcessSettings
    runtime_builder: Callable[[Settings], Any]
    runtime: Any | None = None
    activation: ActivationStatus | None = None
    ready: bool = False


def _environment_snapshot(settings: Settings) -> dict[str, str]:
    values = settings.model_dump()
    snapshot: dict[str, str] = {}
    for key, value in values.items():
        if value is None:
            continue
        if isinstance(value, bool):
            snapshot[key] = "true" if value else "false"
        elif isinstance(value, (tuple, list)):
            snapshot[key] = ",".join(str(item) for item in value)
        else:
            snapshot[key] = str(value)
    # The validator and activation ledger retain public legacy environment
    # names; map the canonical typed projections without re-reading ambient env.
    if settings.LLM_API_KEY:
        snapshot["PROXYLLM_API_KEY"] = settings.LLM_API_KEY
    if settings.LLM_BASE_URL:
        snapshot["PROXYLLM_BASE_URL"] = settings.LLM_BASE_URL
    return snapshot


def get_rag_server_state(request: Request) -> RagServerState:
    return request.app.state.rag_server


def _activation_for(state: RagServerState) -> ActivationStatus:
    if state.activation is not None:
        return state.activation
    from mech_chatbot.governance.feature_activation import (
        activation_status,
        current_git_commit,
    )

    project_root = Path(__file__).resolve().parents[3]
    return activation_status(
        _environment_snapshot(state.settings),
        root=project_root,
        current_commit=current_git_commit(project_root),
    )

@asynccontextmanager
async def lifespan(app: FastAPI):
    from mech_chatbot.config.validate import assert_config_valid, safe_config_summary
    state: RagServerState = app.state.rag_server
    environment = _environment_snapshot(state.settings)
    assert_config_valid(
        environment,
        require_service_auth=state.process_settings.require_service_auth,
    )
    activation = _activation_for(state)
    app.state.rag_server = replace(state, activation=activation)
    if not activation.valid:
        logger.error(
            "RAG activation rejected: scope=%s reason=%s enabled_flags=%s",
            activation.scope,
            activation.reason,
            list(activation.enabled_flags),
        )
        yield
        return
    logger.info("Config OK: %s", safe_config_summary(environment))
    logger.info("=" * 60)
    logger.info("RAG Server starting — loading models (one-time)...")
    logger.info("=" * 60)

    t0 = time.time()

    runtime = None
    try:
        runtime = state.runtime_builder(state.settings)
        # Nap tokenizer trong startup thay vi de request dau tien ganh cold load.
        from mech_chatbot.rag.rerank import tokenize_cached
        tokenize_cached("tai lieu noi bo")
        app.state.rag_server = replace(
            state,
            runtime=runtime,
            activation=activation,
            ready=True,
        )
        elapsed = time.time() - t0
        logger.info(f"RAG system loaded successfully in {elapsed:.1f}s")
    except Exception as e:
        logger.error(f"FATAL: Could not load RAG system: {e}", exc_info=True)
        app.state.rag_server = replace(
            state,
            runtime=None,
            activation=activation,
            ready=False,
        )

    try:
        yield
    finally:
        logger.info("RAG Server shutting down...")
        if runtime is not None:
            runtime.close()
        app.state.rag_server = replace(
            state,
            runtime=None,
            activation=activation,
            ready=False,
        )


router = APIRouter()

# ---------------------------------------------------------------------------
# Request / Response schemas
# ---------------------------------------------------------------------------
class ChatRequest(BaseModel):
    user_id: Optional[int] = None
    username: Optional[str] = None
    user_question: str = Field(..., min_length=1, max_length=20000)
    image_path: Optional[str] = None
    chat_history: List[Dict[str, Any]] = Field(default_factory=list)
    current_part_ids: List[str] = Field(default_factory=list)
    user_department: Optional[str] = None
    user_roles: List[str] = Field(default_factory=list)
    allowed_departments: List[str] = Field(
        default_factory=list,
        description="Deprecated and ignored. Effective RBAC is resolved server-side from the authenticated user profile.",
        json_schema_extra={"deprecated": True},
    )
    max_security_level: Optional[str] = Field(
        default="internal",
        description="Deprecated and ignored. Effective clearance is resolved server-side from the authenticated user profile.",
        json_schema_extra={"deprecated": True},
    )
    allowed_sites: List[str] = Field(default_factory=list)
    response_language: Optional[str] = "vi"
    conversation_context: Optional[Dict[str, Any]] = None


class ChatResponse(BaseModel):
    ok: bool = True
    response: str = ""
    ref_text: str = ""
    ref_images: List[str] = Field(default_factory=list)
    new_part_ids: List[str] = Field(default_factory=list)
    debug_info: Dict[str, Any] = Field(default_factory=dict)
    elapsed_ms: int = 0


class HealthResponse(BaseModel):
    status: str
    rag_loaded: bool
    max_concurrent: int
    current_available: int
    deployment_id: Optional[str] = None
    git_sha: Optional[str] = None
    snapshot_fingerprint: Optional[str] = None
    feature_flags: Dict[str, bool] = Field(default_factory=dict)
    feature_versions: Dict[str, str] = Field(default_factory=dict)
    activation_scope: str = "default_rollout"
    activation_profile: Optional[str] = None
    review_mode: str = "multi_reviewer"
    activation_valid: bool = False
    activation_reason: str = "not_evaluated"
    live_authorized: bool = False
    decision_source_commit: Optional[str] = None
    fallback_features: List[str] = Field(default_factory=list)
    graph_fingerprint: Optional[str] = None
    execution_context: str = "production"
    evaluation_force_ambiguous: bool = False
    request_deadline_seconds: float = 120.0


class UserContextRequest(BaseModel):
    user_id: Optional[int] = None
    username: Optional[str] = None


class SessionHistoryRequest(UserContextRequest):
    session_id: str = Field(..., min_length=1, max_length=100)


class SaveChatRequest(SessionHistoryRequest):
    user_msg: str = Field(..., max_length=20000)
    bot_msg: str = Field(default="", max_length=200000)
    image_path: Optional[str] = None
    ref_images: List[str] = Field(default_factory=list)
    retrieved_docs: List[Dict[str, Any]] = Field(default_factory=list)


class FeedbackRequest(UserContextRequest):
    chat_id: int
    rating: int


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------
async def require_service_auth(
    x_rag_service_token: Optional[str] = Header(default=None, alias="X-RAG-Service-Token"),
    server_state: RagServerState = Depends(get_rag_server_state),
):
    settings = server_state.process_settings
    if not settings.require_service_auth:
        return
    if not settings.service_token:
        raise HTTPException(
            status_code=503,
            detail="RAG service auth is enabled but RAG_SERVICE_TOKEN is not configured.",
        )
    if not x_rag_service_token or not secrets.compare_digest(
        x_rag_service_token,
        settings.service_token,
    ):
        raise HTTPException(status_code=401, detail="Invalid RAG service token.")


def load_profile_or_403(user_id=None, username=None) -> Dict[str, Any]:
    from mech_chatbot.auth.core import load_user_profile

    profile = load_user_profile(user_id=user_id, username=username)
    if not profile:
        raise HTTPException(status_code=403, detail="User identity is invalid or inactive.")
    return profile


def resolve_user_profile(req: UserContextRequest) -> Dict[str, Any]:
    return load_profile_or_403(user_id=req.user_id, username=req.username)


def _is_admin(profile: Dict[str, Any]) -> bool:
    return "admin" in (profile.get("roles") or [])


def _audit_admin_query(
    profile: Dict[str, Any],
    trace_id: str,
    surface: str,
    *,
    outcome: str,
    debug_info: Dict[str, Any] | None = None,
):
    """Audit the legacy-admin global document-read path without raw prompts."""
    if not _is_admin(profile):
        return
    docs = (debug_info or {}).get("retrieved_docs") or []
    doc_ids = []
    levels = []
    for item in docs:
        if not isinstance(item, dict):
            continue
        try:
            doc_ids.append(int(item.get("doc_id")))
        except (TypeError, ValueError):
            pass
        level = item.get("security_level")
        if level:
            levels.append(str(level))
    try:
        from mech_chatbot.services import write_audit_log

        write_audit_log(
            profile.get("username"),
            "admin_global_read_query",
            "RAG",
            None,
            {
                "trace_id": trace_id,
                "surface": surface,
                "outcome": outcome,
                "doc_ids": sorted(set(doc_ids)),
                "security_levels": sorted(set(levels)),
            },
            user_id=profile.get("user_id"),
        )
    except Exception as exc:
        logger.warning("Admin RAG audit write failed: %s", exc)


def _trace_runtime(runtime: Any) -> TraceRuntime:
    return getattr(runtime, "trace_runtime", TraceRuntime())


@router.get("/health", response_model=HealthResponse, tags=["System"])
async def health_check(
    server_state: RagServerState = Depends(get_rag_server_state),
):
    """Health check endpoint for monitoring/load balancer."""
    from mech_chatbot.rag.execution_contracts import RagRuntimeContract
    from mech_chatbot.governance.feature_activation import (
        feature_flags,
        feature_versions,
    )

    process = server_state.process_settings
    runtime_contract = (
        server_state.runtime.runtime_contract
        if server_state.runtime is not None
        else RagRuntimeContract.from_mapping(
            {
                "execution_context": process.execution_context,
                "evaluation_force_ambiguous": process.evaluation_force_ambiguous,
                "request_deadline_seconds": process.request_deadline_seconds,
            }
        )
    )
    activation = _activation_for(server_state)
    environment = _environment_snapshot(server_state.settings)
    semaphore = getattr(server_state.runtime, "semaphore", None)

    return HealthResponse(
        status="ok" if server_state.ready and activation.valid else "degraded",
        rag_loaded=server_state.ready,
        max_concurrent=process.max_concurrent_requests,
        # Semaphore._value gives remaining permits (CPython implementation detail)
        current_available=getattr(semaphore, "_value", -1),
        deployment_id=process.deployment_id,
        git_sha=process.deployment_git_sha,
        snapshot_fingerprint=process.snapshot_fingerprint,
        feature_flags=feature_flags(environment),
        feature_versions=feature_versions(environment),
        activation_scope=activation.scope,
        activation_profile=activation.profile,
        review_mode=activation.review_mode,
        activation_valid=activation.valid,
        activation_reason=activation.reason,
        live_authorized=activation.live_authorized,
        decision_source_commit=activation.decision_source_commit,
        fallback_features=list(activation.fallback_features),
        graph_fingerprint=process.graph_fingerprint,
        **runtime_contract.to_dict(),
    )


@router.post("/chat", response_model=ChatResponse, tags=["RAG"], dependencies=[Depends(require_service_auth)])
async def chat_endpoint(
    req: ChatRequest,
    server_state: RagServerState = Depends(get_rag_server_state),
):
    """
    Process a RAG chat question.
    
    This endpoint uses models loaded at startup — no cold start per request.
    Concurrency is limited by MAX_CONCURRENT_RAG semaphore.
    """
    if not server_state.ready or server_state.runtime is None:
        _lang_pre = (getattr(req, "response_language", None) or "vi").lower()
        raise HTTPException(
            status_code=503,
            detail=(
                "RAG system is not loaded yet. Please wait and retry."
                if _lang_pre.startswith("en")
                else "Hệ thống RAG chưa sẵn sàng. Vui lòng chờ và thử lại."
            ),
        )

    user_profile = resolve_user_profile(req)

    # Try to acquire semaphore with timeout
    try:
        acquired = await asyncio.wait_for(
            server_state.runtime.semaphore.acquire(), timeout=120.0
        )
    except asyncio.TimeoutError:
        _lang = (getattr(req, "response_language", None) or "vi").lower()
        if _lang.startswith("en"):
            _detail_503 = (
                "System is busy "
                f"({server_state.process_settings.max_concurrent_requests} "
                "requests being processed). "
                "Please retry in a moment."
            )
        else:
            _detail_503 = (
                "Hệ thống đang bận "
                f"({server_state.process_settings.max_concurrent_requests} "
                "request đang xử lý). "
                "Vui lòng thử lại sau."
            )
        raise HTTPException(status_code=503, detail=_detail_503)

    t_start = time.time()
    try:
        # Run the synchronous RAG pipeline in a thread pool to avoid blocking
        loop = asyncio.get_event_loop()
        result = await loop.run_in_executor(
            server_state.runtime.thread_pool,
            _run_rag_sync,
            req,
            user_profile,
            server_state.runtime,
        )
        result.elapsed_ms = int((time.time() - t_start) * 1000)
        logger.info(
            "RAG request completed in %sms",
            result.elapsed_ms,
        )
        return result

    except Exception as e:
        elapsed_ms = int((time.time() - t_start) * 1000)
        logger.error(f"RAG request failed after {elapsed_ms}ms: {e}", exc_info=True)
        raise HTTPException(
            status_code=500,
            detail=f"{type(e).__name__}: {str(e)[:500]}",
        )
    finally:
        server_state.runtime.semaphore.release()


def _sse(event, payload):
    return f"event: {event}\ndata: {json.dumps(payload, ensure_ascii=False)}\n\n"


def _final_stream_citations(debug_info: dict[str, Any] | None, answer: str) -> list[dict[str, Any]]:
    """Emit only SourceIDs actually attributed by the completed answer."""
    from mech_chatbot.rag.execution import attributed_citations

    return [dict(item) for item in attributed_citations(debug_info, answer)]


@router.post("/chat/stream", tags=["RAG"], dependencies=[Depends(require_service_auth)])
async def chat_stream_endpoint(
    req: ChatRequest,
    x_rag_pilot_replay: Optional[str] = Header(
        default=None, alias="X-RAG-Pilot-Replay"
    ),
    x_rag_pilot_experiment_id: Optional[str] = Header(
        default=None, alias="X-RAG-Pilot-Experiment-ID"
    ),
    x_rag_matched_pair_id: Optional[str] = Header(
        default=None, alias="X-RAG-Matched-Pair-ID"
    ),
    x_rag_original_trace_id: Optional[str] = Header(
        default=None, alias="X-RAG-Original-Trace-ID"
    ),
    x_rag_assigned_arm: Optional[str] = Header(
        default=None, alias="X-RAG-Assigned-Arm"
    ),
    x_rag_pilot_replay_signature: Optional[str] = Header(
        default=None, alias="X-RAG-Pilot-Replay-Signature"
    ),
    x_rag_pilot_payload_sha256: Optional[str] = Header(
        default=None, alias="X-RAG-Pilot-Payload-SHA256"
    ),
    x_rag_pilot_replay_nonce: Optional[str] = Header(
        default=None, alias="X-RAG-Pilot-Replay-Nonce"
    ),
    x_rag_pilot_replay_expires: Optional[str] = Header(
        default=None, alias="X-RAG-Pilot-Replay-Expires"
    ),
    server_state: RagServerState = Depends(get_rag_server_state),
):
    """Stream real pipeline chunks and final metadata over SSE."""
    if not server_state.ready or server_state.runtime is None:
        raise HTTPException(status_code=503, detail="RAG system is not loaded yet.")
    user_profile = resolve_user_profile(req)
    replay = isinstance(x_rag_pilot_replay, str) and x_rag_pilot_replay.strip().lower() in {
        "1", "true", "yes", "on"
    }
    if replay:
        if (
            not isinstance(x_rag_matched_pair_id, str)
            or not re.fullmatch(r"[A-Za-z0-9_-]{1,100}", x_rag_matched_pair_id)
            or not isinstance(x_rag_original_trace_id, str)
            or not x_rag_original_trace_id.strip()
            or x_rag_assigned_arm not in {"control", "candidate"}
            or not isinstance(x_rag_pilot_experiment_id, str)
            or not x_rag_pilot_experiment_id.strip()
        ):
            raise HTTPException(status_code=400, detail="Invalid CRAG pilot replay headers")
        from mech_chatbot.evaluation.crag_pilot import (
            canonical_payload_sha256,
            verify_replay_signature,
        )

        if hasattr(req, "model_fields_set"):
            fields_set = req.model_fields_set
        else:
            fields_set = req.__fields_set__
        if hasattr(req, "model_dump"):
            signed_payload = req.model_dump(include=fields_set)
        else:
            signed_payload = req.dict(include=fields_set)
        actual_payload_sha256 = canonical_payload_sha256(signed_payload)

        try:
            replay_expires_at = int(x_rag_pilot_replay_expires or 0)
        except (TypeError, ValueError):
            replay_expires_at = 0
        if not verify_replay_signature(
            assignment_salt=server_state.process_settings.pilot_assignment_salt,
            experiment_id=x_rag_pilot_experiment_id,
            matched_pair_id=x_rag_matched_pair_id,
            assigned_arm=x_rag_assigned_arm,
            target_deployment_id=server_state.process_settings.deployment_id or "",
            original_trace_id=x_rag_original_trace_id,
            payload_sha256=actual_payload_sha256,
            nonce=str(x_rag_pilot_replay_nonce or ""),
            expires_at=replay_expires_at,
            signature=str(x_rag_pilot_replay_signature or ""),
        ) or actual_payload_sha256 != x_rag_pilot_payload_sha256:
            raise HTTPException(status_code=403, detail="Invalid CRAG pilot replay signature")
    try:
        await asyncio.wait_for(
            server_state.runtime.semaphore.acquire(),
            timeout=120.0,
        )
    except asyncio.TimeoutError:
        raise HTTPException(status_code=503, detail="RAG server busy")

    loop = asyncio.get_running_loop()
    queue = asyncio.Queue()
    cancel_event = threading.Event()
    started = time.time()

    def emit(event, payload):
        loop.call_soon_threadsafe(queue.put_nowait, (event, payload))

    request_trace_id = f"rag_{secrets.token_hex(8)}"

    def worker():
        events = None
        debug_info: dict[str, Any] = {}
        from mech_chatbot.rag.execution import (
            RagCancelled,
            RagCitation,
            RagCompleted,
            RagFailed,
            RagPrepared,
            RagToken,
        )
        from mech_chatbot.rag.semantic_cache import replay_cache_disabled

        with bind_trace_runtime(
            _trace_runtime(server_state.runtime)
        ), external_processing_context(
            user_profile.get("username"),
            _is_admin(user_profile),
            request_trace_id,
        ), replay_cache_disabled(replay), redact_sensitive_trace_fields(replay):
            if replay:
                log_trace(
                    "pilot_replay_start",
                    request_trace_id,
                    matched_pair_id=x_rag_matched_pair_id,
                    original_trace_id=x_rag_original_trace_id,
                    assigned_arm=x_rag_assigned_arm,
                    deployment_id=server_state.process_settings.deployment_id,
                    snapshot_fingerprint=(
                        server_state.process_settings.snapshot_fingerprint
                    ),
                    cache_disabled=True,
                    side_effects_disabled=True,
                )
            first_token_ms = None
            try:
                if cancel_event.is_set():
                    raise ExternalAICallCancelled("RAG stream da bi client huy truoc khi bat dau")
                events = _open_rag_events(
                    req,
                    user_profile,
                    trace_id=request_trace_id,
                    cancel_event=cancel_event,
                    mode="pilot_replay" if replay else "production",
                    runtime=server_state.runtime,
                )
                completed = False
                for event in events:
                    if cancel_event.is_set():
                        raise ExternalAICallCancelled("RAG stream da bi client huy")
                    if isinstance(event, RagPrepared):
                        debug_info = dict(event.diagnostics)
                        emit(
                            "metadata",
                            {
                                "ref_text": event.ref_text,
                                "ref_images": list(event.ref_images),
                                "new_part_ids": list(event.new_part_ids),
                                "debug_info": debug_info,
                            },
                        )
                    elif isinstance(event, RagToken):
                        if first_token_ms is None:
                            first_token_ms = int((time.time() - started) * 1000)
                            logger.info("RAG first token in %sms", first_token_ms)
                            log_trace("first_token", request_trace_id, latency_ms=first_token_ms)
                        emit("token", {"text": event.text})
                    elif isinstance(event, RagCitation):
                        emit("citation", dict(event.citation))
                    elif isinstance(event, RagCompleted):
                        debug_info = dict(event.diagnostics)
                        completed = True
                    elif isinstance(event, RagCancelled):
                        if event.cause is not None:
                            raise event.cause
                        raise ExternalAICallCancelled(event.reason)
                    elif isinstance(event, RagFailed):
                        raise event.cause
                if not completed:
                    raise RuntimeError("RAG executor ended without a completion event")
                if not replay:
                    _audit_admin_query(
                        user_profile,
                        request_trace_id,
                        "chat_stream",
                        outcome="success",
                        debug_info=debug_info,
                    )
                elapsed_ms = int((time.time() - started) * 1000)
                trace_stages = pop_trace_stage_metrics(request_trace_id)
                if first_token_ms is not None:
                    trace_stages["first_token"] = {"latency_ms": first_token_ms}
                trace_stages["completion"] = {"latency_ms": elapsed_ms}
                emit(
                    "done",
                    {
                        "ok": True,
                        "elapsed_ms": elapsed_ms,
                        "first_token_ms": first_token_ms,
                        "trace_id": request_trace_id,
                        "trace_stages": trace_stages,
                    },
                )
                logger.info(
                    "RAG stream completed in %sms",
                    elapsed_ms,
                )
                log_trace(
                    "complete",
                    request_trace_id,
                    latency_ms=elapsed_ms,
                    first_token_ms=first_token_ms,
                )
            except ExternalAICallCancelled:
                elapsed_ms = int((time.time() - started) * 1000)
                logger.info("RAG stream cancelled after %sms", elapsed_ms)
                if not replay:
                    _audit_admin_query(
                        user_profile,
                        request_trace_id,
                        "chat_stream",
                        outcome="cancelled",
                        debug_info=debug_info,
                    )
                log_trace(
                    "rag_end",
                    request_trace_id,
                    final_latency_ms=elapsed_ms,
                    refusal=True,
                    refusal_reason="client_cancelled",
                    cancelled=True,
                )
            except Exception as exc:
                logger.error("RAG stream failed: %s", exc, exc_info=True)
                if not replay:
                    _audit_admin_query(
                        user_profile,
                        request_trace_id,
                        "chat_stream",
                        outcome="error",
                    )
                emit(
                    "error",
                    {
                        "message": f"{type(exc).__name__}: {str(exc)[:500]}",
                        "elapsed_ms": int((time.time() - started) * 1000),
                    },
                )
            finally:
                close = getattr(events, "close", None)
                if callable(close):
                    try:
                        close()
                    except Exception:
                        pass
                emit("_end", {})

    future = loop.run_in_executor(server_state.runtime.thread_pool, worker)
    future.add_done_callback(
        lambda _f: loop.call_soon_threadsafe(
            server_state.runtime.semaphore.release
        )
    )

    async def event_stream():
        try:
            yield _sse("accepted", {"ok": True})
            while True:
                event, payload = await queue.get()
                if event == "_end":
                    break
                yield _sse(event, payload)
        finally:
            # ThreadPoolExecutor cannot forcibly kill a running provider call.
            # The event is checked before/after every streamed provider chunk,
            # causing the pipeline to close its generator, mark audit cancelled,
            # and release the semaphore from the future callback.
            cancel_event.set()

    return StreamingResponse(
        event_stream(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@router.post("/chat/sessions", tags=["Chat History"], dependencies=[Depends(require_service_auth)])
async def list_chat_sessions(req: UserContextRequest):
    """List chat sessions visible to the current user."""
    from mech_chatbot.services import get_all_sessions

    profile = resolve_user_profile(req)
    return {
        "sessions": get_all_sessions(
            username=profile.get("username"),
            is_admin=False,
        )
    }


@router.post("/chat/history", tags=["Chat History"], dependencies=[Depends(require_service_auth)])
async def load_chat_history(req: SessionHistoryRequest):
    """Load a single chat session with the same RBAC redaction as Streamlit UI."""
    from mech_chatbot.services import get_chat_history

    profile = resolve_user_profile(req)
    return {
        "messages": get_chat_history(
            req.session_id,
            username=profile.get("username"),
            is_admin=_is_admin(profile),
            user_clearance=profile.get("max_security_level", "public"),
            allowed_departments=profile.get("allowed_departments") or [],
            allowed_sites=profile.get("allowed_sites") or [],
        )
    }


@router.post("/chat/history/delete", tags=["Chat History"], dependencies=[Depends(require_service_auth)])
async def delete_chat_history(req: SessionHistoryRequest):
    """Delete one chat session, scoped to the current user unless admin."""
    from mech_chatbot.services import clear_chat_history

    profile = resolve_user_profile(req)
    deleted = clear_chat_history(
        req.session_id,
        username=profile.get("username"),
        is_admin=False,
    )
    return {"ok": True, "deleted": deleted}


@router.post("/chat/history/save", tags=["Chat History"], dependencies=[Depends(require_service_auth)])
async def save_chat_turn(req: SaveChatRequest):
    """Persist one chat turn and its answer sources, matching the Streamlit path."""
    from mech_chatbot.services import (
        save_answer_sources,
        save_answer_evidence,
        save_chat_history,
        write_audit_log,
    )

    profile = resolve_user_profile(req)
    username = profile.get("username")
    chat_id = save_chat_history(
        session_id=req.session_id,
        user_msg=req.user_msg,
        bot_msg=req.bot_msg,
        image_path=req.image_path,
        ref_images=req.ref_images,
        username=username,
    )

    if chat_id:
        save_answer_evidence(chat_id, req.retrieved_docs)
        final_sources = _final_stream_citations(
            {"citation_docs": req.retrieved_docs}, req.bot_msg
        )
        if final_sources:
            save_answer_sources(chat_id, final_sources)

    write_audit_log(
        username=username,
        action="chat_query",
        entity_type="LichSuChat",
        entity_id=chat_id,
        details={"prompt": req.user_msg, "session_id": req.session_id},
    )

    confidential_sources = [
        {
            "doc_id": d.get("doc_id"),
            "file_goc": d.get("file_goc"),
            "version_no": d.get("version_no"),
        }
        for d in req.retrieved_docs
        if isinstance(d, dict) and d.get("security_level") == "confidential"
    ]
    if confidential_sources:
        write_audit_log(
            username=username,
            action="read_confidential",
            entity_type="LichSuChat",
            entity_id=chat_id,
            details={
                "session_id": req.session_id,
                "prompt": req.user_msg,
                "so_tai_lieu_mat": len(confidential_sources),
                "nguon_mat": confidential_sources,
            },
        )

    return {"ok": bool(chat_id), "chat_id": chat_id}


@router.post("/chat/feedback", tags=["Chat History"], dependencies=[Depends(require_service_auth)])
async def save_chat_feedback(req: FeedbackRequest):
    """Persist like/dislike feedback for a saved chat answer."""
    from mech_chatbot.services import update_chat_feedback

    profile = resolve_user_profile(req)
    update_chat_feedback(
        req.chat_id,
        1 if req.rating > 0 else -1,
        voter_username=profile.get("username"),
    )
    return {"ok": True}


def _open_rag_events(
    req: ChatRequest,
    user_profile: Dict[str, Any],
    trace_id: str,
    cancel_event,
    *,
    mode: str = "production",
    runtime,
):
    from mech_chatbot.rag.execution import (
        AccessScope,
        NEVER_CANCELLED,
        RagInvocation,
        RagRequest,
    )

    request = RagRequest(
        question=req.user_question,
        image_path=req.image_path,
        history=tuple(req.chat_history),
        current_part_ids=tuple(req.current_part_ids),
        access=AccessScope(
            department=user_profile.get("department"),
            roles=frozenset(user_profile.get("roles") or ()),
            allowed_departments=frozenset(user_profile.get("allowed_departments") or ()),
            max_security_level=user_profile.get("max_security_level") or "public",
            allowed_sites=frozenset(user_profile.get("allowed_sites") or ()),
        ),
        response_language=req.response_language or "vi",
        conversation_context=req.conversation_context,
    )
    return runtime.executor.run(
        request,
        RagInvocation(trace_id=trace_id, mode=mode),
        cancellation=cancel_event or NEVER_CANCELLED,
    )


def _run_rag_sync(
    req: ChatRequest,
    user_profile: Dict[str, Any],
    runtime,
) -> ChatResponse:
    """Fold the public RAG event stream into the non-streaming response."""
    from mech_chatbot.rag.execution import collect_rag_events

    request_trace_id = f"rag_{secrets.token_hex(8)}"
    debug_info: Dict[str, Any] = {}
    ref_text = ""
    ref_images: list[str] = []
    new_part_ids: list[str] = []
    chunks: list[str] = []
    final_citations: list[dict[str, Any]] = []
    try:
        with bind_trace_runtime(
            _trace_runtime(runtime)
        ), external_processing_context(
            user_profile.get("username"),
            _is_admin(user_profile),
            request_trace_id,
        ):
            result = collect_rag_events(
                _open_rag_events(
                    req,
                    user_profile,
                    trace_id=request_trace_id,
                    cancel_event=None,
                    runtime=runtime,
                )
            )
            ref_text = result.ref_text
            ref_images = list(result.ref_images)
            new_part_ids = list(result.new_part_ids)
            chunks = [result.answer]
            final_citations = [dict(item) for item in result.citations]
            debug_info = dict(result.diagnostics)
    except Exception:
        _audit_admin_query(
            user_profile,
            request_trace_id,
            "chat",
            outcome="error",
            debug_info=debug_info,
        )
        raise

    answer = "".join(chunks)
    if final_citations:
        reference_lines = []
        for item in final_citations:
            file_name = item.get("file_name") or f"Doc {item.get('doc_id')}"
            reference_lines.append(f"- **{file_name}** (Trang {item.get('page_no')})")
        ref_text = "\n\n---\n**Nguồn tham chiếu:**\n" + "\n".join(reference_lines)
    else:
        ref_text = ""
        ref_images = []
    _audit_admin_query(
        user_profile,
        request_trace_id,
        "chat",
        outcome="success",
        debug_info=debug_info,
    )

    return ChatResponse(
        ok=True,
        response=answer,
        ref_text=ref_text or "",
        ref_images=ref_images or [],
        new_part_ids=new_part_ids or [],
        debug_info=debug_info or {},
    )


def create_rag_app(
    settings: Settings,
    *,
    runtime_builder: Callable[[Settings], Any] | None = None,
) -> FastAPI:
    """Create one RAG delivery adapter from an immutable settings snapshot."""

    from mech_chatbot.composition.rag_runtime import build_rag_runtime

    process_settings = RagProcessSettings.from_settings(settings)
    application = FastAPI(
        title="RAG Chat API — Mechanical Engineering",
        version="2.0.0",
        description="FastAPI backend for the Mechanical Engineering RAG Chatbot",
        lifespan=lifespan,
    )
    application.state.rag_server = RagServerState(
        settings=settings,
        process_settings=process_settings,
        runtime_builder=runtime_builder or build_rag_runtime,
    )
    if process_settings.cors_allow_origins:
        application.add_middleware(
            CORSMiddleware,
            allow_origins=list(process_settings.cors_allow_origins),
            allow_credentials=False,
            allow_methods=["*"],
            allow_headers=["*"],
        )
    application.include_router(router)
    return application


app = create_rag_app(load_settings())


# ---------------------------------------------------------------------------
# Entrypoint
# ---------------------------------------------------------------------------
if __name__ == "__main__":
    import uvicorn

    process_settings = app.state.rag_server.process_settings
    logger.info(
        "Starting RAG Server on %s:%s",
        process_settings.host,
        process_settings.port,
    )
    logger.info(
        "Max concurrent RAG requests: %s",
        process_settings.max_concurrent_requests,
    )

    uvicorn.run(
        "mech_chatbot.api.rag_server:app",
        host=process_settings.host,
        port=process_settings.port,
        log_level="info",
        reload=False,
        workers=1,  # Single worker — models are not fork-safe
    )
