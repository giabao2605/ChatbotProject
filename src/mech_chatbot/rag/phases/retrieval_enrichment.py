"""Graph, governed BOM, correction, and access enrichment."""

from __future__ import annotations

import time
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from langchain_core.messages import HumanMessage

from mech_chatbot.config.logging import log_trace, logger
from mech_chatbot.db.repositories.bom import search_bom_facts
from mech_chatbot.db.repositories.graph import traverse_knowledge_graph
from mech_chatbot.llm.external_ai import ExternalAICallCancelled
from mech_chatbot.rag.answer_policy import (
    PolicyEvidence,
    decide_answer_policy,
    explicit_negative_evidence_quote,
    has_explicit_negative_evidence,
)
from mech_chatbot.rag.context_builders import _context_is_mechanical
from mech_chatbot.rag.corrective import (
    merge_corrected_documents,
    run_corrected_retrieval,
    should_attempt_correction,
)
from mech_chatbot.rag.evidence_gate import (
    EvidenceDecision,
    EvidenceState,
    evaluate_answerability,
)
from mech_chatbot.rag.execution import RequestBudgetExceeded, current_execution_context
from mech_chatbot.rag.intent import serialize_qdrant_filter
from mech_chatbot.rag.phases.contracts import PhaseTerminal
from mech_chatbot.rag.phases.diagnostics import (
    make_debug_info,
    make_terminal_debug as _make_terminal_debug,
)
from mech_chatbot.rag.phases.retrieval import PrimaryRetrievalOutcome
from mech_chatbot.rag.phases.retrieval_enrichment_support import (
    inject_bom,
    prepend_image,
)
from mech_chatbot.rag.phases.routing import RouteDecision
from mech_chatbot.rag.pipeline_steps import _assemble_context, _disambiguate
from mech_chatbot.rag.prompt import _normalize_lang, _t_rag
from mech_chatbot.rag.rerank import tokenize_cached
from mech_chatbot.rag.retrieval import current_published_filter, probe_restricted_access


@dataclass(frozen=True, slots=True)
class EnrichmentOutcome:
    documents: tuple[Any, ...]
    new_part_ids: tuple[str, ...]
    base_k: int
    retrieval_mode: str
    active_filter: Any
    has_active_filter: bool
    served_graph_documents: tuple[Any, ...]
    graph_documents: tuple[Any, ...]
    graph_routed: bool
    graph_edge_count: int
    graph_max_hops: int
    community_documents: tuple[Any, ...]
    community_summary_used: bool
    community_summary_count: int
    community_fallback_reason: str
    grounded_math_enabled: bool
    auxiliary_input_tokens: int
    auxiliary_output_tokens: int
    correction_estimated_cost: float
    reason_code: str = "enriched"


@dataclass(frozen=True, slots=True)
class _EnrichmentContext:
    decision: RouteDecision
    trace_id: str
    user_question: str
    response_language: str
    current_part_ids: tuple[str, ...]
    user_roles: tuple[str, ...]
    allowed_departments: tuple[str, ...]
    allowed_sites: tuple[str, ...]
    runtime: Any


@dataclass(frozen=True, slots=True)
class _GraphResult:
    documents: tuple[Any, ...]
    graph_documents: tuple[Any, ...]
    routed: bool
    edge_count: int
    max_hops: int
    seeds: tuple[str, ...]
    graph_enabled: bool
    community_enabled: bool
    access: Mapping[str, Any]


@dataclass(frozen=True, slots=True)
class _CommunityResult:
    documents: tuple[Any, ...]
    community_documents: tuple[Any, ...]
    used: bool
    summary_count: int
    fallback_reason: str


@dataclass(frozen=True, slots=True)
class _CodeMissResult:
    documents: tuple[Any, ...]
    part_ids: tuple[str, ...]
    retrieval_mode: str
    active_filter: Any
    has_active_filter: bool
    terminal: PhaseTerminal | None = None


@dataclass(frozen=True, slots=True)
class _CorrectionResult:
    documents: tuple[Any, ...]
    input_tokens: int = 0
    output_tokens: int = 0
    estimated_cost: float = 0.0


