# -*- coding: utf-8 -*-
"""Auto-split tu rag/service.py (P1.2). Orchestrator: chat_with_rag + citations.
Giu nguyen tung byte cua chat_with_rag; chi di chuyen sang file rieng + re-import cac module con."""

import os
import re
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
from mech_chatbot.db.repository import search_bom_by_code, traverse_knowledge_graph
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
)

# owned names tu cac module con (bao gom ca ten _underscore qua __all__)
from mech_chatbot.rag.bootstrap import *
from mech_chatbot.rag.prompt import *
from mech_chatbot.rag.rerank import *
from mech_chatbot.rag.intent import *
from mech_chatbot.rag.retrieval import *
from mech_chatbot.rag.evidence_gate import *
from mech_chatbot.rag.corrective import (
    merge_corrected_documents,
    run_corrected_retrieval,
    should_attempt_correction,
)
from mech_chatbot.rag.query_decomposition import audit_decomposition_stream


from mech_chatbot.rag.pipeline_steps import _prepare_history, _analyze_image, _assemble_context, _generate, _retrieve, _RETRIEVE_UNSET, _route, _rewrite_and_anchor, _disambiguate

def make_debug_info(docs=None):
    docs = docs or []
    try:
        from mech_chatbot.rag.semantic_cache import pipeline_namespace
        namespace = pipeline_namespace()
    except Exception:
        namespace = "unknown"
    return {
        "pipeline_namespace": namespace,
        "retrieved_docs": serialize_debug_documents(docs),
    }


def serialize_debug_documents(docs=None):
    docs = docs or []
    return [
        {
            "file_goc": d.metadata.get("file_goc"),
            "doc_id": d.metadata.get("doc_id"),
            "version_no": d.metadata.get("version_no"),
            "variant_code": d.metadata.get("variant_code"),
            "is_current": d.metadata.get("is_current"),
            "lifecycle_status": d.metadata.get("lifecycle_status"),
            "review_status": d.metadata.get("review_status"),
            "trang": d.metadata.get("trang_so"),
            "source_id": (
                f"D{d.metadata.get('doc_id')}P{d.metadata.get('trang_so')}"
                if d.metadata.get("doc_id") is not None and d.metadata.get("trang_so") is not None
                else None
            ),
            "vision_used": bool(d.metadata.get("vision_used", False)),
            "score": d.metadata.get("relevance_score"),
            # GD5 muc 3: kem muc mat de tang audit doc tai lieu confidential o tang UI.
            "security_level": d.metadata.get("security_level"),
            "graph_edge_id": d.metadata.get("graph_edge_id"),
            "graph_relation_type": d.metadata.get("graph_relation_type"),
            "graph_source_key": d.metadata.get("graph_source_key"),
            "graph_target_key": d.metadata.get("graph_target_key"),
            "text": str(d.metadata.get("noi_dung_goc") or getattr(d, "page_content", "") or "")[:800],
        }
        for d in docs
    ]


def make_source_snapshot(docs=None):
    """Return citation/evidence metadata without retaining document text.

    This payload is safe to persist with a semantic-cache entry and is enough
    for the browser to resolve final SourceIDs and for history to record the
    complete authorization basis of the answer.
    """
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
                    if normalized_page_no is not None else None
                ),
                "score": metadata.get("relevance_score"),
                "security_level": metadata.get("security_level"),
            }
        )
    return snapshots


