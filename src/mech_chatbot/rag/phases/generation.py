"""Final answer generation and post-generation diagnostics phase."""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

from mech_chatbot.config.logging import logger
from mech_chatbot.llm.external_ai import ExternalAICallCancelled
from mech_chatbot.llm.llm_client import get_llm_model_name
from mech_chatbot.rag.execution import RequestBudgetExceeded
from mech_chatbot.rag.phases.diagnostics import (
    make_decomposition_usage,
    make_debug_info,
    make_phase_diagnostics,
    make_source_snapshot,
)
from mech_chatbot.rag.phases.evidence import EvidenceOutcome
from mech_chatbot.rag.phases.retrieval import PrimaryRetrievalOutcome
from mech_chatbot.rag.phases.retrieval_enrichment import EnrichmentOutcome
from mech_chatbot.rag.phases.retrieval_rerank import RerankOutcome
from mech_chatbot.rag.phases.routing import RouteDecision
from mech_chatbot.rag.pipeline_steps import (
    GenerationControl,
    GenerationEvidence,
    GenerationOutcome,
    GenerationPlan,
    GenerationTurn,
    generate_answer,
)
from mech_chatbot.rag.query_decomposition import audit_decomposition_stream


@dataclass(frozen=True, slots=True)
class GenerationResult:
    prepared: Any
    reason_code: str = "generated"


def _generation_metrics(
    primary: PrimaryRetrievalOutcome,
    enrichment: EnrichmentOutcome,
    evidence: EvidenceOutcome,
    state: Any,
) -> dict[str, Any]:
    return {
        "estimated_cost": (
            enrichment.correction_estimated_cost + primary.planner_estimated_cost
        ),
        "input_tokens": enrichment.auxiliary_input_tokens,
        "output_tokens": enrichment.auxiliary_output_tokens,
        "provider_retries": state.budget.provider_retries,
        "decomposition_usage": make_decomposition_usage(
            primary,
            evidence.context_text,
        ),
    }


def _effective_generation_question(
    decision: RouteDecision,
    primary: PrimaryRetrievalOutcome,
    documents: list[Any],
) -> str:
    if any(
        branch.get("outcome") in {"access_denied", "insufficient_evidence"}
        for branch in primary.decomposition_branches
    ) and not any(
        isinstance(document.metadata.get("calculation_provenance"), dict)
        for document in documents
    ):
        return " và ".join(
            str(branch.get("subquery") or "").strip()
            for branch in primary.decomposition_branches
            if branch.get("outcome") == "full_answer"
            and str(branch.get("subquery") or "").strip()
        )
    if not any(
        isinstance(document.metadata.get("calculation_provenance"), dict)
        for document in documents
    ):
        return decision.effective_question
    remaining = [
        re.sub(
            r"^(?:đối chiếu|so sánh)\s+",
            "",
            str(branch.get("subquery") or "").strip(),
            flags=re.IGNORECASE,
        )
        for branch in primary.decomposition_branches
        if branch.get("outcome") == "full_answer"
        and not branch.get("bom_lookup")
        and str(branch.get("subquery") or "").strip()
    ]
    return " và ".join(remaining)


def _generation_plan(
    decision: RouteDecision,
    primary: PrimaryRetrievalOutcome,
    enrichment: EnrichmentOutcome,
    evidence: EvidenceOutcome,
    state: Any,
    documents: list[Any],
    new_part_ids: list[Any],
    outcome: GenerationOutcome,
) -> GenerationPlan:
    request = decision.request
    return GenerationPlan(
        turn=GenerationTurn(
            user_question=request.user_question,
            effective_question=_effective_generation_question(
                decision,
                primary,
                documents,
            ),
            chat_history_str=request.history_text,
            new_part_ids=new_part_ids,
            response_language=request.response_language,
            user_department=request.user_department,
            user_roles=list(request.user_roles),
        ),
        evidence=GenerationEvidence(
            context_text=evidence.context_text,
            retrieved_docs=documents,
            intent_data=dict(decision.intent_data),
            base_k=enrichment.base_k,
            retrieval_mode=enrichment.retrieval_mode,
            has_active_filter=enrichment.has_active_filter,
            active_filter=(
                enrichment.active_filter if enrichment.has_active_filter else None
            ),
        ),
        control=GenerationControl(
            trace_id=request.trace_id,
            started_at=request.started_at,
            deadline_monotonic=state.budget.deadline_monotonic,
            budget=state.budget,
            outcome=outcome,
        ),
        explicit_negative_answer=evidence.explicit_negative_answer,
        runtime=state,
    )