def _make_context(decision: RouteDecision, state: Any) -> _EnrichmentContext:
    request = decision.request
    return _EnrichmentContext(
        decision=decision,
        trace_id=request.trace_id,
        user_question=request.user_question,
        response_language=request.response_language,
        current_part_ids=tuple(request.current_part_ids),
        user_roles=tuple(request.user_roles),
        allowed_departments=tuple(request.allowed_departments),
        allowed_sites=tuple(request.allowed_sites),
        runtime=state.retrieval_adapter,
    )


def _graph_access(context: _EnrichmentContext) -> dict[str, Any]:
    return {
        "roles": list(context.user_roles or ()),
        "allowed_departments": list(context.allowed_departments or ()),
        "allowed_sites": list(context.allowed_sites or ()),
        "max_security_level": context.decision.request.max_security_level,
    }


def _enrich_graph(
    context: _EnrichmentContext,
    documents: Sequence[Any],
    state: Any,
) -> _GraphResult:
    runtime = state.retrieval_adapter
    graph_enabled = bool(getattr(runtime, "graph_retrieval_enabled", False))
    community_enabled = bool(
        getattr(runtime, "community_summaries_enabled", False)
    )
    access = _graph_access(context)
    if not graph_enabled:
        return _graph_disabled_result(
            documents, graph_enabled, community_enabled, access
        )
    from mech_chatbot.rag.graph_retrieval import (
        filter_servable_edges,
        hydrate_graph_edges,
        select_graph_seeds,
        should_attempt_graph,
    )
    started_at, seeds = _begin_graph(context, state, select_graph_seeds)
    routed = False
    max_hops = 0
    graph_documents: list[Any] = []
    try:
        routed = should_attempt_graph(context.decision.effective_question)
        if routed:
            max_hops = 2
            edges = filter_servable_edges(
                traverse_knowledge_graph(seeds, access, max_hops=2, limit=50), access
            )
            graph_documents = hydrate_graph_edges(
                edges,
                getattr(runtime, "client", None),
                getattr(runtime, "collection_name", "TaiLieuKyThuat_v2"),
            )
        else:
            edges = []
        state.budget.record("graph_edges", len(edges))
        state.checkpoint("graph")
        _log_graph(context, started_at, routed, seeds, edges, graph_documents)
        merged = merge_corrected_documents(documents, graph_documents)
        return _GraphResult(
            tuple(merged), tuple(graph_documents), routed, len(edges), max_hops,
            tuple(seeds), graph_enabled, community_enabled, access,
        )
    except (ExternalAICallCancelled, RequestBudgetExceeded, TimeoutError):
        raise
    except Exception as exc:
        return _graph_failure_result(
            context, documents, graph_documents, routed, max_hops, seeds,
            graph_enabled, community_enabled, access, exc,
        )


def _begin_graph(
    context: _EnrichmentContext, state: Any, select_graph_seeds: Any
) -> tuple[float, Sequence[str]]:
    state.checkpoint("graph")
    started_at = time.time()
    seeds = select_graph_seeds(
        context.decision.effective_question, list(context.decision.new_part_ids)
    )
    return started_at, seeds


def _graph_disabled_result(
    documents: Sequence[Any],
    graph_enabled: bool,
    community_enabled: bool,
    access: Mapping[str, Any],
) -> _GraphResult:
    return _GraphResult(
        tuple(documents), (), False, 0, 0, (), graph_enabled,
        community_enabled, access,
    )


def _graph_failure_result(
    context: _EnrichmentContext,
    documents: Sequence[Any],
    graph_documents: Sequence[Any],
    routed: bool,
    max_hops: int,
    seeds: Sequence[str],
    graph_enabled: bool,
    community_enabled: bool,
    access: Mapping[str, Any],
    error: Exception,
) -> _GraphResult:
    logger.warning("Graph retrieval unavailable: %s", error)
    log_trace(
        "graph_retrieval", context.trace_id,
        error=type(error).__name__, edge_count=0,
    )
    return _GraphResult(
        tuple(documents), tuple(graph_documents), routed, 0, max_hops,
        tuple(seeds), graph_enabled, community_enabled, access,
    )


