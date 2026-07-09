# -*- coding: utf-8 -*-
"""P0 refactor (giu nguyen hanh vi): cac buoc tach tu chat_with_rag de giam do phuc tap.
Moi ham la mot lat cat mechanical extraction tu rag/pipeline.py:chat_with_rag.
KHONG doi logic — chi di chuyen nguyen van + truyen state qua tham so/return.
"""
import os
from mech_chatbot.config.logging import logger
from mech_chatbot.llm.llm_client import cohere_invoke
from langchain_core.messages import HumanMessage
import time
from mech_chatbot.config.logging import log_trace
from PIL import Image
from tenacity import retry, retry_if_exception, wait_exponential, stop_after_attempt
from mech_chatbot.llm.vision_client import is_retryable_error
from mech_chatbot.rag.context_builders import (
    format_docs,
    build_common_metadata_context,
    build_structured_attributes_context,
)


from langchain_core.output_parsers import StrOutputParser
from mech_chatbot.llm.llm_client import get_llm_model_name
from mech_chatbot.rag.answer_checks import (
    has_unsupported_units_symbols,
    has_unsupported_materials,
    has_unsupported_codes,
    requires_source_citation,
    has_required_source_citation,
)
from mech_chatbot.rag.evidence_gate import (
    is_high_risk_question,
    has_unsupported_numbers,
    make_insufficient_evidence_message,
)
from mech_chatbot.rag.bootstrap import llm, STRICT_ANSWER_MODE
from mech_chatbot.rag.prompt import _build_prompt_template
from mech_chatbot.rag.intent import serialize_qdrant_filter
from mech_chatbot.rag.context_builders import _context_is_mechanical, _context_domain


from mech_chatbot.rag.bootstrap import vectorstore
from mech_chatbot.rag.retrieval import current_published_filter

# P0 slices #4/#5/#7: cac helper _route / _rewrite_and_anchor / _disambiguate
from mech_chatbot.rag.prompt import _t_rag
from mech_chatbot.rag.intent import analyze_context, extract_search_intent
from mech_chatbot.rag.rbac import create_rbac_filter
from mech_chatbot.rag.entity_resolver import (
    extract_no_code_constraints,
    resolve_candidates_from_docs,
    build_candidate_table_markdown,
)

_RETRIEVE_UNSET = object()


def _prepare_history(chat_history, conversation_context, response_language):
    """BUOC lich su hoi thoai: dung chat_history_str (token-budgeted windowing)
    + tom tat luy tien (KH-3). Tra ve (chat_history_str, history_summary_new,
    summary_covered_new). Tach nguyen van tu chat_with_rag (P0 slice #1).
    """
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
    return chat_history_str, _history_summary_new, _summary_covered_new


def _analyze_image(image_path, user_question, trace_id):
    """BUOC A: phan tich anh nguoi dung tai len (Vision). Tra ve image_analysis (str).
    Tach nguyen van tu chat_with_rag (P0 slice #2).
    """
    # _VISION_MODEL doc qua module attribute (gan 1 lan luc bootstrap, khong reassign)
    from mech_chatbot.rag import bootstrap as _bootstrap
    _VISION_MODEL = _bootstrap._VISION_MODEL
    # BUOC A: XU LY ANH BANG VISION MODEL
    image_analysis = ""
    if image_path:
        t_img_start = time.time()
        logger.info("Dang dung vision model de phan tich anh tai len...")
        if _VISION_MODEL:
            try:
                img_to_analyze = Image.open(image_path)
                prompt = f"Nguoi dung tai len mot hinh anh va hoi: '{user_question}'. Hay mo ta chinh xac va chi tiet nhung gi ban thay trong anh nay de lam ngu canh tra loi. Neu do la ma code hay giao dien phan mem, hay noi ro. Tra loi bang tieng Viet."
 
                @retry(
                    retry=retry_if_exception(is_retryable_error),
                    wait=wait_exponential(multiplier=2, min=2, max=30),
                    stop=stop_after_attempt(5)
                )
                def call_vision():
                    return _VISION_MODEL.generate_content([prompt, img_to_analyze])
 
                response = call_vision()
                image_analysis = response.text
                logger.info("Phan tich anh bang vision model thanh cong.")
                
                log_trace("image_analysis", trace_id, 
                          latency_ms=int((time.time() - t_img_start)*1000),
                          success=True,
                          analysis_chars=len(image_analysis))
            except Exception as e:
                logger.error(f"Loi khi doc anh bang vision model: {e}", exc_info=True)
                log_trace("image_analysis", trace_id, 
                          latency_ms=int((time.time() - t_img_start)*1000),
                          success=False,
                          error=str(e))
        else:
            logger.warning("Chua co API key vision hop le, bo qua phan tich anh.")
            log_trace("image_analysis", trace_id, 
                      latency_ms=int((time.time() - t_img_start)*1000),
                      success=False,
                      reason="no_vision_model")
    return image_analysis


def _assemble_context(retrieved_docs, user_question):
    """BUOC C (phan lap context): format_docs + metadata chung + thuoc tinh
    co cau truc + chen Golden Answer. Tra ve context_text (str).
    Tach nguyen van tu chat_with_rag (P0 slice #3).
    """
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
    return context_text


