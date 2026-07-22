"""Routing and query preparation before retrieval."""

from __future__ import annotations

import time
from collections.abc import Callable, Iterator, Mapping
from dataclasses import dataclass
from typing import Any

from mech_chatbot.config.logging import log_trace, logger
from mech_chatbot.llm.external_ai import ExternalAICallCancelled
from mech_chatbot.rag.answer_policy import PolicyEvidence, decide_answer_policy
from mech_chatbot.rag.bootstrap import env_bool
from mech_chatbot.rag.corrective import correction_enabled
from mech_chatbot.rag.evidence_gate import EvidenceDecision, EvidenceState
from mech_chatbot.rag.execution import RequestBudgetExceeded
from mech_chatbot.rag.glossary_expand import glossary_expansion_terms
from mech_chatbot.rag.phases.contracts import PreparedValues, RoutingReason
from mech_chatbot.rag.phases.diagnostics import make_debug_info
from mech_chatbot.rag.phases.preparation import PreparedRequest
from mech_chatbot.rag.pipeline_steps import _rewrite_and_anchor, _route
from mech_chatbot.rag.prompt import _t_rag
from mech_chatbot.rag.rerank import tokenize_cached


@dataclass(frozen=True, slots=True)
class RouteDecision:
    request: PreparedRequest
    effective_question: str
    new_part_ids: tuple[str, ...]
    is_inherited: bool
    is_bom_query: bool
    intent_data: Mapping[str, Any]
    strict_filter: Any
    broad_filter: Any
    rbac_filter: Any
    skip_hyde_anchor: bool
    hyde_eligible: bool
    query_to_search: str
    cache_query_embedding: Any
    cache_scope: str | None
    crag_enabled: bool


@dataclass(frozen=True, slots=True)
class RoutingOutcome:
    decision: RouteDecision | None = None
    terminal: PreparedValues | None = None
    reason_code: RoutingReason = "routed"

    def __post_init__(self) -> None:
        if (self.decision is None) == (self.terminal is None):
            raise ValueError("routing must return exactly one outcome")


@dataclass(frozen=True, slots=True)
class _RouteSetup:
    mock_stream: Callable[[], Iterator[str]]
    embed_cached: Callable[[str], Any]


@dataclass(frozen=True, slots=True)
class _SemanticCacheLookup:
    terminal: RoutingOutcome | None
    query_embedding: Any
    scope: str | None


@dataclass(frozen=True, slots=True)
class _RewriteDecision:
    effective_question: str
    new_part_ids: tuple[str, ...]
    is_inherited: bool
    is_bom_query: bool
    intent_data: Mapping[str, Any]
    strict_filter: Any
    broad_filter: Any
    rbac_filter: Any
    skip_hyde_anchor: bool


@dataclass(frozen=True, slots=True)
class _SearchQuery:
    text: str
    hyde_eligible: bool


def _enter_route(prepared: PreparedRequest, state: Any) -> RoutingOutcome | _RouteSetup:
    state.transition("routing")
    route_terminal, route_bundle = _route(
        user_question=prepared.user_question,
        conversation_context=prepared.conversation_context,
        response_language=prepared.response_language,
        user_department=prepared.user_department,
        allowed_departments=list(prepared.allowed_departments),
        current_part_ids=list(prepared.current_part_ids),
        trace_id=prepared.trace_id,
        t_start=prepared.started_at,
        make_debug_info=make_debug_info,
        lifecycle=state,
    )
    state.checkpoint("routing")
    if route_terminal is not None:
        return RoutingOutcome(terminal=route_terminal, reason_code="route_terminal")
    return _RouteSetup(
        mock_stream=route_bundle["mock_stream"],
        embed_cached=route_bundle["_embed_cached"],
    )


def _semantic_cache_terminal(
    prepared: PreparedRequest,
    hit: Mapping[str, Any],
    lookup_started: float,
) -> RoutingOutcome:
    logger.info("Semantic cache HIT -> tra loi tu cache.")
    debug = {
        "retrieved_docs": hit.get("evidence_snapshot") or [],
        "citation_docs": hit.get("citation_snapshot") or [],
        "cache_hit": True,
    }

    def cached_stream():
        yield hit.get("answer", "")

    log_trace(
        "cache",
        prepared.trace_id,
        cache_type="semantic",
        hit=True,
        latency_ms=int((time.time() - lookup_started) * 1000),
    )
    log_trace(
        "rag_end",
        prepared.trace_id,
        final_latency_ms=int((time.time() - prepared.started_at) * 1000),
        refusal=False,
        cache_hit=True,
    )
    return RoutingOutcome(
        terminal=(
            cached_stream(),
            hit.get("ref_text", ""),
            hit.get("ref_images", []),
            prepared.current_part_ids,
            debug,
        ),
        reason_code="semantic_cache_hit",
    )


