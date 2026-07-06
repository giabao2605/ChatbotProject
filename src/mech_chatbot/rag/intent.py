# -*- coding: utf-8 -*-
"""Auto-split tu rag/service.py (P1.2 refactor). Giu nguyen logic goc; chi tach file + import."""

import os
import re
from mech_chatbot.config.logging import logger, log_trace
from qdrant_client import QdrantClient, models
from langchain_core.messages import HumanMessage
import json
from mech_chatbot.llm.llm_client import cohere_invoke, get_cohere_llm, _is_cohere_rate_limit, gpt_rerank_documents, get_llm_model_name
from mech_chatbot.rag.rbac import (
    compose_retrieval_filters,
    create_rbac_filter,
    _security_filter,
    _site_filter,
    _allowed_levels,
    LEVEL_ORDER,
)
import atexit
import concurrent.futures
from concurrent.futures import ThreadPoolExecutor

# cross-module (owned) refs
from mech_chatbot.rag.bootstrap import env_bool


_INTENT_MAX_WORKERS = int(os.getenv("INTENT_MAX_WORKERS", "8"))


_INTENT_TIMEOUT = float(os.getenv("INTENT_TIMEOUT", "6.0"))


_INTENT_EXECUTOR = ThreadPoolExecutor(max_workers=_INTENT_MAX_WORKERS)


atexit.register(lambda: _INTENT_EXECUTOR.shutdown(wait=False))


def serialize_qdrant_filter(f):
    try:
        if hasattr(f, "model_dump"):
            return f.model_dump()
        if hasattr(f, "dict"):
            return f.dict()
        return str(f)
    except Exception:
        return str(f)


def deterministic_version_intent(question):
    q = question.lower()

    versions = []
    for m in re.findall(r'\bv\s*(\d+)\b', q):
        versions.append(int(m))

    for m in re.findall(r'\bversion\s*(\d+)\b', q):
        versions.append(int(m))

    for m in re.findall(r'\brev\s*(\d+)\b', q):
        versions.append(int(m))

    versions = sorted(set(versions))

    compare_keywords = ["so sánh", "khác", "compare", "difference"]
    history_keywords = ["lịch sử", "history", "các version", "toàn bộ version"]
    archive_keywords = ["bản cũ", "archive", "archived", "lưu trữ", "đã thay thế"]

    q_norm = q

    if any(k in q_norm for k in compare_keywords) and len(versions) >= 2:
        return "compare_versions", versions

    if any(k in q_norm for k in history_keywords):
        return "version_history", versions

    if any(k in q_norm for k in archive_keywords):
        return "include_archived", versions

    if len(versions) == 1:
        return "specific_version", versions

    return None, versions


def extract_mechanical_codes(question):
    patterns = [
        r"\b\d+\.\d+\.\d+\b",
        r"\b[A-Z]{2,}[A-Z0-9-]*\d+[A-Z0-9-]*\b",
        r"\b\d{3}-\d{3}\b",
    ]
    codes = []
    for pattern in patterns:
        codes.extend(re.findall(pattern, question, re.IGNORECASE))
    return sorted(set(codes))


