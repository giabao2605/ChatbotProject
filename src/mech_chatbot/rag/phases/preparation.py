"""Request preparation before routing or retrieval."""

from __future__ import annotations

import time
from collections.abc import Iterator, Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from mech_chatbot.config.logging import log_trace, logger
from mech_chatbot.llm.llm_client import get_llm_model_name
from mech_chatbot.rag.pipeline_steps import _analyze_image, _prepare_history


PreparedValues = tuple[
    Iterator[str],
    str,
    Sequence[str],
    Sequence[str],
    Mapping[str, Any],
]


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

    def __post_init__(self) -> None:
        if (self.prepared is None) == (self.terminal is None):
            raise ValueError("preparation must return exactly one outcome")


def _empty_debug() -> dict[str, Any]:
    try:
        from mech_chatbot.rag.semantic_cache import pipeline_namespace

        namespace = pipeline_namespace()
    except Exception:
        namespace = "unknown"
    return {"pipeline_namespace": namespace, "retrieved_docs": []}


def prepare(state: Any) -> PreparationOutcome:
    """Normalize one request and apply pre-routing safety/cache policy."""

    request = state.request
    state.transition("preparing")
    user_question = request.question
    image_path = str(request.image_path) if request.image_path is not None else None
    chat_history = tuple(request.history)
    current_part_ids = tuple(request.current_part_ids)
    user_department = request.access.department
    user_roles = tuple(request.access.roles)
    allowed_departments = tuple(request.access.allowed_departments)
    max_security_level = request.access.max_security_level
    allowed_sites = tuple(request.access.allowed_sites)
    response_language = request.response_language
    conversation_context = (
        dict(request.conversation_context)
        if request.conversation_context is not None
        else None
    )
    trace_id = state.trace_id
    started_at = time.time()

    log_trace(
        "rag_start",
        trace_id,
        question_length=len(user_question or ""),
        has_image=bool(image_path),
        history_count=len(chat_history),
        current_part_ids=list(current_part_ids),
        department=user_department,
        role=",".join(user_roles) if user_roles else "",
        model=get_llm_model_name(),
        pipeline_namespace=_empty_debug()["pipeline_namespace"],
    )

    try:
        from mech_chatbot.rag import route_safety

        safety_reason = route_safety.detect(user_question) if route_safety.enabled() else None
    except Exception as safety_error:
        logger.error(
            "Pre-cache safety check failed: %s",
            safety_error,
            exc_info=True,
        )
        safety_reason = "safety_check_unavailable"
    if safety_reason:
        state.refuse("safety_block")
        from mech_chatbot.rag import route_responses

        safety_text = route_responses.build_safety_response(
            response_language,
            user_department,
            allowed_departments,
        )

        def safety_stream() -> Iterator[str]:
            yield safety_text

        log_trace(
            "safety",
            trace_id,
            reason=safety_reason,
            blocked=True,
            layer="pre_cache",
        )
        log_trace(
            "rag_end",
            trace_id,
            final_latency_ms=int((time.time() - started_at) * 1000),
            refusal=True,
            refusal_reason="safety_block",
        )
        return PreparationOutcome(
            terminal=(safety_stream(), "", (), current_part_ids, _empty_debug())
        )

    cache_scope = None
    cache_eligible = not any(
        (image_path, chat_history, current_part_ids, bool(conversation_context))
    )
    exact_cache_started = time.time()
    if cache_eligible:
        try:
            import mech_chatbot.rag.semantic_cache as semantic_cache

            if semantic_cache.enabled():
                cache_scope = semantic_cache.scope_signature(
                    user_department,
                    allowed_departments,
                    max_security_level,
                    allowed_sites,
                    user_roles,
                )
                exact_hit = semantic_cache.lookup_exact(user_question, cache_scope)
                if exact_hit:
                    logger.info("Exact cache HIT -> tra loi truoc router/embedding.")
                    debug = {
                        "retrieved_docs": exact_hit.get("evidence_snapshot") or [],
                        "citation_docs": exact_hit.get("citation_snapshot") or [],
                        "cache_hit": True,
                        "cache_type": "exact",
                    }

                    def exact_cached_stream() -> Iterator[str]:
                        yield exact_hit.get("answer", "")

                    log_trace(
                        "cache",
                        trace_id,
                        cache_type="exact",
                        hit=True,
                        latency_ms=int((time.time() - exact_cache_started) * 1000),
                    )
                    log_trace(
                        "rag_end",
                        trace_id,
                        final_latency_ms=int((time.time() - started_at) * 1000),
                        refusal=False,
                        cache_hit=True,
                        cache_type="exact",
                    )
                    return PreparationOutcome(
                        terminal=(
                            exact_cached_stream(),
                            exact_hit.get("ref_text", ""),
                            exact_hit.get("ref_images", []),
                            current_part_ids,
                            debug,
                        )
                    )
        except Exception as cache_error:
            logger.warning("exact cache lookup loi: %s", cache_error)
    if cache_eligible:
        log_trace(
            "cache",
            trace_id,
            cache_type="exact",
            hit=False,
            latency_ms=int((time.time() - exact_cache_started) * 1000),
        )

    state.checkpoint("history")
    history_text, history_summary, summary_covered = _prepare_history(
        list(chat_history),
        conversation_context,
        response_language,
        trace_id=trace_id,
    )
    state.checkpoint("history")
    state.checkpoint("vision")
    image_analysis = _analyze_image(
        image_path,
        user_question,
        trace_id,
        retry_budget=state.budget,
    )
    state.checkpoint("vision")

    return PreparationOutcome(
        prepared=PreparedRequest(
            user_question=user_question,
            image_path=image_path,
            chat_history=chat_history,
            current_part_ids=current_part_ids,
            user_department=user_department,
            user_roles=user_roles,
            allowed_departments=allowed_departments,
            max_security_level=max_security_level,
            allowed_sites=allowed_sites,
            response_language=response_language,
            conversation_context=conversation_context,
            trace_id=trace_id,
            started_at=started_at,
            cache_scope=cache_scope,
            cache_eligible=cache_eligible,
            history_text=history_text,
            history_summary=history_summary,
            summary_covered=summary_covered,
            image_analysis=image_analysis,
        )
    )


__all__ = ["PreparationOutcome", "PreparedRequest", "prepare"]
