"""Candidate reranking and parent-context hydration."""

from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Any

from mech_chatbot.config.logging import log_trace, logger
from mech_chatbot.llm.external_ai import ExternalAICallCancelled
from mech_chatbot.rag.context_builders import hydrate_parent_context, parent_context_max_workers
from mech_chatbot.rag.corrective import merge_corrected_documents
from mech_chatbot.rag.execution import RequestBudgetExceeded
from mech_chatbot.rag.phases.contracts import PhaseTerminal
from mech_chatbot.rag.phases.diagnostics import make_terminal_debug as _make_terminal_debug
from mech_chatbot.rag.phases.retrieval_enrichment import EnrichmentOutcome
from mech_chatbot.rag.phases.routing import RouteDecision
from mech_chatbot.rag.intent import serialize_qdrant_filter
from mech_chatbot.rag.prompt import _t_rag
from mech_chatbot.rag.rerank import (
    RerankPolicy,
    diversify_candidates,
    long_context_reorder,
    prioritize_document_types,
    rerank_docs,
    voyage_failure_metadata,
    voyage_rerank_documents,
)


@dataclass(frozen=True, slots=True)
class RerankOutcome:
    documents: tuple[Any, ...]
    served_graph_documents: tuple[Any, ...]
    reason_code: str = "reranked"


def _prepare_candidates(
    retrieved_docs: list[Any],
    intent_data: dict[str, Any],
    runtime: Any,
) -> tuple[list[Any], list[Any]]:
    fake_docs = [
        doc
        for doc in retrieved_docs
        if doc.metadata.get("loai_du_lieu") == "image_summary"
        and doc.metadata.get("file_goc") == "Anh dinh kem tu nguoi dung"
    ]
    real_docs = prioritize_document_types(
        [doc for doc in retrieved_docs if doc not in fake_docs],
        intent_data.get("document_type_hints"),
    )
    return fake_docs, diversify_candidates(
        real_docs,
        max_per_document=int(
            getattr(runtime, "rerank_max_chunks_per_document", 4)
        ),
        max_per_section=int(
            getattr(runtime, "rerank_max_chunks_per_section", 1)
        ),
        cap=int(getattr(runtime, "rerank_candidate_cap", 20)),
    )


def _late_interaction_rerank(
    real_docs: list[Any],
    effective_question: str,
    trace_id: str,
    new_part_ids: list[Any],
    runtime: Any,
) -> tuple[list[Any], bool]:
    late_config = getattr(runtime, "late_interaction_config", None)
    if not real_docs or not getattr(late_config, "interaction_enabled", False):
        return real_docs, False
    try:
        from mech_chatbot.rag.late_interaction import (
            attempt_shadow_rerank,
            enabled as late_enabled,
        )

        if not late_enabled(late_config):
            raise RuntimeError("late interaction encoder has not passed smoke preflight")
        late_top_n = min(
            int(getattr(runtime, "rerank_top_n_cap", 20)),
            max(
                1,
                int(getattr(runtime, "rerank_per_part", 8))
                * max(1, len(new_part_ids) or 1),
            ),
        )
        late_result = attempt_shadow_rerank(
            real_docs,
            effective_question,
            getattr(runtime, "client", None),
            top_n=late_top_n,
            query_encoder=getattr(runtime, "late_query_encoder", None),
            config=late_config,
        )
        log_trace(
            "late_interaction",
            trace_id,
            latency_ms=round(late_result.total_latency_ms, 2),
            encode_latency_ms=round(late_result.encode_latency_ms, 2),
            query_latency_ms=round(late_result.query_latency_ms, 2),
            candidate_count=late_result.candidate_count,
            shadow_hits=late_result.shadow_hits,
            coverage=late_result.coverage,
            index_version=getattr(late_config, "index_version", "late-v2"),
            used_shadow=late_result.used_shadow,
            fallback_reason=late_result.fallback_reason,
        )
        return list(late_result.documents), late_result.used_shadow
    except (ExternalAICallCancelled, RequestBudgetExceeded):
        raise
    except Exception as exc:
        logger.warning(
            "Late interaction unavailable, keeping existing reranker: %s", exc
        )
        log_trace(
            "late_interaction", trace_id, error=type(exc).__name__, fallback=True
        )
        return real_docs, False


