import fitz
import re
import json
import os
from mech_chatbot.llm.llm_client import cohere_invoke
from langchain_core.messages import HumanMessage
from mech_chatbot.db.repository import engine
from sqlalchemy import text
from mech_chatbot.config.logging import logger

def extract_first_pages(file_path, num_pages=2):
    text_content = ""
    try:
        doc = fitz.open(file_path)
        for i in range(min(num_pages, len(doc))):
            text_content += f"--- Page {i+1} ---\n"
            text_content += doc[i].get_text() + "\n"
        doc.close()
    except Exception as e:
        logger.error(f"Loi doc file PDF classification {file_path}: {e}")
    return text_content

def extract_pages_for_classification(file_path, max_pages=6, char_budget=6000):
    """P2-4: Lay noi dung DAI DIEN de phan loai theo NOI DUNG (khong chi ten file).
    - Tai lieu ngan (<= max_pages trang): doc het.
    - Tai lieu dai: 2 trang dau + vai trang giua (cach deu) + trang cuoi,
      gioi han so trang & so ky tu de tiet kiem chi phi/toc do.
    """
    text_content = ""
    try:
        doc = fitz.open(file_path)
        total = len(doc)
        if total == 0:
            doc.close()
            return ""
        if total <= max_pages:
            indices = list(range(total))
        else:
            first = [0, 1]
            last = [total - 1]
            remaining = max_pages - len(first) - len(last)
            mids = []
            if remaining > 0:
                step = max(1, (total - 3) // (remaining + 1))
                pg = 2 + step
                while pg < total - 1 and len(mids) < remaining:
                    mids.append(pg)
                    pg += step
            indices = sorted(set(first + mids + last))
        for i in indices:
            if len(text_content) >= char_budget:
                break
            text_content += f"--- Page {i+1} ---\n"
            text_content += doc[i].get_text() + "\n"
        doc.close()
    except Exception as e:
        logger.error(f"Loi doc file PDF classification {file_path}: {e}")
    return text_content[:char_budget]


def check_existing_family(base_code):
    try:
        with engine.connect() as conn:
            row = conn.execute(text("SELECT FamilyID, FamilyName FROM DocumentFamily WHERE BaseCode = :b"), {"b": base_code}).fetchone()
            if row:
                return row[0]
            return None
    except Exception as e:
        logger.error(f"Loi check existing family: {e}")
        return None

def normalize_filename_to_classification(filename):
    # Xoa extension khong phan biet hoa thuong
    name_without_ext = re.sub(r'\.pdf$', '', filename, flags=re.IGNORECASE)
    
    # Tim version: _v2, -rev3, _version4
    match = re.search(r'([_-](v|rev|version)(\d+))$', name_without_ext, flags=re.IGNORECASE)
    
    res = {
        "base_code": name_without_ext,
        "version_no": 1,
        "version_label": ""
    }
    
    if match:
        res["version_label"] = match.group(2) + match.group(3)
        res["version_no"] = int(match.group(3))
        res["base_code"] = name_without_ext[:match.start()]
        
    # Chuan hoa them neu can (vd strip trailing spaces, uppercase...)
    res["base_code"] = res["base_code"].strip()
    return res

def _load_active_document_types(department_code):
    """Read the serving profile without making classification depend on SQL availability."""
    if not department_code:
        return []
    try:
        from mech_chatbot.db.repositories.knowledge_governance import get_department_domain_profile

        profile = get_department_domain_profile(department_code)
        if not profile or not profile.get("is_active"):
            return []
        return [
            str(item).strip().lower()
            for item in (profile.get("document_types") or [])
            if str(item).strip()
        ]
    except Exception as exc:
        logger.warning(
            "Khong tai duoc domain profile cho %s; dung classifier fallback: %s",
            department_code,
            exc,
        )
        return []


def _canonical_profile_type(raw):
    """Normalize an LLM label while preserving valid profile-specific codes."""
    from mech_chatbot.ingestion.doc_type_registry import normalize_doc_type

    canonical = normalize_doc_type(raw)
    if canonical:
        return canonical
    return re.sub(r"[^a-z0-9]+", "_", str(raw or "").strip().lower()).strip("_")


def _profile_type_code(raw):
    """Return the literal normalized code stored in DocumentTypesJson."""
    return re.sub(r"[^a-z0-9]+", "_", str(raw or "").strip().lower()).strip("_")


def _validate_document_type(parsed, allowed_types, fallback_doc_type):
    """Fail closed to the handler fallback when the LLM escapes the profile allowlist."""
    allowed_codes = [_profile_type_code(item) for item in (allowed_types or [])]
    allowed_codes = [item for item in allowed_codes if item]
    candidate_literal = _profile_type_code(parsed.get("document_type"))
    candidate_canonical = _canonical_profile_type(parsed.get("document_type"))

    # Prefer the exact profile code. If the LLM returned a label/synonym, map
    # it back to the matching code that is actually present in the profile so
    # publication validation sees the same value. This also preserves
    # profile codes such as ``other`` which the legacy registry aliases to
    # ``generic``.
    candidate = candidate_literal if candidate_literal in allowed_codes else None
    if candidate is None and allowed_codes:
        candidate = next(
            (code for code in allowed_codes if _canonical_profile_type(code) == candidate_canonical),
            None,
        )
    if not allowed_codes:
        candidate = candidate_canonical or candidate_literal

    fallback_literal = _profile_type_code(fallback_doc_type)
    fallback_canonical = _canonical_profile_type(fallback_doc_type)
    fallback = fallback_literal if fallback_literal in allowed_codes else None
    if fallback is None and allowed_codes:
        fallback = next(
            (code for code in allowed_codes if _canonical_profile_type(code) == fallback_canonical),
            allowed_codes[0],
        )
    fallback = fallback or fallback_canonical or fallback_literal or fallback_doc_type

    if allowed_codes and candidate is None:
        original = parsed.get("document_type")
        parsed["document_type"] = fallback
        detail = f"document_type '{original}' nam ngoai active department profile; fallback '{fallback}'."
        parsed["reason"] = " ".join(part for part in [str(parsed.get("reason") or "").strip(), detail] if part)
        parsed["document_type_validation"] = "profile_fallback"
    else:
        parsed["document_type"] = candidate or fallback
        parsed["document_type_validation"] = "profile_valid" if allowed_codes else "legacy_fallback"
    return parsed


def classify_document(file_path, original_filename=None, thu_muc=None, document_types=None):
    """Phan loai tai lieu 2 tang:
      Tang 1: xac dinh domain tu thu_muc (mechanical / tabular / generic, tra cuu Departments)
      Tang 2: phan loai chi tiet bang LLM theo domain
    """
    if not original_filename:
        original_filename = os.path.basename(file_path)

    # ---- Tang 1: Domain routing ----
    from mech_chatbot.ingestion.domain_registry import resolve_domain_by_department, resolve_security_by_department
    domain = resolve_domain_by_department(thu_muc)
    is_mechanical = (domain == 'mechanical')

    text_content = extract_pages_for_classification(file_path)
    
    # 1. Deterministic Regex Pre-processing
    norm_data = normalize_filename_to_classification(original_filename)
    regex_base_code = norm_data["base_code"]
    regex_version_no = norm_data["version_no"]
    regex_version_label = norm_data["version_label"]

    # ---- Tang 2: LLM Classification qua DomainHandler (GD3) ----
    from mech_chatbot.ingestion.domain_handlers import get_handler
    handler = get_handler(domain)
    active_document_types = (
        [str(item).strip().lower() for item in document_types if str(item).strip()]
        if document_types is not None
        else _load_active_document_types(thu_muc)
    )
    prompt, fallback_doc_type = handler.build_classify_prompt(
        original_filename=original_filename,
        text_content=text_content,
        regex_base_code=regex_base_code,
        regex_version_label=regex_version_label,
        regex_version_no=regex_version_no,
        domain=domain,
        document_types=active_document_types,
    )

    default_res = {
        "base_code": regex_base_code,
        "version_label": regex_version_label,
        "version_no": regex_version_no,
        "variant_code": "default",
        "document_type": fallback_doc_type,
        "detected_action": "new_document",
        "possible_existing_family": None,
        "confidence": 0.5,
        "reason": "Fallback do LLM loi hoac regex mac dinh.",
        "domain": domain,
        "security_level": resolve_security_by_department(thu_muc),
    }
    
    try:
        resp = cohere_invoke(
            [HumanMessage(content=prompt)], surface="document_classification"
        )
        clean_json = resp.content.replace('```json', '').replace('```', '').strip()
        parsed = json.loads(clean_json)
        if not isinstance(parsed, dict):
            raise ValueError("Classification response phai la JSON object")
        parsed = {**default_res, **parsed}
        parsed = _validate_document_type(parsed, active_document_types, fallback_doc_type)
        
        base_code = parsed.get("base_code", default_res["base_code"])
        
        from mech_chatbot.db.repository import normalize_base_code
        base_code = normalize_base_code(base_code)
        parsed["base_code"] = base_code
        
        family_id = check_existing_family(base_code)
        
        parsed["possible_existing_family"] = base_code if family_id else None
        
        if family_id and parsed.get("detected_action") == "new_document":
            parsed["detected_action"] = "new_version" if parsed.get("version_no", 1) > 1 else "new_variant"

        # Gan domain + security_level vao ket qua
        parsed["domain"] = domain
        parsed["security_level"] = resolve_security_by_department(thu_muc)

        return parsed
    except Exception as e:
        logger.error(f"Loi classification LLM: {e}")
        default_res["classification_failed"] = True
        default_res["document_type_validation"] = "classifier_error_fallback"
        default_res["reason"] = f"Classifier fallback: {type(e).__name__}."
        return default_res

if __name__ == "__main__":
    # Test script
    import sys
    if len(sys.argv) > 1:
        res = classify_document(sys.argv[1])
        print(json.dumps(res, indent=2, ensure_ascii=False))
