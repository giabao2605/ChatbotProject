# -*- coding: utf-8 -*-
"""Private orchestration core and the one-release legacy tuple adapter.

New callers use :mod:`mech_chatbot.rag.execution`; this module keeps the
existing retrieval/generation implementation and compatibility surface.
"""

from mech_chatbot.rag.phases.citations import (
    build_source_citations,
    select_citation_docs,
)
from mech_chatbot.rag.phases.diagnostics import make_debug_info
from mech_chatbot.rag.phases.contracts import PhaseTerminal


def execute_pipeline(state):
    """Execute all RAG stages through one request-owned execution state."""
    from mech_chatbot.rag.phases.preparation import prepare

    preparation = prepare(state)
    if preparation.terminal is not None:
        return state.prepared(preparation.terminal)
    prepared = preparation.prepared
    if prepared is None:  # pragma: no cover - guarded by PreparationOutcome
        raise RuntimeError("preparation returned no request")

    from mech_chatbot.rag.phases.routing import route

    routing = route(prepared, state)
    if routing.terminal is not None:
        return state.prepared(routing.terminal)
    route_decision = routing.decision
    if route_decision is None:  # pragma: no cover - guarded by RoutingOutcome
        raise RuntimeError("routing returned no decision")

    from mech_chatbot.rag.phases.retrieval import retrieve_primary

    primary_retrieval = retrieve_primary(route_decision, state)

    from mech_chatbot.rag.phases.retrieval_enrichment import enrich_retrieval

    enrichment = enrich_retrieval(route_decision, primary_retrieval, state)
    if isinstance(enrichment, PhaseTerminal):
        return enrichment.prepared

    from mech_chatbot.rag.phases.retrieval_rerank import rerank_retrieval

    reranked = rerank_retrieval(route_decision, enrichment, state)
    if isinstance(reranked, PhaseTerminal):
        return reranked.prepared

    from mech_chatbot.rag.phases.evidence import evaluate_evidence

    evidence = evaluate_evidence(
        route_decision,
        primary_retrieval,
        enrichment,
        reranked,
        state,
    )
    if isinstance(evidence, PhaseTerminal):
        return evidence.prepared

    from mech_chatbot.rag.phases.generation import generate

    return generate(
        route_decision,
        primary_retrieval,
        enrichment,
        reranked,
        evidence,
        state,
    ).prepared


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