def _log_graph(
    context: _EnrichmentContext,
    started_at: float,
    routed: bool,
    seeds: Sequence[str],
    edges: Sequence[Any],
    documents: Sequence[Any],
) -> None:
    log_trace(
        "graph_retrieval", context.trace_id,
        latency_ms=int((time.time() - started_at) * 1000),
        routed=routed, route_scope="relational" if routed else "regular",
        seed_count=len(seeds), edge_count=len(edges),
        hydrated_count=len(documents), max_hops=2, edge_limit=50,
    )


def _enrich_community(
    context: _EnrichmentContext,
    graph: _GraphResult,
    state: Any,
) -> _CommunityResult:
    from mech_chatbot.rag.community_summaries import load_community_context

    started_at = time.time()
    runtime = state.retrieval_adapter
    serving_epoch = getattr(
        runtime,
        "community_serving_epoch",
        "community-v1",
    )
    result = load_community_context(
        context.decision.effective_question,
        graph_enabled=graph.graph_enabled,
        community_enabled=graph.community_enabled,
        access_context=graph.access,
        seed_keys=list(graph.seeds),
        serving_epoch=serving_epoch,
        graph_fingerprint=getattr(runtime, "graph_fingerprint", "") or "",
        client=getattr(runtime, "client", None),
        collection_name=getattr(
            runtime,
            "collection_name",
            "TaiLieuKyThuat_v2",
        ),
    )
    community_documents = list(result.documents)
    documents = list(graph.documents)
    if community_documents:
        documents = merge_corrected_documents(documents, community_documents)
    log_trace(
        "community_summaries", context.trace_id,
        latency_ms=int((time.time() - started_at) * 1000), used=result.used,
        summary_count=result.summary_count,
        source_document_count=len(community_documents),
        fallback_reason=result.reason,
        serving_epoch=serving_epoch,
    )
    return _CommunityResult(
        tuple(documents), tuple(community_documents), result.used,
        result.summary_count, result.reason,
    )


def _message_stream(message: str) -> Any:
    def stream() -> Any:
        yield message

    return stream()


def _access_denied_terminal(
    context: _EnrichmentContext,
    state: Any,
    access_reason: str,
) -> PhaseTerminal:
    state.refuse("access_denied")
    stub_vi = (
        "Có tài liệu liên quan đến câu hỏi, nhưng tài khoản của bạn chưa đủ quyền truy cập. "
        "Nội dung được bảo vệ theo chính sách truy cập.\n\n"
        "Bạn có thể vào trang 'Yêu cầu quyền' để gửi yêu cầu cấp quyền; "
        "quản trị hoặc phụ trách phòng ban sẽ duyệt."
    )
    stub_en = (
        "There are documents related to your question, but your account is not cleared "
        "to access them. The content is protected by access control.\n\n"
        "You can open the 'Access requests' page to request access; "
        "an admin or department owner will review it."
    )
    message = stub_en if _normalize_lang(context.response_language) == "en" else stub_vi
    debug = _make_terminal_debug(
        context.user_question, "access_denied", [], access_denied=True
    )
    debug["access_hint"] = {
        "restricted": True,
        "reason": access_reason,
        "question": context.user_question,
    }
    log_trace(
        "rag_end", context.trace_id,
        final_latency_ms=int((time.time() - context.decision.request.started_at) * 1000),
        refusal=True, refusal_reason="access_denied", access_reason=access_reason,
    )
    return PhaseTerminal(
        prepared=state.prepared(
            (_message_stream(message), "", [], list(context.current_part_ids), debug)
        ),
        reason_code="access_denied",
    )


