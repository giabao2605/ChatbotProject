# -*- coding: utf-8 -*-
"""Auto-split tu rag/service.py (P1.2 refactor). Giu nguyen logic goc; chi tach file + import."""

import concurrent.futures
import json
from dataclasses import dataclass
from concurrent.futures import ThreadPoolExecutor
from contextvars import copy_context
import re
from typing import Any, Callable, Protocol
import unicodedata

from langchain_core.messages import HumanMessage
from qdrant_client import models

from mech_chatbot.config.logging import logger
from mech_chatbot.llm.external_ai import ExternalAICallCancelled, question_only_processing_context
from mech_chatbot.llm.llm_client import cohere_invoke
from mech_chatbot.rag.chitchat import is_chitchat
from mech_chatbot.rag.conversation_state import has_missing_referent
from mech_chatbot.rag.entity_resolver import extract_explicit_codes
from mech_chatbot.rag.execution import RequestBudgetExceeded
from mech_chatbot.rag.rbac import (
    LEVEL_ORDER,
    _allowed_levels,
    _security_filter,
    _site_filter,
    compose_retrieval_filters,
    create_rbac_filter,
)

# cross-module (owned) refs


def env_bool(name, default=False, *, values=None):
    """Parse an explicitly supplied environment-style mapping."""
    raw = values.get(name) if values is not None else None
    if raw is None:
        return bool(default)
    return str(raw).strip().lower() in {"1", "true", "yes", "on"}


class IntentFuture(Protocol):
    def result(self, timeout: float | None = None) -> Any: ...

    def cancel(self) -> bool: ...


class IntentExecutor(Protocol):
    def submit(self, function: Callable[..., Any], *args: Any) -> IntentFuture: ...

    def shutdown(self, *, wait: bool, cancel_futures: bool) -> None: ...


@dataclass(frozen=True, slots=True)
class IntentRuntime:
    """Lifecycle-owned execution resources for intent and context calls."""

    executor: IntentExecutor
    intent_timeout: float = 10.0
    context_timeout: float = 10.0
    query_rewrite_enabled: bool = True
    owns_executor: bool = False

    def close(self) -> None:
        if self.owns_executor:
            self.executor.shutdown(wait=False, cancel_futures=True)


def build_intent_runtime(
    *,
    max_workers: int = 8,
    intent_timeout: float = 10.0,
    context_timeout: float = 10.0,
    query_rewrite_enabled: bool = True,
    executor: IntentExecutor | None = None,
) -> IntentRuntime:
    """Create an explicit process-lifecycle dependency."""
    owned = executor is None
    active_executor = (
        executor if executor is not None else ThreadPoolExecutor(max_workers=max_workers)
    )
    return IntentRuntime(
        executor=active_executor,
        intent_timeout=intent_timeout,
        context_timeout=context_timeout,
        query_rewrite_enabled=query_rewrite_enabled,
        owns_executor=owned,
    )


def _runtime_for_call(runtime: IntentRuntime | None) -> tuple[IntentRuntime, bool]:
    if runtime is not None:
        return runtime, False
    return build_intent_runtime(), True


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

    if any(k in q_norm for k in compare_keywords) and (
        len(versions) >= 2 or re.search(r"\b(?:phiên bản|versions?)\b", q_norm)
    ):
        return "compare_versions", versions

    if any(k in q_norm for k in history_keywords):
        return "version_history", versions

    if any(k in q_norm for k in archive_keywords):
        return "include_archived", versions

    if len(versions) == 1:
        return "specific_version", versions

    return None, versions


def extract_mechanical_codes(question):
    return extract_explicit_codes(question)


def _business_normalize(value):
    normalized = unicodedata.normalize("NFD", str(value or "").lower())
    normalized = "".join(char for char in normalized if unicodedata.category(char) != "Mn")
    return re.sub(r"\s+", " ", normalized).strip()