def chat_with_rag(user_question, image_path=None, chat_history=None, current_part_ids=None, user_department=None, user_roles=None, allowed_departments=None, max_security_level="public", allowed_sites=None, response_language="vi", conversation_context=None, trace_id=None, cancel_event=None):
    if chat_history is None:
        chat_history = []
        
    trace_id = trace_id or f"rag_{datetime.now().strftime('%Y%m%d_%H%M%S')}_{uuid.uuid4().hex[:6]}"
    t_start = time.time()
    request_started_monotonic = time.monotonic()
    
    log_trace("rag_start", trace_id,
              question_length=len(user_question or ""),
              has_image=bool(image_path),
              history_count=len(chat_history),
              current_part_ids=current_part_ids,
              department=user_department,
              role=",".join(user_roles) if user_roles else "",
              model=get_llm_model_name(),
              pipeline_namespace=make_debug_info([])["pipeline_namespace"])

    # Deterministic safety must run before every cache lookup. Otherwise an
    # entry produced under an older rule set could bypass a tightened policy.
    try:
        from mech_chatbot.rag import route_safety as _pre_cache_safety
        _safety_reason = (_pre_cache_safety.detect(user_question)
                          if _pre_cache_safety.enabled() else None)
    except Exception as _safety_error:
        logger.error("Pre-cache safety check failed: %s", _safety_error, exc_info=True)
        _safety_reason = "safety_check_unavailable"
    if _safety_reason:
        from mech_chatbot.rag import route_responses as _route_responses_sb
        _safety_text = _route_responses_sb.build_safety_response(
            response_language, user_department, allowed_departments
        )
        def _pre_cache_safety_stream():
            yield _safety_text
        log_trace("safety", trace_id, reason=_safety_reason, blocked=True, layer="pre_cache")
        log_trace("rag_end", trace_id, final_latency_ms=int((time.time() - t_start)*1000), refusal=True, refusal_reason="safety_block")
        return _pre_cache_safety_stream(), "", [], current_part_ids or [], make_debug_info([])

    _sc_qemb = None
    _sc_scope = None
    _sc_cache_eligible = not any(
        [image_path, chat_history, current_part_ids, bool(conversation_context)]
    )
    _exact_cache_started = time.time()
    if _sc_cache_eligible:
        try:
            import mech_chatbot.rag.semantic_cache as _sc_fast

            if _sc_fast.enabled():
                _sc_scope = _sc_fast.scope_signature(
                    user_department,
                    allowed_departments,
                    max_security_level,
                    allowed_sites,
                    user_roles,
                )
                _exact_hit = _sc_fast.lookup_exact(user_question, _sc_scope)
                if _exact_hit:
                    logger.info("Exact cache HIT -> tra loi truoc router/embedding.")
                    _dbg = {
                        "retrieved_docs": _exact_hit.get("evidence_snapshot") or [],
                        "citation_docs": _exact_hit.get("citation_snapshot") or [],
                    }
                    _dbg["cache_hit"] = True
                    _dbg["cache_type"] = "exact"

                    def _exact_cached_stream():
                        yield _exact_hit.get("answer", "")

                    log_trace("cache", trace_id, cache_type="exact", hit=True,
                              latency_ms=int((time.time() - _exact_cache_started) * 1000))
                    log_trace(
                        "rag_end",
                        trace_id,
                        final_latency_ms=int((time.time() - t_start) * 1000),
                        refusal=False,
                        cache_hit=True,
                        cache_type="exact",
                    )
                    return (
                        _exact_cached_stream(),
                        _exact_hit.get("ref_text", ""),
                        _exact_hit.get("ref_images", []),
                        current_part_ids or [],
                        _dbg,
                    )
        except Exception as _sce_fast:
            logger.warning(f"exact cache lookup loi: {_sce_fast}")
    if _sc_cache_eligible:
        log_trace("cache", trace_id, cache_type="exact", hit=False,
                  latency_ms=int((time.time() - _exact_cache_started) * 1000))

    # P0 slice #1: lich su hoi thoai tach sang pipeline_steps._prepare_history
    chat_history_str, _history_summary_new, _summary_covered_new = _prepare_history(
        chat_history, conversation_context, response_language, trace_id=trace_id
    )
 
    # P0 slice #2: phan tich anh tach sang pipeline_steps._analyze_image
    image_analysis = _analyze_image(image_path, user_question, trace_id)
 
    # BUOC B: TIM KIEM THONG MINH KET HOP STATE MEMORY
    # P0 slice #4: dinh tuyen hoi thoai (interaction router + safety + meta + chitchat) tach sang pipeline_steps._route
    _route_terminal, _route_bundle = _route(
        user_question=user_question,
        conversation_context=conversation_context,
        response_language=response_language,
        user_department=user_department,
        allowed_departments=allowed_departments,
        current_part_ids=current_part_ids,
        trace_id=trace_id,
        t_start=t_start,
        make_debug_info=make_debug_info,
    )
    if _route_terminal is not None:
        return _route_terminal
    mock_stream = _route_bundle["mock_stream"]
    _embed_cached = _route_bundle["_embed_cached"]
    is_chitchat = _route_bundle["is_chitchat"]

    retrieved_docs = []
    skip_retrieval = False
    decomposition_notice = ""
    decomposition_states = []
    decomposition_branches = []
    planner_count = 0
    subquery_count = 0
    deadline_exceeded = False
    final_generation_count = 0
    auxiliary_input_tokens = 0
    auxiliary_output_tokens = 0
    planner_estimated_cost = 0.0
    auxiliary_retry_counter = {"count": 0}
    correction_attempts = 0
    correction_estimated_cost = 0.0
    crag_enabled = env_bool("RAG_CRAG_ENABLED", False)
    query_to_search = user_question  # Mac dinh, cac nhanh ben duoi se override neu can
    logger.info("Dang phan tich intent de tim kiem du lieu...")
    t_intent = time.time()

    # P2-9: Semantic cache LOOKUP (best-effort). Hit -> tra ngay, bo qua retrieval + LLM.
    _semantic_cache_started = time.time()
    try:
        import mech_chatbot.rag.semantic_cache as _sc
        if _sc.enabled() and _sc_cache_eligible:
            _sc_qemb = _embed_cached(user_question)
            if _sc_scope is None:
                _sc_scope = _sc.scope_signature(user_department, allowed_departments, max_security_level, allowed_sites, user_roles)
            _hit = _sc.lookup(user_question, _sc_qemb, _sc_scope)
            if _hit:
                logger.info("Semantic cache HIT -> tra loi tu cache.")
                _dbg = {
                    "retrieved_docs": _hit.get("evidence_snapshot") or [],
                    "citation_docs": _hit.get("citation_snapshot") or [],
                }
                _dbg["cache_hit"] = True
                def _cached_stream():
                    yield _hit.get("answer", "")
                log_trace("cache", trace_id, cache_type="semantic", hit=True,
                          latency_ms=int((time.time() - _semantic_cache_started) * 1000))
                log_trace("rag_end", trace_id, final_latency_ms=int((time.time() - t_start) * 1000), refusal=False, cache_hit=True)
                return _cached_stream(), _hit.get("ref_text", ""), _hit.get("ref_images", []), current_part_ids, _dbg
    except Exception as _sce:
        logger.warning(f"semantic cache lookup loi: {_sce}")
    if _sc_cache_eligible:
        log_trace("cache", trace_id, cache_type="semantic", hit=False,
                  latency_ms=int((time.time() - _semantic_cache_started) * 1000))

    # === BUOC B0 (P0-1): PHAN DOAN NGU CANH + QUERY REWRITING + NEO STATE MEMORY ===
    # P0 slice #5: tach sang pipeline_steps._rewrite_and_anchor (analyze_context + rewrite + anchor + intent)
    (effective_question, new_part_ids, is_inherited, is_bom_query, intent_data,
     strict_filter, broad_filter, rbac_filter, _skip_hyde_anchor) = _rewrite_and_anchor(
        user_question=user_question,
        chat_history=chat_history,
        current_part_ids=current_part_ids,
        conversation_context=conversation_context,
        user_department=user_department,
        user_roles=user_roles,
        allowed_departments=allowed_departments,
        max_security_level=max_security_level,
        allowed_sites=allowed_sites,
        trace_id=trace_id,
        t_intent=t_intent,
    )

    if intent_data.get("version_policy") == "compare_versions" and not intent_data.get("detected_versions"):
        logger.info("Nguoi dung muon so sanh nhung khong chi dinh version. Yeu cau xac minh.")
        _ver_vi = ("Bạn muốn so sánh tài liệu này với phiên bản nào? (Ví dụ: v1 và v2, hoặc bản "
                   "đang lưu hành và bản bị lưu trữ gần nhất). Vui lòng chỉ định rõ phiên bản để "
                   "mình đối chiếu số liệu chính xác nhé.")
        def ask_version_stream():
            yield _t_rag(_ver_vi, response_language)
        log_trace("rag_end", trace_id, final_latency_ms=int((time.time() - t_start)*1000), refusal=True, reason="missing_compare_versions")
        return ask_version_stream(), "", [], current_part_ids, make_debug_info([])

    if intent_data.get("is_chitchat"):
        logger.info("LLM xac nhan la cau hoi ngoai le/xa giao. Bo qua toan bo Retrieval va HyDE.")
        log_trace("route", trace_id, route="chitchat", layer="L2_llm_intent", confidence=1.0)
        log_trace("rag_end", trace_id, final_latency_ms=int((time.time() - t_start)*1000), refusal=False, is_chitchat=True)
        return mock_stream(), "", [], current_part_ids, make_debug_info([])
    else:
        # Tien xu ly cau hoi bang underthesea de match voi du lieu BM25
        tokenized_question = tokenize_cached(effective_question)
        query_to_search = tokenized_question

        # Retrieval-first: HyDE is an expensive recall fallback, not a mandatory
        # pre-retrieval call for every short question.
        _hyde_eligible = (
            env_bool("HYDE_ENABLED", True)
            and len(tokenized_question.split()) < 25
            and not new_part_ids
            and not _skip_hyde_anchor
        )

        # P0-3: mo rong truy van bang glossary/synonym theo domain (tang recall cho phong phi co khi)
        try:
            _gloss_add = glossary_expansion_terms(effective_question, user_department)
            if _gloss_add:
                query_to_search = str(query_to_search) + " " + tokenize_cached(_gloss_add)
                log_trace("glossary_expansion", trace_id, added=_gloss_add[:200])
        except Exception as _ge:
            logger.warning(f"glossary expansion loi: {_ge}")

        decomposition_enabled = env_bool("RAG_QUERY_DECOMPOSITION_ENABLED", False)
        if decomposition_enabled:
            from mech_chatbot.rag.query_decomposition import (
                build_plan, build_partial_answer_instruction, codes_in_query,
                BranchRetrievalResult, CorrectionBudget, execute_plan,
                merge_branch_documents, sufficient_branch_documents,
            )

            def _planner(question):
                nonlocal planner_count, auxiliary_input_tokens, auxiliary_output_tokens, planner_estimated_cost
                planner_count += 1
                prompt = (
                    "Tach cau hoi noi bo phuc hop thanh toi da 3 truy van doc lap. "
                    "Giu nguyen thu tu cac y trong cau hoi goc. "
                    "Khong them ma tai lieu, phien ban, phong ban hay site khong co trong cau goc. "
                    "Chi tra JSON theo schema {\"subqueries\":[\"...\"]}.\nCAU HOI:\n" + question
                )
                response = cohere_invoke(
                    [HumanMessage(content=prompt)],
                    surface="query_decomposition",
                    trace_id=trace_id,
                    retry_counter=auxiliary_retry_counter,
                ).content
                planner_input = len(prompt) // 4
                planner_output = len(str(response)) // 4
                auxiliary_input_tokens += planner_input
                auxiliary_output_tokens += planner_output
                planner_estimated_cost += (
                    planner_input * 2.5 + planner_output * 15.0
                ) / 1_000_000
                return _safe_json_loads(response)

            decomp_plan = build_plan(
                effective_question,
                planner=_planner,
                planner_version=os.getenv("RAG_PLANNER_VERSION", "planner-v1"),
            )
            if decomp_plan.is_complex:
                try:
                    request_deadline_seconds = max(
                        0.1, float(os.getenv("RAG_REQUEST_DEADLINE_SECONDS", "120"))
                    )
                except (TypeError, ValueError):
                    request_deadline_seconds = 120.0
                request_deadline_monotonic = request_started_monotonic + request_deadline_seconds
                access_context = {
                    "user_department": user_department,
                    "roles": tuple(user_roles or ()),
                    "allowed_departments": tuple(allowed_departments or ()),
                    "allowed_sites": tuple(allowed_sites or ()),
                    "max_security_level": max_security_level,
                    "version_policy": intent_data.get("version_policy"),
                }
                shared_correction_budget = CorrectionBudget(1)

                def _retrieve_branch(
                    subquery, inherited_access, _correction_budget, branch_deadline_monotonic,
                ):
                    if (
                        branch_deadline_monotonic is not None
                        and time.monotonic() >= branch_deadline_monotonic
                    ):
                        return BranchRetrievalResult(
                            [], 0, "deadline_exceeded", time.time(), _RETRIEVE_UNSET,
                            deadline_exceeded=True,
                        )
                    branch_codes = set(codes_in_query(subquery))
                    branch_part_ids = [
                        part_id for part_id in new_part_ids
                        if str(part_id).lower() in branch_codes
                    ]
                    if not branch_part_ids and len(new_part_ids) == 1:
                        branch_part_ids = list(new_part_ids)
                    common_must = list(getattr(strict_filter, "must", ()) or ())
                    if new_part_ids and common_must:
                        common_must = common_must[:-1]
                    branch_strict, branch_broad = compose_retrieval_filters(
                        common_must, branch_part_ids
                    )
                    result = _retrieve(
                        new_part_ids=branch_part_ids,
                        strict_filter=branch_strict,
                        broad_filter=branch_broad,
                        is_bom_query=is_bom_query,
                        query_to_search=tokenize_cached(subquery),
                        rbac_filter=rbac_filter,
                        trace_id=trace_id,
                    )
                    correction_cost = 0.0
                    correction_input = 0
                    correction_output = 0
                    corrected = False
                    branch_docs = result[0]
                    branch_deadline_exceeded = bool(
                        branch_deadline_monotonic is not None
                        and time.monotonic() >= branch_deadline_monotonic
                    )
                    if branch_deadline_exceeded:
                        result = (
                            [], result[1], result[2] + "+deadline_exceeded",
                            result[3], result[4],
                        )
                        branch_docs = []
                    branch_decision = evaluate_answerability(
                        subquery,
                        _assemble_context(branch_docs, subquery) if branch_docs else "",
                        docs=branch_docs,
                        trace_id=trace_id,
                    )
                    if (
                        crag_enabled
                        and not branch_deadline_exceeded
                        and branch_decision.state is EvidenceState.AMBIGUOUS
                        and _correction_budget.claim()
                    ):
                        corrected = True
                        try:
                            rewrite_prompt = (
                                "Rewrite this internal-document subquery once to improve evidence recall. "
                                "Keep every technical code and do not add facts. Return only the query.\n\n"
                                f"Question: {subquery}\nMissing evidence: {branch_decision.reason}"
                            )
                            rewritten = cohere_invoke(
                                [HumanMessage(content=rewrite_prompt)],
                                surface="corrective_retrieval",
                                trace_id=trace_id,
                                retry_counter=auxiliary_retry_counter,
                            ).content
                            corrected_result = run_corrected_retrieval(
                                _retrieve,
                                corrected_query=tokenize_cached(str(rewritten or subquery)),
                                new_part_ids=branch_part_ids,
                                strict_filter=branch_strict,
                                broad_filter=branch_broad,
                                is_bom_query=is_bom_query,
                                rbac_filter=rbac_filter,
                                trace_id=trace_id,
                            )
                            correction_cost = (
                                (len(rewrite_prompt) // 4) * 2.5 + (len(str(rewritten)) // 4) * 15.0
                            ) / 1_000_000
                            correction_input = len(rewrite_prompt) // 4
                            correction_output = len(str(rewritten)) // 4
                            result = (
                                merge_corrected_documents(result[0], corrected_result[0]),
                                result[1],
                                result[2] + "+corrected:" + corrected_result[2],
                                result[3],
                                result[4],
                            )
                            log_trace(
                                "corrective_retrieval", trace_id,
                                strategy="decomposed_query_rewrite", attempt=1,
                                before_docs=len(branch_docs), after_docs=len(result[0]),
                                evaluator_state=branch_decision.state.value,
                                estimated_cost=correction_cost,
                            )
                        except Exception as exc:
                            logger.warning("Decomposed corrective retrieval failed: %s", exc)
                            log_trace(
                                "corrective_retrieval", trace_id,
                                strategy="decomposed_query_rewrite", attempt=1,
                                before_docs=len(branch_docs), after_docs=len(branch_docs),
                                evaluator_state=branch_decision.state.value,
                                error=type(exc).__name__,
                            )
                    access_denied = False
                    if not result[0] and branch_part_ids and not branch_deadline_exceeded:
                        access_denied, _ = probe_restricted_access(
                            subquery,
                            user_department=inherited_access["user_department"],
                            allowed_departments=inherited_access["allowed_departments"],
                            max_security_level=inherited_access["max_security_level"],
                            allowed_sites=inherited_access["allowed_sites"],
                            part_ids=branch_part_ids,
                        )
                    branch_deadline_exceeded = bool(
                        branch_deadline_monotonic is not None
                        and time.monotonic() >= branch_deadline_monotonic
                    )
                    if branch_deadline_exceeded:
                        result = (
                            [], result[1], result[2] + "+deadline_exceeded",
                            result[3], result[4],
                        )
                    return BranchRetrievalResult(
                        documents=result[0], base_k=result[1], retrieval_mode=result[2],
                        started_at=result[3], active_filter=result[4],
                        correction_cost=correction_cost,
                        correction_attempted=corrected,
                        correction_input_tokens=correction_input,
                        correction_output_tokens=correction_output,
                        access_denied=bool(access_denied),
                        deadline_exceeded=branch_deadline_exceeded,
                    )

                branch_results = execute_plan(
                    decomp_plan,
                    _retrieve_branch,
                    access_context,
                    deadline_monotonic=request_deadline_monotonic,
                    on_timeout=lambda _query: BranchRetrievalResult(
                        [], 0, "deadline_exceeded", time.time(), _RETRIEVE_UNSET,
                        deadline_exceeded=True,
                    ),
                )
                subquery_count = len(decomp_plan.subqueries)
                correction_estimated_cost += sum(result.correction_cost for result in branch_results)
                correction_attempts += sum(result.correction_attempted for result in branch_results)
                auxiliary_input_tokens += sum(result.correction_input_tokens for result in branch_results)
                auxiliary_output_tokens += sum(result.correction_output_tokens for result in branch_results)
                retrieved_docs = merge_branch_documents(result.documents for result in branch_results)
                base_k = max((result.base_k for result in branch_results), default=0)
                retrieval_mode = "decomposed_" + "+".join(sorted({result.retrieval_mode for result in branch_results}))
                t_retrieval = min((result.started_at for result in branch_results), default=time.time())
                _af = next((result.active_filter for result in branch_results if result.active_filter is not _RETRIEVE_UNSET), _RETRIEVE_UNSET)
                for branch_index, (subquery, result) in enumerate(
                    zip(decomp_plan.subqueries, branch_results), 1
                ):
                    branch_docs = result.documents
                    decision = evaluate_answerability(
                        subquery,
                        _assemble_context(branch_docs, subquery) if branch_docs else "",
                        docs=branch_docs,
                        trace_id=trace_id,
                    )
                    decomposition_states.append(decision.state.value)
                    deadline_exceeded = deadline_exceeded or result.deadline_exceeded
                    if result.access_denied:
                        branch_outcome = "access_denied"
                    elif decision.state is EvidenceState.SUFFICIENT:
                        branch_outcome = "full_answer"
                    else:
                        branch_outcome = "insufficient_evidence"
                    decomposition_branches.append({
                        "branch_id": f"branch-{branch_index}",
                        "outcome": branch_outcome,
                        "evaluator_state": decision.state.value,
                        "citations": make_source_snapshot(branch_docs),
                        "correction_attempted": result.correction_attempted,
                        "deadline_exceeded": result.deadline_exceeded,
                    })
                retrieved_docs = sufficient_branch_documents(
                    branch_results, decomposition_branches
                )
                decomposition_notice = build_partial_answer_instruction(
                    decomposition_branches
                )
                log_trace(
                    "query_decomposition",
                    trace_id,
                    planner_count=planner_count,
                    subquery_count=subquery_count,
                    evaluator_states=decomposition_states,
                    correction_budget=1,
                    deadline_exceeded=deadline_exceeded,
                    estimated_cost=planner_estimated_cost + correction_estimated_cost,
                    input_tokens=auxiliary_input_tokens,
                    output_tokens=auxiliary_output_tokens,
                )
            else:
                (retrieved_docs, base_k, retrieval_mode, t_retrieval, _af) = _retrieve(
                    new_part_ids=new_part_ids, strict_filter=strict_filter,
                    broad_filter=broad_filter, is_bom_query=is_bom_query,
                    query_to_search=query_to_search, rbac_filter=rbac_filter, trace_id=trace_id,
                )
        else:
            (retrieved_docs, base_k, retrieval_mode, t_retrieval, _af) = _retrieve(
                new_part_ids=new_part_ids,
                strict_filter=strict_filter,
                broad_filter=broad_filter,
                is_bom_query=is_bom_query,
                query_to_search=query_to_search,
                rbac_filter=rbac_filter,
                trace_id=trace_id,
            )
        if _af is not _RETRIEVE_UNSET:
            active_filter = _af

        if not retrieved_docs and _hyde_eligible:
            logger.info("Retrieval rong; kich hoat HyDE fallback mot lan.")
            try:
                hyde_prompt = (
                    "Viet mot doan van ban ngan gon (1-2 cau) tra loi cho cau hoi sau "
                    f"dua tren tai lieu noi bo: '{effective_question}'"
                )
                t_hyde = time.time()
                hyde_response = cohere_invoke(
                    [HumanMessage(content=hyde_prompt)], surface="hyde",
                    trace_id=trace_id, retry_counter=auxiliary_retry_counter,
                ).content
                hyde_query = tokenize_cached(hyde_response)
                (retrieved_docs, base_k, retrieval_mode, t_retrieval, _af) = _retrieve(
                    new_part_ids=new_part_ids,
                    strict_filter=strict_filter,
                    broad_filter=broad_filter,
                    is_bom_query=is_bom_query,
                    query_to_search=hyde_query,
                    rbac_filter=rbac_filter,
                    trace_id=trace_id,
                )
                retrieval_mode = f"{retrieval_mode}_hyde_fallback"
                if _af is not _RETRIEVE_UNSET:
                    active_filter = _af
                log_trace(
                    "hyde",
                    trace_id,
                    latency_ms=int((time.time() - t_hyde) * 1000),
                    used=True,
                    hyde_chars=len(hyde_response),
                    fallback_docs=len(retrieved_docs),
                )
            except Exception as e:
                logger.warning(f"Loi HyDE fallback: {e}")
                log_trace("hyde", trace_id, used=True, error=str(e))
 
    graph_routed = False
    graph_edge_count = 0
    graph_max_hops = 0
    graph_docs = []
    served_graph_docs = []
    if not skip_retrieval and env_bool("RAG_GRAPH_RETRIEVAL_ENABLED", False):
        from mech_chatbot.rag.graph_retrieval import (
            filter_servable_edges, hydrate_graph_edges, select_graph_seeds,
            should_attempt_graph,
        )
        graph_started = time.time()
        graph_seeds = select_graph_seeds(effective_question, new_part_ids)
        graph_access = {
            "roles": user_roles or [],
            "allowed_departments": allowed_departments or [],
            "allowed_sites": allowed_sites or [],
            "max_security_level": max_security_level,
        }
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
            log_trace(
                "graph_retrieval", trace_id,
                latency_ms=int((time.time() - graph_started) * 1000),
                routed=graph_routed,
                route_scope="relational" if graph_routed else "regular",
                seed_count=len(graph_seeds), edge_count=len(graph_edges),
                hydrated_count=len(graph_docs), max_hops=2, edge_limit=50,
            )
        except Exception as exc:
            logger.warning("Graph retrieval unavailable: %s", exc)
            log_trace("graph_retrieval", trace_id, error=type(exc).__name__, edge_count=0)

    def _access_denied_terminal(access_reason):
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

        debug = make_debug_info([])
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
        return restricted_stream(), "", [], current_part_ids, debug

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
            return insufficient_evidence_stream(), "", [], current_part_ids, make_debug_info([])

    if not skip_retrieval:
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
        )
        if _disambig_terminal is not None:
            return _disambig_terminal

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

    # Inject SQL BOM Data
    if not skip_retrieval and new_part_ids and _context_is_mechanical(retrieved_docs, new_part_ids):
        t_sql = time.time()
        try:
            bom_results = search_bom_by_code(
                new_part_ids,
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
                    (
                        doc_id,
                        page_no,
                        ma,
                        ten,
                        vat_lieu,
                        sl,
                        gc,
                        file,
                        version_no,
                        security_level,
                        site,
                        external_processing_policy,
                        bom_row_id,
                        unit,
                    ) = row
                    try:
                        source_key = (int(doc_id), int(page_no))
                    except (TypeError, ValueError):
                        # The SQL query excludes NULL pages, but keep the
                        # context fail-closed if a legacy row is malformed.
                        continue
                    source = bom_by_source.setdefault(
                        source_key,
                        {
                            "file_goc": file,
                            "version_no": version_no,
                            "security_level": security_level,
                            "site": site,
                            "external_processing_policy": external_processing_policy,
                            "lines": [],
                            "facts": [],
                        },
                    )
                    source["lines"].append(
                        f"- Mã: {ma}, Tên: {ten}, Vật liệu: {vat_lieu}, "
                        f"SL: {sl}, Ghi chú: {gc}"
                    )
                    if env_bool("RAG_GROUNDED_MATH_ENABLED", False) and sl is not None:
                        try:
                            from decimal import Decimal
                            from mech_chatbot.rag.grounded_math import GroundedFact
                            source["facts"].append(GroundedFact(
                                value=Decimal(str(sl)), unit=str(unit or "").strip(),
                                doc_id=int(doc_id), page=int(page_no), version=int(version_no),
                                source_id=f"BOM-{int(bom_row_id)}",
                                label=str(ma or "").strip(),
                            ))
                        except (ValueError, TypeError, ArithmeticError):
                            pass

                calculation_claims = {}
                if env_bool("RAG_GROUNDED_MATH_ENABLED", False):
                    from mech_chatbot.rag.grounded_math import (
                        build_calculation_plan,
                        derive_claim,
                    )
                    facts_by_document = {}
                    for (source_doc_id, _), source in bom_by_source.items():
                        claim_key = (source_doc_id, int(source["version_no"]))
                        facts_by_document.setdefault(claim_key, []).extend(source["facts"])
                    all_facts = tuple(
                        fact
                        for facts in facts_by_document.values()
                        for fact in facts
                    )
                    plan = build_calculation_plan(user_question, all_facts)
                    if plan is not None and facts_by_document:
                        operand_keys = tuple(dict.fromkeys(
                            (fact.doc_id, fact.version) for fact in plan.operands
                        ))
                        claim_key = operand_keys[0] if operand_keys else next(iter(facts_by_document))
                        calculation_claims[claim_key] = (plan, derive_claim(plan))

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
                                    source["external_processing_policy"] or "all_external"
                                ),
                                "calculation_provenance": calculation_provenance,
                            },
                        )
                    )
                retrieved_docs = bom_docs + retrieved_docs
                logger.info("Da them %s dong BOM tu SQL vao context (%s nguon co the citation).", len(bom_results), len(bom_docs))
                log_trace("sql_bom", trace_id, latency_ms=int((time.time() - t_sql)*1000), rows=len(bom_results), part_ids=new_part_ids)
            else:
                log_trace("sql_bom", trace_id, latency_ms=int((time.time() - t_sql)*1000), rows=0, part_ids=new_part_ids)
        except Exception as e:
            logger.error(f"Loi inject SQL BOM: {e}")
            log_trace("sql_bom", trace_id, latency_ms=int((time.time() - t_sql)*1000), error=str(e), part_ids=new_part_ids)
 
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

        return empty_stream(), "", [], current_part_ids, make_debug_info([])

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
            os.getenv("RAG_EXECUTION_CONTEXT", "production").strip().lower() == "evaluation"
            and os.getenv("RAG_EVAL_FORCE_AMBIGUOUS", "false").strip().lower() in {"1", "true", "yes", "on"}
        ):
            coverage_decision = EvidenceDecision(
                EvidenceState.AMBIGUOUS,
                reason="controlled_evaluation_correction_fixture",
                stage="evaluation_fixture",
                telemetry_status="heuristic_block",
            )
            log_trace("evaluation_override", trace_id, override="force_ambiguous")
        if should_attempt_correction(
            coverage_decision, attempts=correction_attempts, enabled=crag_enabled
        ):
            correction_started = time.time()
            before_count = len(retrieved_docs)
            correction_attempts += 1
            try:
                rewrite_prompt = (
                    "Rewrite this internal-document search query once to improve evidence recall. "
                    "Keep every technical code and do not add facts. Return only the query.\n\n"
                    f"Question: {effective_question}\nMissing evidence: {coverage_decision.reason}"
                )
                rewritten = cohere_invoke(
                    [HumanMessage(content=rewrite_prompt)],
                    surface="corrective_retrieval",
                    trace_id=trace_id,
                    retry_counter=auxiliary_retry_counter,
                ).content
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
                correction_estimated_cost += correction_cost
                retrieved_docs = merge_corrected_documents(retrieved_docs, corrected_docs)
                log_trace(
                    "corrective_retrieval",
                    trace_id,
                    latency_ms=int((time.time() - correction_started) * 1000),
                    strategy="query_rewrite",
                    attempt=correction_attempts,
                    before_docs=before_count,
                    corrected_docs=len(corrected_docs),
                    after_docs=len(retrieved_docs),
                    retrieval_mode=corrected_mode,
                    evaluator_state=coverage_decision.state.value,
                    estimated_cost=correction_cost,
                )
            except Exception as exc:
                logger.warning("Corrective retrieval failed: %s", exc)
                log_trace(
                    "corrective_retrieval",
                    trace_id,
                    latency_ms=int((time.time() - correction_started) * 1000),
                    strategy="query_rewrite",
                    attempt=correction_attempts,
                    before_docs=before_count,
                    after_docs=len(retrieved_docs),
                    evaluator_state=coverage_decision.state.value,
                    error=type(exc).__name__,
                )
 
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
            return mock_stream(), "", [], new_part_ids, make_debug_info([])

        t_parent_context = time.time()
        real_docs = hydrate_parent_context(real_docs)
        if graph_docs:
            from mech_chatbot.rag.graph_retrieval import attach_served_graph_context
            real_docs, served_graph_docs = attach_served_graph_context(real_docs, graph_docs)
        log_trace(
            "parent_context",
            trace_id,
            latency_ms=int((time.time() - t_parent_context) * 1000),
            sections=len(real_docs),
        )
        retrieved_docs = fake_docs + real_docs

        retrieved_docs = long_context_reorder(retrieved_docs)

    # BUOC C: SINH CAU TRA LOI (STREAMING)
    context_text = _assemble_context(retrieved_docs, user_question) + decomposition_notice

    # Citations are evidence actually relevant to the answer, not every
    # candidate that happened to survive retrieval/reranking. BOM questions in
    # particular must not display unrelated HR/procedure thumbnails.
    citation_docs = select_citation_docs(
        retrieved_docs,
        question=user_question,
        is_bom_query=is_bom_query,
        part_ids=new_part_ids,
    )
    ref_text, ref_images = build_source_citations(citation_docs)
    _conf_docs = [d.metadata.get("file_goc") for d in retrieved_docs if d.metadata.get("security_level") == "confidential"]
    if _conf_docs:
        logger.warning(f"[audit][confidential] dept={user_department} roles={user_roles} truy cap tai lieu mat: {_conf_docs}")

    # LOP PHONG THU 2: Evidence Gate cho cau hoi bay / cau hoi can so lieu
    t_gate = time.time()
    evidence_decision = evaluate_answerability(
        user_question,
        context_text,
        docs=retrieved_docs,
        trace_id=trace_id,
    )
    has_sufficient_decomposition_branch = any(
        branch.get("outcome") == "full_answer"
        for branch in decomposition_branches
    )
    answerable = (
        evidence_decision.answerable or has_sufficient_decomposition_branch
    )
    evidence_reason = evidence_decision.reason
    evidence_quotes = list(evidence_decision.evidence_quotes)
    log_trace(
        "evidence_gate",
        trace_id,
        latency_ms=int((time.time() - t_gate)*1000),
        answerable=answerable,
        state=evidence_decision.state.value,
        stage=evidence_decision.stage,
        status=evidence_decision.telemetry_status,
        reason=evidence_reason,
        correction_attempts=correction_attempts,
        partial_serving=(
            has_sufficient_decomposition_branch
            and not evidence_decision.answerable
        ),
    )
    
    if not answerable:
        logger.warning(f"Evidence gate BLOCK cau hoi: {evidence_reason}")
        safe_msg = make_insufficient_evidence_message(user_question, evidence_reason, lang=response_language)
        def refusal_stream():
            yield safe_msg
        log_trace("rag_end", trace_id, final_latency_ms=int((time.time() - t_start)*1000), refusal=True, refusal_reason="evidence_gate", docs_count=len(retrieved_docs), doc_ids=[d.metadata.get("doc_id") for d in retrieved_docs], retrieved_file_goc=[d.metadata.get("file_goc") for d in retrieved_docs], version_no=[d.metadata.get("version_no") for d in retrieved_docs], variant_code=[d.metadata.get("variant_code") for d in retrieved_docs], is_current=[d.metadata.get("is_current") for d in retrieved_docs], lifecycle_status=[d.metadata.get("lifecycle_status") for d in retrieved_docs], review_status=[d.metadata.get("review_status") for d in retrieved_docs], version_policy=intent_data.get("version_policy") if "intent_data" in locals() else None, filter_used=serialize_qdrant_filter(active_filter) if "active_filter" in locals() else None, top_k=base_k if "base_k" in locals() else None, retrieval_mode=retrieval_mode, retrieval_scores=[d.metadata.get("relevance_score") for d in retrieved_docs], user_department=user_department, user_roles=user_roles)
        _refusal_debug = make_debug_info(retrieved_docs)
        _refusal_debug["citation_docs"] = make_debug_info(retrieved_docs)["retrieved_docs"]
        _refusal_debug["evidence_state"] = evidence_decision.state.value
        _refusal_debug["evidence_stage"] = evidence_decision.stage
        _refusal_debug["correction_count"] = correction_attempts
        _refusal_debug["generation_metrics"] = {
            "estimated_cost": correction_estimated_cost + planner_estimated_cost,
            "input_tokens": auxiliary_input_tokens,
            "output_tokens": auxiliary_output_tokens,
            "provider_retries": int(auxiliary_retry_counter["count"]),
            "repair_count": 0,
        }
        _refusal_debug["planner_count"] = planner_count
        _refusal_debug["subquery_count"] = subquery_count
        _refusal_debug["final_generation_count"] = final_generation_count
        _refusal_debug["deadline_exceeded"] = deadline_exceeded
        _refusal_debug["decomposition_branches"] = decomposition_branches
        _refusal_debug["graph_traversal_count"] = len(served_graph_docs)
        _refusal_debug["graph_evidence"] = serialize_debug_documents(served_graph_docs)
        _refusal_debug["graph_routed"] = graph_routed
        _refusal_debug["graph_edge_count"] = graph_edge_count
        _refusal_debug["graph_max_hops"] = graph_max_hops
        return refusal_stream(), ref_text, ref_images, new_part_ids, _refusal_debug

    generation_metrics = {
        "estimated_cost": correction_estimated_cost + planner_estimated_cost,
        "input_tokens": auxiliary_input_tokens,
        "output_tokens": auxiliary_output_tokens,
        "provider_retries": int(auxiliary_retry_counter["count"]),
    }
    final_generation_count = 1
    stream = _generate(
        context_text=context_text,
        user_question=user_question,
        chat_history_str=chat_history_str,
        retrieved_docs=retrieved_docs,
        new_part_ids=new_part_ids,
        response_language=response_language,
        trace_id=trace_id,
        t_start=t_start,
        user_department=user_department,
        user_roles=user_roles,
        effective_question=effective_question,
        intent_data=intent_data,
        base_k=base_k,
        retrieval_mode=retrieval_mode,
        _has_active_filter=("active_filter" in locals()),
        _active_filter=(active_filter if "active_filter" in locals() else None),
        cancel_event=cancel_event,
        metrics=generation_metrics,
    )

    # BUOC D: TU DONG TAO TRICH DAN NGUON VA HINH ANH (Tra ve cung stream)
    debug_info = make_debug_info(retrieved_docs)
    debug_info["evidence_state"] = evidence_decision.state.value
    debug_info["evidence_stage"] = evidence_decision.stage
    debug_info["correction_count"] = correction_attempts
    debug_info["planner_count"] = planner_count
    debug_info["subquery_count"] = subquery_count
    debug_info["final_generation_count"] = final_generation_count
    debug_info["deadline_exceeded"] = deadline_exceeded
    debug_info["decomposition_branches"] = decomposition_branches
    debug_info["graph_traversal_count"] = len(served_graph_docs)
    debug_info["graph_evidence"] = serialize_debug_documents(served_graph_docs)
    debug_info["graph_routed"] = graph_routed
    debug_info["graph_edge_count"] = graph_edge_count
    debug_info["graph_max_hops"] = graph_max_hops
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
    return stream, ref_text, ref_images, new_part_ids, debug_info


