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
      Tang 1: xac dinh domain tu thu_muc (co_khi / ke_toan / nhan_su / ...)
      Tang 2: phan loai chi tiet bang LLM theo domain
    """
    if not original_filename:
        original_filename = os.path.basename(file_path)

    # ---- Tang 1: Domain routing ----
    from mech_chatbot.ingestion.domain_registry import resolve_domain_by_department, get_default_security
    domain = resolve_domain_by_department(thu_muc) if thu_muc else 'chung'
    is_mechanical = domain in ('co_khi', 'ky_thuat')

    text_content = extract_first_pages(file_path, 2)
    
    # 1. Deterministic Regex Pre-processing
    norm_data = normalize_filename_to_classification(original_filename)
    regex_base_code = norm_data["base_code"]
    regex_version_no = norm_data["version_no"]
    regex_version_label = norm_data["version_label"]

    # ---- Tang 2: LLM Classification tuy theo domain ----
    if is_mechanical:
        prompt = f"""
    Thuc hien AI Classification cho tai lieu co khi.
    Ten file: {original_filename}
    Noi dung 2 trang dau:
    {text_content[:3000]}
    
    Hệ thống đã trích xuất sơ bộ từ tên file bằng Regex:
    - Base Code đề xuất: {regex_base_code}
    - Version Label đề xuất: {regex_version_label}
    - Version No đề xuất: {regex_version_no}
    
    Dựa vào thông tin trên và nội dung file, hãy phân tích và tra ve MOT JSON object duy nhat voi cac key sau:
    - "base_code": Ma ban ve goc. Uu tien dung Base Code de xuat neu hop ly. Bat buoc phai co.
    - "version_label": Nhan version (VD: v2, r1, rev2). Neu khong co, tra ve chuoi rong "".
    - "version_no": So version kieu nguyen (VD: v2 -> 2). Mac dinh la 1.
    - "variant_code": Nhan bien the (VD: neu file la banveso1.2 thi variant_code la "1.2"). Mac dinh "default".
    - "document_type": "technical_drawing", "bom", hoac "other".
    - "detected_action": Hanh dong de xuat ("new_version", "new_variant", "new_document").
    - "confidence": Do tu tin (tu 0.0 den 1.0).
    - "reason": Giai thich ngan gon ly do phan loai.
    
    Chi tra ve dung JSON. Khong giai thich gi them.
    """
        fallback_doc_type = "technical_drawing"
    else:
        # Prompt cho domain phi co khi (ke_toan, nhan_su, chung)
        prompt = f"""
    Phan loai tai lieu hanh chinh/van phong.
    Domain: {domain}
    Ten file: {original_filename}
    Noi dung 2 trang dau:
    {text_content[:3000]}
    
    Tra ve MOT JSON object duy nhat voi cac key sau:
    - "base_code": Ma hoac ten rut gon cua tai lieu. Bat buoc.
    - "version_label": Nhan version neu co, mac dinh "".
    - "version_no": So version, mac dinh 1.
    - "variant_code": Mac dinh "default".
    - "document_type": Loai tai lieu. Vi du: "invoice", "contract", "payroll", "decision", "report", "form", "generic".
    - "detected_action": "new_document".
    - "confidence": Do tu tin (0.0 - 1.0).
    - "reason": Giai thich ngan gon.
    
    Chi tra ve dung JSON. Khong giai thich gi them.
    """
        fallback_doc_type = "generic"

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
        "security_level": get_default_security(domain),
    }
    
    try:
        resp = cohere_invoke([HumanMessage(content=prompt)])
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
        parsed["security_level"] = get_default_security(domain)

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
