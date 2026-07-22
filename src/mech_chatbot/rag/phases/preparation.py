"""Request preparation before routing or retrieval."""

from __future__ import annotations

import time
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from mech_chatbot.config.logging import log_trace, logger
from mech_chatbot.llm.external_ai import ExternalAICallCancelled
from mech_chatbot.llm.llm_client import get_llm_model_name
from mech_chatbot.rag.execution import RequestBudgetExceeded
from mech_chatbot.rag.phases.contracts import PreparationReason, PreparedValues
from mech_chatbot.rag.pipeline_steps import _analyze_image, _prepare_history


@dataclass(frozen=True, slots=True)
class PreparedRequest:
    user_question: str
    image_path: str | None
    chat_history: tuple[Mapping[str, Any], ...]
    current_part_ids: tuple[str, ...]
    user_department: str | None
    user_roles: tuple[str, ...]
    allowed_departments: tuple[str, ...]
    max_security_level: str
    allowed_sites: tuple[str, ...]
    response_language: str
    conversation_context: Mapping[str, Any] | None
    trace_id: str
    started_at: float
    cache_scope: str | None
    cache_eligible: bool
    history_text: str
    history_summary: str | None
    summary_covered: int | None
    image_analysis: str | None


@dataclass(frozen=True, slots=True)
class PreparationOutcome:
    prepared: PreparedRequest | None = None
    terminal: PreparedValues | None = None
    reason_code: PreparationReason = "prepared"

    def __post_init__(self) -> None:
        if (self.prepared is None) == (self.terminal is None):
            raise ValueError("preparation must return exactly one outcome")


@dataclass(frozen=True, slots=True)
class _PreparationContext:
    user_question: str
    image_path: str | None
    chat_history: tuple[Mapping[str, Any], ...]
    current_part_ids: tuple[str, ...]
    user_department: str | None
    user_roles: tuple[str, ...]
    allowed_departments: tuple[str, ...]
    max_security_level: str
    allowed_sites: tuple[str, ...]
    response_language: str
    conversation_context: Mapping[str, Any] | None
    trace_id: str
    started_at: float
    cache_eligible: bool


@dataclass(frozen=True, slots=True)
class _CacheLookup:
    terminal: PreparationOutcome | None
    scope: str | None


def _empty_debug() -> dict[str, Any]:
    try:
        from mech_chatbot.rag.semantic_cache import pipeline_namespace

        namespace = pipeline_namespace()
    except Exception:
        namespace = "unknown"
    return {"pipeline_namespace": namespace, "retrieved_docs": []}


def _normalize_request(state: Any) -> _PreparationContext:
    request = state.request
    image_path = str(request.image_path) if request.image_path is not None else None
    chat_history = tuple(request.history)
    current_part_ids = tuple(request.current_part_ids)
    conversation_context = (
        dict(request.conversation_context)
        if request.conversation_context is not None
        else None
    )
    return _PreparationContext(
        user_question=request.question,
        image_path=image_path,
        chat_history=chat_history,
        current_part_ids=current_part_ids,
        user_department=request.access.department,
        user_roles=tuple(request.access.roles),
        allowed_departments=tuple(request.access.allowed_departments),
        max_security_level=request.access.max_security_level,
        allowed_sites=tuple(request.access.allowed_sites),
        response_language=request.response_language,
        conversation_context=conversation_context,
        trace_id=state.trace_id,
        started_at=time.time(),
        cache_eligible=not any(
            (image_path, chat_history, current_part_ids, bool(conversation_context))
        ),
    )


def _log_start(context: _PreparationContext) -> None:
    log_trace(
        "rag_start",
        context.trace_id,
        question_length=len(context.user_question or ""),
        has_image=bool(context.image_path),
        history_count=len(context.chat_history),
        current_part_ids=list(context.current_part_ids),
        department=context.user_department,
        role=",".join(context.user_roles) if context.user_roles else "",
        model=get_llm_model_name(),
        pipeline_namespace=_empty_debug()["pipeline_namespace"],
    )


def _detect_safety_reason(context: _PreparationContext) -> str | None:
    try:
        from mech_chatbot.rag import route_safety

        return (
            route_safety.detect(context.user_question)
            if route_safety.enabled()
            else None
        )
    except (ExternalAICallCancelled, RequestBudgetExceeded):
        raise
    except Exception as safety_error:
        logger.error("Pre-cache safety check failed: %s", safety_error, exc_info=True)
        return "safety_check_unavailable"