_BOM_QUERY_TERMS = (
    "vat tu", "bang ke", "bom", "danh sach", "chi tiet", "gom nhung gi",
    "cau tao", "linh kien", "part list", "thanh phan", "chi tiet con",
    "vat lieu", "cum nay", "ma nao",
)


def is_bom_lookup(question, *, query_type=""):
    from mech_chatbot.rag.text_utils import remove_accents

    normalized = remove_accents(str(question or "").lower())
    return query_type == "bom_lookup" or any(
        term in normalized for term in _BOM_QUERY_TERMS
    )


def deterministic_business_document_intent(question):
    """Recognize PO, contract and form requests without an LLM round trip."""
    raw = str(question or "")
    normalized = _business_normalize(raw)
    document_types = []
    if (
        re.search(r"\bpo\s*[-/]?\s*\d+", raw, re.IGNORECASE)
        or "purchase order" in normalized
        or "don dat hang" in normalized
        or "lenh dat hang" in normalized
    ):
        document_types.append("purchase_order")
    if (
        re.search(r"\b(?:hd|hop\s*dong|contract)\s*[-/]?\s*\d+", normalized, re.IGNORECASE)
        or "hop dong" in normalized
        or "contract" in normalized
    ):
        document_types.append("contract")
    if (
        re.search(r"\b(?:bm|fm|form)\s*[-/]?\s*\d+", normalized, re.IGNORECASE)
        or "bieu mau" in normalized
        or "mau don" in normalized
        or "form" in normalized
    ):
        document_types.append("form")
    references = re.findall(
        r"\b(?:PO|P/O|HD|HĐ|BM|FM|FORM|CONTRACT)\s*[-/]?\s*[A-Z0-9]{2,}\b",
        raw,
        re.IGNORECASE,
    )
    aliases = {
        "purchase_order": ["purchase_order", "po", "purchase order", "đơn đặt hàng", "don dat hang"],
        "contract": ["contract", "hợp đồng", "hop dong"],
        "form": ["form", "biểu mẫu", "bieu mau", "mẫu đơn", "mau don"],
    }
    filter_values = []
    for kind in document_types:
        filter_values.extend(aliases[kind])
    return {
        "document_types": list(dict.fromkeys(document_types)),
        "document_type_values": list(dict.fromkeys(filter_values)),
        "document_references": list(dict.fromkeys(reference.upper() for reference in references)),
    }