def _probe_exact_code_access(
    context: _EnrichmentContext,
    part_ids: Sequence[str],
) -> tuple[bool, str | None]:
    request = context.decision.request
    try:
        return probe_restricted_access(
            context.decision.query_to_search,
            user_department=request.user_department,
            allowed_departments=list(context.allowed_departments),
            max_security_level=request.max_security_level,
            allowed_sites=list(context.allowed_sites),
            part_ids=list(part_ids),
            client=getattr(context.runtime, "client", None),
            collection_name=getattr(context.runtime, "collection_name", None),
        )
    except (ExternalAICallCancelled, RequestBudgetExceeded):
        raise
    except Exception:
        return False, None


def _exact_code_terminal(
    context: _EnrichmentContext,
    state: Any,
    part_ids: Sequence[str],
) -> PhaseTerminal:
    codes = ", ".join(part_ids)
    if _normalize_lang(context.response_language) == "en":
        message = (
            f"Sorry, I couldn't find the code '{codes}' in the current drawing system. "
            "Please double-check the code or provide more details."
        )
    else:
        message = (
            f"Rất tiếc, mình không tìm thấy mã số '{codes}' nào trong hệ thống bản vẽ hiện tại. "
            "Vui lòng kiểm tra lại mã hoặc mô tả rõ hơn."
        )
    log_trace(
        "rag_end", context.trace_id,
        final_latency_ms=int((time.time() - context.decision.request.started_at) * 1000),
        refusal=True, refusal_reason="no_docs_for_exact_code",
    )
    state.refuse("no_docs_for_exact_code")
    return PhaseTerminal(
        prepared=state.prepared((
            _message_stream(message), "", [], list(context.current_part_ids),
            _make_terminal_debug(context.user_question, "no_docs_for_exact_code"),
        )),
        reason_code="no_docs_for_exact_code",
    )


def _handle_exact_code_miss(
    context: _EnrichmentContext,
    state: Any,
    documents: Sequence[Any],
    retrieval_mode: str,
    active_filter: Any,
    has_active_filter: bool,
) -> _CodeMissResult:
    part_ids = list(context.decision.new_part_ids)
    if documents or not part_ids:
        return _CodeMissResult(
            tuple(documents), tuple(part_ids), retrieval_mode,
            active_filter, has_active_filter,
        )
    if not context.decision.is_inherited:
        logger.info(
            "Khong tim thay bat ky tai lieu nao cho ma %s. Tu choi fallback semantic.",
            part_ids,
        )
        blocked, reason = _probe_exact_code_access(context, part_ids)
        terminal = (
            _access_denied_terminal(context, state, reason)
            if blocked and reason
            else _exact_code_terminal(context, state, part_ids)
        )
        return _CodeMissResult((), tuple(part_ids), retrieval_mode, active_filter,
                               has_active_filter, terminal)
    logger.info(
        "Khong co doc cho ma KE THUA %s. Huy ke thua, tim kiem chung.", part_ids
    )
    try:
        general_filter = current_published_filter(context.decision.rbac_filter)
        general_retriever = state.retrieval_adapter.vectorstore.as_retriever(
            search_type="similarity", search_kwargs={"k": 30, "filter": general_filter}
        )
        fallback_documents = general_retriever.invoke(context.decision.query_to_search)
        return _CodeMissResult(
            tuple(fallback_documents), (), "general_after_inherit_miss",
            general_filter, True,
        )
    except (ExternalAICallCancelled, RequestBudgetExceeded):
        raise
    except Exception as fallback_error:
        logger.warning("Fallback general sau inherit-miss loi: %s", fallback_error)
        return _CodeMissResult((), (), retrieval_mode, active_filter, has_active_filter)


def _disambiguate_documents(
    context: _EnrichmentContext,
    state: Any,
    documents: Sequence[Any],
    part_ids: Sequence[str],
    primary: PrimaryRetrievalOutcome,
    retrieval_mode: str,
) -> tuple[tuple[Any, ...], PhaseTerminal | None]:
    terminal, resolved_documents = _disambiguate(
        retrieved_docs=list(documents),
        user_question=context.user_question,
        new_part_ids=list(part_ids),
        intent_data=dict(context.decision.intent_data),
        response_language=context.response_language,
        current_part_ids=list(context.current_part_ids),
        trace_id=context.trace_id,
        t_start=context.decision.request.started_at,
        make_debug_info=make_debug_info,
        lifecycle=state,
    )
    if terminal is not None:
        return (), PhaseTerminal(
            prepared=state.prepared(terminal), reason_code="disambiguation_required"
        )
    _log_retrieval(
        context, resolved_documents, part_ids, primary, retrieval_mode
    )
    return tuple(resolved_documents), None


