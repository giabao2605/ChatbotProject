"""Evidence policy phase between retrieval and answer generation."""

from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Any

from mech_chatbot.config.logging import log_trace, logger
from mech_chatbot.rag.answer_policy import (
    PolicyEvidence,
    decide_answer_policy,
    explicit_negative_evidence_quote,
    has_explicit_negative_evidence,
    render_cited_explicit_negative_answer,
)
from mech_chatbot.rag.evidence_gate import (
    evaluate_answerability,
    make_insufficient_evidence_message,
)
from mech_chatbot.rag.intent import serialize_qdrant_filter
from mech_chatbot.rag.phases.citations import (
    build_source_citations,
    select_citation_docs,
)
from mech_chatbot.rag.phases.contracts import PhaseTerminal
from mech_chatbot.rag.phases.diagnostics import (
    make_debug_info,
    make_phase_diagnostics,
)
from mech_chatbot.rag.phases.retrieval import PrimaryRetrievalOutcome
from mech_chatbot.rag.phases.retrieval_enrichment import EnrichmentOutcome
from mech_chatbot.rag.phases.retrieval_rerank import RerankOutcome
from mech_chatbot.rag.phases.routing import RouteDecision
from mech_chatbot.rag.pipeline_steps import _assemble_context


@dataclass(frozen=True, slots=True)
class EvidenceOutcome:
    context_text: str
    ref_text: str
    ref_images: tuple[str, ...]
    answer_policy: Any
    evidence_decision: Any
    evidence_quotes: tuple[str, ...]
    explicit_negative_answer: str
    reason_code: str = "evidence_approved"


def evaluate_evidence(
    decision: RouteDecision,
    primary: PrimaryRetrievalOutcome,
    enrichment: EnrichmentOutcome,
    reranked: RerankOutcome,
    state: Any,
) -> EvidenceOutcome | PhaseTerminal:
    """Apply citation and answerability policy to the retrieved evidence."""

    request = decision.request
    documents = list(reranked.documents)
    new_part_ids = list(enrichment.new_part_ids)
    decomposition_branches = list(primary.decomposition_branches)
    context_text = _assemble_context(documents, request.user_question) + primary.decomposition_notice

    citation_docs = select_citation_docs(
        documents,
        question=request.user_question,
        is_bom_query=decision.is_bom_query,
        part_ids=new_part_ids,
    )
    if enrichment.grounded_math_enabled:
        from mech_chatbot.rag.grounded_math import (
            select_grounded_answer_citation_documents,
        )

        calculation_docs = select_grounded_answer_citation_documents(
            documents,
            decomposition_branches,
        )
        if calculation_docs:
            citation_docs = calculation_docs
    ref_text, ref_images = build_source_citations(citation_docs)
    confidential_docs = [
        doc.metadata.get("file_goc")
        for doc in documents
        if doc.metadata.get("security_level") == "confidential"
    ]
    if confidential_docs:
        logger.warning(
            "[audit][confidential] dept=%s roles=%s truy cap tai lieu mat: %s",
            request.user_department,
            list(request.user_roles),
            confidential_docs,
        )

    state.transition("evidence")
    gate_started = time.time()
    evidence_decision = evaluate_answerability(
        request.user_question,
        context_text,
        docs=documents,
        trace_id=request.trace_id,
    )
    sufficient_branch_count = sum(
        branch.get("outcome") == "full_answer" or branch.get("grounded_negative")
        for branch in decomposition_branches
    )
    has_sufficient_branch = sufficient_branch_count > 0
    answer_policy = decide_answer_policy(
        request.user_question,
        PolicyEvidence(
            decision=evidence_decision,
            has_retrieved_evidence=bool(documents),
            retrieval_can_improve=bool(
                decision.crag_enabled
                and state.budget.corrections < state.budget.limits.corrections
            ),
            negative_evidence=has_explicit_negative_evidence(
                request.user_question,
                context_text,
            ),
            negative_evidence_quote=explicit_negative_evidence_quote(
                request.user_question,
                context_text,
            ),
            sufficient_branch_count=sufficient_branch_count,
            total_branch_count=len(decomposition_branches),
        ),
        {},
    )
    evidence_quotes = tuple(answer_policy.evidence_quotes)
    log_trace(
        "evidence_gate",
        request.trace_id,
        latency_ms=int((time.time() - gate_started) * 1000),
        answerable=answer_policy.allows_answer_generation,
        state=answer_policy.evidence_state.value,
        outcome=answer_policy.outcome.value,
        correction_allowed=answer_policy.correction_allowed,
        stage=evidence_decision.stage,
        status=evidence_decision.telemetry_status,
        reason=answer_policy.reason,
        correction_attempts=state.budget.corrections,
        partial_serving=has_sufficient_branch and not evidence_decision.answerable,
    )

    if not answer_policy.allows_answer_generation:
        return _prepare_refusal(
            decision,
            primary,
            enrichment,
            reranked,
            state,
            answer_policy,
            evidence_decision,
            evidence_quotes,
            ref_text,
            ref_images,
        )

    explicit_negative_quote = (
        evidence_quotes[0]
        if answer_policy.reason == "explicit_negative_evidence" and evidence_quotes
        else ""
    )
    return EvidenceOutcome(
        context_text=context_text,
        ref_text=ref_text,
        ref_images=tuple(ref_images),
        answer_policy=answer_policy,
        evidence_decision=evidence_decision,
        evidence_quotes=evidence_quotes,
        explicit_negative_answer=render_cited_explicit_negative_answer(
            explicit_negative_quote,
            documents,
            language=request.response_language,
        ),
    )