def extract_search_intent(question, current_part_ids=None, user_department=None, user_roles=None, allowed_departments=None, max_security_level=None, allowed_sites=None, force_part_ids=False, trace_id=None, invoke_provider=None, runtime=None, provider_question=None):
    """Phan tich cau hoi de lay danh sach ma doi tuong va intent versioning bang LLM (co timeout)."""
    if current_part_ids is None:
        current_part_ids = []
 
    prompt_question = question if provider_question is None else provider_question
    prompt_intent = f"""
    Trich xuat thong tin tim kiem tu cau hoi cua nguoi dung: '{prompt_question}'.
    Tra ve MOT JSON object duy nhat voi cac truong sau:
    1. "base_codes": Mang cac ma so ban ve/linh kien/tieu chuan (vd: ["banve-1", "9.3.03844"]). Neu cau hoi la xa giao (chao, cam on, thoi tiet), tra ve ["CHITCHAT"].
    2. "detected_versions": Mang cac so version (nguyen) neu user nhac den (vd v1 -> [1], v2 va v3 -> [2, 3]). Neu khong co, tra ve [].
    3. "variant_codes": Mang cac chuoi variant neu nhac den.
    4. "version_policy": một trong:
    - "current_only": hỏi chung hoặc chỉ hỏi hiện nay, hiện hành, đang áp dụng; chỉ lấy bản đang lưu hành.
    - "specific_version": hỏi phiên bản kỹ thuật cụ thể như v1, v2.
    - "compare_versions": so sánh quy định giữa các thời kỳ, các năm, trước đây với hiện nay hoặc nhiều phiên bản.
    - "include_archived": hỏi quy định cũ, trước đây, văn bản bị thay thế hoặc hết hiệu lực.
    - "version_history": hỏi lịch sử các phiên bản hoặc diễn biến thay thế văn bản.
    - "all_current_variants": hỏi mã có nhiều biến thể cùng lưu hành.
    Văn bản pháp luật có thể là các văn bản độc lập thay thế nhau theo thời gian,
    không phải các version của cùng một tệp. Vẫn dùng include_archived/compare_versions
    khi hỏi quy định cũ hoặc so sánh các thời kỳ. Năm ban hành không phải số version:
    không đưa năm vào detected_versions; chỉ đưa số v/rev/version được nêu rõ.
    Nếu chỉ hỏi hiện nay/hiện hành mà không yêu cầu đối chiếu quá khứ, dùng current_only.
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
        "document_type_hints": [],
        "document_references": [],
    }

    regex_codes = extract_mechanical_codes(question)
    if is_chitchat(question):
        intent_data["is_chitchat"] = True
    else:
        def call_llm():
            provider = invoke_provider or cohere_invoke
            with question_only_processing_context():
                response = provider(
                    [HumanMessage(content=prompt_intent)],
                    surface="intent_routing",
                    trace_id=trace_id,
                )
            return response.content
 
        call_runtime, owns_runtime = _runtime_for_call(runtime)
        future = None
        try:
            request_context = copy_context()
            future = call_runtime.executor.submit(request_context.run, call_llm)
            raw_response = future.result(timeout=call_runtime.intent_timeout)
            clean_json = raw_response.replace('```json', '').replace('```', '').strip()
            parsed = json.loads(clean_json)
            if not isinstance(parsed, dict):
                raise ValueError("intent response must be an object")
            policy = parsed.get("version_policy", "current_only")
            if policy not in ("current_only", "specific_version", "compare_versions",
                              "include_archived", "version_history", "all_current_variants"):
                raise ValueError("invalid version policy")
            candidate = dict(intent_data)
            candidate["base_codes"] = [str(c) for c in parsed.get("base_codes", []) if c]
            
            # Xy ly parse int an toan
            d_vers = []
            for v in parsed.get("detected_versions", []):
                try: d_vers.append(int(v))
                except: pass
            candidate["detected_versions"] = d_vers
            
            candidate["variant_codes"] = [str(v) for v in parsed.get("variant_codes", []) if v]
            candidate["version_policy"] = policy
            candidate["query_type"] = parsed.get("query_type", "general_lookup")
            # --- Mo rong (no-code resolver): cong them field, khong pha field cu ---
            candidate["product_names"] = [str(v) for v in parsed.get("product_names", []) if v]
            candidate["materials"] = [str(v) for v in parsed.get("materials", []) if v]
            candidate["dimensions"] = [str(v) for v in parsed.get("dimensions", []) if v]
            candidate["models"] = [str(v) for v in parsed.get("models", []) if v]
            candidate["query_scope"] = parsed.get("query_scope", "single_candidate")
            candidate["need_disambiguation"] = bool(parsed.get("need_disambiguation", False))
            # Neu LLM bat duoc model/variant ma chua co o variant_codes -> bo sung
            for _m in candidate["models"]:
                if _m and _m not in candidate["variant_codes"]:
                    candidate["variant_codes"].append(_m)
            intent_data = candidate
        except concurrent.futures.TimeoutError:
            if future is not None:
                future.cancel()
            logger.warning(f"LLM Intent Extraction bi timeout. Fallback ve Regex.")
        except (ExternalAICallCancelled, RequestBudgetExceeded):
            raise
        except Exception as e:
            logger.warning(f"Loi LLM Intent Extraction: {e}. Fallback ve Regex.")
        finally:
            if owns_runtime:
                call_runtime.close()

    seen_codes = {
        str(code).strip().casefold()
        for code in intent_data["base_codes"]
        if str(code).strip()
    }
    for code in regex_codes:
        normalized = str(code).strip().casefold()
        if normalized and normalized not in seen_codes:
            intent_data["base_codes"].append(code)
            seen_codes.add(normalized)

    det_policy, det_versions = deterministic_version_intent(question)

    business_intent = deterministic_business_document_intent(question)
    intent_data["document_type_hints"] = business_intent["document_types"]
    intent_data["document_references"] = business_intent["document_references"]

    if det_versions or det_policy == "compare_versions":
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
    if (not det_policy and intent_data["version_policy"] in {"current_only", "all_current_variants"}
            and any(kw in _q_all_kw for kw in ALL_VARIANT_KEYWORDS)):
        intent_data["version_policy"] = "all_current_variants"
        intent_data["query_scope"] = "compare_candidates"

    # P1: TACH dinh tuyen khoi trich ma. Sentinel "CHITCHAT" (do LLM tra ve) ->
    # co rieng intent_data["is_chitchat"], KHONG de ro ri vao base_codes/new_part_ids/rbac.
    if any(str(c).strip().upper() == "CHITCHAT" for c in intent_data["base_codes"]):
        intent_data["is_chitchat"] = True
        intent_data["base_codes"] = [c for c in intent_data["base_codes"] if str(c).strip().upper() != "CHITCHAT"]

    from mech_chatbot.db.repositories._shared import normalize_base_code
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
    # Bat bien serving dung chung cho moi version policy. Tai lieu staged,
    # rejected, superseded hoac publication dang loi khong duoc truy xuat.
    must_conditions = [
        models.FieldCondition(
            key="metadata.servable",
            match=models.MatchValue(value=True),
        ),
        models.FieldCondition(
            key="metadata.publication_state",
            match=models.MatchValue(value="published"),
        ),
    ]
    # Record a rule-first metadata preference for PO, contracts and forms.
    # It is applied after retrieval rather than as a hard filter so legacy
    # documents missing the new family field remain discoverable during pilot.
    if business_intent["document_type_values"]:
        intent_data["metadata_document_type_preference"] = business_intent["document_types"]
    vp = intent_data["version_policy"]
    d_vers = intent_data["detected_versions"]
    if vp in {"specific_version", "compare_versions", "include_archived", "version_history"}:
        from mech_chatbot.auth.authorization import role_allows
        if not role_allows(user_roles, "document_history_reader"):
            # Historical retrieval is a separate capability.  Keep the query
            # fail-closed on the current corpus instead of letting an admin or
            # reviewer role imply access to old source bytes.
            intent_data["history_access_denied"] = True
            intent_data["version_policy"] = "current_only"
            vp = "current_only"
    
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
    if intent_data.get("history_access_denied"):
        # Do not silently answer a historical request from the current version.
        must_conditions.append(models.FieldCondition(
            key="metadata.version_no", match=models.MatchValue(value=-2147483648)
        ))

    if not new_part_ids:
        # Fallback filter
        qdrant_filter = models.Filter(must=must_conditions)
        return qdrant_filter, qdrant_filter, new_part_ids, is_inherited, False, intent_data
 
    is_bom_query = is_bom_lookup(
        question,
        query_type=intent_data["query_type"],
    )
 
    # Ghep strict & broad qua MOT nguon duy nhat (rbac.py) -> chong noi quyen.
    strict_filter, broad_filter = compose_retrieval_filters(must_conditions, new_part_ids)
    
    return strict_filter, broad_filter, new_part_ids, is_inherited, is_bom_query, intent_data


_REFERENCE_NOUNS = r'quy trinh|chinh sach|thu tuc|tai lieu|viec|noi dung|truong hop|muc|dieu|cai'
_REFERENCE_FILLER = frozenset(
    'the nao ra sao lam thuc hien can phai nen duoc bao ai truoc sau lau gi o dau nhu co khong la '
    'how does do work what when who where is are it please'.split())


def _context_reference(question):
    text = ''.join(char for char in unicodedata.normalize('NFKD', str(question).casefold())
                   if not unicodedata.combining(char)).replace('đ', 'd')
    return re.search(
        rf'\b(?:{_REFERENCE_NOUNS})\s+(?:do|nay|kia|ay)\b'
        r'|^(?:(?:con|the|vay|va)\s+)*(?:no|chung)(?:\s|[?!.]|$)'
        r'|\b(?:this|that|these|those)\s+(?:process|procedure|policy|document|case|item)\b', text)


def has_context_reference(question):
    return _context_reference(question) is not None


def _reference_has_subject(reference):
    remaining = reference.string[:reference.start()] + reference.string[reference.end():]
    return bool(set(re.findall(r'[a-z]+', remaining)) - _REFERENCE_FILLER)


def _previous_user_request_scope(chat_history):
    """Keep an unambiguous user request as scope, never an assistant's answer."""
    from mech_chatbot.rag.query_decomposition import _INDEPENDENT_REQUEST, _INTENT_SEPARATOR, _fold

    for message in reversed((chat_history or [])[-6:]):
        if message.get('role') != 'user':
            continue
        previous = str(message.get('content') or '').strip().rstrip('?!.').rstrip()
        if not previous or len(previous) > 300:
            return None
        reference = _context_reference(previous)
        if reference:
            if _reference_has_subject(reference) or extract_explicit_codes(previous):
                return None
            continue
        if has_missing_referent(previous):
            return None
        # The shared splitter can retain several uncertain subjects as one intent;
        # require one clause here instead of guessing which subject was meant.
        clauses = [part for part in _INTENT_SEPARATOR.split(previous) if part.strip()]
        if (len(clauses) != 1 or not _INDEPENDENT_REQUEST.search(previous)
                or re.search(r'[.!?;\n]|\b(?:and|hay|hoặc|hoac|or)\b', previous, re.IGNORECASE)):
            return None
        subject = re.sub(rf'\b(?:{_REFERENCE_NOUNS}|process|procedure|policy|document|case|item)\b',
                         '', _fold(_INDEPENDENT_REQUEST.sub('', previous)))
        if not set(re.findall(r'[a-z]+', subject)) - _REFERENCE_FILLER:
            return None
        return previous
    return None


