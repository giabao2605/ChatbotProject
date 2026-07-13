"""
Domain Handlers (GD3) — gom TOAN BO hanh vi rieng theo tung "kieu doc" (domain)
vao MOT class cho moi domain. Them domain moi = viet 1 class + dang ky, KHONG
phai sua rai rac trong classifier / pdf_processor / rag.

3 handler hien co: mechanical | tabular | generic.

Moi handler quyet dinh:
  - extractor_kind        : 'mechanical' | 'tabular' | 'generic'
  - attribute_strategy    : 'technical' (luu TaiLieuKyThuat) | 'document' (DocumentAttributes)
  - vision_always         : co LUON day moi trang qua GPT Vision khong (ban ve = True)
  - uses_bom_search       : RAG co tra cuu BOM tu SQL khong (chi co khi)
  - uses_mechanical_guard : RAG co ap guard chong bia vat lieu/ma/dung sai khong
  - quality(report)       : (score, status)
  - build_classify_prompt(...) : (prompt_llm, fallback_doc_type)

Luu y: noi dung system prompt RAG (mechanical vs trung lap) van nam o rag/service.py;
o day chi cung CO (flag) de service chon prompt + bat/tat guard cho dung domain.
"""


# ---------------------------------------------------------------------------
# Quality scoring (chuyen tu pdf_processor sang day de gom ve 1 noi).
# Cac ham nhan 'report' dict va tra ve (score:int, status:str).
# ---------------------------------------------------------------------------
def quality_mechanical(report):
    """Compatibility delegate to the canonical balanced quality policy."""
    from mech_chatbot.ingestion.pdf.quality import calculate_quality_status
    return calculate_quality_status(report, "mechanical")


def quality_generic(report):
    """Compatibility delegate to the canonical balanced quality policy."""
    from mech_chatbot.ingestion.pdf.quality import calculate_quality_status
    return calculate_quality_status(report, "generic")


# ---------------------------------------------------------------------------
# Handlers
# ---------------------------------------------------------------------------
class DomainHandler:
    """Handler MAC DINH = 'generic' (hanh chinh / van ban). Cac domain khac ke thua."""
    key = "generic"
    label = "Hanh chinh / Van ban"
    extractor_kind = "generic"
    attribute_strategy = "document"     # 'technical' | 'document'
    vision_always = False
    uses_bom_search = False
    uses_mechanical_guard = False

    def quality(self, report):
        return quality_generic(report)

    def build_classify_prompt(self, original_filename, text_content,
                              regex_base_code, regex_version_label,
                              regex_version_no, domain, document_types=None):
        """Prompt phan loai cho tai lieu hanh chinh/van phong (tabular + generic)."""
        allowed_types = [str(item).strip().lower() for item in (document_types or []) if str(item).strip()]
        type_instruction = (
            f'Chi chon mot "document_type" trong danh sach: {allowed_types}.'
            if allowed_types else
            'Loai tai lieu vi du: "invoice", "contract", "payroll", "decision", "report", "form", "generic".'
        )
        fallback = "generic" if not allowed_types or "generic" in allowed_types else allowed_types[0]
        prompt = f"""
    Phan loai tai lieu hanh chinh/van phong.
    Domain: {domain}
    Ten file: {original_filename}
    Noi dung trich xuat (nhieu trang dai dien):
    {text_content[:6000]}

    Tra ve MOT JSON object duy nhat voi cac key sau:
    - "base_code": Ma hoac ten rut gon cua tai lieu. Bat buoc.
    - "version_label": Nhan version neu co, mac dinh "".
    - "version_no": So version, mac dinh 1.
    - "variant_code": Mac dinh "default".
    - "document_type": {type_instruction}
    - "detected_action": "new_document".
    - "confidence": Do tu tin (0.0 - 1.0).
    - "reason": Giai thich ngan gon.

    Uu tien phan loai dua tren NOI DUNG tai lieu o tren; ten file chi la goi y phu.
    Chi tra ve dung JSON. Khong giai thich gi them.
    """
        return prompt, fallback