def _voyage_top_n(
    user_question: str,
    new_part_ids: list[Any],
    *,
    per_part=8,
    cap=20,
) -> int:
    target_top_n = int(per_part) * max(1, len(new_part_ids) or 1)
    from mech_chatbot.rag.text_utils import remove_accents

    q_norm = remove_accents(user_question.lower())
    if any(kw in q_norm for kw in ["toan bo", "tat ca", "quy trinh", "liet ke"]):
        target_top_n = max(target_top_n, 25)
        logger.info(
            "Phat hien tu khoa liet ke, mo rong target_top_n len %s", target_top_n
        )
    return min(int(cap), target_top_n)


def _voyage_rerank(
    real_docs: list[Any],
    effective_question: str,
    user_question: str,
    trace_id: str,
    new_part_ids: list[Any],
    input_count: int,
    runtime: Any,
) -> list[Any]:
    try:
        top_n = _voyage_top_n(
            user_question,
            new_part_ids,
            per_part=getattr(runtime, "rerank_per_part", 8),
            cap=getattr(runtime, "rerank_top_n_cap", 20),
        )
        logger.info(
            "Dang su dung Voyage Rerank de filter %s tai lieu (top_n=%s)...",
            len(real_docs),
            top_n,
        )
        started = time.time()
        result = voyage_rerank_documents(
            real_docs,
            effective_question,
            top_n=top_n,
            trace_id=trace_id,
            runtime=getattr(runtime, "voyage_runtime", None),
            timeout_seconds=getattr(runtime, "voyage_timeout_seconds", 15.0),
        )
        scores = [
            {
                "file": doc.metadata.get("file_goc"),
                "page": doc.metadata.get("trang_so"),
                "score": doc.metadata.get("relevance_score", 1.0),
            }
            for doc in result[:5]
        ]
        log_trace(
            "rerank",
            trace_id,
            latency_ms=int((time.time() - started) * 1000),
            input_docs=input_count,
            output_docs=len(result),
            scores=scores,
            backend="voyage",
            status="success",
            fallback=False,
            retry_attempted=False,
        )
        return result
    except (ExternalAICallCancelled, RequestBudgetExceeded):
        raise
    except Exception as exc:
        logger.error("Loi khi su dung Voyage Rerank: %s. Fallback to manual rerank.", exc)
        result = rerank_docs(real_docs)
        log_trace("rerank", trace_id, **voyage_failure_metadata(exc))
        return result


def _apply_rerank_backend(
    real_docs: list[Any],
    late_used: bool,
    effective_question: str,
    user_question: str,
    trace_id: str,
    new_part_ids: list[Any],
    input_count: int,
    runtime: Any,
) -> list[Any]:
    backend = (
        "late_interaction"
        if late_used
        else RerankPolicy(
            enabled=bool(getattr(runtime, "voyage_enabled", True)),
            runtime=getattr(runtime, "voyage_runtime", None),
        ).select_backend(real_docs)
    )
    if real_docs and backend == "voyage":
        return _voyage_rerank(
            real_docs,
            effective_question,
            user_question,
            trace_id,
            new_part_ids,
            input_count,
            runtime,
        )
    if backend == "late_interaction":
        logger.info("Dung late_interaction cho rerank candidate set")
    elif real_docs:
        logger.info("Dung %s cho rerank candidate set", backend)
    return real_docs if backend == "late_interaction" else rerank_docs(real_docs)


def _empty_context_terminal(
    decision: RouteDecision,
    enrichment: EnrichmentOutcome,
    state: Any,
    new_part_ids: list[Any],
) -> PhaseTerminal:
    request = decision.request
    logger.warning("BLOCKER: Context rong, chan goi LLM de tranh Hallucination.")
    empty_msg = _t_rag(
        "Tài liệu hiện tại không ghi chú thông tin về câu hỏi của bạn. "
        "Vui lòng kiểm tra lại hoặc cung cấp thêm bản vẽ.",
        request.response_language,
    )

    def mock_stream():
        yield empty_msg

    log_trace(
        "rag_end",
        request.trace_id,
        final_latency_ms=int((time.time() - request.started_at) * 1000),
        refusal=True,
        refusal_reason="empty_context",
        docs_count=0,
        version_policy=decision.intent_data.get("version_policy"),
        filter_used=(
            serialize_qdrant_filter(enrichment.active_filter)
            if enrichment.has_active_filter
            else None
        ),
        top_k=enrichment.base_k,
        user_department=request.user_department,
        user_roles=list(request.user_roles),
    )
    state.refuse("empty_context")
    return PhaseTerminal(
        prepared=state.prepared(
            (
                mock_stream(),
                "",
                [],
                new_part_ids,
                _make_terminal_debug(request.user_question, "empty_context"),
            )
        ),
        reason_code="empty_context",
    )