def _generate(*, context_text, user_question, chat_history_str, retrieved_docs,
              new_part_ids, response_language, trace_id, t_start,
              user_department, user_roles, effective_question, intent_data,
              base_k, retrieval_mode, _has_active_filter=False, _active_filter=None):
    """BUOC C/D: sinh cau tra loi streaming (guarded_stream / normal_stream).
    Tra ve stream. Tach nguyen van tu chat_with_rag (P0 slice #4).
    active_filter bind co dieu kien de bao toan ngu nghia locals() nhu ban goc.
    """
    if _has_active_filter:
        active_filter = _active_filter
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
    return stream


def _retrieve(*, new_part_ids, strict_filter, broad_filter, is_bom_query,
              query_to_search, rbac_filter):
    """BUOC: truy xuat tai lieu (strict_exact / broad_fallback / general).
    Tra ve (retrieved_docs, base_k, retrieval_mode, t_retrieval, active_filter_or_sentinel).
    active_filter tra _RETRIEVE_UNSET neu chua set (duong hiem) de caller bind co dieu kien,
    bao toan ngu nghia '"active_filter" in locals()' nhu ban goc. Tach P0 slice #5.
    """
    retrieved_docs = []
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
    return retrieved_docs, base_k, retrieval_mode, t_retrieval, (active_filter if "active_filter" in locals() else _RETRIEVE_UNSET)


def _route(*, user_question, conversation_context, response_language,
           user_department, allowed_departments, current_part_ids,
           trace_id, t_start, make_debug_info):
    """P0 slice #4: dinh tuyen hoi thoai (Interaction Router L0/L1/L2 + safety_block + meta + chitchat).
    Tra ve (terminal_or_None, bundle). terminal la 5-tuple return som (safety/meta/chitchat).
    bundle chua closure dung chung (mock_stream, _embed_cached) + is_chitchat de caller dung tiep.
    Tach nguyen van tu chat_with_rag (P0 slice #4).
    """
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
        return (safety_stream(), "", [], current_part_ids, make_debug_info([])), None

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
            return (meta_stream(), "", [], current_part_ids, make_debug_info([])), None

    if is_chitchat:
        logger.info("Cau hoi la giao tiep co ban, bo qua truy xuat DB.")
        log_trace("rag_end", trace_id, final_latency_ms=int((time.time() - t_start)*1000), refusal=False, is_chitchat=True)
        return (mock_stream(), "", [], current_part_ids, make_debug_info([])), None

    return None, {"mock_stream": mock_stream, "_embed_cached": _embed_cached, "is_chitchat": is_chitchat}


def _rewrite_and_anchor(*, user_question, chat_history, current_part_ids,
                        conversation_context, user_department, user_roles,
                        allowed_departments, max_security_level, allowed_sites,
                        trace_id, t_intent):
    """P0 slice #5: phan doan ngu canh + query rewriting + neo State Memory (ConvState)
    + tao RBAC filter + trich xuat intent. Tra ve cac gia tri dieu khien luong phia sau,
    kem _skip_hyde_anchor (tinh ngay tai day de giu _cs cuc bo, bao toan hanh vi HyDE anchor).
    Tach nguyen van tu chat_with_rag (P0 slice #5).
    """
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

    # HyDE anchor guard: tinh ngay tai day (giu _cs cuc bo). Neu _cs chua bind (import loi)
    # -> NameError -> except -> False, dung nhu ban goc line inline truoc kia.
    try:
        _skip_hyde_anchor = bool(_forced_sel) or (bool(_active_doc_refs_in) and _cs.is_continuation(user_question))
    except Exception:
        _skip_hyde_anchor = False

    return (effective_question, new_part_ids, is_inherited, is_bom_query, intent_data,
            strict_filter, broad_filter, rbac_filter, _skip_hyde_anchor)


def _disambiguate(*, retrieved_docs, user_question, new_part_ids, intent_data,
                  response_language, current_part_ids, trace_id, t_start,
                  make_debug_info):
    """P0 slice #7: disambiguation (resolve candidates + bang lua chon variant + insufficient).
    Tra ve (terminal_or_None, retrieved_docs). terminal la 5-tuple return som (ambiguous/insufficient).
    'single' -> retrieved_docs duoc thu hep; 'pass'/khong disambig -> giu nguyen.
    Tach nguyen van tu chat_with_rag (P0 slice #7).
    """
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
                return (variant_ambiguity_stream(), "", [], current_part_ids, _dbg_amb), retrieved_docs
            elif resolution["decision"] == "insufficient":
                # Co mo ta nhung khong tai lieu nao khop du chac -> xin them thong tin.
                logger.info(f"Khong resolve duoc candidate du chac voi rang buoc {_constraints}.")
                def insufficient_candidate_stream():
                    _insuf_vi = ("Mình chưa xác định chắc chắn được tài liệu/bản vẽ cần tra theo mô tả của bạn. "
                                 "Bạn vui lòng cung cấp thêm mã bản vẽ, model, tên sản phẩm, kích thước hoặc "
                                 "vật liệu cụ thể hơn nhé.")
                    yield _t_rag(_insuf_vi, response_language)
                log_trace("rag_end", trace_id, final_latency_ms=int((time.time() - t_start)*1000), refusal=True, reason="no_confident_candidate")
                return (insufficient_candidate_stream(), "", [], current_part_ids, make_debug_info([])), retrieved_docs
            # decision == "pass": de nguyen, tra loi binh thuong

    return None, retrieved_docs