class GenericHandler(DomainHandler):
    key = "generic"
    label = "Hanh chinh / Van ban"


class TabularHandler(DomainHandler):
    """Bang bieu / tai chinh (ke toan, mua hang, kho, sales).
    Dung chung extractor 'tabular' + quality_generic; KHONG ap guard co khi.
    """
    key = "tabular"
    label = "Bang bieu / Tai chinh"
    extractor_kind = "tabular"
    attribute_strategy = "document"
    vision_always = False
    uses_bom_search = False
    uses_mechanical_guard = False


class MechanicalHandler(DomainHandler):
    """Ban ve / BOM co khi. Giu nguyen hanh vi cu (Vision luon bat, guard co khi)."""
    key = "mechanical"
    label = "Co khi / Ky thuat"
    extractor_kind = "mechanical"
    attribute_strategy = "technical"
    vision_always = True
    uses_bom_search = True
    uses_mechanical_guard = True

    def quality(self, report):
        return quality_mechanical(report)

    def build_classify_prompt(self, original_filename, text_content,
                              regex_base_code, regex_version_label,
                              regex_version_no, domain, document_types=None):
        """Prompt phan loai chuyen cho tai lieu co khi (giu nguyen prompt cu)."""
        allowed_types = [str(item).strip().lower() for item in (document_types or []) if str(item).strip()]
        type_instruction = (
            f'Chi chon mot gia tri trong danh sach: {allowed_types}.'
            if allowed_types else
            '"technical_drawing", "bom", hoac "other".'
        )
        fallback = "technical_drawing" if not allowed_types or "technical_drawing" in allowed_types else allowed_types[0]
        prompt = f"""
    Thuc hien AI Classification cho tai lieu co khi.
    Ten file: {original_filename}
    Noi dung trich xuat (nhieu trang dai dien):
    {text_content[:6000]}

    Hệ thống đã trích xuất sơ bộ từ tên file bằng Regex:
    - Base Code đề xuất: {regex_base_code}
    - Version Label đề xuất: {regex_version_label}
    - Version No đề xuất: {regex_version_no}

    Dựa vào thông tin trên và nội dung file, hãy phân tích và tra ve MOT JSON object duy nhat voi cac key sau:
    - "base_code": Ma ban ve goc. Uu tien dung Base Code de xuat neu hop ly. Bat buoc phai co.
    - "version_label": Nhan version (VD: v2, r1, rev2). Neu khong co, tra ve chuoi rong "".
    - "version_no": So version kieu nguyen (VD: v2 -> 2). Mac dinh la 1.
    - "variant_code": Nhan bien the (VD: neu file la banveso1.2 thi variant_code la "1.2"). Mac dinh "default".
    - "document_type": {type_instruction}
    - "detected_action": Hanh dong de xuat ("new_version", "new_variant", "new_document").
    - "confidence": Do tu tin (tu 0.0 den 1.0).
    - "reason": Giai thich ngan gon ly do phan loai.

    Uu tien phan loai dua tren NOI DUNG tai lieu o tren; ten file chi la goi y phu.
    Chi tra ve dung JSON. Khong giai thich gi them.
    """
        return prompt, fallback


_HANDLERS = {
    "mechanical": MechanicalHandler(),
    "tabular": TabularHandler(),
    "generic": GenericHandler(),
}


def get_handler(domain):
    """Tra ve handler cho domain (chuan hoa ca key cu). Fallback 'generic'."""
    from mech_chatbot.ingestion.domain_registry import _normalize_domain_value, DEFAULT_DOMAIN
    key = _normalize_domain_value(domain) or DEFAULT_DOMAIN
    return _HANDLERS.get(key, _HANDLERS[DEFAULT_DOMAIN])


def get_handler_for_department(thu_muc):
    """Tien ich: phong ban / thu muc -> handler (qua domain_registry)."""
    from mech_chatbot.ingestion.domain_registry import resolve_domain_by_department
    return get_handler(resolve_domain_by_department(thu_muc))
