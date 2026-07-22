"""Candidate reranking and parent-context hydration."""

from __future__ import annotations

import os
import time
from dataclasses import dataclass
from typing import Any

from mech_chatbot.config.logging import log_trace, logger
from mech_chatbot.rag.bootstrap import RERANK_PER_PART, RERANK_TOP_N_CAP, env_bool
from mech_chatbot.rag.context_builders import hydrate_parent_context, parent_context_max_workers
from mech_chatbot.rag.corrective import merge_corrected_documents
from mech_chatbot.rag.phases.diagnostics import make_terminal_debug as _make_terminal_debug
from mech_chatbot.rag.phases.retrieval_enrichment import EnrichmentOutcome
from mech_chatbot.rag.phases.routing import RouteDecision
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


def rerank_retrieval(decision: RouteDecision, enrichment: EnrichmentOutcome, state: Any):
    request = decision.request
    trace_id = request.trace_id
    user_question = request.user_question
    response_language = request.response_language
    user_department = request.user_department
    user_roles = list(request.user_roles)
    t_start = request.started_at
    effective_question = decision.effective_question
    new_part_ids = list(enrichment.new_part_ids)
    intent_data = dict(decision.intent_data)
    retrieved_docs = list(enrichment.documents)
    graph_docs = list(enrichment.graph_documents)
    served_graph_docs = list(enrichment.served_graph_documents)
    community_docs = list(enrichment.community_documents)
    base_k = enrichment.base_k
    retrieval_mode = enrichment.retrieval_mode
    if enrichment.has_active_filter:
        active_filter = enrichment.active_filter

    # BUOC B2: VOYAGE RE-RANK & REORDER (CHONG LOST IN THE MIDDLE)
    if retrieved_docs:
        # Tach fake_doc (anh nguoi dung upload) ra khoi qua trinh rerank
        fake_docs = [d for d in retrieved_docs if d.metadata.get("loai_du_lieu") == "image_summary" and d.metadata.get("file_goc") == "Anh dinh kem tu nguoi dung"]
        real_docs = [d for d in retrieved_docs if d not in fake_docs]
        real_docs = prioritize_document_types(
            real_docs,
            (intent_data.get("document_type_hints") if "intent_data" in locals() else None),
        )
        real_docs = diversify_candidates(
            real_docs,
            max_per_document=int(os.getenv("RERANK_MAX_CHUNKS_PER_DOCUMENT", "4")),
            max_per_section=int(os.getenv("RERANK_MAX_CHUNKS_PER_SECTION", "1")),
            cap=int(os.getenv("RERANK_CANDIDATE_CAP", "20")),
        )

        late_used = False
        if real_docs and env_bool("RAG_LATE_INTERACTION_ENABLED", False):
            try:
                from mech_chatbot.rag.late_interaction import (
                    attempt_shadow_rerank,
                    enabled as late_enabled,
                )
                if not late_enabled():
                    raise RuntimeError("late interaction encoder has not passed smoke preflight")
                late_top_n = min(RERANK_TOP_N_CAP, max(1, RERANK_PER_PART * max(1, len(new_part_ids) if new_part_ids else 1)))
                late_result = attempt_shadow_rerank(
                    real_docs, effective_question,
                    client,
                    top_n=late_top_n,
                )
                real_docs = list(late_result.documents)
                late_used = late_result.used_shadow
                log_trace(
                    "late_interaction", trace_id,
                    latency_ms=round(late_result.total_latency_ms, 2),
                    encode_latency_ms=round(late_result.encode_latency_ms, 2),
                    query_latency_ms=round(late_result.query_latency_ms, 2),
                    candidate_count=late_result.candidate_count,
                    shadow_hits=late_result.shadow_hits,
                    coverage=late_result.coverage,
                    index_version=os.getenv("RAG_LATE_INDEX_VERSION", "late-v2"),
                    used_shadow=late_result.used_shadow,
                    fallback_reason=late_result.fallback_reason,
                )
            except Exception as exc:
                logger.warning("Late interaction unavailable, keeping existing reranker: %s", exc)
                log_trace("late_interaction", trace_id, error=type(exc).__name__, fallback=True)

        rerank_backend = "late_interaction" if late_used else RerankPolicy().select_backend(real_docs)
        if real_docs and rerank_backend == "voyage":
            try:
                target_top_n = RERANK_PER_PART * max(1, len(new_part_ids) if new_part_ids else 1)

                # MUC A: Nhan dien tu khoa liet ke de mo rong top_n, tranh bi cat cong doan
                from mech_chatbot.rag.text_utils import remove_accents
                q_norm = remove_accents(user_question.lower())
                list_keywords = ["toan bo", "tat ca", "quy trinh", "liet ke"]
                if any(kw in q_norm for kw in list_keywords):
                    target_top_n = max(target_top_n, 25)
                    logger.info(f"Phat hien tu khoa liet ke, mo rong target_top_n len {target_top_n}")

                top_n = min(RERANK_TOP_N_CAP, target_top_n)
                logger.info(f"Dang su dung Voyage Rerank de filter {len(real_docs)} tai lieu (top_n={top_n})...")
                t_rerank = time.time()
                real_docs = voyage_rerank_documents(
                    real_docs, effective_question, top_n=top_n, trace_id=trace_id
                )

                scores = [{"file": d.metadata.get("file_goc"), "page": d.metadata.get("trang_so"), "score": d.metadata.get("relevance_score", 1.0)} for d in real_docs[:5]]
                log_trace(
                    "rerank", trace_id,
                    latency_ms=int((time.time() - t_rerank)*1000),
                    input_docs=len(retrieved_docs), output_docs=len(real_docs),
                    scores=scores, backend="voyage", status="success",
                    fallback=False, retry_attempted=False,
                )
            except Exception as e:
                logger.error(f"Loi khi su dung Voyage Rerank: {e}. Fallback to manual rerank.")
                real_docs = rerank_docs(real_docs)
                log_trace("rerank", trace_id, **voyage_failure_metadata(e))
        elif rerank_backend == "late_interaction":
            logger.info("Dung late_interaction cho rerank candidate set")
        else:
            if real_docs:
                logger.info("Dung %s cho rerank candidate set", rerank_backend)
            real_docs = rerank_docs(real_docs)

        # LOP PHONG THU 1 (CODE): Chan hoan toan LLM neu khong co tai lieu that (va khong phai chitchat/co anh)
        if not real_docs and not fake_docs:
            logger.warning("BLOCKER: Context rong, chan goi LLM de tranh Hallucination.")
            _empty2_vi = ("Tài liệu hiện tại không ghi chú thông tin về câu hỏi của bạn. "
                          "Vui lòng kiểm tra lại hoặc cung cấp thêm bản vẽ.")
            empty_msg = _t_rag(_empty2_vi, response_language)
            def mock_stream():
                yield empty_msg
            log_trace("rag_end", trace_id, final_latency_ms=int((time.time() - t_start)*1000), refusal=True, refusal_reason="empty_context", docs_count=0, version_policy=intent_data.get("version_policy") if "intent_data" in locals() else None, filter_used=serialize_qdrant_filter(active_filter) if "active_filter" in locals() else None, top_k=base_k if "base_k" in locals() else None, user_department=user_department, user_roles=user_roles)
            state.refuse("empty_context")
            return state.prepared(
                (mock_stream(), "", [], new_part_ids, _make_terminal_debug(
                    user_question, "empty_context",
                ))
            )

        t_parent_context = time.time()
        parent_workers = parent_context_max_workers()
        real_docs = hydrate_parent_context(real_docs, max_workers=parent_workers)
        if graph_docs:
            from mech_chatbot.rag.graph_retrieval import attach_served_graph_context
            real_docs, served_graph_docs = attach_served_graph_context(real_docs, graph_docs)
        if community_docs:
            real_docs = merge_corrected_documents(community_docs, real_docs)
        log_trace(
            "parent_context",
            trace_id,
            latency_ms=int((time.time() - t_parent_context) * 1000),
            sections=len(real_docs),
            max_workers=parent_workers,
        )
        retrieved_docs = fake_docs + real_docs

        retrieved_docs = long_context_reorder(retrieved_docs)


    return RerankOutcome(
        documents=tuple(retrieved_docs),
        served_graph_documents=tuple(served_graph_docs),
    )


__all__ = ["RerankOutcome", "rerank_retrieval"]
