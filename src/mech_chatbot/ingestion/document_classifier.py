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

def classify_document(file_path, original_filename=None, thu_muc=None):
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
    prompt, fallback_doc_type = handler.build_classify_prompt(
        original_filename=original_filename,
        text_content=text_content,
        regex_base_code=regex_base_code,
        regex_version_label=regex_version_label,
        regex_version_no=regex_version_no,
        domain=domain,
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
        return default_res

if __name__ == "__main__":
    # Test script
    import sys
    if len(sys.argv) > 1:
        res = classify_document(sys.argv[1])
        print(json.dumps(res, indent=2, ensure_ascii=False))
