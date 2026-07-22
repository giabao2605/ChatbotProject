# -*- coding: utf-8 -*-
"""Private orchestration core and the one-release legacy tuple adapter.

New callers use :mod:`mech_chatbot.rag.execution`; this module keeps the
existing retrieval/generation implementation and compatibility surface.
"""

import time
import uuid
from datetime import datetime
from mech_chatbot.config.logging import logger, log_trace
from PIL import Image
from tenacity import retry, retry_if_exception_type, retry_if_exception, wait_exponential, stop_after_attempt
from langchain_core.output_parsers import StrOutputParser
from langchain_core.documents import Document
from langchain_core.messages import HumanMessage
from mech_chatbot.llm.llm_client import cohere_invoke, get_cohere_llm, _is_cohere_rate_limit, get_llm_model_name
from mech_chatbot.llm.external_ai import ExternalAICallCancelled
from mech_chatbot.db.repository import search_bom_facts, traverse_knowledge_graph
from mech_chatbot.rag.rbac import (
    compose_retrieval_filters,
    create_rbac_filter,
    _security_filter,
    _site_filter,
    _allowed_levels,
    LEVEL_ORDER,
)
from mech_chatbot.rag.entity_resolver import (
    extract_no_code_constraints,
    resolve_candidates_from_docs,
    build_candidate_table_markdown,
)
from mech_chatbot.llm.vision_client import build_vision_model, is_retryable_error
from mech_chatbot.rag.answer_checks import (  # noqa: F401
    _safe_json_loads,
    _extract_numbers,
    extract_units_and_symbols,
    has_unsupported_units_symbols,
    KNOWN_MATERIALS,
    _known_materials,
    extract_known_materials,
    has_unsupported_materials,
    extract_codes,
    has_unsupported_codes,
    requires_source_citation,
    has_required_source_citation,
)
from mech_chatbot.rag.glossary_expand import (  # noqa: F401
    _GLOSSARY_TTL,
    _GLOSSARY_CACHE,
    _glossary_domains_for_department,
    _load_glossary_cached,
    glossary_expansion_terms,
)
from mech_chatbot.rag.context_builders import (  # noqa: F401
    _context_is_mechanical,
    _context_domain,
    build_structured_attributes_context,
    build_common_metadata_context,
    format_docs,
    hydrate_parent_context,
    parent_context_max_workers,
)

# owned names tu cac module con (bao gom ca ten _underscore qua __all__)
from mech_chatbot.rag.bootstrap import *
from mech_chatbot.rag.prompt import *
from mech_chatbot.rag.rerank import *
from mech_chatbot.rag.intent import *
from mech_chatbot.rag.retrieval import *
from mech_chatbot.rag.evidence_gate import *
from mech_chatbot.rag.answer_policy import (
    PolicyEvidence,
    decide_answer_policy,
    decide_terminal_policy,
    has_explicit_negative_evidence,
    explicit_negative_evidence_quote,
    render_cited_explicit_negative_answer,
)
from mech_chatbot.rag.corrective import (
    correction_enabled,
    merge_corrected_documents,
    run_corrected_retrieval,
    should_attempt_correction,
)
from mech_chatbot.rag.query_decomposition import audit_decomposition_stream


from mech_chatbot.rag.execution import (
    _raise_if_request_budget_exceeded,
    current_execution_context,
)
from mech_chatbot.rag.pipeline_steps import GenerationControl, GenerationEvidence, GenerationOutcome, GenerationPlan, GenerationTurn, _prepare_history, _analyze_image, _assemble_context, generate_answer, _retrieve, _RETRIEVE_UNSET, _route, _rewrite_and_anchor, _disambiguate
from mech_chatbot.rag.phases.citations import (
    build_source_citations,
    select_citation_docs,
)
from mech_chatbot.rag.phases.diagnostics import (
    make_debug_info,
    make_source_snapshot,
    make_terminal_debug as _make_terminal_debug,
    serialize_debug_documents,
)