def select_citation_docs(docs, question="", is_bom_query=False, part_ids=None, limit=None):
    """Return a small, evidence-focused citation set.

    Retrieval candidates remain available to generation and diagnostics, while
    the UI only receives sources relevant to the requested answer type.
    """
    docs = list(docs or [])
    if not docs:
        return []
    try:
        from mech_chatbot.rag.text_utils import remove_accents
        q_norm = remove_accents(str(question or "").lower())
    except Exception:
        q_norm = str(question or "").lower()
    bom_mode = bool(is_bom_query) or any(
        token in q_norm for token in ("bom", "bang ke vat tu", "vat tu", "bill of materials")
    )
    wanted_codes = {str(value).strip().lower() for value in (part_ids or []) if value}

    def _values(md, *keys):
        out = []
        for key in keys:
            value = md.get(key)
            if isinstance(value, (list, tuple, set)):
                out.extend(value)
            elif value is not None:
                out.append(value)
        return {str(value).strip().lower() for value in out if str(value).strip()}

    def _is_bom_evidence(doc):
        md = getattr(doc, "metadata", {}) or {}
        kind = str(md.get("loai_du_lieu") or "").lower()
        source = str(md.get("file_goc") or "").lower()
        if kind in {"sql_bom", "bang_ke_vat_tu", "bom"} or "bom" in source:
            return True
        if wanted_codes:
            codes = _values(md, "base_code", "ma_chinh", "ma_doi_tuong", "ma_btp", "ma_vat_tu")
            return bool(codes & wanted_codes)
        return False

    pool = [doc for doc in docs if _is_bom_evidence(doc)] if bom_mode else docs
    if not pool:
        pool = docs
    max_sources = int(limit or os.getenv("CITATION_MAX_SOURCES", "5"))
    if bom_mode:
        max_sources = min(max_sources, int(os.getenv("BOM_CITATION_MAX_SOURCES", "3")))

    selected = []
    seen = set()
    for doc in pool:
        md = getattr(doc, "metadata", {}) or {}
        key = (
            md.get("doc_id") or md.get("file_goc"),
            md.get("trang_so") or md.get("parent_page"),
        )
        if key in seen:
            continue
        seen.add(key)
        selected.append(doc)
        if len(selected) >= max_sources:
            break
    return selected