_REWRITE_PROTECTED_TOKEN = re.compile(
    r"(?<!\w)(?:(?:v|version|rev(?:ision)?)\s*\d+|"
    r"[+-]?\d+(?:[\.,]\d+)?(?:\s*(?:mm|cm|m|kg|g|h|giờ|gio|phút|phut|ngày|ngay|tháng|thang|năm|nam|%)\b)?|"
    r"\d{1,4}[/-]\d{1,2}(?:[/-]\d{1,4})?|"
    r"(?:không|khong|chưa|chua|không được|khong duoc|không có|khong co|ngoại trừ|ngoai tru|trừ|tru|"
    r"no|not|without)"
    r")(?!\w)",
    re.IGNORECASE,
)
_REWRITE_PROTECTED_FILE = re.compile(r"(?<!\w)[\w.-]+\.(?:pdf|docx?|xlsx?|csv|md)\b", re.IGNORECASE)


def _fold_text(value):
    normalized = unicodedata.normalize("NFKD", str(value or "").casefold())
    return "".join(char for char in normalized if not unicodedata.combining(char)).replace("đ", "d")


def _rewrite_preserves_request_constraints(original, rewritten):
    """Reject a rewrite that drops a code, quantity, date or negation."""
    original_text = str(original or "")
    rewritten_text = str(rewritten or "")
    if not rewritten_text.strip():
        return False
    original_folded = _fold_text(original_text)
    rewritten_folded = _fold_text(rewritten_text)
    for token in _REWRITE_PROTECTED_TOKEN.findall(original_folded):
        if token.casefold() not in rewritten_folded:
            return False
    for filename in _REWRITE_PROTECTED_FILE.findall(original_text):
        if _fold_text(filename) not in rewritten_folded:
            return False
    original_codes = {str(code).casefold() for code in extract_explicit_codes(original_text)}
    rewritten_codes = {str(code).casefold() for code in extract_explicit_codes(rewritten_text)}
    return rewritten_codes.issubset(original_codes)