def _log_retrieval(
    context: _EnrichmentContext,
    documents: Sequence[Any],
    part_ids: Sequence[str],
    primary: PrimaryRetrievalOutcome,
    retrieval_mode: str,
) -> None:
    intent_data = context.decision.intent_data
    log_trace(
        "retrieval", context.trace_id,
        latency_ms=int((time.time() - primary.started_at) * 1000),
        mode=retrieval_mode, docs_count=len(documents),
        is_bom_query=context.decision.is_bom_query if part_ids else False,
        part_ids=list(part_ids), version_policy=intent_data.get("version_policy"),
        detected_versions=intent_data.get("detected_versions"),
        variant_codes=intent_data.get("variant_codes"),
        strict_filter=serialize_qdrant_filter(context.decision.strict_filter),
        broad_filter=serialize_qdrant_filter(context.decision.broad_filter),
        top_k=primary.base_k,
    )


def _inject_bom(
    context: _EnrichmentContext,
    documents: Sequence[Any],
    part_ids: Sequence[str],
    state: Any,
) -> tuple[tuple[Any, ...], bool]:
    grounded_math_enabled = bool(
        getattr(
            state.retrieval_adapter,
            "grounded_math_enabled",
            False,
        )
    )
    return inject_bom(
        context, documents, part_ids,
        env_bool=lambda _name, _default: grounded_math_enabled,
        context_is_mechanical=_context_is_mechanical,
        search_bom_facts=search_bom_facts,
    )


def _prepend_image(
    documents: Sequence[Any], image_analysis: str
) -> tuple[Any, ...]:
    return prepend_image(documents, image_analysis)


def _empty_documents_terminal(
    context: _EnrichmentContext,
    state: Any,
    part_ids: Sequence[str],
) -> PhaseTerminal:
    logger.warning("BLOCKER: Khong tim thay tai lieu nao, chan LLM de tranh hallucination.")
    blocked, reason = _probe_exact_code_access(context, part_ids)
    if blocked and reason:
        return _access_denied_terminal(context, state, reason)
    message = _t_rag(
        "Tài liệu hiện tại chưa có dữ liệu liên quan đến câu hỏi của bạn. "
        "Mình không thể trả lời dựa trên suy đoán. "
        "Vui lòng nạp tài liệu vào hệ thống trước, hoặc hỏi nội dung đã có trong dữ liệu.",
        context.response_language,
    )
    log_trace(
        "rag_end", context.trace_id,
        final_latency_ms=int((time.time() - context.decision.request.started_at) * 1000),
        refusal=True, refusal_reason="no_retrieved_docs", docs_count=0,
    )
    state.refuse("no_retrieved_docs")
    return PhaseTerminal(
        prepared=state.prepared((
            _message_stream(message), "", [], list(context.current_part_ids),
            _make_terminal_debug(context.user_question, "no_retrieved_docs"),
        )),
        reason_code="no_retrieved_docs",
    )