def _hydrate_reranked_context(
    real_docs: list[Any],
    graph_docs: list[Any],
    community_docs: list[Any],
    served_graph_docs: list[Any],
    trace_id: str,
    runtime: Any,
) -> tuple[list[Any], list[Any]]:
    started = time.time()
    parent_workers = parent_context_max_workers(
        getattr(runtime, "parent_context_max_workers", 4)
    )
    real_docs = hydrate_parent_context(
        real_docs,
        max_workers=parent_workers,
        max_sections=getattr(runtime, "parent_context_max_sections", 8),
        max_chunks_per_section=getattr(
            runtime,
            "parent_context_max_chunks",
            6,
        ),
        enabled=bool(getattr(runtime, "parent_context_enabled", True)),
        client=getattr(runtime, "client", None),
        collection_name=getattr(runtime, "collection_name", None),
    )
    if graph_docs:
        from mech_chatbot.rag.graph_retrieval import attach_served_graph_context

        real_docs, served_graph_docs = attach_served_graph_context(
            real_docs, graph_docs
        )
    if community_docs:
        real_docs = merge_corrected_documents(community_docs, real_docs)
    log_trace(
        "parent_context",
        trace_id,
        latency_ms=int((time.time() - started) * 1000),
        sections=len(real_docs),
        max_workers=parent_workers,
    )
    return real_docs, served_graph_docs


def rerank_retrieval(
    decision: RouteDecision,
    enrichment: EnrichmentOutcome,
    state: Any,
    *,
    answerable_branch_count: int = 0,
) -> RerankOutcome | PhaseTerminal:
    request = decision.request
    trace_id = request.trace_id
    user_question = request.user_question
    effective_question = decision.effective_question
    new_part_ids = list(enrichment.new_part_ids)
    intent_data = dict(decision.intent_data)
    retrieved_docs = list(enrichment.documents)
    graph_docs = list(enrichment.graph_documents)
    served_graph_docs = list(enrichment.served_graph_documents)
    community_docs = list(enrichment.community_documents)
    runtime = state.retrieval_adapter

    if retrieved_docs:
        fake_docs, real_docs = _prepare_candidates(
            retrieved_docs,
            intent_data,
            runtime,
        )
        real_docs, late_used = _late_interaction_rerank(
            real_docs,
            effective_question,
            trace_id,
            new_part_ids,
            runtime,
        )
        branch_rank_is_final = (
            answerable_branch_count > 0
            and str(enrichment.retrieval_mode).startswith("decomposed_")
            and len(real_docs) <= answerable_branch_count
            and not graph_docs
            and not community_docs
            and not late_used
        )
        if branch_rank_is_final:
            real_docs = rerank_docs(real_docs)
            log_trace(
                "rerank",
                trace_id,
                input_docs=len(retrieved_docs),
                output_docs=len(real_docs),
                backend="branch_rank",
                status="skipped",
                fallback=False,
                reason="decomposition_already_ranked",
            )
        else:
            real_docs = _apply_rerank_backend(
                real_docs,
                late_used,
                effective_question,
                user_question,
                trace_id,
                new_part_ids,
                len(retrieved_docs),
                runtime,
            )

        if not real_docs and not fake_docs:
            return _empty_context_terminal(
                decision, enrichment, state, new_part_ids
            )

        real_docs, served_graph_docs = _hydrate_reranked_context(
            real_docs,
            graph_docs,
            community_docs,
            served_graph_docs,
            trace_id,
            runtime,
        )
        retrieved_docs = fake_docs + real_docs
        retrieved_docs = long_context_reorder(retrieved_docs)

    return RerankOutcome(
        documents=tuple(retrieved_docs),
        served_graph_documents=tuple(served_graph_docs),
    )


__all__ = ["RerankOutcome", "rerank_retrieval"]