def _generation_debug(
    decision: RouteDecision,
    primary: PrimaryRetrievalOutcome,
    enrichment: EnrichmentOutcome,
    reranked: RerankOutcome,
    evidence: EvidenceOutcome,
    state: Any,
    documents: list[Any],
    generation_metrics: dict[str, Any],
) -> dict[str, Any]:
    debug_info = make_debug_info(documents)
    debug_info.update(
        make_phase_diagnostics(
            primary,
            enrichment,
            reranked,
            state,
            evidence.answer_policy,
            evidence.evidence_decision,
            evidence.evidence_quotes,
        )
    )
    debug_info.update(
        {
            "community_summary_used": enrichment.community_summary_used,
            "community_summary_count": enrichment.community_summary_count,
            "community_summary_source_count": len(enrichment.community_documents),
            "community_summary_fallback_reason": enrichment.community_fallback_reason,
            "late_interaction_hits": sum(
                1
                for doc in documents
                if doc.metadata.get("rerank_backend") == "late_interaction"
            ),
            "calculation_provenance": [
                doc.metadata.get("calculation_provenance")
                for doc in documents
                if doc.metadata.get("calculation_provenance")
            ],
        }
    )
    debug_info["generation_metrics"] = generation_metrics
    debug_info["decomposition_usage"] = generation_metrics[
        "decomposition_usage"
    ]
    from mech_chatbot.domain.serving_state import is_currently_servable
    from mech_chatbot.rag.rbac import document_matches_access_scope
    from mech_chatbot.rag.phases.retrieval_enrichment_support import (
        is_governed_sql_bom_document,
    )

    request = decision.request
    access_scope_passed = bool(documents) and all(
        is_governed_sql_bom_document(document)
        or document_matches_access_scope(
            getattr(document, "metadata", None),
            user_department=request.user_department,
            user_roles=request.user_roles,
            allowed_departments=request.allowed_departments,
            max_security_level=request.max_security_level,
            allowed_sites=request.allowed_sites,
        )
        for document in documents
    )
    serving_state_passed = bool(documents) and all(
        is_governed_sql_bom_document(document) or is_currently_servable(
            getattr(document, "metadata", None),
            require_current=True,
        )
        for document in documents
    )
    debug_info["pilot_request_validation"] = {
        "access_scope_passed": access_scope_passed,
        "citation_structure_passed": (
            generation_metrics.get("citation_structure_passed") is True
        ),
        "provenance_passed": generation_metrics.get("provenance_passed") is True,
        "leakage_passed": access_scope_passed and serving_state_passed,
    }
    return debug_info


def _decorate_generated_stream(
    stream: Any,
    decision: RouteDecision,
    evidence: EvidenceOutcome,
    documents: list[Any],
    decomposition_branches: list[Any],
    debug_info: dict[str, Any],
    state: Any,
) -> Any:
    citation_snapshot = make_source_snapshot(documents)
    evidence_snapshot = make_source_snapshot(documents)
    debug_info["citation_docs"] = citation_snapshot
    if decomposition_branches:
        stream = audit_decomposition_stream(stream, decomposition_branches)
    _store_conversation_document_refs(
        debug_info,
        documents,
        enabled=bool(
            getattr(
                state.retrieval_adapter,
                "conversation_state_enabled",
                False,
            )
        ),
    )
    _store_history_summary(debug_info, decision.request)
    return _wrap_semantic_cache(
        stream,
        decision,
        evidence,
        documents,
        citation_snapshot,
        evidence_snapshot,
        state,
    )


def _start_generation(
    decision: RouteDecision,
    primary: PrimaryRetrievalOutcome,
    enrichment: EnrichmentOutcome,
    evidence: EvidenceOutcome,
    state: Any,
    documents: list[Any],
    new_part_ids: list[Any],
) -> tuple[Any, dict[str, Any]]:
    generation_metrics = _generation_metrics(primary, enrichment, evidence, state)
    state.transition("generation")
    generation_outcome = GenerationOutcome()
    state.bind_generation(generation_outcome)
    stream = generate_answer(
        _generation_plan(
            decision,
            primary,
            enrichment,
            evidence,
            state,
            documents,
            new_part_ids,
            generation_outcome,
        ),
        cancel_event=state.cancellation,
        metrics=generation_metrics,
    )
    return stream, generation_metrics