def execute_pipeline(state):
    """Execute all RAG stages through one request-owned execution state."""
    from mech_chatbot.rag.phases.preparation import prepare

    preparation = prepare(state)
    if preparation.terminal is not None:
        return state.prepared(preparation.terminal)
    prepared = preparation.prepared
    if prepared is None:  # pragma: no cover - guarded by PreparationOutcome
        raise RuntimeError("preparation returned no request")

    trace_id = prepared.trace_id
    cancel_event = state.cancellation
    user_question = prepared.user_question
    image_path = prepared.image_path
    chat_history = list(prepared.chat_history)
    current_part_ids = list(prepared.current_part_ids)
    user_department = prepared.user_department
    user_roles = list(prepared.user_roles)
    allowed_departments = list(prepared.allowed_departments)
    max_security_level = prepared.max_security_level
    allowed_sites = list(prepared.allowed_sites)
    response_language = prepared.response_language
    conversation_context = prepared.conversation_context
    t_start = prepared.started_at
    _sc_qemb = None
    _sc_scope = prepared.cache_scope
    _sc_cache_eligible = prepared.cache_eligible
    chat_history_str = prepared.history_text
    _history_summary_new = prepared.history_summary
    _summary_covered_new = prepared.summary_covered
    image_analysis = prepared.image_analysis
 
    from mech_chatbot.rag.phases.routing import route

    routing = route(prepared, state)
    if routing.terminal is not None:
        return state.prepared(routing.terminal)
    route_decision = routing.decision
    if route_decision is None:  # pragma: no cover - guarded by RoutingOutcome
        raise RuntimeError("routing returned no decision")

    effective_question = route_decision.effective_question
    new_part_ids = list(route_decision.new_part_ids)
    is_inherited = route_decision.is_inherited
    is_bom_query = route_decision.is_bom_query
    intent_data = dict(route_decision.intent_data)
    strict_filter = route_decision.strict_filter
    broad_filter = route_decision.broad_filter
    rbac_filter = route_decision.rbac_filter
    _hyde_eligible = route_decision.hyde_eligible
    query_to_search = route_decision.query_to_search
    _sc_qemb = route_decision.cache_query_embedding
    _sc_scope = route_decision.cache_scope
    crag_enabled = route_decision.crag_enabled
    is_chitchat = False
    retrieved_docs = []
    skip_retrieval = False
    decomposition_notice = ""
    decomposition_states = []
    decomposition_branches = []
    decomposition_intents = []
    decomposition_intent_coverage = []
    decomposition_used_fallback = False
    decomposition_intent_overflow = False
    auxiliary_input_tokens = 0
    auxiliary_output_tokens = 0
    planner_estimated_cost = 0.0
    correction_estimated_cost = 0.0
    from mech_chatbot.rag.phases.retrieval import retrieve_primary

    primary_retrieval = retrieve_primary(route_decision, state)
    retrieved_docs = list(primary_retrieval.documents)
    base_k = primary_retrieval.base_k
    retrieval_mode = primary_retrieval.retrieval_mode
    t_retrieval = primary_retrieval.started_at
    if primary_retrieval.has_active_filter:
        active_filter = primary_retrieval.active_filter
    decomposition_notice = primary_retrieval.decomposition_notice
    decomposition_states = list(primary_retrieval.decomposition_states)
    decomposition_branches = list(primary_retrieval.decomposition_branches)
    decomposition_intents = list(primary_retrieval.decomposition_intents)
    decomposition_intent_coverage = list(primary_retrieval.decomposition_intent_coverage)
    decomposition_used_fallback = primary_retrieval.decomposition_used_fallback
    decomposition_intent_overflow = primary_retrieval.decomposition_intent_overflow
    auxiliary_input_tokens = primary_retrieval.auxiliary_input_tokens
    auxiliary_output_tokens = primary_retrieval.auxiliary_output_tokens
    planner_estimated_cost = primary_retrieval.planner_estimated_cost
    correction_estimated_cost = primary_retrieval.correction_estimated_cost

    from mech_chatbot.rag.phases.retrieval_enrichment import (
        EnrichmentOutcome,
        enrich_retrieval,
    )

    enrichment = enrich_retrieval(route_decision, primary_retrieval, state)
    if not isinstance(enrichment, EnrichmentOutcome):
        return enrichment
    retrieved_docs = list(enrichment.documents)
    new_part_ids = list(enrichment.new_part_ids)
    base_k = enrichment.base_k
    retrieval_mode = enrichment.retrieval_mode
    if enrichment.has_active_filter:
        active_filter = enrichment.active_filter
    served_graph_docs = list(enrichment.served_graph_documents)
    graph_docs = list(enrichment.graph_documents)
    graph_routed = enrichment.graph_routed
    graph_edge_count = enrichment.graph_edge_count
    graph_max_hops = enrichment.graph_max_hops
    community_docs = list(enrichment.community_documents)
    community_summary_used = enrichment.community_summary_used
    community_summary_count = enrichment.community_summary_count
    community_fallback_reason = enrichment.community_fallback_reason
    grounded_math_enabled = enrichment.grounded_math_enabled
    auxiliary_input_tokens = enrichment.auxiliary_input_tokens
    auxiliary_output_tokens = enrichment.auxiliary_output_tokens
    correction_estimated_cost = enrichment.correction_estimated_cost

    from mech_chatbot.rag.phases.retrieval_rerank import RerankOutcome, rerank_retrieval

    reranked = rerank_retrieval(route_decision, enrichment, state)
    if not isinstance(reranked, RerankOutcome):
        return reranked
    retrieved_docs = list(reranked.documents)
    served_graph_docs = list(reranked.served_graph_documents)

    from mech_chatbot.rag.phases.evidence import EvidenceOutcome, evaluate_evidence

    evidence = evaluate_evidence(
        route_decision,
        primary_retrieval,
        enrichment,
        reranked,
        state,
    )
    if not isinstance(evidence, EvidenceOutcome):
        return evidence
    context_text = evidence.context_text
    ref_text = evidence.ref_text
    ref_images = list(evidence.ref_images)
    answer_policy = evidence.answer_policy
    evidence_decision = evidence.evidence_decision
    evidence_quotes = list(evidence.evidence_quotes)
    explicit_negative_answer = evidence.explicit_negative_answer

    generation_metrics = {
        "estimated_cost": correction_estimated_cost + planner_estimated_cost,
        "input_tokens": auxiliary_input_tokens,
        "output_tokens": auxiliary_output_tokens,
        "provider_retries": state.budget.provider_retries,
    }
    state.budget.record("final_generations", 0 if explicit_negative_answer else 1)
    state.transition("generation")
    generation_outcome = GenerationOutcome()
    state.bind_generation(generation_outcome)
    stream = generate_answer(
        GenerationPlan(
            turn=GenerationTurn(
                user_question=user_question,
                effective_question=effective_question,
                chat_history_str=chat_history_str,
                new_part_ids=new_part_ids,
                response_language=response_language,
                user_department=user_department,
                user_roles=user_roles,
            ),
            evidence=GenerationEvidence(
                context_text=context_text,
                retrieved_docs=retrieved_docs,
                intent_data=intent_data,
                base_k=base_k,
                retrieval_mode=retrieval_mode,
                has_active_filter=("active_filter" in locals()),
                active_filter=(active_filter if "active_filter" in locals() else None),
            ),
            control=GenerationControl(
                trace_id=trace_id,
                started_at=t_start,
                deadline_monotonic=state.budget.deadline_monotonic,
                budget=state.budget,
                outcome=generation_outcome,
            ),
            explicit_negative_answer=explicit_negative_answer,
        ),
        cancel_event=cancel_event,
        metrics=generation_metrics,
    )

    # BUOC D: TU DONG TAO TRICH DAN NGUON VA HINH ANH (Tra ve cung stream)
    debug_info = make_debug_info(retrieved_docs)
    debug_info["evidence_state"] = answer_policy.evidence_state.value
    debug_info["answer_outcome"] = answer_policy.outcome.value
    debug_info["correction_allowed"] = bool(
        state.budget.corrections > 0 or answer_policy.correction_allowed
    )
    debug_info["evidence_stage"] = evidence_decision.stage
    debug_info["evidence_quotes"] = evidence_quotes
    debug_info["correction_count"] = state.budget.corrections
    debug_info["planner_count"] = state.budget.planners
    debug_info["subquery_count"] = state.budget.subqueries
    debug_info["final_generation_count"] = state.budget.final_generations
    debug_info["deadline_exceeded"] = state.budget.deadline_exceeded
    debug_info["decomposition_branches"] = decomposition_branches
    debug_info["decomposition_intent_count"] = len(decomposition_intents)
    debug_info["decomposition_intent_coverage"] = decomposition_intent_coverage
    debug_info["decomposition_used_fallback"] = decomposition_used_fallback
    debug_info["decomposition_intent_overflow"] = decomposition_intent_overflow
    debug_info["graph_traversal_count"] = len(served_graph_docs)
    debug_info["graph_evidence"] = serialize_debug_documents(served_graph_docs)
    debug_info["graph_routed"] = graph_routed
    debug_info["graph_edge_count"] = graph_edge_count
    debug_info["graph_max_hops"] = graph_max_hops
    debug_info["community_summary_used"] = community_summary_used
    debug_info["community_summary_count"] = community_summary_count
    debug_info["community_summary_source_count"] = len(community_docs)
    debug_info["community_summary_fallback_reason"] = community_fallback_reason
    debug_info["late_interaction_hits"] = sum(
        1 for doc in retrieved_docs if doc.metadata.get("rerank_backend") == "late_interaction"
    )
    debug_info["calculation_provenance"] = [
        doc.metadata.get("calculation_provenance")
        for doc in retrieved_docs if doc.metadata.get("calculation_provenance")
    ]
    # The generator mutates this object after the caller consumes the stream.
    debug_info["generation_metrics"] = generation_metrics
    # Keep the full source registry. The browser-facing API will resolve the
    # stable SourceIDs emitted in the final answer and expose only those cards.
    _citation_snapshot = make_source_snapshot(retrieved_docs)
    _evidence_snapshot = make_source_snapshot(retrieved_docs)
    debug_info["citation_docs"] = _citation_snapshot

    if decomposition_branches:
        stream = audit_decomposition_stream(stream, decomposition_branches)
    # KH-2 (sua V4): neo lai tai lieu vua dung de tra loi cho luot tiep theo.
    try:
        from mech_chatbot.rag import conversation_state as _cs3
        if _cs3.is_enabled() and retrieved_docs:
            _adr_out = _cs3.dominant_doc_refs(retrieved_docs)
            if _adr_out:
                _cc_out = debug_info.get("conversation_context") or {}
                _cc_out["active_doc_refs"] = _adr_out
                _cc_out.setdefault("last_intent", "answered")
                debug_info["conversation_context"] = _cc_out
    except Exception as _e_adr:
        logger.warning(f"[ConvState] luu active_doc_refs loi: {_e_adr}")

    # KH-3: luu tom tat luy tien vao conversation_context (chi ton tai trong cuoc tro chuyen nay).
    try:
        if _history_summary_new is not None or _summary_covered_new is not None:
            _cc_sum_out = debug_info.get("conversation_context") or {}
            if _history_summary_new:
                _cc_sum_out["history_summary"] = _history_summary_new
            if _summary_covered_new is not None:
                _cc_sum_out["summary_covered"] = _summary_covered_new
            debug_info["conversation_context"] = _cc_sum_out
    except Exception as _e_sumout:
        logger.warning(f"[KH-3] luu history_summary loi: {_e_sumout}")
        
    # P2-9: Semantic cache STORE (best-effort, khong lam gay pipeline)
    try:
        import mech_chatbot.rag.semantic_cache as _sc2
        if _sc2.enabled() and _sc_qemb is not None and retrieved_docs:
            _sc_doc_ids = [d.metadata.get("doc_id") for d in retrieved_docs if d is not None and d.metadata.get("doc_id") is not None]
            _in_len = len(context_text) + len(user_question) + len(chat_history_str)
            stream = _sc2.teeing_store_stream(
                stream, question=user_question, embedding=_sc_qemb, scope_sig=_sc_scope,
                ref_text=ref_text, ref_images=ref_images, source_doc_ids=_sc_doc_ids,
                model=get_llm_model_name(), input_char_len=_in_len,
                citation_snapshot=_citation_snapshot,
                evidence_snapshot=_evidence_snapshot,
            )
    except Exception as _sce2:
        logger.warning(f"semantic cache store loi: {_sce2}")
    return state.prepared((stream, ref_text, ref_images, new_part_ids, debug_info))


