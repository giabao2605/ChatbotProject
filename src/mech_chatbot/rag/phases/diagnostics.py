"""Private diagnostic builders shared by RAG phases."""

from __future__ import annotations

from typing import Any

from mech_chatbot.rag.answer_policy import decide_terminal_policy


def serialize_debug_documents(docs=None) -> list[dict[str, Any]]:
    return [
        {
            "file_goc": doc.metadata.get("file_goc"),
            "doc_id": doc.metadata.get("doc_id"),
            "version_no": doc.metadata.get("version_no"),
            "variant_code": doc.metadata.get("variant_code"),
            "is_current": doc.metadata.get("is_current"),
            "lifecycle_status": doc.metadata.get("lifecycle_status"),
            "review_status": doc.metadata.get("review_status"),
            "trang": doc.metadata.get("trang_so"),
            "source_id": (
                f"D{doc.metadata.get('doc_id')}P{doc.metadata.get('trang_so')}"
                if doc.metadata.get("doc_id") is not None
                and doc.metadata.get("trang_so") is not None
                else None
            ),
            "vision_used": bool(doc.metadata.get("vision_used", False)),
            "score": doc.metadata.get("relevance_score"),
            "security_level": doc.metadata.get("security_level"),
            "graph_edge_id": doc.metadata.get("graph_edge_id"),
            "graph_relation_type": doc.metadata.get("graph_relation_type"),
            "graph_source_key": doc.metadata.get("graph_source_key"),
            "graph_target_key": doc.metadata.get("graph_target_key"),
            "text": str(
                doc.metadata.get("noi_dung_goc")
                or getattr(doc, "page_content", "")
                or ""
            )[:800],
        }
        for doc in docs or []
    ]


def make_debug_info(docs=None) -> dict[str, Any]:
    try:
        from mech_chatbot.rag.semantic_cache import pipeline_namespace

        namespace = pipeline_namespace()
    except Exception:
        namespace = "unknown"
    return {
        "pipeline_namespace": namespace,
        "retrieved_docs": serialize_debug_documents(docs),
    }


def make_terminal_debug(
    question: str,
    reason: str,
    docs=None,
    *,
    access_denied: bool = False,
) -> dict[str, Any]:
    decision = decide_terminal_policy(
        question,
        reason=reason,
        access_denied=access_denied,
    )
    debug = make_debug_info(docs)
    debug.update(
        {
            "answer_outcome": decision.outcome.value,
            "evidence_state": decision.evidence_state.value,
            "evidence_stage": "terminal",
            "correction_allowed": False,
            "evidence_quotes": list(decision.evidence_quotes),
        }
    )
    return debug


def make_source_snapshot(docs=None) -> list[dict[str, Any]]:
    snapshots = []
    for doc in docs or []:
        metadata = getattr(doc, "metadata", {}) or {}
        doc_id = metadata.get("doc_id")
        page_no = metadata.get("trang_so")
        try:
            normalized_doc_id = int(doc_id) if doc_id is not None else None
        except (TypeError, ValueError):
            normalized_doc_id = None
        try:
            normalized_page_no = int(page_no) if page_no is not None else None
        except (TypeError, ValueError):
            normalized_page_no = None
        if normalized_doc_id is None:
            continue
        snapshots.append(
            {
                "file_goc": metadata.get("file_goc"),
                "doc_id": normalized_doc_id,
                "version_no": metadata.get("version_no"),
                "variant_code": metadata.get("variant_code"),
                "is_current": metadata.get("is_current"),
                "lifecycle_status": metadata.get("lifecycle_status"),
                "review_status": metadata.get("review_status"),
                "trang": normalized_page_no,
                "source_id": (
                    f"D{normalized_doc_id}P{normalized_page_no}"
                    if normalized_page_no is not None
                    else None
                ),
                "score": metadata.get("relevance_score"),
                "security_level": metadata.get("security_level"),
            }
        )
    return snapshots


def make_phase_diagnostics(
    primary,
    enrichment,
    reranked,
    state,
    answer_policy,
    evidence_decision,
    evidence_quotes,
) -> dict[str, Any]:
    """Build fields shared by refusal and generated-answer diagnostics."""

    return {
        "evidence_state": answer_policy.evidence_state.value,
        "answer_outcome": answer_policy.outcome.value,
        "correction_allowed": bool(
            state.budget.corrections > 0 or answer_policy.correction_allowed
        ),
        "evidence_stage": evidence_decision.stage,
        "evidence_quotes": list(evidence_quotes),
        "correction_count": state.budget.corrections,
        "planner_count": state.budget.planners,
        "subquery_count": state.budget.subqueries,
        "final_generation_count": state.budget.final_generations,
        "deadline_exceeded": state.budget.deadline_exceeded,
        "decomposition_branches": list(primary.decomposition_branches),
        "decomposition_intent_count": len(primary.decomposition_intents),
        "decomposition_intent_coverage": list(primary.decomposition_intent_coverage),
        "decomposition_used_fallback": primary.decomposition_used_fallback,
        "decomposition_intent_overflow": primary.decomposition_intent_overflow,
        "graph_traversal_count": len(reranked.served_graph_documents),
        "graph_evidence": serialize_debug_documents(reranked.served_graph_documents),
        "graph_routed": enrichment.graph_routed,
        "graph_edge_count": enrichment.graph_edge_count,
        "graph_max_hops": enrichment.graph_max_hops,
    }


def make_decomposition_usage(primary, context_text: str) -> dict[str, Any]:
    source = dict(getattr(primary, "decomposition_usage", None) or {})
    planner = dict(source.get("planner") or {})
    branches = []
    for branch in source.get("branches") or ():
        retrieval = dict(branch.get("retrieval") or {})
        correction = dict(branch.get("correction") or {})
        branches.append(
            {
                "branch_id": str(branch.get("branch_id") or ""),
                "retrieval": {
                    "latency_ms": int(retrieval.get("latency_ms") or 0),
                    "document_count": int(retrieval.get("document_count") or 0),
                    "estimated_input_tokens": int(
                        retrieval.get("estimated_input_tokens") or 0
                    ),
                    "estimated_cost": retrieval.get("estimated_cost"),
                    "cost_status": str(retrieval.get("cost_status") or "unpriced"),
                },
                "correction": {
                    "attempted": bool(correction.get("attempted")),
                    "input_tokens": int(correction.get("input_tokens") or 0),
                    "output_tokens": int(correction.get("output_tokens") or 0),
                    "estimated_cost": float(
                        correction.get("estimated_cost") or 0.0
                    ),
                },
            }
        )
    context_tokens = len(str(context_text or "")) // 4
    return {
        "schema": "rag-decomposition-usage-v1",
        "planner": {
            "calls": int(planner.get("calls") or 0),
            "input_tokens": int(planner.get("input_tokens") or 0),
            "output_tokens": int(planner.get("output_tokens") or 0),
            "estimated_cost": float(planner.get("estimated_cost") or 0.0),
        },
        "branches": branches,
        "final_context": {
            "estimated_input_tokens": context_tokens,
            "estimated_input_cost": context_tokens * 2.5 / 1_000_000,
            "included_in_final_generation": True,
        },
        "final_generation": {
            "calls": 0,
            "input_tokens": 0,
            "output_tokens": 0,
            "estimated_cost": 0.0,
        },
    }


__all__ = [
    "make_decomposition_usage",
    "make_debug_info",
    "make_phase_diagnostics",
    "make_source_snapshot",
    "make_terminal_debug",
    "serialize_debug_documents",
]