def _lookup_semantic_cache(
    prepared: PreparedRequest,
    embed_cached: Callable[[str], Any],
) -> _SemanticCacheLookup:
    query_embedding = None
    cache_scope = prepared.cache_scope
    lookup_started = time.time()
    try:
        import mech_chatbot.rag.semantic_cache as semantic_cache

        if semantic_cache.enabled() and prepared.cache_eligible:
            query_embedding = embed_cached(prepared.user_question)
            if cache_scope is None:
                cache_scope = semantic_cache.scope_signature(
                    prepared.user_department,
                    prepared.allowed_departments,
                    prepared.max_security_level,
                    prepared.allowed_sites,
                    prepared.user_roles,
                )
            hit = semantic_cache.lookup(
                prepared.user_question,
                query_embedding,
                cache_scope,
            )
            if hit:
                terminal = _semantic_cache_terminal(prepared, hit, lookup_started)
                return _SemanticCacheLookup(terminal, query_embedding, cache_scope)
    except (ExternalAICallCancelled, RequestBudgetExceeded):
        raise
    except Exception as cache_error:
        logger.warning("semantic cache lookup loi: %s", cache_error)
    if prepared.cache_eligible:
        log_trace(
            "cache",
            prepared.trace_id,
            cache_type="semantic",
            hit=False,
            latency_ms=int((time.time() - lookup_started) * 1000),
        )
    return _SemanticCacheLookup(None, query_embedding, cache_scope)


def _rewrite_request(prepared: PreparedRequest, state: Any) -> _RewriteDecision:
    intent_started = time.time()
    logger.info("Dang phan tich intent de tim kiem du lieu...")
    state.checkpoint("rewrite_and_anchor")
    values = _rewrite_and_anchor(
        user_question=prepared.user_question,
        chat_history=list(prepared.chat_history),
        current_part_ids=list(prepared.current_part_ids),
        conversation_context=prepared.conversation_context,
        user_department=prepared.user_department,
        user_roles=list(prepared.user_roles),
        allowed_departments=list(prepared.allowed_departments),
        max_security_level=prepared.max_security_level,
        allowed_sites=list(prepared.allowed_sites),
        trace_id=prepared.trace_id,
        t_intent=intent_started,
        invoke_provider=state.invoke_provider,
    )
    state.checkpoint("rewrite_and_anchor")
    return _RewriteDecision(
        effective_question=values[0],
        new_part_ids=tuple(values[1]),
        is_inherited=bool(values[2]),
        is_bom_query=bool(values[3]),
        intent_data=dict(values[4]),
        strict_filter=values[5],
        broad_filter=values[6],
        rbac_filter=values[7],
        skip_hyde_anchor=bool(values[8]),
    )


def _needs_version_clarification(rewrite: _RewriteDecision) -> bool:
    return bool(
        rewrite.intent_data.get("version_policy") == "compare_versions"
        and not rewrite.intent_data.get("detected_versions")
    )


def _version_policy(prepared: PreparedRequest):
    return decide_answer_policy(
        prepared.user_question,
        PolicyEvidence(
            decision=EvidenceDecision(
                EvidenceState.AMBIGUOUS,
                reason="missing_compare_versions",
                stage="intent",
                telemetry_status="heuristic_block",
            ),
            has_retrieved_evidence=False,
            clarification_required=True,
        ),
        {},
    )