def _safety_terminal(
    context: _PreparationContext,
    state: Any,
) -> PreparationOutcome | None:
    safety_reason = _detect_safety_reason(context)
    if not safety_reason:
        return None
    state.refuse("safety_block")
    from mech_chatbot.rag import route_responses

    safety_text = route_responses.build_safety_response(
        context.response_language,
        context.user_department,
        context.allowed_departments,
    )

    def safety_stream():
        yield safety_text

    log_trace(
        "safety",
        context.trace_id,
        reason=safety_reason,
        blocked=True,
        layer="pre_cache",
    )
    log_trace(
        "rag_end",
        context.trace_id,
        final_latency_ms=int((time.time() - context.started_at) * 1000),
        refusal=True,
        refusal_reason="safety_block",
    )
    return PreparationOutcome(
        terminal=(
            safety_stream(),
            "",
            (),
            context.current_part_ids,
            _empty_debug(),
        ),
        reason_code="safety_block",
    )


def _exact_cache_terminal(
    context: _PreparationContext,
    hit: Mapping[str, Any],
    lookup_started: float,
) -> PreparationOutcome:
    logger.info("Exact cache HIT -> tra loi truoc router/embedding.")
    debug = {
        "retrieved_docs": hit.get("evidence_snapshot") or [],
        "citation_docs": hit.get("citation_snapshot") or [],
        "cache_hit": True,
        "cache_type": "exact",
    }

    def exact_cached_stream():
        yield hit.get("answer", "")

    log_trace(
        "cache",
        context.trace_id,
        cache_type="exact",
        hit=True,
        latency_ms=int((time.time() - lookup_started) * 1000),
    )
    log_trace(
        "rag_end",
        context.trace_id,
        final_latency_ms=int((time.time() - context.started_at) * 1000),
        refusal=False,
        cache_hit=True,
        cache_type="exact",
    )
    return PreparationOutcome(
        terminal=(
            exact_cached_stream(),
            hit.get("ref_text", ""),
            hit.get("ref_images", []),
            context.current_part_ids,
            debug,
        ),
        reason_code="exact_cache_hit",
    )


def _lookup_exact_cache(context: _PreparationContext) -> _CacheLookup:
    cache_scope = None
    lookup_started = time.time()
    if context.cache_eligible:
        try:
            import mech_chatbot.rag.semantic_cache as semantic_cache

            if semantic_cache.enabled():
                cache_scope = semantic_cache.scope_signature(
                    context.user_department,
                    context.allowed_departments,
                    context.max_security_level,
                    context.allowed_sites,
                    context.user_roles,
                )
                hit = semantic_cache.lookup_exact(context.user_question, cache_scope)
                if hit:
                    return _CacheLookup(
                        _exact_cache_terminal(context, hit, lookup_started),
                        cache_scope,
                    )
        except (ExternalAICallCancelled, RequestBudgetExceeded):
            raise
        except Exception as cache_error:
            logger.warning("exact cache lookup loi: %s", cache_error)
        log_trace(
            "cache",
            context.trace_id,
            cache_type="exact",
            hit=False,
            latency_ms=int((time.time() - lookup_started) * 1000),
        )
    return _CacheLookup(None, cache_scope)


def _complete_preparation(
    context: _PreparationContext,
    state: Any,
    cache_scope: str | None,
) -> PreparedRequest:
    state.checkpoint("history")
    history_text, history_summary, summary_covered = _prepare_history(
        list(context.chat_history),
        context.conversation_context,
        context.response_language,
        trace_id=context.trace_id,
        invoke_provider=state.invoke_provider,
    )
    state.checkpoint("history")
    state.checkpoint("vision")
    image_analysis = _analyze_image(
        context.image_path,
        context.user_question,
        context.trace_id,
        retry_budget=state.budget,
    )
    state.checkpoint("vision")
    return PreparedRequest(
        user_question=context.user_question,
        image_path=context.image_path,
        chat_history=context.chat_history,
        current_part_ids=context.current_part_ids,
        user_department=context.user_department,
        user_roles=context.user_roles,
        allowed_departments=context.allowed_departments,
        max_security_level=context.max_security_level,
        allowed_sites=context.allowed_sites,
        response_language=context.response_language,
        conversation_context=context.conversation_context,
        trace_id=context.trace_id,
        started_at=context.started_at,
        cache_scope=cache_scope,
        cache_eligible=context.cache_eligible,
        history_text=history_text,
        history_summary=history_summary,
        summary_covered=summary_covered,
        image_analysis=image_analysis,
    )


def prepare(state: Any) -> PreparationOutcome:
    """Normalize one request and apply pre-routing safety/cache policy."""

    state.transition("preparing")
    context = _normalize_request(state)
    _log_start(context)
    safety_terminal = _safety_terminal(context, state)
    if safety_terminal is not None:
        return safety_terminal
    cache_lookup = _lookup_exact_cache(context)
    if cache_lookup.terminal is not None:
        return cache_lookup.terminal
    return PreparationOutcome(
        prepared=_complete_preparation(context, state, cache_lookup.scope)
    )


__all__ = ["PreparationOutcome", "PreparedRequest", "PreparedValues", "prepare"]
