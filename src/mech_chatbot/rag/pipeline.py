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
from mech_chatbot.llm.llm_client import cohere_invoke, get_cohere_llm, _is_cohere_rate_limit, gpt_rerank_documents, get_llm_model_name
from mech_chatbot.db.repository import search_bom_by_code
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
)

# owned names tu cac module con (bao gom ca ten _underscore qua __all__)
from mech_chatbot.rag.bootstrap import *
from mech_chatbot.rag.prompt import *
from mech_chatbot.rag.rerank import *
from mech_chatbot.rag.intent import *
from mech_chatbot.rag.retrieval import *
from mech_chatbot.rag.evidence_gate import *


def make_debug_info(docs=None):
    docs = docs or []
    return {
        "retrieved_docs": [
            {
                "file_goc": d.metadata.get("file_goc"),
                "doc_id": d.metadata.get("doc_id"),
                "version_no": d.metadata.get("version_no"),
                "variant_code": d.metadata.get("variant_code"),
                "is_current": d.metadata.get("is_current"),
                "lifecycle_status": d.metadata.get("lifecycle_status"),
                "review_status": d.metadata.get("review_status"),
                "trang": d.metadata.get("trang_so"),
                "score": d.metadata.get("relevance_score"),
                # GD5 muc 3: kem muc mat de tang audit doc tai lieu confidential o tang UI.
                "security_level": d.metadata.get("security_level"),
                "text": str(d.metadata.get("noi_dung_goc") or getattr(d, "page_content", "") or "")[:800],
            }
            for d in docs
        ]
    }