def _prepare_refusal(
    decision: RouteDecision,
    primary: PrimaryRetrievalOutcome,
    enrichment: EnrichmentOutcome,
    reranked: RerankOutcome,
    state: Any,
    answer_policy: Any,
    evidence_decision: Any,
    evidence_quotes: tuple[str, ...],
    ref_text: str,
    ref_images: list[str],
) -> PhaseTerminal:
    request = decision.request
    documents = list(reranked.documents)
    logger.warning("Evidence gate BLOCK cau hoi: %s", answer_policy.reason)
    safe_message = make_insufficient_evidence_message(
        request.user_question,
        answer_policy.reason,
        lang=request.response_language,
    )

    def refusal_stream():
        yield safe_message

    log_trace(
        "rag_end",
        request.trace_id,
        final_latency_ms=int((time.time() - request.started_at) * 1000),
        refusal=True,
        refusal_reason="evidence_gate",
        docs_count=len(documents),
        doc_ids=[doc.metadata.get("doc_id") for doc in documents],
        retrieved_file_goc=[doc.metadata.get("file_goc") for doc in documents],
        version_no=[doc.metadata.get("version_no") for doc in documents],
        variant_code=[doc.metadata.get("variant_code") for doc in documents],
        is_current=[doc.metadata.get("is_current") for doc in documents],
        lifecycle_status=[doc.metadata.get("lifecycle_status") for doc in documents],
        review_status=[doc.metadata.get("review_status") for doc in documents],
        version_policy=decision.intent_data.get("version_policy"),
        filter_used=(
            serialize_qdrant_filter(enrichment.active_filter)
            if enrichment.has_active_filter
            else None
        ),
        top_k=enrichment.base_k,
        retrieval_mode=enrichment.retrieval_mode,
        retrieval_scores=[doc.metadata.get("relevance_score") for doc in documents],
        user_department=request.user_department,
        user_roles=list(request.user_roles),
    )
    debug = make_debug_info(documents)
    debug.update(
        make_phase_diagnostics(
            primary,
            enrichment,
            reranked,
            state,
            answer_policy,
            evidence_decision,
            evidence_quotes,
        )
    )
    debug["citation_docs"] = make_debug_info(documents)["retrieved_docs"]
    debug["generation_metrics"] = {
        "estimated_cost": (
            enrichment.correction_estimated_cost + primary.planner_estimated_cost
        ),
        "input_tokens": enrichment.auxiliary_input_tokens,
        "output_tokens": enrichment.auxiliary_output_tokens,
        "provider_retries": state.budget.provider_retries,
        "repair_count": 0,
    }
    state.refuse("evidence_gate")
    return PhaseTerminal(
        prepared=state.prepared(
            (
                refusal_stream(),
                ref_text,
                ref_images,
                list(enrichment.new_part_ids),
                debug,
            )
        ),
        reason_code="evidence_gate",
    )