def extract_search_intent(question, current_part_ids=None, user_department=None, user_roles=None, allowed_departments=None, max_security_level=None, allowed_sites=None, force_part_ids=False):
    """Phan tich cau hoi de lay danh sach ma doi tuong va intent versioning bang LLM (co timeout)."""
    if current_part_ids is None:
        current_part_ids = []
 
    prompt_intent = f"""
    Trich xuat thong tin tim kiem tu cau hoi cua nguoi dung: '{question}'.
    Tra ve MOT JSON object duy nhat voi cac truong sau:
    1. "base_codes": Mang cac ma so ban ve/linh kien/tieu chuan (vd: ["banve-1", "9.3.03844"]). Neu cau hoi la xa giao (chao, cam on, thoi tiet), tra ve ["CHITCHAT"].
    2. "detected_versions": Mang cac so version (nguyen) neu user nhac den (vd v1 -> [1], v2 va v3 -> [2, 3]). Neu khong co, tra ve [].
    3. "variant_codes": Mang cac chuoi variant neu nhac den.
    4. "version_policy": một trong:
    - "current_only": hỏi chung, chỉ lấy bản đang lưu hành
    - "specific_version": hỏi version cụ thể như v1, v2
    - "compare_versions": hỏi so sánh nhiều version
    - "include_archived": user nói rõ muốn gồm bản cũ/archive
    - "version_history": user hỏi lịch sử version
    - "all_current_variants": user hỏi mã có nhiều variant cùng lưu hành
    5. "query_type": "general_lookup" hoac "bom_lookup" (hoi vat tu, bang ke).
    6. "product_names": Mang ten san pham/chi tiet neu user mo ta (vd: ["Khung sat + inox 201"]). Neu khong co, tra ve [].
    7. "materials": Mang vat lieu neu user nhac (vd: ["inox 201", "SUS304", "SS400"]). Neu khong co, tra ve [].
    8. "dimensions": Mang kich thuoc neu user nhac (vd: ["381x470x990.6mm"]). Neu khong co, tra ve [].
    9. "models": Mang model/variant neu user nhac (vd: ["Model7"]). Neu khong co, tra ve [].
    10. "query_scope": mot trong "single_candidate" (hoi 1 san pham cu the), "compare_candidates" (hoi nhieu/so sanh/tat ca model), "general_policy" (hoi quy trinh/tieu chuan chung khong gan tai lieu cu the).
    11. "need_disambiguation": true neu can hoi lai de chon dung tai lieu, nguoc lai false.

    Quy tac quan trong: Neu user KHONG dua ma ban ve nhung co ten san pham, vat lieu,
    hoac kich thuoc, hay trich xuat vao product_names/materials/dimensions/models.
    TUYET DOI KHONG tu bia ra ma ban ve (base_codes) khi user khong cung cap.

    Luu y: Chi tra ve dung JSON, khong giai thich gi them.
    """
 
    intent_data = {
        "base_codes": [],
        "detected_versions": [],
        "variant_codes": [],
        "version_policy": "current_only",
        "query_type": "general_lookup",
        "product_names": [],
        "materials": [],
        "dimensions": [],
        "models": [],
        "query_scope": "single_candidate",
        "need_disambiguation": False,
        "is_chitchat": False,
    }

    force_llm = bool(re.search(r'\bv\d+\b|version|so sanh|khac nhau|cu\b|moi nhat|archive', question, re.IGNORECASE))
    regex_codes = extract_mechanical_codes(question)
 
    if regex_codes and not force_llm:
        seen_rc = set()
        for c in regex_codes:
            if c not in seen_rc:
                seen_rc.add(c)
                intent_data["base_codes"].append(c)
        logger.info(f"H4: Trich ma bang regex (bo qua LLM intent): {intent_data['base_codes']}")
    else:
        def call_llm():
            response = cohere_invoke([HumanMessage(content=prompt_intent)])
            return response.content
 
        try:
            future = _INTENT_EXECUTOR.submit(call_llm)
            raw_response = future.result(timeout=_INTENT_TIMEOUT)
            clean_json = raw_response.replace('```json', '').replace('```', '').strip()
            parsed = json.loads(clean_json)
            intent_data["base_codes"] = [str(c) for c in parsed.get("base_codes", []) if c]
            
            # Xy ly parse int an toan
            d_vers = []
            for v in parsed.get("detected_versions", []):
                try: d_vers.append(int(v))
                except: pass
            intent_data["detected_versions"] = d_vers
            
            intent_data["variant_codes"] = [str(v) for v in parsed.get("variant_codes", []) if v]
            intent_data["version_policy"] = parsed.get("version_policy", "current_only")
            intent_data["query_type"] = parsed.get("query_type", "general_lookup")
            # --- Mo rong (no-code resolver): cong them field, khong pha field cu ---
            intent_data["product_names"] = [str(v) for v in parsed.get("product_names", []) if v]
            intent_data["materials"] = [str(v) for v in parsed.get("materials", []) if v]
            intent_data["dimensions"] = [str(v) for v in parsed.get("dimensions", []) if v]
            intent_data["models"] = [str(v) for v in parsed.get("models", []) if v]
            intent_data["query_scope"] = parsed.get("query_scope", "single_candidate")
            intent_data["need_disambiguation"] = bool(parsed.get("need_disambiguation", False))
            # Neu LLM bat duoc model/variant ma chua co o variant_codes -> bo sung
            for _m in intent_data["models"]:
                if _m and _m not in intent_data["variant_codes"]:
                    intent_data["variant_codes"].append(_m)
        except concurrent.futures.TimeoutError:
            future.cancel()
            logger.warning(f"LLM Intent Extraction bi timeout. Fallback ve Regex.")
        except Exception as e:
            logger.warning(f"Loi LLM Intent Extraction: {e}. Fallback ve Regex.")

    det_policy, det_versions = deterministic_version_intent(question)

    if det_versions:
        intent_data["detected_versions"] = det_versions

    if det_policy:
        intent_data["version_policy"] = det_policy

    # Muc 5: keyword "cac model / tat ca / so sanh" -> coi nhu hoi tat ca variant,
    # khong hoi lai chon model. Dung khong dau de bat ca 2 kieu go.
    from mech_chatbot.rag.text_utils import remove_accents as _ra_intent
    _q_all_kw = _ra_intent(question.lower())
    ALL_VARIANT_KEYWORDS = [
        "cac model", "tat ca model", "tat ca cac model", "moi model",
        "tung model", "cac variant", "tat ca variant", "so sanh cac model",
        "so sanh model",
    ]
    if any(kw in _q_all_kw for kw in ALL_VARIANT_KEYWORDS):
        intent_data["version_policy"] = "all_current_variants"
        intent_data["query_scope"] = "compare_candidates"

    # P1: TACH dinh tuyen khoi trich ma. Sentinel "CHITCHAT" (do LLM tra ve) ->
    # co rieng intent_data["is_chitchat"], KHONG de ro ri vao base_codes/new_part_ids/rbac.
    if any(str(c).strip().upper() == "CHITCHAT" for c in intent_data["base_codes"]):
        intent_data["is_chitchat"] = True
        intent_data["base_codes"] = [c for c in intent_data["base_codes"] if str(c).strip().upper() != "CHITCHAT"]

    from mech_chatbot.db.repository import normalize_base_code
    extracted_codes = [normalize_base_code(c) for c in intent_data["base_codes"] if c]
    
    # Co che cap nhat State
    if extracted_codes:
        new_part_ids = extracted_codes
        is_inherited = False
    else:
        new_part_ids = current_part_ids
        is_inherited = True
        
        if is_inherited and new_part_ids and not force_part_ids:
            from mech_chatbot.rag.text_utils import remove_accents
            q_norm = remove_accents(question.lower())
            broad_keywords = ["toan bo", "tat ca", "danh sach", "co nhung ma", "co nhung san pham", "cac ma", "cac san pham"]
            if any(kw in q_norm for kw in broad_keywords):
                logger.info(f"Phat hien cau hoi tong quat. Reset state (huy ke thua ma {new_part_ids}).")
                new_part_ids = []
                is_inherited = False
            # FIX B: neu user mo ta tai lieu (ten/vat lieu/kich thuoc/model) -> coi nhu
            # chi dinh tai lieu MOI. Huy ke thua ma cu de resolver chay tren mo ta,
            # tranh dinh ma cu sai roi bao "khong tim thay ma".
            _has_descr_ref = any(intent_data.get(_k) for _k in ("product_names", "materials", "dimensions", "models"))
            if new_part_ids and _has_descr_ref:
                logger.info(f"User mo ta tai lieu moi. Huy ke thua ma cu {new_part_ids}, chuyen sang resolver theo mo ta.")
                new_part_ids = []
                is_inherited = False
 
    # Build Must conditions based on version policy
    must_conditions = []
    vp = intent_data["version_policy"]
    d_vers = intent_data["detected_versions"]
    
    if vp in ["current_only", "all_current_variants"]:
        must_conditions.append(models.FieldCondition(key="metadata.lifecycle_status", match=models.MatchValue(value="published")))
        must_conditions.append(models.FieldCondition(key="metadata.review_status", match=models.MatchValue(value="approved")))
        must_conditions.append(models.FieldCondition(key="metadata.is_current", match=models.MatchValue(value=True)))
    elif vp == "specific_version":
        if d_vers:
            must_conditions.append(models.FieldCondition(key="metadata.version_no", match=models.MatchValue(value=d_vers[0])))
        if intent_data["variant_codes"]:
            must_conditions.append(models.FieldCondition(key="metadata.variant_code", match=models.MatchAny(any=intent_data["variant_codes"])))
        must_conditions.append(models.FieldCondition(key="metadata.lifecycle_status", match=models.MatchAny(any=["published", "archived", "superseded"])))
        must_conditions.append(models.FieldCondition(key="metadata.review_status", match=models.MatchValue(value="approved")))
    elif vp == "compare_versions":
        must_conditions.append(models.FieldCondition(key="metadata.lifecycle_status", match=models.MatchAny(any=["published", "archived", "superseded"])))
        must_conditions.append(models.FieldCondition(key="metadata.review_status", match=models.MatchValue(value="approved")))
        if d_vers:
            must_conditions.append(models.FieldCondition(key="metadata.version_no", match=models.MatchAny(any=d_vers)))
        if intent_data["variant_codes"]:
            must_conditions.append(models.FieldCondition(key="metadata.variant_code", match=models.MatchAny(any=intent_data["variant_codes"])))
    elif vp == "include_archived":
        must_conditions.append(
            models.FieldCondition(
                key="metadata.lifecycle_status",
                match=models.MatchAny(any=["published", "archived", "superseded", "retired"])
            )
        )
        must_conditions.append(
            models.FieldCondition(
                key="metadata.review_status",
                match=models.MatchValue(value="approved")
            )
        )
    elif vp == "version_history":
        must_conditions.append(
            models.FieldCondition(
                key="metadata.lifecycle_status",
                match=models.MatchAny(any=["published", "archived", "superseded", "retired"])
            )
        )
        must_conditions.append(
            models.FieldCondition(
                key="metadata.review_status",
                match=models.MatchValue(value="approved")
            )
        )
    else:
        must_conditions.append(models.FieldCondition(key="metadata.is_current", match=models.MatchValue(value=True)))

    rbac_filter = create_rbac_filter(user_department, user_roles, allowed_departments=allowed_departments, max_security_level=max_security_level, allowed_sites=allowed_sites)
    if rbac_filter:
        must_conditions.append(rbac_filter)

    if not new_part_ids:
        # Fallback filter
        qdrant_filter = models.Filter(must=must_conditions)
        return qdrant_filter, qdrant_filter, new_part_ids, is_inherited, False, intent_data
 
    from mech_chatbot.rag.text_utils import remove_accents
    q_norm = remove_accents(question.lower())
    is_bom_query = intent_data["query_type"] == "bom_lookup" or any(kw in q_norm for kw in ["vat tu", "bang ke", "bom", "danh sach", "chi tiet", "gom nhung gi", "cau tao", "linh kien", "part list", "thanh phan", "chi tiet con", "vat lieu", "cum nay", "ma nao"])
 
    # Ghep strict & broad qua MOT nguon duy nhat (rbac.py) -> chong noi quyen.
    strict_filter, broad_filter = compose_retrieval_filters(must_conditions, new_part_ids)
    
    return strict_filter, broad_filter, new_part_ids, is_inherited, is_bom_query, intent_data