def generate(
    decision: RouteDecision,
    primary: PrimaryRetrievalOutcome,
    enrichment: EnrichmentOutcome,
    reranked: RerankOutcome,
    evidence: EvidenceOutcome,
    state: Any,
) -> GenerationResult:
    request = decision.request
    documents = list(reranked.documents)
    new_part_ids = list(enrichment.new_part_ids)
    decomposition_branches = list(primary.decomposition_branches)
    stream, generation_metrics = _start_generation(
        decision,
        primary,
        enrichment,
        evidence,
        state,
        documents,
        new_part_ids,
    )

    debug_info = _generation_debug(
        decision,
        primary,
        enrichment,
        reranked,
        evidence,
        state,
        documents,
        generation_metrics,
    )
    stream = _decorate_generated_stream(
        stream,
        decision,
        evidence,
        documents,
    decomposition_branches,
    debug_info,
        state,
    )
    return GenerationResult(
        prepared=state.prepared(
            (
                stream,
                evidence.ref_text,
                list(evidence.ref_images),
                new_part_ids,
                debug_info,
            )
        )
    )


def _store_conversation_document_refs(
    debug_info: dict[str, Any],
    documents: list[Any],
    *,
    enabled: bool,
) -> None:
    try:
        from mech_chatbot.rag import conversation_state

        if conversation_state.is_enabled(enabled) and documents:
            active_refs = conversation_state.dominant_doc_refs(documents)
            if active_refs:
                context = dict(debug_info.get("conversation_context") or {})
                context["active_doc_refs"] = active_refs
                context.setdefault("last_intent", "answered")
                debug_info["conversation_context"] = context
    except (ExternalAICallCancelled, RequestBudgetExceeded):
        raise
    except Exception as exc:
        logger.warning("[ConvState] luu active_doc_refs loi: %s", exc)


def _store_history_summary(debug_info: dict[str, Any], request: Any) -> None:
    try:
        if request.history_summary is not None or request.summary_covered is not None:
            context = dict(debug_info.get("conversation_context") or {})
            if request.history_summary:
                context["history_summary"] = request.history_summary
            if request.summary_covered is not None:
                context["summary_covered"] = request.summary_covered
            debug_info["conversation_context"] = context
    except (ExternalAICallCancelled, RequestBudgetExceeded):
        raise
    except Exception as exc:
        logger.warning("[KH-3] luu history_summary loi: %s", exc)


def _wrap_semantic_cache(
    stream: Any,
    decision: RouteDecision,
    evidence: EvidenceOutcome,
    documents: list[Any],
    citation_snapshot: list[Any],
    evidence_snapshot: list[Any],
    state: Any,
) -> Any:
    request = decision.request
    try:
        import mech_chatbot.rag.semantic_cache as semantic_cache

        runtime = state.retrieval_adapter
        if (
            semantic_cache.enabled(
                getattr(runtime, "semantic_cache_enabled", True),
                state.invocation.mode,
            )
            and decision.cache_query_embedding is not None
            and documents
        ):
            source_doc_ids = [
                doc.metadata.get("doc_id")
                for doc in documents
                if doc is not None and doc.metadata.get("doc_id") is not None
            ]
            input_length = (
                len(evidence.context_text)
                + len(request.user_question)
                + len(request.history_text)
            )
            return semantic_cache.teeing_store_stream(
                stream,
                question=request.user_question,
                embedding=decision.cache_query_embedding,
                scope_sig=decision.cache_scope,
                ref_text=evidence.ref_text,
                ref_images=list(evidence.ref_images),
                source_doc_ids=source_doc_ids,
                model=get_llm_model_name(state.provider_adapter),
                input_char_len=input_length,
                citation_snapshot=citation_snapshot,
                evidence_snapshot=evidence_snapshot,
                cache_enabled=bool(
                    getattr(runtime, "semantic_cache_enabled", True)
                ),
            )
    except (ExternalAICallCancelled, RequestBudgetExceeded):
        raise
    except Exception as exc:
        logger.warning("semantic cache store loi: %s", exc)
    return stream