def _missing_version_terminal(prepared: PreparedRequest, state: Any) -> RoutingOutcome:
    clarification_text = (
        "Bạn muốn so sánh tài liệu này với phiên bản nào? (Ví dụ: v1 và v2, "
        "hoặc bản đang lưu hành và bản bị lưu trữ gần nhất). Vui lòng chỉ "
        "định rõ phiên bản để mình đối chiếu số liệu chính xác nhé."
    )

    def ask_version_stream():
        yield _t_rag(clarification_text, prepared.response_language)

    policy = _version_policy(prepared)
    log_trace(
        "evidence_gate",
        prepared.trace_id,
        answerable=False,
        state=policy.evidence_state.value,
        outcome=policy.outcome.value,
        correction_allowed=False,
        stage="intent",
        status="heuristic_block",
        reason=policy.reason,
    )
    log_trace(
        "rag_end",
        prepared.trace_id,
        final_latency_ms=int((time.time() - prepared.started_at) * 1000),
        refusal=True,
        refusal_reason="missing_compare_versions",
    )
    debug = make_debug_info([])
    debug.update(
        {
            "answer_outcome": policy.outcome.value,
            "evidence_state": policy.evidence_state.value,
            "evidence_stage": "intent",
            "correction_allowed": False,
            "correction_count": 0,
        }
    )
    state.refuse("clarification_required")
    return RoutingOutcome(
        terminal=(ask_version_stream(), "", (), prepared.current_part_ids, debug),
        reason_code="missing_compare_versions",
    )


def _chitchat_terminal(
    prepared: PreparedRequest,
    mock_stream: Callable[[], Iterator[str]],
) -> RoutingOutcome:
    logger.info("LLM xac nhan la cau hoi ngoai le/xa giao. Bo qua Retrieval.")
    log_trace(
        "route",
        prepared.trace_id,
        route="chitchat",
        layer="L2_llm_intent",
        confidence=1.0,
    )
    log_trace(
        "rag_end",
        prepared.trace_id,
        final_latency_ms=int((time.time() - prepared.started_at) * 1000),
        refusal=False,
        is_chitchat=True,
    )
    return RoutingOutcome(
        terminal=(
            mock_stream(),
            "",
            (),
            prepared.current_part_ids,
            make_debug_info([]),
        ),
        reason_code="chitchat",
    )


def _build_search_query(
    prepared: PreparedRequest,
    rewrite: _RewriteDecision,
) -> _SearchQuery:
    tokenized_question = tokenize_cached(rewrite.effective_question)
    query_to_search = tokenized_question
    hyde_eligible = (
        env_bool("HYDE_ENABLED", True)
        and len(tokenized_question.split()) < 25
        and not rewrite.new_part_ids
        and not rewrite.skip_hyde_anchor
    )
    try:
        glossary_terms = glossary_expansion_terms(
            rewrite.effective_question,
            prepared.user_department,
        )
        if glossary_terms:
            query_to_search += " " + tokenize_cached(glossary_terms)
            log_trace(
                "glossary_expansion",
                prepared.trace_id,
                added=glossary_terms[:200],
            )
    except (ExternalAICallCancelled, RequestBudgetExceeded):
        raise
    except Exception as glossary_error:
        logger.warning("glossary expansion loi: %s", glossary_error)
    return _SearchQuery(str(query_to_search), bool(hyde_eligible))


def _build_route_decision(
    prepared: PreparedRequest,
    rewrite: _RewriteDecision,
    search: _SearchQuery,
    cache: _SemanticCacheLookup,
) -> RouteDecision:
    return RouteDecision(
        request=prepared,
        effective_question=rewrite.effective_question,
        new_part_ids=rewrite.new_part_ids,
        is_inherited=rewrite.is_inherited,
        is_bom_query=rewrite.is_bom_query,
        intent_data=rewrite.intent_data,
        strict_filter=rewrite.strict_filter,
        broad_filter=rewrite.broad_filter,
        rbac_filter=rewrite.rbac_filter,
        skip_hyde_anchor=rewrite.skip_hyde_anchor,
        hyde_eligible=search.hyde_eligible,
        query_to_search=search.text,
        cache_query_embedding=cache.query_embedding,
        cache_scope=cache.scope,
        crag_enabled=correction_enabled(),
    )


def route(prepared: PreparedRequest, state: Any) -> RoutingOutcome:
    """Select a terminal response or produce a typed retrieval decision."""

    setup = _enter_route(prepared, state)
    if isinstance(setup, RoutingOutcome):
        return setup
    cache = _lookup_semantic_cache(prepared, setup.embed_cached)
    if cache.terminal is not None:
        return cache.terminal
    rewrite = _rewrite_request(prepared, state)
    if _needs_version_clarification(rewrite):
        return _missing_version_terminal(prepared, state)
    if rewrite.intent_data.get("is_chitchat"):
        return _chitchat_terminal(prepared, setup.mock_stream)
    search = _build_search_query(prepared, rewrite)
    return RoutingOutcome(
        decision=_build_route_decision(prepared, rewrite, search, cache)
    )


__all__ = ["RouteDecision", "RoutingOutcome", "route"]