_CONTEXT_TIMEOUT = float(os.getenv("CONTEXT_TIMEOUT", "5.0"))


def analyze_context(user_question, chat_history=None, current_part_ids=None, active_doc_refs=None):
    """P0-1: Phan doan ngu canh hoi thoai + query rewriting (1 LLM call, co timeout).

    Tra ve dict:
      - context_action: continue | switch_topic | broaden
      - standalone_question: cau hoi da viet lai thanh doc lap (hoac cau goc)

    Fallback AN TOAN (giu nguyen hanh vi cu = ke thua State Memory + cau goc) khi:
    tat tinh nang, chua co ngu canh, loi parse hoac timeout.
    """
    fallback = {"context_action": "continue", "standalone_question": user_question, "llm_resolved": False}
    if not env_bool("ENABLE_QUERY_REWRITE", True):
        return fallback
    if not chat_history:
        return fallback

    # P0-A: KHONG con yeu cau current_part_ids -> chay cho MOI luot co lich su.
    # Phong ban phi co khi khong bao gio co part_ids van can decontextualize.
    # Toi uu chi phi (volume cao): chi goi LLM khi cau co dau hieu phu thuoc
    # ngu canh (dai tu / tinh luoc / cau ngan) HOAC dang co neo ngu canh.
    from mech_chatbot.rag.text_utils import remove_accents as _ra_ctx
    _q_ctx = _ra_ctx(str(user_question).lower())
    _followup_markers = [
        "no ", "no?", "cai do", "cai nay", "cai kia", "cai ay", "chung",
        "ban truoc", "ban nay", "ban do", "phien ban truoc", "version truoc",
        "con ", "the con", "vay con", "thi sao", "so voi", "so sanh voi",
        "muc ", "phan ", "dieu ", "chuong ", "o tren", "ben tren", "vua roi",
        "vua noi", "nhu vay", "tiep tuc", "chi tiet hon", "them", "the nao",
        # KH-2: nhan dien cau "lam tiep" (tra loi loi moi cua bot) de van rewrite.
        "trich", "liet ke", "cu the", "day du", "noi ro", "noi them",
        "giai thich", "lam ro", "cho xem", "ra di", "lam di", "chi tiet",
    ]
    _has_followup_signal = (
        any(m in _q_ctx for m in _followup_markers)
        or len(_q_ctx.split()) <= 6
    )
    # Coi active_doc_refs nhu mot "neo" -> van cho LLM chay de phan biet continue /
    # switch_topic ke ca voi cau dai KHONG co tu khoa tiep dien.
    _has_anchor = bool(current_part_ids) or bool(active_doc_refs)
    if not _has_anchor and not _has_followup_signal:
        # Cau dau doc lap / khong phu thuoc ngu canh -> khoi goi LLM (tiet kiem cost)
        return fallback

    hist_lines = []
    for msg in chat_history[-6:]:
        role = "Khach" if msg.get("role") == "user" else "Bot"
        content = str(msg.get("content", ""))
        if len(content) > 300:
            content = content[:300] + " [...]"
        hist_lines.append(f"{role}: {content}")
    hist_str = chr(10).join(hist_lines)

    template = """Ban la bo phan tich ngu canh hoi thoai cho he thong tra cuu tai lieu ky thuat.
Ngu canh hien tai dang gan voi cac ma/tai lieu: __PARTIDS__.

Lich su hoi thoai gan nhat:
__HIST__

Cau hoi moi cua nguoi dung: "__QUESTION__"

Hay tra ve DUNG 1 JSON object theo schema (khong markdown):
{
  "context_action": "continue | switch_topic | broaden",
  "standalone_question": "cau hoi day du, doc lap, khong con dai tu chi dinh"
}

Quy tac:
- continue: cau hoi moi VAN noi ve cung ma/tai lieu dang trong ngu canh. Viet lai standalone_question de bo sung ro ma/tai lieu dang noi toi.
- switch_topic: cau hoi chuyen sang ma/san pham/chu de KHAC. Khong gan vao ma cu.
- broaden: cau hoi tong quat/liet ke toan bo (vd co nhung ma nao, tat ca san pham). Khong gan vao mot ma cu the.
- standalone_question bang tieng Viet, giu nguyen y dinh goc; chi bo sung ngu canh khi context_action = continue.
- Neu chua ghim ma/tai lieu cu the (PARTIDS = chua ghim): VAN phai viet lai standalone_question bang cach thay dai tu (no, cai do, cai kia, chung, ban truoc...) va bo sung chu the con thieu dua tren lich su hoi thoai.
- CHI tra ve JSON, khong giai thich."""
    if current_part_ids:
        part_ctx = str(current_part_ids)
    elif active_doc_refs:
        part_ctx = f"(chua ghim ma cu the; tai lieu dang trao doi: {active_doc_refs})"
    else:
        part_ctx = "(chua ghim ma/tai lieu cu the)"
    prompt = (template
              .replace("__PARTIDS__", part_ctx)
              .replace("__HIST__", hist_str)
              .replace("__QUESTION__", str(user_question)))

    def call_llm():
        return cohere_invoke([HumanMessage(content=prompt)]).content

    try:
        future = _INTENT_EXECUTOR.submit(call_llm)
        raw_response = future.result(timeout=_CONTEXT_TIMEOUT)
        clean_json = raw_response.replace("```json", "").replace("```", "").strip()
        parsed = json.loads(clean_json)
        action = parsed.get("context_action", "continue")
        if action not in ("continue", "switch_topic", "broaden"):
            action = "continue"
        standalone = parsed.get("standalone_question") or user_question
        if not isinstance(standalone, str) or not standalone.strip():
            standalone = user_question
        return {"context_action": action, "standalone_question": standalone.strip(), "llm_resolved": True}
    except concurrent.futures.TimeoutError:
        try:
            future.cancel()
        except Exception:
            pass
        logger.warning("analyze_context bi timeout -> fallback continue + cau goc.")
        return fallback
    except Exception as e:
        logger.warning(f"Loi analyze_context: {e} -> fallback continue + cau goc.")
        return fallback

__all__ = [
    '_INTENT_MAX_WORKERS',
    '_INTENT_TIMEOUT',
    '_INTENT_EXECUTOR',
    'serialize_qdrant_filter',
    'deterministic_version_intent',
    'extract_mechanical_codes',
    'extract_search_intent',
    '_CONTEXT_TIMEOUT',
    'analyze_context',
]
