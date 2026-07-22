"""Graph, governed BOM, correction, and access enrichment."""

from __future__ import annotations

import os
import time
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any

from langchain_core.documents import Document
from langchain_core.messages import HumanMessage

from mech_chatbot.config.logging import log_trace, logger
from mech_chatbot.db.repository import search_bom_facts, traverse_knowledge_graph
from mech_chatbot.llm.external_ai import ExternalAICallCancelled
from mech_chatbot.llm.llm_client import cohere_invoke
from mech_chatbot.rag.answer_policy import (
    PolicyEvidence,
    decide_answer_policy,
    explicit_negative_evidence_quote,
    has_explicit_negative_evidence,
)
from mech_chatbot.rag.bootstrap import client, env_bool, vectorstore
from mech_chatbot.rag.context_builders import (
    _context_is_mechanical,
    hydrate_parent_context,
    parent_context_max_workers,
)
from mech_chatbot.rag.corrective import (
    merge_corrected_documents,
    run_corrected_retrieval,
    should_attempt_correction,
)
from mech_chatbot.rag.evidence_gate import EvidenceDecision, EvidenceState, evaluate_answerability
from mech_chatbot.rag.execution import RequestBudgetExceeded, current_execution_context
from mech_chatbot.rag.intent import serialize_qdrant_filter
from mech_chatbot.rag.phases.diagnostics import (
    make_debug_info,
    make_terminal_debug as _make_terminal_debug,
)
from mech_chatbot.rag.phases.contracts import PhaseTerminal
from mech_chatbot.rag.phases.retrieval import PrimaryRetrievalOutcome
from mech_chatbot.rag.phases.routing import RouteDecision
from mech_chatbot.rag.pipeline_steps import _assemble_context, _disambiguate, _retrieve
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