def _coverage_policy(
    context: _EnrichmentContext,
    documents: Sequence[Any],
    state: Any,
) -> tuple[Any, Any]:
    runtime = state.retrieval_adapter
    preliminary_context = _assemble_context(documents, context.user_question)
    decision = evaluate_answerability(
        context.user_question, preliminary_context,
        docs=documents, trace_id=context.trace_id,
        strict_answer_mode=bool(
            getattr(runtime, "strict_answer_mode", True)
        ),
        crag_enabled=bool(getattr(runtime, "crag_enabled", False)),
        verifier_enabled=bool(
            getattr(runtime, "evidence_verifier_enabled", False)
        ),
    )
    force_ambiguous = bool(
        getattr(state.invocation, "evaluation_force_ambiguous", False)
    )
    if not force_ambiguous and current_execution_context() == "evaluation":
        force_ambiguous = bool(
            getattr(runtime, "evaluation_force_ambiguous", False)
        )
    if force_ambiguous:
        decision = EvidenceDecision(
            EvidenceState.AMBIGUOUS,
            reason="controlled_evaluation_correction_fixture",
            stage="evaluation_fixture", telemetry_status="heuristic_block",
        )
        log_trace("evaluation_override", context.trace_id, override="force_ambiguous")
    policy = decide_answer_policy(
        context.user_question,
        PolicyEvidence(
            decision=decision, has_retrieved_evidence=bool(documents),
            retrieval_can_improve=True,
            negative_evidence=has_explicit_negative_evidence(
                context.user_question, preliminary_context
            ),
            negative_evidence_quote=explicit_negative_evidence_quote(
                context.user_question, preliminary_context
            ),
        ),
        {},
    )
    return decision, policy


def _correct_retrieval(
    context: _EnrichmentContext,
    state: Any,
    documents: Sequence[Any],
    part_ids: Sequence[str],
    coverage_decision: Any,
) -> _CorrectionResult:
    started_at = time.time()
    input_tokens = 0
    output_tokens = 0
    state.budget.record("corrections", 1, cumulative=True)
    try:
        state.checkpoint("corrective_retrieval")
        prompt = _correction_prompt(context, coverage_decision)
        rewritten = state.invoke_provider(
            [HumanMessage(content=prompt)], surface="query_disambiguation",
            trace_id=context.trace_id, retry_counter=state.budget,
        ).content
        state.checkpoint("corrective_retrieval")
        input_tokens = len(prompt) // 4
        output_tokens = len(str(rewritten)) // 4
        estimated_cost = (input_tokens * 2.5 + output_tokens * 15.0) / 1_000_000
        corrected_documents, _, corrected_mode, _, _ = run_corrected_retrieval(
            state.retrieve,
            corrected_query=tokenize_cached(
                str(rewritten or context.decision.effective_question)
            ),
            new_part_ids=list(part_ids),
            strict_filter=context.decision.strict_filter,
            broad_filter=context.decision.broad_filter,
            is_bom_query=context.decision.is_bom_query,
            rbac_filter=context.decision.rbac_filter,
            trace_id=context.trace_id,
        )
        state.checkpoint("corrective_retrieval")
        return _successful_correction(
            context, state, started_at, documents, corrected_documents,
            corrected_mode, coverage_decision, input_tokens, output_tokens,
            estimated_cost,
        )
    except (ExternalAICallCancelled, RequestBudgetExceeded, TimeoutError):
        raise
    except Exception as exc:
        logger.warning("Corrective retrieval failed: %s", exc)
        _log_correction_failure(
            context, state, started_at, documents, coverage_decision, exc
        )
        return _CorrectionResult(
            tuple(documents), input_tokens=input_tokens, output_tokens=output_tokens
        )


def _correction_prompt(
    context: _EnrichmentContext, coverage_decision: Any
) -> str:
    return (
        "Rewrite this internal-document search query once to improve evidence recall. "
        "Keep every technical code and do not add facts. Return only the query.\n\n"
        f"Question: {context.decision.effective_question}\n"
        f"Missing evidence: {coverage_decision.reason}"
    )


def _successful_correction(
    context: _EnrichmentContext,
    state: Any,
    started_at: float,
    documents: Sequence[Any],
    corrected_documents: Sequence[Any],
    corrected_mode: str,
    coverage_decision: Any,
    input_tokens: int,
    output_tokens: int,
    estimated_cost: float,
) -> _CorrectionResult:
    merged = merge_corrected_documents(documents, corrected_documents)
    _log_correction(
        context, state, started_at, documents, corrected_documents,
        merged, corrected_mode, coverage_decision, estimated_cost,
    )
    return _CorrectionResult(
        tuple(merged), input_tokens, output_tokens, estimated_cost
    )