def build_source_citations(docs):
    references = []
    ref_images = []
    for doc in docs:
        source = doc.metadata.get('file_goc', 'Khong ro')
        page = doc.metadata.get('trang_so', '?')
        cong_doan = doc.metadata.get('cong_doan', 'Khong ro')
        loai = doc.metadata.get('loai_du_lieu', '')
        # Lay thu_muc de reconstruct ten file anh dung format (Fix Bug #7)
        thu_muc = doc.metadata.get('phong_ban_quyen', '')
        if isinstance(thu_muc, (list, tuple)):
            thu_muc = thu_muc[0] if thu_muc else ''
        # P1.3: bo sung dinh danh nguon de mo dung tai lieu goc
        doc_id = doc.metadata.get('doc_id')
        site = doc.metadata.get('site')
        version_no = doc.metadata.get('version_no')
 
        cite = f"**{source}** (Trang {page}) - {cong_doan}"
        # Hau to dinh danh: phong/khu + phien ban + ma tai lieu (de tra cuu trong Kho tai lieu)
        tags = []
        if thu_muc:
            tags.append(str(thu_muc))
        if site:
            tags.append(f"khu {site}")
        if version_no:
            tags.append(f"v{version_no}")
        if doc_id is not None:
            tags.append(f"DocID {doc_id}")
        if tags:
            cite += "  \u00b7 _" + " | ".join(tags) + "_"
        if loai == 'image_summary':
            cite += " *(phan tich hinh anh)*"
        if cite not in references:
            references.append(cite)
 
        # Trich xuat duong dan anh tham chieu
        # Format luu: {safe_thu_muc}_{ten_file_ko_ext}_page{N}.png
        if source != 'Anh dinh kem tu nguoi dung':
            safe_thu_muc = re.sub(r'[\\/*?:"<>|]', "", thu_muc) if thu_muc else ""
            base_name = os.path.splitext(str(source))[0]
            if safe_thu_muc:
                img_name = f"{safe_thu_muc}_{base_name}_page{page}.png"
            else:
                img_name = f"{base_name}_page{page}.png"
 
            _proj_root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
            img_path = os.path.join(_proj_root, "data", "processed", img_name)
            if img_path not in ref_images and os.path.exists(img_path):
                ref_images.append(img_path)
 
    if not references:
        return "", []
 
    ref_text = "\n\n---\n**Nguon tham chieu:**\n" + "\n".join([f"- {r}" for r in references])
    return ref_text, ref_images

__all__ = [
    'make_debug_info',
    'chat_with_rag',
    'select_citation_docs',
    'build_source_citations',
]