def _context_document_labels(references):
    """Keep display labels only; never serialize source metadata or objects."""
    labels = []
    for reference in references if isinstance(references, (list, tuple)) else ():
        values = ([reference] if isinstance(reference, str) else
                  [reference.get(key) for key in ("base_code", "file_goc", "label")]
                  if isinstance(reference, dict) else [])
        for value in values:
            if isinstance(value, str) and value.strip() and "\n" not in value and "\r" not in value:
                label = value.strip()[:256]
                if label not in labels:
                    labels.append(label)
    return labels


def analyze_context(user_question, chat_history=None, current_part_ids=None, active_doc_refs=None, trace_id=None, invoke_provider=None, runtime=None, pending_candidates=None):
    """P0-1: Phan doan ngu canh hoi thoai + query rewriting (1 LLM call, co timeout).

    Tra ve dict:
      - context_action: continue | switch_topic | broaden
      - standalone_question: cau hoi da viet lai thanh doc lap (hoac cau goc)

    Fallback AN TOAN (giu nguyen hanh vi cu = ke thua State Memory + cau goc) khi:
    tat tinh nang, chua co ngu canh, loi parse hoac timeout.
    """
    has_history = bool(chat_history)
    chat_history = [
        {"role": "user", "content": message["content"]}
        for message in (chat_history or [])
        if isinstance(message, dict) and message.get("role") in ("user", "Khach")
        and isinstance(message.get("content"), str)
    ]
    current_part_ids = _context_document_labels(current_part_ids)
    active_doc_refs = _context_document_labels(active_doc_refs)
    fallback = {"context_action": "continue", "standalone_question": user_question, "llm_resolved": False}
    needs_reference = (has_context_reference(user_question) or has_missing_referent(user_question)) and not extract_explicit_codes(user_question)
    reference = _context_reference(user_question)
    subject_candidate = _reference_has_subject(reference) if reference else False
    if needs_reference:
        fallback = {**fallback, 'clarification_required': True}
    if runtime is not None and not runtime.query_rewrite_enabled:
        return fallback
    if needs_reference and reference is not None and not subject_candidate and not pending_candidates:
        previous_scope = _previous_user_request_scope(chat_history)
        if previous_scope:
            return {
                'context_action': 'continue',
                'standalone_question': f'Theo chủ đề trong câu hỏi trước của người dùng «{previous_scope}», {user_question}',
                'llm_resolved': False,
            }
    if not has_history and not (needs_reference and (current_part_ids or active_doc_refs or subject_candidate)):
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
        needs_reference or any(m in _q_ctx for m in _followup_markers)
        or len(_q_ctx.split()) <= 6
        or has_missing_referent(user_question)
    )
    # Coi active_doc_refs nhu mot "neo" -> van cho LLM chay de phan biet continue /
    # switch_topic ke ca voi cau dai KHONG co tu khoa tiep dien.
    _has_anchor = bool(current_part_ids) or bool(active_doc_refs)
    if not _has_anchor and not _has_followup_signal:
        # Cau dau doc lap / khong phu thuoc ngu canh -> khoi goi LLM (tiet kiem cost)
        return fallback

    hist_lines = []
    for msg in (chat_history or [])[-6:]:
        content = msg["content"]
        if len(content) > 300:
            content = content[:300] + " [...]"
        hist_lines.append(f"Khach: {content}")
    hist_str = chr(10).join(hist_lines)

    template = """Ban la bo phan tich ngu canh hoi thoai cho he thong tra cuu tai lieu ky thuat.
Ngu canh hien tai dang gan voi cac ma/tai lieu: __PARTIDS__.

Cac cau hoi gan nhat cua nguoi dung (khong co cau tra loi cua bot):
__HIST__

Cau hoi moi cua nguoi dung: "__QUESTION__"

Hay tra ve DUNG 1 JSON object theo schema (khong markdown):
{
  "context_action": "continue | switch_topic | broaden | clarify",
  "standalone_question": "cau hoi day du, doc lap, khong con dai tu chi dinh"
}

Quy tac:
- continue: cau hoi moi VAN noi ve cung ma/tai lieu dang trong ngu canh. Viet lai standalone_question de bo sung ro ma/tai lieu dang noi toi.
- switch_topic: cau hoi chuyen sang ma/san pham/chu de KHAC. Khong gan vao ma cu.
- broaden: cau hoi tong quat/liet ke toan bo (vd co nhung ma nao, tat ca san pham). Khong gan vao mot ma cu the.
- clarify: khong xac dinh duoc doi tuong cau hoi tu lich su, hoac co nhieu doi tuong co the. Giu nguyen cau hoi, khong tu doan.
- Uu tien chu de neu ro ngay trong cau hoi moi (vd "Toi muon xin nghi phep, quy trinh do the nao?"); khong doi lich su neu cau da du ngu canh.
- Chi dung cac cau hoi cua nguoi dung de hieu doi tuong; khong coi lich su la bang chung nghiep vu.
- standalone_question bang tieng Viet, giu nguyen y dinh goc; chi bo sung ngu canh khi context_action = continue.
- Neu chua ghim ma/tai lieu cu the (PARTIDS = chua ghim): VAN phai viet lai standalone_question bang cach thay dai tu (no, cai do, cai kia, chung, ban truoc...) va bo sung chu the con thieu dua tren cac cau hoi cua nguoi dung.
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
        provider = invoke_provider or cohere_invoke
        with question_only_processing_context():
            return provider(
                [HumanMessage(content=prompt)],
                surface="query_disambiguation",
                trace_id=trace_id,
            ).content

    call_runtime, owns_runtime = _runtime_for_call(runtime)
    future = None
    try:
        request_context = copy_context()
        future = call_runtime.executor.submit(request_context.run, call_llm)
        raw_response = future.result(timeout=call_runtime.context_timeout)
        clean_json = raw_response.replace("```json", "").replace("```", "").strip()
        parsed = json.loads(clean_json)
        action = parsed.get("context_action", "continue")
        if action == 'clarify':
            return {**fallback, 'clarification_required': True}
        if action not in ("continue", "switch_topic", "broaden"):
            if needs_reference:
                return fallback
            action = "continue"
        standalone = parsed.get("standalone_question") or user_question
        if not isinstance(standalone, str) or not standalone.strip():
            standalone = user_question
        if not _rewrite_preserves_request_constraints(user_question, standalone):
            logger.warning("analyze_context rewrite dropped a protected request constraint; using original question")
            return {**fallback, 'context_action': action, 'llm_resolved': False}
        if needs_reference and (has_context_reference(standalone) or has_missing_referent(standalone)) and not extract_explicit_codes(standalone):
            return {**fallback, 'clarification_required': True}
        return {"context_action": action, "standalone_question": standalone.strip(), "llm_resolved": True}
    except concurrent.futures.TimeoutError:
        if future is not None:
            future.cancel()
        logger.warning("analyze_context bi timeout -> fallback continue + cau goc.")
        return fallback
    except (ExternalAICallCancelled, RequestBudgetExceeded):
        raise
    except Exception as e:
        logger.warning(f"Loi analyze_context: {e} -> fallback continue + cau goc.")
        return fallback
    finally:
        if owns_runtime:
            call_runtime.close()

__all__ = [
    'IntentRuntime',
    'IntentExecutor',
    'build_intent_runtime',
    'env_bool',
    'serialize_qdrant_filter',
    'deterministic_version_intent',
    'extract_mechanical_codes',
    'extract_search_intent',
    'analyze_context',
]