def chat_with_rag(user_question, image_path=None, chat_history=None, current_part_ids=None, user_department=None, user_roles=None, allowed_departments=None, max_security_level="public", allowed_sites=None, response_language="vi", conversation_context=None):
    if chat_history is None:
        chat_history = []
        
    trace_id = f"rag_{datetime.now().strftime('%Y%m%d_%H%M%S')}_{uuid.uuid4().hex[:6]}"
    t_start = time.time()
    
    log_trace("rag_start", trace_id, 
              question=user_question[:500],
              has_image=bool(image_path),
              history_count=len(chat_history),
              current_part_ids=current_part_ids,
              department=user_department,
              role=",".join(user_roles) if user_roles else "",
              model=get_llm_model_name())

    # Tao chuoi lich su (Token-Budgeted Windowing) de nap vao prompt cho mach lac hoi thoai
    # FIX HOI THOAI DAI: Thay vi co dinh 4 message (bot response dai chiem hang ngan token,
    # lan at context tai lieu khien LLM tra loi kem), dung budget ky tu co dinh.
    chat_history_str = ""
    try:
        HISTORY_BUDGET = int(os.getenv("HISTORY_BUDGET", "4000"))
    except Exception:
        HISTORY_BUDGET = 4000
    try:
        from mech_chatbot.rag import conversation_state as _cs_h
        _overflow_msgs, recent_history = _cs_h.split_history_for_summary(chat_history or [])
    except Exception:
        _overflow_msgs, recent_history = [], (chat_history or [])[-12:]
 
    built_parts = []
    budget_used = 0
    for msg in reversed(recent_history):  # Uu tien tin nhan moi nhat
        role = "Khach" if msg["role"] == "user" else "Bot"
        content = msg['content']
        # Bot response thuong rat dai (bang, trich dan) -> cat manh tay
        if role == "Bot" and len(content) > 400:
            cut_pos = content[:400].rfind('.')
            content = (content[:cut_pos + 1] if cut_pos > 50 else content[:400]) + " [...]"
        elif role == "Khach" and len(content) > 1200:
            content = content[:1200] + " [...]"
        line = f"{role}: {content}\n"
        if budget_used + len(line) > HISTORY_BUDGET:
            break
        built_parts.append(line)
        budget_used += len(line)
    chat_history_str = "".join(reversed(built_parts))

    # KH-3 (V3): tom tat luy tien cho phan hoi thoai tran ra ngoai cua so nguyen van.
    _history_summary_new = None
    _summary_covered_new = None
    try:
        from mech_chatbot.rag import conversation_state as _cs_sum
        if _cs_sum.history_summary_enabled():
            _cc_prev = conversation_context or {}
            _prev_summary = (_cc_prev.get("history_summary") or "").strip()
            _prev_covered = int(_cc_prev.get("summary_covered") or 0)
            _ov = _overflow_msgs or []
            if _cs_sum.needs_summary_refresh(len(_ov), _prev_covered):
                _to_sum = []
                for _m in _ov:
                    _r = "Khach" if _m.get("role") == "user" else "Bot"
                    _c = str(_m.get("content") or "")
                    if _r == "Bot" and len(_c) > 300:
                        _c = _c[:300] + " [...]"
                    _to_sum.append(f"{_r}: {_c}")
                _sum_prompt = (
                    "Ban la bo nho cua tro chuyen. Hay CAP NHAT ban tom tat hoi thoai "
                    "(toi da 8 dong gach dau dong), giu: chu de dang ban, tai lieu/ma da nhac, "
                    "cac ket luan/so lieu quan trong, va cau hoi con dang mo. "
                    "Chi tra ve tom tat, khong giai thich.\n\n"
                    f"TOM TAT HIEN CO:\n{_prev_summary or '(chua co)'}\n\n"
                    "CAC LUOT MOI CAN GOP:\n" + "\n".join(_to_sum)
                )
                try:
                    _history_summary_new = cohere_invoke([HumanMessage(content=_sum_prompt)]).content.strip()
                    _summary_covered_new = len(_ov)
                except Exception as _e_sum:
                    logger.warning(f"[KH-3] Tom tat hoi thoai loi: {_e_sum}")
                    _history_summary_new = _prev_summary or None
                    _summary_covered_new = _prev_covered
            else:
                _history_summary_new = _prev_summary or None
                _summary_covered_new = _prev_covered
            _eff_summary = (_history_summary_new or _prev_summary or "").strip()
            if _eff_summary:
                _is_en = str(response_language or "").lower().startswith("en")
                _summary_label = "=== EARLIER CONVERSATION SUMMARY ===" if _is_en else "=== TOM TAT HOI THOAI TRUOC DO ==="
                chat_history_str = f"{_summary_label}\n{_eff_summary}\n\n{chat_history_str}"
    except Exception as _e_sumwrap:
        logger.warning(f"[KH-3] Summary buffer loi: {_e_sumwrap}")
 
    # BUOC A: XU LY ANH BANG GEMINI
    image_analysis = ""
    if image_path:
        t_img_start = time.time()
        logger.info("Dang dung Gemini de phan tich anh tai len...")
        if _VISION_MODEL:
            try:
                img_to_analyze = Image.open(image_path)
                prompt = f"Nguoi dung tai len mot hinh anh va hoi: '{user_question}'. Hay mo ta chinh xac va chi tiet nhung gi ban thay trong anh nay de lam ngu canh tra loi. Neu do la ma code hay giao dien phan mem, hay noi ro. Tra loi bang tieng Viet."
 
                @retry(
                    retry=retry_if_exception(is_retryable_error),
                    wait=wait_exponential(multiplier=2, min=2, max=30),
                    stop=stop_after_attempt(5)
                )
                def call_gemini():
                    return _VISION_MODEL.generate_content([prompt, img_to_analyze])
 
                response = call_gemini()
                image_analysis = response.text
                logger.info("Phan tich anh bang Gemini thanh cong.")
                
                log_trace("image_analysis", trace_id, 
                          latency_ms=int((time.time() - t_img_start)*1000),
                          success=True,
                          analysis_chars=len(image_analysis))
            except Exception as e:
                logger.error(f"Loi khi doc anh bang Gemini: {e}", exc_info=True)
                log_trace("image_analysis", trace_id, 
                          latency_ms=int((time.time() - t_img_start)*1000),
                          success=False,
                          error=str(e))
        else:
            logger.warning("Chua co API Key Gemini hop le, bo qua phan tich anh.")
            log_trace("image_analysis", trace_id, 
                      latency_ms=int((time.time() - t_img_start)*1000),
                      success=False,
                      reason="no_vision_model")
 
    # BUOC B: TIM KIEM THONG MINH KET HOP STATE MEMORY
    # P0/P1 (Interaction Router): NGUON DUY NHAT cho dinh tuyen hoi thoai.
    # L0 (chitchat.py) + L1 (semantic router). Thay set inline + substring cu.
    from mech_chatbot.rag import interaction_router as _interaction_router
    # P2: cache embedding cau hoi -> tai dung cho router + semantic cache (tranh embed 2 lan).
    _qemb_cache = {}
    def _embed_cached(_t):
        _k = _t if isinstance(_t, str) else str(_t)
        if _k in _qemb_cache:
            return _qemb_cache[_k]
        try:
            _v = vectorstore.embeddings.embed_query(_k)
        except Exception:
            _v = None
        _qemb_cache[_k] = _v
        return _v
    def _router_embedder(_t):
        return _embed_cached(_t)
    # P2: L2 LLM classifier fallback (chi chay khi L0/L1 khong du tu tin).
    def _llm_classifier(_t, _ctx=None):
        try:
            from mech_chatbot.rag import route_llm as _route_llm
            return _route_llm.classify_llm(_t, _ctx)
        except Exception:
            return None
    _route_result = _interaction_router.classify(user_question, context=conversation_context, embedder=_router_embedder, llm_classifier=_llm_classifier)
    is_chitchat = _route_result.is_chitchat()
    log_trace("route", trace_id, route=_route_result.route, layer=_route_result.layer, confidence=_route_result.confidence)

    # P2: safety_block -> chan NGAY truoc pipeline + log audit.
    if _route_result.route == _interaction_router.ROUTE_SAFETY_BLOCK:
        from mech_chatbot.rag import route_responses as _route_responses_sb
        _safety_text = _route_responses_sb.build_safety_response(response_language, user_department, allowed_departments)
        def safety_stream():
            yield _safety_text
        logger.warning("Route safety_block -> chan yeu cau (reason=%s).", getattr(_route_result, "reason", ""))
        log_trace("safety", trace_id, reason=getattr(_route_result, "reason", ""), blocked=True)
        log_trace("rag_end", trace_id, final_latency_ms=int((time.time() - t_start)*1000), refusal=True, refusal_reason="safety_block", is_chitchat=False, route=_route_result.route)
        return safety_stream(), "", [], current_part_ids, make_debug_info([])
 
    retrieved_docs = []
    skip_retrieval = False
    query_to_search = user_question  # Mac dinh, cac nhanh ben duoi se override neu can
    _sc_qemb = None   # P2-9 semantic cache: embedding cau hoi
    _sc_scope = None  # P2-9 semantic cache: chu ky pham vi RBAC
 
    _chitchat_vi = ("Chào bạn! Mình là Trợ lý Tài liệu Nội bộ của công ty. Bạn có thể hỏi mình về tài liệu, "
                    "quy trình, chính sách hay số liệu của các phòng ban, hoặc upload tài liệu để mình học thêm.")
    def mock_stream():
        yield _t_rag(_chitchat_vi, response_language)

    # P1 (L1): route "meta" (nang luc / huong dan / ngoai pham vi) tra loi bang
    # template DONG theo RBAC, BO QUA retrieval RAG.
    if (not is_chitchat) and _route_result.route in _interaction_router.META_ROUTES:
        from mech_chatbot.rag import route_responses as _route_responses
        _meta_text = _route_responses.build_meta_response(
            _route_result.route, response_language, user_department, allowed_departments)
        if _meta_text:
            def meta_stream():
                yield _meta_text
            logger.info("Route meta -> tra template, bo qua retrieval.")
            log_trace("rag_end", trace_id, final_latency_ms=int((time.time() - t_start)*1000), refusal=(_route_result.route == _interaction_router.ROUTE_OUT_OF_SCOPE), is_chitchat=False, route=_route_result.route)
            return meta_stream(), "", [], current_part_ids, make_debug_info([])

    if is_chitchat:
        logger.info("Cau hoi la giao tiep co ban, bo qua truy xuat DB.")
        log_trace("rag_end", trace_id, final_latency_ms=int((time.time() - t_start)*1000), refusal=False, is_chitchat=True)
        return mock_stream(), "", [], current_part_ids, make_debug_info([])
    else:
        logger.info("Dang phan tich intent de tim kiem du lieu...")
        t_intent = time.time()

        # P2-9: Semantic cache LOOKUP (best-effort). Hit -> tra ngay, bo qua retrieval + LLM.
        try:
            import mech_chatbot.rag.semantic_cache as _sc
            if _sc.enabled():
                _sc_qemb = _embed_cached(user_question)
                _sc_scope = _sc.scope_signature(user_department, allowed_departments, max_security_level, allowed_sites, user_roles)
                _hit = _sc.lookup(user_question, _sc_qemb, _sc_scope)
                if _hit:
                    logger.info("Semantic cache HIT -> tra loi tu cache.")
                    _dbg = make_debug_info([])
                    _dbg["cache_hit"] = True
                    def _cached_stream():
                        yield _hit.get("answer", "")
                    log_trace("rag_end", trace_id, final_latency_ms=int((time.time() - t_start) * 1000), refusal=False, cache_hit=True)
                    return _cached_stream(), _hit.get("ref_text", ""), _hit.get("ref_images", []), current_part_ids, _dbg
        except Exception as _sce:
            logger.warning(f"semantic cache lookup loi: {_sce}")

        # === BUOC B0 (P0-1): PHAN DOAN NGU CANH + QUERY REWRITING ===
        # Tu dong quyet dinh GIU / CLEAR State Memory (thay vi phu thuoc nut "Xoa ngu canh")
        # va viet lai cau hoi noi tiep thanh cau doc lap TRUOC khi retrieve.
        effective_question = user_question
        effective_part_ids = current_part_ids
        # Doc active_doc_refs SOM de (a) cho analyze_context biet tai lieu dang trao
        # doi, (b) quyet dinh neo lai theo phan doan cua LLM.
        _cc_in = conversation_context or {}
        _active_doc_refs_in = _cc_in.get("active_doc_refs") if _cc_in else None
        t_ctx = time.time()
        ctx_result = analyze_context(user_question, chat_history, current_part_ids, active_doc_refs=_active_doc_refs_in)
        context_action = ctx_result["context_action"]
        _ctx_llm_resolved = bool(ctx_result.get("llm_resolved"))
        if context_action in ("switch_topic", "broaden"):
            effective_part_ids = []  # Tu dong reset ngu canh khi doi chu de / hoi tong quat
        if ctx_result.get("standalone_question"):
            effective_question = ctx_result["standalone_question"]
        if effective_question != user_question or effective_part_ids != current_part_ids:
            logger.info(
                f"[Context] action={context_action} | goc={user_question} -> "
                f"rewrite={effective_question} | part_ids {current_part_ids}->{effective_part_ids}"
            )
        log_trace("context_analysis", trace_id,
                  latency_ms=int((time.time() - t_ctx) * 1000),
                  context_action=context_action,
                  rewritten=(effective_question != user_question),
                  original_question=user_question[:300],
                  standalone_question=effective_question[:300],
                  part_ids_before=current_part_ids,
                  part_ids_after=effective_part_ids)

        # === Tang B moi (ConvState): DST tat dinh - chon tu bang ung vien ===
        _forced_sel = False
        try:
            from mech_chatbot.rag import conversation_state as _cs
            _cc_in = conversation_context or {}
            _cs_pending = _cc_in.get("pending_candidates") if _cc_in else None
            if _cs_pending and _cs.is_enabled():
                _sel_res = _cs.resolve_selection(user_question, _cs_pending)
                if _sel_res.get("matched"):
                    _cand = _sel_res["candidate"] or {}
                    _code = str(_cand.get("base_code") or "").strip()
                    if _code:
                        effective_part_ids = [_code]
                        _forced_sel = True
                    else:
                        effective_part_ids = []
                        _desc = _cs.describe_candidate(_cand)
                        if _desc:
                            effective_question = _desc
                    logger.info(f"[ConvState] Chon ung vien {_cand.get('key')} qua {_sel_res.get('match_type')} -> part_ids={effective_part_ids}, forced={_forced_sel}")
            # KH-2 (sua V4) + NANG NEO: neo lai tai lieu dang hoi khi cau tiep dien
            # khong kem ma moi. Quyet dinh dua tren 2 tin hieu: (1) heuristic tu vung
            # is_continuation, HOAC (2) LLM phan doan context_action == "continue".
            # TUYET DOI khong neo khi LLM bao switch_topic / broaden.
            _llm_says_continue = (_ctx_llm_resolved and context_action == "continue")
            _should_anchor = (
                context_action not in ("switch_topic", "broaden")
                and (_cs.is_continuation(user_question) or _llm_says_continue)
            )
            if (not _forced_sel and _cs.is_enabled() and not effective_part_ids
                    and not _cs.has_explicit_code(user_question)
                    and _should_anchor):
                _adr = _active_doc_refs_in
                if _adr:
                    effective_part_ids = list(_adr)
                    _forced_sel = True
                    logger.info(f"[ConvState] Neo lai tai lieu {effective_part_ids} (is_cont={_cs.is_continuation(user_question)}, llm_continue={_llm_says_continue})")
        except Exception as _cse:
            logger.warning(f"[ConvState] resolve_selection loi: {_cse}")
        rbac_filter = create_rbac_filter(user_department, user_roles, allowed_departments, max_security_level=max_security_level, allowed_sites=allowed_sites)
        strict_filter, broad_filter, new_part_ids, is_inherited, is_bom_query, intent_data = extract_search_intent(
            effective_question, effective_part_ids, user_department, user_roles, allowed_departments, max_security_level, allowed_sites=allowed_sites, force_part_ids=_forced_sel
        )
        
        log_trace("intent", trace_id, 
                  latency_ms=int((time.time() - t_intent)*1000),
                  part_ids=new_part_ids,
                  is_inherited=is_inherited,
                  is_bom_query=is_bom_query,
                  version_policy=intent_data.get("version_policy", "current_only"))
 
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
 
            # HyDE (Hypothetical Document Embeddings) Trigger
            try:
                _skip_hyde_anchor = bool(_forced_sel) or (bool(_active_doc_refs_in) and _cs.is_continuation(user_question))
            except Exception:
                _skip_hyde_anchor = False
            if len(tokenized_question.split()) < 25 and not new_part_ids and not _skip_hyde_anchor:
                logger.info("Cau hoi ngan VA khong co ma ban ve, kich hoat HyDE de mo rong ngu canh...")
                try:
                    hyde_prompt = f"Viet mot doan van ban ngan gon (1-2 cau) tra loi cho cau hoi sau dua tren tai lieu noi bo: '{effective_question}'"
                    t_hyde = time.time()
                    hyde_response = cohere_invoke([HumanMessage(content=hyde_prompt)]).content
                    query_to_search = tokenize_cached(hyde_response)
                    log_trace("hyde", trace_id, latency_ms=int((time.time() - t_hyde)*1000), used=True, hyde_chars=len(hyde_response))
                except Exception as e:
                    logger.warning(f"Loi HyDE: {e}")
                    log_trace("hyde", trace_id, used=True, error=str(e))
 
            # P0-3: mo rong truy van bang glossary/synonym theo domain (tang recall cho phong phi co khi)
            try:
                _gloss_add = glossary_expansion_terms(effective_question, user_department)
                if _gloss_add:
                    query_to_search = str(query_to_search) + " " + tokenize_cached(_gloss_add)
                    log_trace("glossary_expansion", trace_id, added=_gloss_add[:200])
            except Exception as _ge:
                logger.warning(f"glossary expansion loi: {_ge}")

            t_retrieval = time.time()
            retrieval_mode = "unknown"
            if new_part_ids:
                base_k = 15 * len(new_part_ids)
                
                logger.info(f"Dang truy xuat CHINH XAC (strict) cho ma chinh: {new_part_ids} (k={base_k})...")
                retrieval_mode = "strict_exact"
                try:
                    retriever_strict = vectorstore.as_retriever(
                        search_type="similarity",
                        search_kwargs={"k": base_k, "filter": strict_filter}
                    )
                    active_filter = strict_filter
                    strict_docs = retriever_strict.invoke(query_to_search)
                except Exception as e:
                    logger.warning(f"Strict retrieval that bai: {e}")
                    strict_docs = []
                
                if strict_docs and not is_bom_query:
                    logger.info("Tim thay ket qua strict, khong lay them du lieu rong de tranh nhieu.")
                    retrieved_docs = strict_docs
                else:
                    logger.info(f"Khong co ket qua strict hoac hoi BOM, mo rong truy xuat (broad) cho cac ma: {new_part_ids}...")
                    retrieval_mode = "broad_fallback"
                    try:
                        retriever_broad = vectorstore.as_retriever(
                            search_type="similarity",
                            search_kwargs={"k": base_k * 2, "filter": broad_filter}
                        )
                        active_filter = broad_filter
                        broad_docs = retriever_broad.invoke(query_to_search)
                        
                        # Merge if bom query, otherwise just use broad
                        if strict_docs:
                            existing_docs = strict_docs
                            merged_docs = []
                            seen = set()
                            for doc in existing_docs + broad_docs:
                                key = doc.page_content[:200]
                                if key not in seen:
                                    seen.add(key)
                                    merged_docs.append(doc)
                            retrieved_docs = merged_docs
                        else:
                            retrieved_docs = broad_docs
                    except Exception as e:
                        logger.warning(f"Broad retrieval that bai: {e}")
                        retrieved_docs = strict_docs
            else:
                # Tim kiem chung neu khong co ma
                try:
                    from mech_chatbot.db.repository import get_app_setting_int
                    base_k = get_app_setting_int("rag_general_top_k", 30)
                except Exception:
                    base_k = 30
                if not base_k or base_k < 1:
                    base_k = 30
                retrieval_mode = "general"
                logger.info(f"Khong co ma cu tinh, dang tim kiem tren toan bo Database (Pure Hybrid Search) k={base_k}...")
                
                general_filter = current_published_filter(rbac_filter)
                retriever = vectorstore.as_retriever(
                    search_type="similarity",
                    search_kwargs={"k": base_k, "filter": general_filter}
                )
                active_filter = general_filter
                retrieved_docs = retriever.invoke(query_to_search)
 
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
            _codes_str = ', '.join(new_part_ids)
            if _normalize_lang(response_language) == "en":
                _no_code_msg = f"Sorry, I couldn't find the code '{_codes_str}' in the current drawing system. Please double-check the code or provide more details."
            else:
                _no_code_msg = f"Rất tiếc, mình không tìm thấy mã số '{_codes_str}' nào trong hệ thống bản vẽ hiện tại. Vui lòng kiểm tra lại mã hoặc mô tả rõ hơn."
            def insufficient_evidence_stream():
                yield _no_code_msg
            log_trace("rag_end", trace_id, final_latency_ms=int((time.time() - t_start)*1000), refusal=True, reason="no_docs_for_exact_code")
            return insufficient_evidence_stream(), "", [], current_part_ids, make_debug_info([])

    if not skip_retrieval:
        # Rule 3 (NANG CAP): khi co nhieu variant/base_code, KHONG voi tu choi.
        # Truoc tien thu DISAMBIGUATE bang rang buoc trong cau hoi (ten san pham,
        # vat lieu, kich thuoc). Chi hoi lai khi that su khong tach duoc.
        from mech_chatbot.rag.text_utils import remove_accents as _ra
        _qn_all = _ra(user_question.lower())
        _all_kw = [
            "cac model", "tat ca model", "tat ca cac model", "moi model",
            "tung model", "cac variant", "tat ca variant", "so sanh",
        ]
        _wants_all = (
            ("intent_data" in locals() and intent_data.get("version_policy") in ["compare_versions", "all_current_variants"])
            or any(k in _qn_all for k in _all_kw)
        )

        if retrieved_docs and not _wants_all:
            # Tap hop cac "ho" tai lieu khac nhau (base_code + variant_code)
            distinct_families = set()
            unique_variants = set()
            for doc in retrieved_docs:
                _md = doc.metadata or {}
                _bc = (_md.get("base_code") or "").strip()
                _vc = (_md.get("variant_code") or "default").strip()
                distinct_families.add((_bc, _vc))
                if _vc and _vc != "default":
                    unique_variants.add(_vc)

            # Kich hoat resolver khi:
            #  - co nhieu variant (nhu logic cu), HOAC
            #  - cau hoi KHONG co ma nhung mo ta san pham va co nhieu ho tai lieu.
            _constraints = extract_no_code_constraints(user_question)
            # Gop them rang buoc do LLM intent trich (product_names/materials/dimensions/models)
            if "intent_data" in locals():
                from mech_chatbot.rag.entity_resolver import _norm_text as _nt, _norm_dim as _nd
                for _nm in (intent_data.get("product_names") or []):
                    _v = _nt(_nm)
                    if _v and _v not in _constraints["quoted_names"]:
                        _constraints["quoted_names"].append(_v)
                for _mt in (intent_data.get("materials") or []):
                    _v = _nt(_mt)
                    if _v and _v not in _constraints["materials"]:
                        _constraints["materials"].append(_v)
                for _dm in (intent_data.get("dimensions") or []):
                    _v = _nd(_dm)
                    if _v and _v not in _constraints["dimensions"]:
                        _constraints["dimensions"].append(_v)
            _has_constraints = any(_constraints.values())
            # KH-4: chi bung bang khi co tin hieu MANH (kich thuoc/vat lieu/ten trong ngoac)
            # hoac nhieu variant cung base_code. Free-term chung chung KHONG bung bang nua.
            _strong_constraints = bool(
                _constraints.get("dimensions") or _constraints.get("materials") or _constraints.get("quoted_names")
            )
            _need_disambig = (len(unique_variants) > 1) or (
                not new_part_ids and _strong_constraints and len(distinct_families) > 1
            )

            if _need_disambig:
                resolution = resolve_candidates_from_docs(retrieved_docs, _constraints)
                if resolution["decision"] == "single":
                    _sel = resolution["selected"]
                    logger.info(f"Disambiguation: chot 1 candidate {_sel.get('key')} tu rang buoc {_constraints}.")
                    retrieved_docs = resolution["selected_docs"] or retrieved_docs
                elif resolution["decision"] == "ambiguous":
                    logger.info(f"Nhieu candidate sau disambiguation: {[c.get('key') for c in resolution['candidates']]}.")
                    _table_md = build_candidate_table_markdown(resolution["candidates"])
                    def variant_ambiguity_stream():
                        _header_vi = ("Mình tìm thấy nhiều tài liệu có thể khớp với mô tả của bạn. "
                                      "Bạn muốn tra theo tài liệu nào dưới đây?")
                        _footer_vi = ("Bạn có thể trả lời bằng mã/model ở cột đầu, hoặc yêu cầu 'so sánh các model' "
                                      "để mình lập bảng đối chiếu.")
                        yield (
                            _t_rag(_header_vi, response_language) + "\n\n"
                            + _table_md
                            + "\n\n" + _t_rag(_footer_vi, response_language)
                        )
                    log_trace("rag_end", trace_id, final_latency_ms=int((time.time() - t_start)*1000), refusal=True, reason="multiple_candidates_need_choice")
                    _dbg_amb = make_debug_info([])
                    try:
                        from mech_chatbot.rag import conversation_state as _cs2
                        if _cs2.is_enabled():
                            _dbg_amb["conversation_context"] = {"pending_candidates": _cs2.public_candidates(resolution["candidates"]), "last_intent": "await_selection"}
                    except Exception as _e_cs:
                        logger.warning(f"[ConvState] luu pending loi: {_e_cs}")
                    return variant_ambiguity_stream(), "", [], current_part_ids, _dbg_amb
                elif resolution["decision"] == "insufficient":
                    # Co mo ta nhung khong tai lieu nao khop du chac -> xin them thong tin.
                    logger.info(f"Khong resolve duoc candidate du chac voi rang buoc {_constraints}.")
                    def insufficient_candidate_stream():
                        _insuf_vi = ("Mình chưa xác định chắc chắn được tài liệu/bản vẽ cần tra theo mô tả của bạn. "
                                     "Bạn vui lòng cung cấp thêm mã bản vẽ, model, tên sản phẩm, kích thước hoặc "
                                     "vật liệu cụ thể hơn nhé.")
                        yield _t_rag(_insuf_vi, response_language)
                    log_trace("rag_end", trace_id, final_latency_ms=int((time.time() - t_start)*1000), refusal=True, reason="no_confident_candidate")
                    return insufficient_candidate_stream(), "", [], current_part_ids, make_debug_info([])
                # decision == "pass": de nguyen, tra loi binh thuong

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
            )
            if bom_results:
                bom_text = "Dữ liệu cấu trúc Bảng Kê Vật Tư (BOM) từ SQL Database (Rất chính xác):\n"
                for row in bom_results:
                    ma, ten, vat_lieu, sl, gc, file, version_no = row
                    bom_text += f"- Mã: {ma}, Tên: {ten}, Vật liệu: {vat_lieu}, SL: {sl}, Ghi chú: {gc} (Nguồn: {file}, Version: {version_no})\n"
                
                bom_doc = Document(
                    page_content=bom_text,
                    metadata={
                        "file_goc": "SQL_Database_BOM",
                        "loai_du_lieu": "sql_bom",
                        "doc_status": "published"
                    }
                )
                retrieved_docs.insert(0, bom_doc)
                logger.info(f"Da them {len(bom_results)} dong BOM tu SQL vao context.")
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
            _blocked, _needed_lvl = probe_restricted_access(
                query_to_search, user_department=user_department,
                allowed_departments=allowed_departments,
                max_security_level=max_security_level, allowed_sites=allowed_sites)
        except Exception:
            _blocked, _needed_lvl = False, None
        if _blocked and _needed_lvl:
            _lvl_vi = {"internal": "noi bo (internal)", "confidential": "mat (confidential)"}.get(_needed_lvl, _needed_lvl)
            _stub_vi = (
                "Co tai lieu lien quan den cau hoi cua ban, nhung o muc " + _lvl_vi +
                " ma tai khoan cua ban chua du quyen xem. Noi dung duoc bao mat theo phan quyen.\n\n"
                "Ban co the vao trang 'Yeu cau quyen' de gui yeu cau cap quyen; quan tri / phu trach phong ban se duyet."
            )
            _stub_en = (
                "There are documents related to your question, but they are classified '" + str(_needed_lvl) +
                "' and your account is not cleared to view them. The content is protected by access control.\n\n"
                "You can open the 'Access requests' page to request access; an admin / department owner will review it."
            )
            _stub_msg = _stub_en if _normalize_lang(response_language) == "en" else _stub_vi
            def restricted_stream():
                yield _stub_msg
            _dbg = make_debug_info([])
            _dbg["access_hint"] = {"restricted": True, "needed_level": _needed_lvl, "question": user_question}
            log_trace("rag_end", trace_id, final_latency_ms=int((time.time() - t_start)*1000), refusal=True, refusal_reason="restricted_by_clearance", needed_level=_needed_lvl)
            return restricted_stream(), "", [], current_part_ids, _dbg

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
 
    # BUOC B2: CROSS-ENCODER RE-RANK & REORDER (CHONG LOST IN THE MIDDLE)
    if retrieved_docs:
        # Tach fake_doc (anh nguoi dung upload) ra khoi qua trinh rerank
        fake_docs = [d for d in retrieved_docs if d.metadata.get("loai_du_lieu") == "image_summary" and d.metadata.get("file_goc") == "Anh dinh kem tu nguoi dung"]
        real_docs = [d for d in retrieved_docs if d not in fake_docs]
 
        if real_docs and use_gpt_rerank():
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
                logger.info(f"Dang su dung GPT-5.4 Rerank de filter {len(real_docs)} tai lieu (top_n={top_n})...")
                t_rerank = time.time()
                compressed_docs = cohere_rerank(None, real_docs, user_question, top_n=top_n)
                
                # LOP PHONG THU 1: Score Cutoff
                # Chi lay cac tai lieu co relevance_score >= RERANK_SCORE_CUTOFF (da duoc calibrated boi Cohere)
                filtered_docs = [doc for doc in compressed_docs if doc.metadata.get("relevance_score", 1.0) >= RERANK_SCORE_CUTOFF]
                
                if not filtered_docs and compressed_docs:
                    logger.info("Tat ca tai lieu deu duoi nguong relevance_score. Fallback giu lai top 3 tai lieu thay vi xoa sach.")
                    real_docs = compressed_docs[:3]
                else:
                    real_docs = filtered_docs
                
                scores = [{"file": d.metadata.get("file_goc"), "page": d.metadata.get("trang_so"), "score": d.metadata.get("relevance_score", 1.0)} for d in real_docs[:5]]
                log_trace("rerank", trace_id, latency_ms=int((time.time() - t_rerank)*1000), input_docs=len(retrieved_docs), output_docs=len(real_docs), scores=scores)
            except Exception as e:
                logger.error(f"Loi khi su dung GPT-5.4 Rerank: {e}. Fallback to manual rerank.")
                real_docs = rerank_docs(real_docs)
                log_trace("rerank", trace_id, error=str(e))
        else:
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

        retrieved_docs = fake_docs + real_docs

        retrieved_docs = long_context_reorder(retrieved_docs)

    # BUOC C: SINH CAU TRA LOI (STREAMING)
    context_text = format_docs(retrieved_docs)
    # P1.2: chen metadata tong quat (phong ban / hieu luc) tu CSDL
    common_meta_context = build_common_metadata_context(retrieved_docs)
    if common_meta_context:
        context_text = common_meta_context + (chr(10) + chr(10)) + context_text
    structured_context = build_structured_attributes_context(retrieved_docs)
    if structured_context:
        context_text = structured_context + "\n\n" + context_text
    # P3-4: chen Golden Answer (cau tra loi da duyet) lam context uu tien cao nhat
    try:
        from mech_chatbot.db.repository import find_golden_answer
        _golden = find_golden_answer(user_question)
    except Exception as _e:
        logger.error(f"Loi tra cuu Golden Answer: {_e}")
        _golden = None
    if _golden and _golden.get("answer"):
        _g_src = _golden.get("source_doc_id")
        _gp = ["[GOLDEN ANSWER - CHUYEN GIA DA DUYET - UU TIEN CAO NHAT]", str(_golden.get("answer")).strip()]
        if _g_src:
            _gp.append("(Nguon da duyet: DocID %s)" % _g_src)
        _gp.append("[HET GOLDEN ANSWER]")
        context_text = chr(10).join(_gp) + chr(10) + chr(10) + context_text
        logger.info("Da chen Golden Answer vao context (uu tien cao nhat).")
    logger.info(f"Da tim thay {len(retrieved_docs)} tai lieu lien quan. Dang phan tich...")

    # Tao trich dan truoc de neu evidence gate tu choi van co the hien thi tai lieu da tim thay
    ref_text, ref_images = build_source_citations(retrieved_docs)
    _conf_docs = [d.metadata.get("file_goc") for d in retrieved_docs if d.metadata.get("security_level") == "confidential"]
    if _conf_docs:
        logger.warning(f"[audit][confidential] dept={user_department} roles={user_roles} truy cap tai lieu mat: {_conf_docs}")

    # LOP PHONG THU 2: Evidence Gate cho cau hoi bay / cau hoi can so lieu
    t_gate = time.time()
    answerable, evidence_reason, evidence_quotes = verify_answerability(user_question, context_text)
    log_trace("evidence_gate", trace_id, latency_ms=int((time.time() - t_gate)*1000), answerable=answerable, reason=evidence_reason)
    
    if not answerable:
        logger.warning(f"Evidence gate BLOCK cau hoi: {evidence_reason}")
        safe_msg = make_insufficient_evidence_message(user_question, evidence_reason, lang=response_language)
        def refusal_stream():
            yield safe_msg
        log_trace("rag_end", trace_id, final_latency_ms=int((time.time() - t_start)*1000), refusal=True, refusal_reason="evidence_gate", docs_count=len(retrieved_docs), doc_ids=[d.metadata.get("doc_id") for d in retrieved_docs], retrieved_file_goc=[d.metadata.get("file_goc") for d in retrieved_docs], version_no=[d.metadata.get("version_no") for d in retrieved_docs], variant_code=[d.metadata.get("variant_code") for d in retrieved_docs], is_current=[d.metadata.get("is_current") for d in retrieved_docs], lifecycle_status=[d.metadata.get("lifecycle_status") for d in retrieved_docs], review_status=[d.metadata.get("review_status") for d in retrieved_docs], version_policy=intent_data.get("version_policy") if "intent_data" in locals() else None, filter_used=serialize_qdrant_filter(active_filter) if "active_filter" in locals() else None, top_k=base_k if "base_k" in locals() else None, retrieval_mode=retrieval_mode, retrieval_scores=[d.metadata.get("relevance_score") for d in retrieved_docs], user_department=user_department, user_roles=user_roles)
        return refusal_stream(), ref_text, ref_images, new_part_ids, make_debug_info(retrieved_docs)

    # GD3: chon prompt + gate guard co khi theo ngu canh truy hoi
    _ctx_is_mech = _context_is_mechanical(retrieved_docs, new_part_ids)
    _ctx_domain = _context_domain(retrieved_docs, new_part_ids)
    chain = _build_prompt_template(_ctx_domain, response_language) | llm | StrOutputParser()

    stream_input = {
        "context": context_text,
        # P0-B: dung cau da decontextualize (effective_question) neu co; fallback cau goc.
        "question": (effective_question if ("effective_question" in locals() and effective_question) else user_question),
        "chat_history_str": chat_history_str
    }

    if STRICT_ANSWER_MODE or is_high_risk_question(user_question):
        # LOP PHONG THU 3: Post-check so lieu. Voi cau hoi rui ro, tam hoan streaming de kiem tra
        # LLM co tu tao so lieu moi (vd 24 gio) khong co trong context/user question hay khong.
        def guarded_stream():
            t_llm = time.time()
            chunks = []
            has_error = False
            error_msg = ""
            try:
                for chunk in chain.stream(stream_input):
                    chunks.append(chunk)
                answer = "".join(chunks)
                
                input_tokens = len(context_text + user_question + chat_history_str) // 4
                output_tokens = len(answer) // 4
                estimated_cost = (input_tokens * 2.5 + output_tokens * 15.0) / 1000000
                doc_ids = [d.metadata.get("doc_id") for d in retrieved_docs]
                retrieval_scores = [d.metadata.get("relevance_score") for d in retrieved_docs]
                
                if _ctx_is_mech:
                    bad_mats, unsupported_mats = has_unsupported_materials(answer, context_text)
                    bad_codes, unsupported_codes = has_unsupported_codes(answer, context_text, user_question)
                    bad_units, unsupported_units = has_unsupported_units_symbols(answer, context_text, user_question)
                else:
                    # Ngu canh phi co khi: bo qua guard vat lieu/ma/don vi ky thuat
                    bad_mats, unsupported_mats = False, []
                    bad_codes, unsupported_codes = False, []
                    bad_units, unsupported_units = False, []
                
                if bad_mats or bad_codes:
                    ans = make_insufficient_evidence_message(
                        user_question,
                        f"Câu trả lời chứa thông tin tự tạo không có trong nguồn: materials={unsupported_mats}, codes={unsupported_codes}",
                        lang=response_language,
                    )
                    yield ans
                    log_trace("llm_generation", trace_id, model=get_llm_model_name(), latency_ms=int((time.time() - t_llm)*1000), answer_chars=len(ans), blocked_by_post_check=True, input_tokens=input_tokens, output_tokens=output_tokens, estimated_cost=estimated_cost)
                    log_trace("rag_end", trace_id, final_latency_ms=int((time.time() - t_start)*1000), refusal=True, refusal_reason="post_check_materials_codes", docs_count=len(retrieved_docs), doc_ids=doc_ids, retrieved_file_goc=[d.metadata.get("file_goc") for d in retrieved_docs], version_no=[d.metadata.get("version_no") for d in retrieved_docs], variant_code=[d.metadata.get("variant_code") for d in retrieved_docs], is_current=[d.metadata.get("is_current") for d in retrieved_docs], lifecycle_status=[d.metadata.get("lifecycle_status") for d in retrieved_docs], review_status=[d.metadata.get("review_status") for d in retrieved_docs], version_policy=intent_data.get("version_policy") if "intent_data" in locals() else None, filter_used=serialize_qdrant_filter(active_filter) if "active_filter" in locals() else None, top_k=base_k if "base_k" in locals() else None, retrieval_mode=retrieval_mode, retrieval_scores=retrieval_scores, user_department=user_department, user_roles=user_roles)
                elif bad_units:
                    ans = make_insufficient_evidence_message(
                        user_question,
                        f"Câu trả lời chứa đơn vị/ký hiệu kỹ thuật không có trong nguồn: {unsupported_units}",
                        lang=response_language,
                    )
                    yield ans
                    log_trace("llm_generation", trace_id, model=get_llm_model_name(), latency_ms=int((time.time() - t_llm)*1000), answer_chars=len(ans), blocked_by_post_check=True, input_tokens=input_tokens, output_tokens=output_tokens, estimated_cost=estimated_cost)
                    log_trace("rag_end", trace_id, final_latency_ms=int((time.time() - t_start)*1000), refusal=True, refusal_reason="post_check_units", docs_count=len(retrieved_docs), doc_ids=doc_ids, retrieved_file_goc=[d.metadata.get("file_goc") for d in retrieved_docs], version_no=[d.metadata.get("version_no") for d in retrieved_docs], variant_code=[d.metadata.get("variant_code") for d in retrieved_docs], is_current=[d.metadata.get("is_current") for d in retrieved_docs], lifecycle_status=[d.metadata.get("lifecycle_status") for d in retrieved_docs], review_status=[d.metadata.get("review_status") for d in retrieved_docs], version_policy=intent_data.get("version_policy") if "intent_data" in locals() else None, filter_used=serialize_qdrant_filter(active_filter) if "active_filter" in locals() else None, top_k=base_k if "base_k" in locals() else None, retrieval_mode=retrieval_mode, retrieval_scores=retrieval_scores, user_department=user_department, user_roles=user_roles)
                elif has_unsupported_numbers(answer, context_text, user_question, strict_mode=STRICT_ANSWER_MODE):
                    ans = make_insufficient_evidence_message(
                        user_question,
                        "cau tra loi sinh ra co so lieu khong truy vet duoc trong tai lieu",
                        lang=response_language,
                    )
                    yield ans
                    log_trace("llm_generation", trace_id, model=get_llm_model_name(), latency_ms=int((time.time() - t_llm)*1000), answer_chars=len(ans), blocked_by_post_check=True, input_tokens=input_tokens, output_tokens=output_tokens, estimated_cost=estimated_cost)
                    log_trace("rag_end", trace_id, final_latency_ms=int((time.time() - t_start)*1000), refusal=True, refusal_reason="post_check_numbers", docs_count=len(retrieved_docs), doc_ids=doc_ids, retrieved_file_goc=[d.metadata.get("file_goc") for d in retrieved_docs], version_no=[d.metadata.get("version_no") for d in retrieved_docs], variant_code=[d.metadata.get("variant_code") for d in retrieved_docs], is_current=[d.metadata.get("is_current") for d in retrieved_docs], lifecycle_status=[d.metadata.get("lifecycle_status") for d in retrieved_docs], review_status=[d.metadata.get("review_status") for d in retrieved_docs], version_policy=intent_data.get("version_policy") if "intent_data" in locals() else None, filter_used=serialize_qdrant_filter(active_filter) if "active_filter" in locals() else None, top_k=base_k if "base_k" in locals() else None, retrieval_mode=retrieval_mode, retrieval_scores=retrieval_scores, user_department=user_department, user_roles=user_roles)
                elif (
                    STRICT_ANSWER_MODE
                    and requires_source_citation(user_question)
                    and not has_required_source_citation(answer, require_version=True)
                ):
                    ans = make_insufficient_evidence_message(
                        user_question,
                        "câu trả lời không có đủ nguồn file/trang/version rõ ràng",
                        lang=response_language,
                    )
                    yield ans
                    log_trace(
                        "rag_end",
                        trace_id,
                        final_latency_ms=int((time.time() - t_start) * 1000),
                        refusal=True,
                        refusal_reason="missing_source_page_version"
                    )
                    return
                else:
                    yield answer
                    log_trace("llm_generation", trace_id, model=get_llm_model_name(), latency_ms=int((time.time() - t_llm)*1000), answer_chars=len(answer), input_tokens=input_tokens, output_tokens=output_tokens, estimated_cost=estimated_cost)
                    log_trace("rag_end", trace_id, final_latency_ms=int((time.time() - t_start)*1000), refusal=False, docs_count=len(retrieved_docs), doc_ids=doc_ids, retrieved_file_goc=[d.metadata.get("file_goc") for d in retrieved_docs], version_no=[d.metadata.get("version_no") for d in retrieved_docs], variant_code=[d.metadata.get("variant_code") for d in retrieved_docs], is_current=[d.metadata.get("is_current") for d in retrieved_docs], lifecycle_status=[d.metadata.get("lifecycle_status") for d in retrieved_docs], review_status=[d.metadata.get("review_status") for d in retrieved_docs], version_policy=intent_data.get("version_policy") if "intent_data" in locals() else None, filter_used=serialize_qdrant_filter(active_filter) if "active_filter" in locals() else None, top_k=base_k if "base_k" in locals() else None, retrieval_mode=retrieval_mode, retrieval_scores=retrieval_scores, user_department=user_department, user_roles=user_roles)
            except Exception as e:
                has_error = True
                error_msg = str(e)
                logger.error(f"Loi LLM guarded stream: {e}", exc_info=True)
                raise e
            finally:
                if has_error:
                    log_trace("rag_error", trace_id, error=error_msg, stage="llm_generation")
                    log_trace("rag_end", trace_id, final_latency_ms=int((time.time() - t_start)*1000), refusal=True, refusal_reason="llm_error", has_error=True)

        stream = guarded_stream()
    else:
        def normal_stream():
            t_llm = time.time()
            chunks = []
            has_error = False
            error_msg = ""
            try:
                for chunk in chain.stream(stream_input):
                    chunks.append(chunk)
                    yield chunk
            except Exception as e:
                has_error = True
                error_msg = str(e)
                logger.error(f"Loi LLM stream: {e}", exc_info=True)
                raise e
            finally:
                if has_error:
                    log_trace("rag_error", trace_id, error=error_msg, stage="llm_generation")
                    log_trace("rag_end", trace_id, final_latency_ms=int((time.time() - t_start)*1000), refusal=True, refusal_reason="llm_error", has_error=True)
                else:
                    answer = "".join(chunks)
                    input_tokens = len(context_text + user_question + chat_history_str) // 4
                    output_tokens = len(answer) // 4
                    estimated_cost = (input_tokens * 2.5 + output_tokens * 15.0) / 1000000
                    doc_ids = [d.metadata.get("doc_id") for d in retrieved_docs]
                    retrieval_scores = [d.metadata.get("relevance_score") for d in retrieved_docs]
                    
                    retrieved_file_goc = [d.metadata.get("file_goc") for d in retrieved_docs]
                    version_no = [d.metadata.get("version_no") for d in retrieved_docs]
                    variant_code = [d.metadata.get("variant_code") for d in retrieved_docs]
                    is_current = [d.metadata.get("is_current") for d in retrieved_docs]
                    lifecycle_status = [d.metadata.get("lifecycle_status") for d in retrieved_docs]
                    
                    log_trace("llm_generation", trace_id, model=get_llm_model_name(), latency_ms=int((time.time() - t_llm)*1000), answer_chars=len(answer), input_tokens=input_tokens, output_tokens=output_tokens, estimated_cost=estimated_cost)
                    log_trace("rag_end", trace_id, final_latency_ms=int((time.time() - t_start)*1000), refusal=False, docs_count=len(retrieved_docs), doc_ids=doc_ids, retrieved_file_goc=retrieved_file_goc, version_no=version_no, variant_code=variant_code, is_current=is_current, lifecycle_status=lifecycle_status, review_status=[d.metadata.get("review_status") for d in retrieved_docs], version_policy=intent_data.get("version_policy") if "intent_data" in locals() else None, filter_used=serialize_qdrant_filter(active_filter) if "active_filter" in locals() else None, top_k=base_k if "base_k" in locals() else None, retrieval_mode=retrieval_mode, retrieval_scores=retrieval_scores, user_department=user_department, user_roles=user_roles)
        stream = normal_stream()

    # BUOC D: TU DONG TAO TRICH DAN NGUON VA HINH ANH (Tra ve cung stream)
    debug_info = make_debug_info(retrieved_docs)
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
            )
    except Exception as _sce2:
        logger.warning(f"semantic cache store loi: {_sce2}")
    return stream, ref_text, ref_images, new_part_ids, debug_info


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
    'build_source_citations',
]