def _log_correction_failure(
    context: _EnrichmentContext,
    state: Any,
    started_at: float,
    documents: Sequence[Any],
    coverage_decision: Any,
    error: Exception,
) -> None:
    log_trace(
        "corrective_retrieval", context.trace_id,
        latency_ms=int((time.time() - started_at) * 1000),
        strategy="query_rewrite", attempt=state.budget.corrections,
        before_docs=len(documents), after_docs=len(documents),
        evaluator_state=coverage_decision.state.value,
        error=type(error).__name__,
    )


def _log_correction(
    context: _EnrichmentContext,
    state: Any,
    started_at: float,
    documents: Sequence[Any],
    corrected_documents: Sequence[Any],
    merged: Sequence[Any],
    corrected_mode: str,
    coverage_decision: Any,
    estimated_cost: float,
) -> None:
    log_trace(
        "corrective_retrieval", context.trace_id,
        latency_ms=int((time.time() - started_at) * 1000), strategy="query_rewrite",
        attempt=state.budget.corrections, before_docs=len(documents),
        corrected_docs=len(corrected_documents), after_docs=len(merged),
        retrieval_mode=corrected_mode,
        evaluator_state=coverage_decision.state.value,
        estimated_cost=estimated_cost,
    )


def _apply_crag(
    context: _EnrichmentContext,
    state: Any,
    documents: Sequence[Any],
    part_ids: Sequence[str],
) -> _CorrectionResult:
    if not documents or not context.decision.crag_enabled:
        return _CorrectionResult(tuple(documents))
    decision, policy = _coverage_policy(context, documents, state)
    if not should_attempt_correction(
        policy, attempts=state.budget.corrections,
        enabled=context.decision.crag_enabled,
    ):
        return _CorrectionResult(tuple(documents))
    return _correct_retrieval(context, state, documents, part_ids, decision)


def enrich_retrieval(
    decision: RouteDecision,
    primary: PrimaryRetrievalOutcome,
    state: Any,
) -> EnrichmentOutcome | PhaseTerminal:
    context = _make_context(decision, state)
    graph = _enrich_graph(context, primary.documents, state)
    community = _enrich_community(context, graph, state)
    code_result = _handle_exact_code_miss(
        context, state, community.documents, primary.retrieval_mode,
        primary.active_filter, primary.has_active_filter,
    )
    if code_result.terminal is not None:
        return code_result.terminal
    state.transition("retrieval")
    documents, terminal = _disambiguate_documents(
        context, state, code_result.documents, code_result.part_ids, primary,
        code_result.retrieval_mode,
    )
    if terminal is not None:
        return terminal
    documents, grounded_math_enabled = _inject_bom(
        context, documents, code_result.part_ids, state
    )
    documents = _prepend_image(documents, decision.request.image_analysis)
    if not documents:
        return _empty_documents_terminal(context, state, code_result.part_ids)
    correction = _apply_crag(context, state, documents, code_result.part_ids)
    return EnrichmentOutcome(
        documents=correction.documents, new_part_ids=code_result.part_ids,
        base_k=primary.base_k, retrieval_mode=code_result.retrieval_mode,
        active_filter=code_result.active_filter,
        has_active_filter=code_result.has_active_filter,
        served_graph_documents=(), graph_documents=graph.graph_documents,
        graph_routed=graph.routed, graph_edge_count=graph.edge_count,
        graph_max_hops=graph.max_hops,
        community_documents=community.community_documents,
        community_summary_used=community.used,
        community_summary_count=community.summary_count,
        community_fallback_reason=community.fallback_reason,
        grounded_math_enabled=grounded_math_enabled,
        auxiliary_input_tokens=primary.auxiliary_input_tokens + correction.input_tokens,
        auxiliary_output_tokens=primary.auxiliary_output_tokens + correction.output_tokens,
        correction_estimated_cost=(
            primary.correction_estimated_cost + correction.estimated_cost
        ),
    )


__all__ = ["EnrichmentOutcome", "enrich_retrieval"]