def chat_with_rag(user_question, image_path=None, chat_history=None, current_part_ids=None, user_department=None, user_roles=None, allowed_departments=None, max_security_level="public", allowed_sites=None, response_language="vi", conversation_context=None, trace_id=None, cancel_event=None):
    """Compatibility adapter for the legacy five-value RAG interface.

    New in-repo callers should consume ``mech_chatbot.rag.execution`` events.
    This adapter remains for one release so external imports keep their tuple
    shape and the same mutable debug dictionary lifecycle.
    """
    from mech_chatbot.llm.external_ai import ExternalAICallCancelled
    from mech_chatbot.rag.execution import (
        AccessScope,
        DefaultRagExecutor,
        NEVER_CANCELLED,
        RagCancelled,
        RagCompleted,
        RagFailed,
        RagInvocation,
        RagPrepared,
        RagRequest,
        RagToken,
        _prepare_legacy_events,
        current_execution_context,
    )

    request = RagRequest(
        question=user_question,
        image_path=image_path,
        history=tuple(chat_history or ()),
        current_part_ids=tuple(current_part_ids or ()),
        access=AccessScope(
            department=user_department,
            roles=frozenset(user_roles or ()),
            allowed_departments=frozenset(allowed_departments or ()),
            max_security_level=max_security_level,
            allowed_sites=frozenset(allowed_sites or ()),
        ),
        response_language=response_language,
        conversation_context=conversation_context,
    )
    ambient_context = current_execution_context()
    invocation_mode = (
        ambient_context if ambient_context in {"evaluation", "test"} else "production"
    )
    events = iter(
        DefaultRagExecutor().run(
            request,
            RagInvocation(trace_id=trace_id or "", mode=invocation_mode),
            cancellation=cancel_event or NEVER_CANCELLED,
        )
    )
    first = _prepare_legacy_events(events)
    if isinstance(first, RagFailed):
        raise first.cause
    if isinstance(first, RagCancelled):
        if first.cause is not None:
            raise first.cause
        raise ExternalAICallCancelled(first.reason)
    if not isinstance(first, RagPrepared):
        raise RuntimeError("RAG executor did not emit RagPrepared first")

    legacy_debug = dict(first.diagnostics)

    def legacy_stream():
        try:
            for event in events:
                if isinstance(event, RagToken):
                    yield event.text
                elif isinstance(event, RagCompleted):
                    legacy_debug.clear()
                    legacy_debug.update(event.diagnostics)
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

    return (
        legacy_stream(),
        first.ref_text,
        list(first.ref_images),
        list(first.new_part_ids),
        legacy_debug,
    )


__all__ = [
    'make_debug_info',
    'chat_with_rag',
    'select_citation_docs',
    'build_source_citations',
]