def enrich_retrieval(
    decision: RouteDecision,
    primary: PrimaryRetrievalOutcome,
    state: Any,
) -> EnrichmentOutcome | PhaseTerminal:
    request = decision.request
    trace_id = request.trace_id
    user_question = request.user_question
    response_language = request.response_language
    current_part_ids = list(request.current_part_ids)
    user_department = request.user_department
    user_roles = list(request.user_roles)
    allowed_departments = list(request.allowed_departments)
    allowed_sites = list(request.allowed_sites)
    max_security_level = request.max_security_level
    image_analysis = request.image_analysis
    t_start = request.started_at
    effective_question = decision.effective_question
    new_part_ids = list(decision.new_part_ids)
    is_inherited = decision.is_inherited
    is_bom_query = decision.is_bom_query
    intent_data = dict(decision.intent_data)
    strict_filter = decision.strict_filter
    broad_filter = decision.broad_filter
    rbac_filter = decision.rbac_filter
    query_to_search = decision.query_to_search
    crag_enabled = decision.crag_enabled
    retrieved_docs = list(primary.documents)
    base_k = primary.base_k
    retrieval_mode = primary.retrieval_mode
    t_retrieval = primary.started_at
    if primary.has_active_filter:
        active_filter = primary.active_filter
    decomposition_branches = list(primary.decomposition_branches)
    auxiliary_input_tokens = primary.auxiliary_input_tokens
    auxiliary_output_tokens = primary.auxiliary_output_tokens
    correction_estimated_cost = primary.correction_estimated_cost
    skip_retrieval = False
    is_chitchat = False

    graph_routed = False
    graph_edge_count = 0
    graph_max_hops = 0
    graph_docs = []
    served_graph_docs = []
    graph_enabled = env_bool("RAG_GRAPH_RETRIEVAL_ENABLED", False)
    community_enabled = env_bool("RAG_GRAPH_COMMUNITY_SUMMARIES_ENABLED", False)
    graph_seeds = []
    graph_access = {
        "roles": user_roles or [],
        "allowed_departments": allowed_departments or [],
        "allowed_sites": allowed_sites or [],
        "max_security_level": max_security_level,
    }
    if not skip_retrieval and graph_enabled:
        state.checkpoint("graph")
        from mech_chatbot.rag.graph_retrieval import (
            filter_servable_edges, hydrate_graph_edges, select_graph_seeds,
            should_attempt_graph,
        )
        graph_started = time.time()
        graph_seeds = select_graph_seeds(effective_question, new_part_ids)
        try:
            graph_routed = should_attempt_graph(effective_question)
            if graph_routed:
                graph_max_hops = 2
                graph_edges = filter_servable_edges(
                    traverse_knowledge_graph(graph_seeds, graph_access, max_hops=2, limit=50),
                    graph_access,
                )
                graph_docs = hydrate_graph_edges(
                    graph_edges,
                    client,
                    os.getenv("QDRANT_COLLECTION", "TaiLieuKyThuat_v2"),
                )
                retrieved_docs = merge_corrected_documents(retrieved_docs, graph_docs)
            else:
                graph_edges, graph_docs = [], []
            graph_edge_count = len(graph_edges)
            state.budget.record("graph_edges", graph_edge_count)
            state.checkpoint("graph")
            log_trace(
                "graph_retrieval", trace_id,
                latency_ms=int((time.time() - graph_started) * 1000),
                routed=graph_routed,
                route_scope="relational" if graph_routed else "regular",
                seed_count=len(graph_seeds), edge_count=len(graph_edges),
                hydrated_count=len(graph_docs), max_hops=2, edge_limit=50,
            )
        except (ExternalAICallCancelled, RequestBudgetExceeded, TimeoutError):
            raise
        except Exception as exc:
            logger.warning("Graph retrieval unavailable: %s", exc)
            log_trace("graph_retrieval", trace_id, error=type(exc).__name__, edge_count=0)

    community_docs = []
    community_summary_used = False
    community_summary_count = 0
    community_fallback_reason = "not_attempted"
    if not skip_retrieval:
        from mech_chatbot.rag.community_summaries import load_community_context

        community_started = time.time()
        community_result = load_community_context(
            effective_question,
            graph_enabled=graph_enabled,
            community_enabled=community_enabled,
            access_context=graph_access,
            seed_keys=graph_seeds,
            serving_epoch=os.getenv("RAG_COMMUNITY_SERVING_EPOCH", "community-v1"),
            graph_fingerprint=os.getenv("RAG_GRAPH_FINGERPRINT", ""),
            client=client,
            collection_name=os.getenv("QDRANT_COLLECTION", "TaiLieuKyThuat_v2"),
        )
        community_docs = list(community_result.documents)
        community_summary_used = community_result.used
        community_summary_count = community_result.summary_count
        community_fallback_reason = community_result.reason
        if community_docs:
            retrieved_docs = merge_corrected_documents(retrieved_docs, community_docs)
        log_trace(
            "community_summaries",
            trace_id,
            latency_ms=int((time.time() - community_started) * 1000),
            used=community_summary_used,
            summary_count=community_summary_count,
            source_document_count=len(community_docs),
            fallback_reason=community_fallback_reason,
            serving_epoch=os.getenv("RAG_COMMUNITY_SERVING_EPOCH", "community-v1"),
        )

    def _access_denied_terminal(access_reason):
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
        message = stub_en if _normalize_lang(response_language) == "en" else stub_vi

        def restricted_stream():
            yield message

        debug = _make_terminal_debug(
            user_question, "access_denied", [], access_denied=True,
        )
        debug["access_hint"] = {
            "restricted": True,
            "reason": access_reason,
            "question": user_question,
        }
        log_trace(
            "rag_end", trace_id,
            final_latency_ms=int((time.time() - t_start) * 1000),
            refusal=True,
            refusal_reason="access_denied",
            access_reason=access_reason,
        )
        return PhaseTerminal(
            prepared=state.prepared(
                (restricted_stream(), "", [], current_part_ids, debug)
            ),
            reason_code="access_denied",
        )

    # Kiem tra ket qua tim kiem ma cu the (khong fallback semantic lung tung)
    if not skip_retrieval and not retrieved_docs and new_part_ids:
        if is_inherited:
            # FIX C: ma nay do KE THUA (user khong go). Khong cung nhac "khong tim thay ma";
            # ha ve tim kiem chung roi de resolver/generation xu ly.
            logger.info(f"Khong co doc cho ma KE THUA {new_part_ids}. Huy ke thua, tim kiem chung.")
            new_part_ids = []
            try:
                general_filter = current_published_filter(rbac_filter)
                _retr_fb = vectorstore.as_retriever(search_type="similarity", search_kwargs={"k": 30, "filter": general_filter})
                active_filter = general_filter
                retrieval_mode = "general_after_inherit_miss"
                retrieved_docs = _retr_fb.invoke(query_to_search)
            except (ExternalAICallCancelled, RequestBudgetExceeded):
                raise
            except Exception as _e_fb:
                logger.warning(f"Fallback general sau inherit-miss loi: {_e_fb}")
                retrieved_docs = []
        else:
            logger.info(f"Khong tim thay bat ky tai lieu nao cho ma {new_part_ids}. Tu choi fallback semantic.")
            try:
                _blocked, _access_reason = probe_restricted_access(
                    query_to_search,
                    user_department=user_department,
                    allowed_departments=allowed_departments,
                    max_security_level=max_security_level,
                    allowed_sites=allowed_sites,
                    part_ids=new_part_ids,
                )
            except (ExternalAICallCancelled, RequestBudgetExceeded):
                raise
            except Exception:
                _blocked, _access_reason = False, None
            if _blocked and _access_reason:
                return _access_denied_terminal(_access_reason)
            _codes_str = ', '.join(new_part_ids)
            if _normalize_lang(response_language) == "en":
                _no_code_msg = f"Sorry, I couldn't find the code '{_codes_str}' in the current drawing system. Please double-check the code or provide more details."
            else:
                _no_code_msg = f"Rất tiếc, mình không tìm thấy mã số '{_codes_str}' nào trong hệ thống bản vẽ hiện tại. Vui lòng kiểm tra lại mã hoặc mô tả rõ hơn."
            def insufficient_evidence_stream():
                yield _no_code_msg
            log_trace(
                "rag_end", trace_id,
                final_latency_ms=int((time.time() - t_start) * 1000),
                refusal=True,
                refusal_reason="no_docs_for_exact_code",
            )
            state.refuse("no_docs_for_exact_code")
            return PhaseTerminal(
                prepared=state.prepared(
                    (
                        insufficient_evidence_stream(),
                        "",
                        [],
                        current_part_ids,
                        _make_terminal_debug(user_question, "no_docs_for_exact_code"),
                    )
                ),
                reason_code="no_docs_for_exact_code",
            )

    if not skip_retrieval:
        state.transition("retrieval")
        # P0 slice #7: resolve candidates + bang lua chon variant + insufficient tach sang pipeline_steps._disambiguate
        _disambig_terminal, retrieved_docs = _disambiguate(
            retrieved_docs=retrieved_docs,
            user_question=user_question,
            new_part_ids=new_part_ids,
            intent_data=intent_data,
            response_language=response_language,
            current_part_ids=current_part_ids,
            trace_id=trace_id,
            t_start=t_start,
            make_debug_info=make_debug_info,
            lifecycle=state,
        )
        if _disambig_terminal is not None:
            return PhaseTerminal(
                prepared=state.prepared(_disambig_terminal),
                reason_code="disambiguation_required",
            )

        log_trace("retrieval", trace_id,
                  latency_ms=int((time.time() - t_retrieval)*1000),
                  mode=retrieval_mode,
                  docs_count=len(retrieved_docs),
                  is_bom_query=is_bom_query if new_part_ids else False,
                  part_ids=new_part_ids,
                  version_policy=intent_data.get("version_policy") if "intent_data" in locals() else None,
                  detected_versions=intent_data.get("detected_versions") if "intent_data" in locals() else None,
                  variant_codes=intent_data.get("variant_codes") if "intent_data" in locals() else None,
                  strict_filter=serialize_qdrant_filter(strict_filter) if "strict_filter" in locals() else None,
                  broad_filter=serialize_qdrant_filter(broad_filter) if "broad_filter" in locals() else None,
                  top_k=base_k if "base_k" in locals() else None)

    # Inject governed SQL BOM data. Grounded aggregate questions may not name a
    # part code, so scope those reads to the exact documents already retrieved.
    grounded_math_enabled = env_bool("RAG_GROUNDED_MATH_ENABLED", False)
    bom_document_ids = []
    if grounded_math_enabled and not new_part_ids:
        from mech_chatbot.rag.grounded_math import select_grounded_bom_document_ids
        bom_document_ids = select_grounded_bom_document_ids(
            retrieved_docs, user_question,
        )
    should_inject_bom = bool(new_part_ids or bom_document_ids)
    if (
        not skip_retrieval
        and should_inject_bom
        and _context_is_mechanical(retrieved_docs, new_part_ids)
    ):
        t_sql = time.time()
        try:
            bom_results = search_bom_facts(
                part_codes=new_part_ids,
                document_ids=bom_document_ids,
                version_policy=intent_data.get("version_policy", "current_only"),
                detected_versions=intent_data.get("detected_versions"),
                user_department=user_department,
                user_roles=user_roles,
                allowed_departments=allowed_departments,
                max_security_level=max_security_level,
                allowed_sites=allowed_sites,
            )
            if bom_results:
                # Keep structured BOM facts tied to the originating document
                # page.  A synthetic aggregate with no DocID/PageNo cannot be
                # attributed safely in the final answer or chat history.
                bom_by_source = {}
                for row in bom_results:
                    try:
                        source_key = (int(row.doc_id), int(row.page))
                    except (TypeError, ValueError):
                        # The SQL query excludes NULL pages, but keep the
                        # context fail-closed if a legacy row is malformed.
                        continue
                    source = bom_by_source.setdefault(
                        source_key,
                        {
                            "file_goc": row.document,
                            "version_no": row.version,
                            "security_level": row.security_level,
                            "site": row.site,
                            "external_processing_policy": row.external_processing_policy,
                            "lines": [],
                            "facts": [],
                        },
                    )
                    source["lines"].append(
                        f"- Mã: {row.part_code}, Tên: {row.description}, "
                        f"Vật liệu: {row.material}, SL: {row.quantity} "
                        f"{row.unit or ''}, Ghi chú: {row.note}"
                    )
                    if (
                            grounded_math_enabled
                        and row.quantity is not None
                    ):
                        try:
                            from decimal import Decimal
                            from mech_chatbot.rag.grounded_math import GroundedFact
                            source["facts"].append(GroundedFact(
                                value=Decimal(str(row.quantity)),
                                unit=str(row.unit or "").strip(),
                                doc_id=int(row.doc_id), page=int(row.page),
                                version=int(row.version),
                                source_id=str(row.source_row_id),
                                label=str(row.part_code or "").strip(),
                            ))
                        except (ValueError, TypeError, ArithmeticError):
                            pass

                calculation_claims = {}
                if grounded_math_enabled:
                    from mech_chatbot.rag.grounded_math import solve_grounded_calculation
                    facts_by_document = {}
                    for (source_doc_id, _), source in bom_by_source.items():
                        claim_key = (source_doc_id, int(source["version_no"]))
                        facts_by_document.setdefault(claim_key, []).extend(source["facts"])
                    all_facts = tuple(
                        fact
                        for facts in facts_by_document.values()
                        for fact in facts
                    )
                    calculation_result = solve_grounded_calculation(
                        user_question, all_facts
                    )
                    if calculation_result.plan is not None and facts_by_document:
                        plan = calculation_result.plan
                        operand_keys = tuple(dict.fromkeys(
                            (fact.doc_id, fact.version) for fact in plan.operands
                        ))
                        claim_key = operand_keys[0] if operand_keys else next(iter(facts_by_document))
                        calculation_claims[claim_key] = (
                            plan, calculation_result.claim,
                        )

                bom_docs = []
                emitted_calculations = set()
                for (doc_id, page_no), source in bom_by_source.items():
                    bom_text = (
                        "Dữ liệu cấu trúc Bảng Kê Vật Tư (BOM) đã trích xuất "
                        "từ đúng trang tài liệu:\n" + "\n".join(source["lines"])
                    )
                    calculation_provenance = None
                    claim_key = (doc_id, int(source["version_no"]))
                    if claim_key in calculation_claims and claim_key not in emitted_calculations:
                        emitted_calculations.add(claim_key)
                        plan, claim = calculation_claims[claim_key]
                        from mech_chatbot.rag.grounded_math import make_calculation_provenance
                        calculation_provenance = make_calculation_provenance(plan, claim)
                        if claim.status == "valid":
                            qualifier = "xấp xỉ " if claim.approximate else ""
                            bom_text += (
                                f"\n- Kết quả tính xác định từ các dòng BOM trên: {qualifier}"
                                f"{claim.display_value} {claim.unit}. Công thức: {claim.formula}."
                            )
                        else:
                            bom_text += (
                                "\n- Tôi trả lời được một phần: không thể hoàn thành phép tính có kiểm soát "
                                f"vì dữ kiện không hợp lệ ({claim.status}); không tự suy diễn."
                            )
                    bom_docs.append(
                        Document(
                            page_content=bom_text,
                            metadata={
                                "doc_id": doc_id,
                                "trang_so": page_no,
                                "file_goc": source["file_goc"],
                                "version_no": source["version_no"],
                                "security_level": source["security_level"],
                                "site": source["site"],
                                "domain": "mechanical",
                                "loai_du_lieu": "sql_bom",
                                "doc_status": "published",
                                "external_processing_policy": (
                                    source["external_processing_policy"] or "internal_only"
                                ),
                                "calculation_provenance": calculation_provenance,
                            },
                        )
                    )
                retrieved_docs = bom_docs + retrieved_docs
                logger.info("Da them %s dong BOM tu SQL vao context (%s nguon co the citation).", len(bom_results), len(bom_docs))
                log_trace(
                    "sql_bom", trace_id,
                    latency_ms=int((time.time() - t_sql)*1000), rows=len(bom_results),
                    part_ids=new_part_ids, document_ids=bom_document_ids,
                )
            else:
                log_trace(
                    "sql_bom", trace_id,
                    latency_ms=int((time.time() - t_sql)*1000), rows=0,
                    part_ids=new_part_ids, document_ids=bom_document_ids,
                )
        except (ExternalAICallCancelled, RequestBudgetExceeded):
            raise
        except Exception as e:
            logger.error(f"Loi inject SQL BOM: {e}")
            log_trace(
                "sql_bom", trace_id,
                latency_ms=int((time.time() - t_sql)*1000), error=str(e),
                part_ids=new_part_ids, document_ids=bom_document_ids,
            )

    if image_analysis:
        fake_doc = Document(
            page_content=f"Phan tich noi dung anh nguoi dung tai len: {image_analysis}",
            metadata={
                "file_goc": "Anh dinh kem tu nguoi dung",
                "loai_du_lieu": "image_summary",
                "trang_so": "1",
                "cong_doan": "Anh truc tiep"
            }
        )
        retrieved_docs.insert(0, fake_doc)

    if not retrieved_docs and not is_chitchat and not skip_retrieval:
        logger.warning("BLOCKER: Khong tim thay tai lieu nao, chan LLM de tranh hallucination.")

        # P0-2: co the bi chan vi ton tai tai lieu MAT khop pham vi nhung vuot clearance
        try:
            _blocked, _access_reason = probe_restricted_access(
                query_to_search, user_department=user_department,
                allowed_departments=allowed_departments,
                max_security_level=max_security_level, allowed_sites=allowed_sites,
                part_ids=new_part_ids)
        except (ExternalAICallCancelled, RequestBudgetExceeded):
            raise
        except Exception:
            _blocked, _access_reason = False, None
        if _blocked and _access_reason:
            return _access_denied_terminal(_access_reason)

        _empty_vi = (
            "Tài liệu hiện tại chưa có dữ liệu liên quan đến câu hỏi của bạn. "
            "Mình không thể trả lời dựa trên suy đoán. "
            "Vui lòng nạp tài liệu vào hệ thống trước, hoặc hỏi nội dung đã có trong dữ liệu."
        )
        empty_msg = _t_rag(_empty_vi, response_language)

        def empty_stream():
            yield empty_msg

        log_trace(
            "rag_end",
            trace_id,
            final_latency_ms=int((time.time() - t_start) * 1000),
            refusal=True,
            refusal_reason="no_retrieved_docs",
            docs_count=0,
        )

        state.refuse("no_retrieved_docs")
        return PhaseTerminal(
            prepared=state.prepared(
                (
                    empty_stream(),
                    "",
                    [],
                    current_part_ids,
                    _make_terminal_debug(user_question, "no_retrieved_docs"),
                )
            ),
            reason_code="no_retrieved_docs",
        )

    # Optional CRAG pass.  It reuses the exact same strict/broad/RBAC filters;
    # only the query formulation changes, so correction can never widen access.
    if retrieved_docs and crag_enabled and not skip_retrieval:
        preliminary_context = _assemble_context(retrieved_docs, user_question)
        coverage_decision = evaluate_answerability(
            user_question,
            preliminary_context,
            docs=retrieved_docs,
            trace_id=trace_id,
        )
        if (
            current_execution_context() == "evaluation"
            and os.getenv("RAG_EVAL_FORCE_AMBIGUOUS", "false").strip().lower() in {"1", "true", "yes", "on"}
        ):
            coverage_decision = EvidenceDecision(
                EvidenceState.AMBIGUOUS,
                reason="controlled_evaluation_correction_fixture",
                stage="evaluation_fixture",
                telemetry_status="heuristic_block",
            )
            log_trace("evaluation_override", trace_id, override="force_ambiguous")
        coverage_policy = decide_answer_policy(
            user_question,
            PolicyEvidence(
                decision=coverage_decision,
                has_retrieved_evidence=bool(retrieved_docs),
                retrieval_can_improve=True,
                negative_evidence=has_explicit_negative_evidence(
                    user_question, preliminary_context
                ),
                negative_evidence_quote=explicit_negative_evidence_quote(
                    user_question, preliminary_context
                ),
            ),
            {},
        )
        if should_attempt_correction(
            coverage_policy, attempts=state.budget.corrections, enabled=crag_enabled
        ):
            correction_started = time.time()
            before_count = len(retrieved_docs)
            state.budget.record("corrections", 1, cumulative=True)
            try:
                state.checkpoint("corrective_retrieval")
                rewrite_prompt = (
                    "Rewrite this internal-document search query once to improve evidence recall. "
                    "Keep every technical code and do not add facts. Return only the query.\n\n"
                    f"Question: {effective_question}\nMissing evidence: {coverage_decision.reason}"
                )
                rewritten = cohere_invoke(
                    [HumanMessage(content=rewrite_prompt)],
                    surface="query_disambiguation",
                    trace_id=trace_id,
                    retry_counter=state.budget,
                ).content
                state.checkpoint("corrective_retrieval")
                corrected_query = tokenize_cached(str(rewritten or effective_question))
                correction_cost = (
                    (len(rewrite_prompt) // 4) * 2.5 + (len(str(rewritten)) // 4) * 15.0
                ) / 1_000_000
                auxiliary_input_tokens += len(rewrite_prompt) // 4
                auxiliary_output_tokens += len(str(rewritten)) // 4
                corrected_docs, _, corrected_mode, _, _ = run_corrected_retrieval(
                    _retrieve,
                    corrected_query=corrected_query,
                    new_part_ids=new_part_ids,
                    strict_filter=strict_filter,
                    broad_filter=broad_filter,
                    is_bom_query=is_bom_query,
                    rbac_filter=rbac_filter,
                    trace_id=trace_id,
                )
                state.checkpoint("corrective_retrieval")
                correction_estimated_cost += correction_cost
                retrieved_docs = merge_corrected_documents(retrieved_docs, corrected_docs)
                log_trace(
                    "corrective_retrieval",
                    trace_id,
                    latency_ms=int((time.time() - correction_started) * 1000),
                    strategy="query_rewrite",
                    attempt=state.budget.corrections,
                    before_docs=before_count,
                    corrected_docs=len(corrected_docs),
                    after_docs=len(retrieved_docs),
                    retrieval_mode=corrected_mode,
                    evaluator_state=coverage_decision.state.value,
                    estimated_cost=correction_cost,
                )
            except (ExternalAICallCancelled, RequestBudgetExceeded, TimeoutError):
                raise
            except Exception as exc:
                logger.warning("Corrective retrieval failed: %s", exc)
                log_trace(
                    "corrective_retrieval",
                    trace_id,
                    latency_ms=int((time.time() - correction_started) * 1000),
                    strategy="query_rewrite",
                    attempt=state.budget.corrections,
                    before_docs=before_count,
                    after_docs=len(retrieved_docs),
                    evaluator_state=coverage_decision.state.value,
                    error=type(exc).__name__,
                )


    return EnrichmentOutcome(
        documents=tuple(retrieved_docs),
        new_part_ids=tuple(new_part_ids),
        base_k=base_k,
        retrieval_mode=retrieval_mode,
        active_filter=locals().get("active_filter"),
        has_active_filter="active_filter" in locals(),
        served_graph_documents=tuple(served_graph_docs),
        graph_documents=tuple(graph_docs),
        graph_routed=graph_routed,
        graph_edge_count=graph_edge_count,
        graph_max_hops=graph_max_hops,
        community_documents=tuple(community_docs),
        community_summary_used=community_summary_used,
        community_summary_count=community_summary_count,
        community_fallback_reason=community_fallback_reason,
        grounded_math_enabled=grounded_math_enabled,
        auxiliary_input_tokens=auxiliary_input_tokens,
        auxiliary_output_tokens=auxiliary_output_tokens,
        correction_estimated_cost=correction_estimated_cost,
    )


__all__ = ["EnrichmentOutcome", "enrich_retrieval"]
