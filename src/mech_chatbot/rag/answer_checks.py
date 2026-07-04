"""Refactor (GD4): cac ham KIEM TRA CAU TRA LOI (anti-hallucination) thuan tuy,
tach khoi rag/service.py de giam kich thuoc file + de unit test rieng.

NGUYEN TAC: COPY NGUYEN VAN (byte-for-byte, trich bang ast) tu service.py -> KHONG doi logic.
Chi phu thuoc stdlib (re, json) + lazy import material_registry -> KHONG the gay circular import.
service.py re-import cac ten nay nen moi cho goi cu + tests van chay.
"""
import json
import re


def _safe_json_loads(raw):
    raw = str(raw or "").strip().replace("```json", "").replace("```", "").strip()
    try:
        return json.loads(raw)
    except Exception:
        m = re.search(r"\{.*\}", raw, flags=re.DOTALL)
        if m:
            try:
                return json.loads(m.group(0))
            except Exception:
                return None
    return None


def _extract_numbers(text):
    nums = re.findall(r"(?<![\w.])\d+(?:[\.,]\d+)?(?![\w.])", str(text or ""))
    return {n.replace(",", ".") for n in nums}


def extract_units_and_symbols(text):
    text = str(text or "")
    patterns = [
        r"±\s*\d+(?:[\.,]\d+)?",
        r"Ø\s*\d+(?:[\.,]\d+)?",
        r"\bR\s*\d+(?:[\.,]\d+)?\b",
        r"\bM\d+(?:x\d+)?\b",
        r"\b\d+(?:[\.,]\d+)?\s*mm\b",
        r"\b\d+(?:[\.,]\d+)?\s*kg\b",
        r"\bASTM[-\w]*\b",
        r"\bJIS[-\w]*\b",
    ]

    found = set()
    for p in patterns:
        for m in re.findall(p, text, re.IGNORECASE):
            found.add(str(m).upper().replace(" ", ""))

    return found


def has_unsupported_units_symbols(answer, context_text, question):
    answer_items = extract_units_and_symbols(answer)
    allowed_items = (
        extract_units_and_symbols(context_text)
        | extract_units_and_symbols(question)
    )

    unsupported = answer_items - allowed_items

    return bool(unsupported), list(unsupported)


KNOWN_MATERIALS = [
    "SUS304", "SUS316", "SS400", "SPCC", "AL6061", "A5052", 
    "S45C", "SKD11", "SKD61"
]


def _known_materials():
    try:
        from mech_chatbot.ingestion.material_registry import get_known_materials
        mats = get_known_materials()
        if mats:
            return mats
    except Exception:
        pass
    return KNOWN_MATERIALS


def extract_known_materials(text):
    text_upper = str(text or "").upper().replace(" ", "")
    found = set()
    for mat in _known_materials():
        if mat.upper().replace(" ", "") in text_upper:
            found.add(mat.upper())
    return found


def has_unsupported_materials(answer, context_text):
    answer_mats = extract_known_materials(answer)
    context_mats = extract_known_materials(context_text)
    unsupported = answer_mats - context_mats
    return bool(unsupported), list(unsupported)


def extract_codes(text):
    patterns = [
        r"\b\d+\.\d+\.\d+\b",
        r"\b\d{3}-\d{3}\b",
        r"\b[A-Z]{2,}[A-Z0-9-]*\d+[A-Z0-9-]*\b",
    ]
    codes = []
    for p in patterns:
        codes.extend(re.findall(p, str(text or ""), re.IGNORECASE))
    return set(c.upper() for c in codes)


def has_unsupported_codes(answer, context_text, question):
    answer_codes = extract_codes(answer)
    allowed_codes = extract_codes(context_text) | extract_codes(question)
    unsupported = answer_codes - allowed_codes
    return bool(unsupported), list(unsupported)


def requires_source_citation(question):
    q = str(question or "").lower()

    chitchat_keywords = [
        "xin chào", "chào", "hello", "hi", "cảm ơn", "thank"
    ]

    if any(k in q for k in chitchat_keywords):
        return False

    return True


def has_required_source_citation(answer, require_version=True):
    """
    Kiểm tra câu trả lời có nguồn rõ ràng không.

    Yêu cầu tối thiểu:
    - Có Nguồn/Source
    - Có Trang/Page
    - Nếu require_version=True thì phải có Version/ver/v
    """
    if not answer:
        return False

    text = str(answer)

    has_source = bool(
        re.search(r"(Ngu[oồ]n|Source)\s*:", text, re.IGNORECASE)
    )

    has_page = bool(
        re.search(
            r"(trang|page)\s*(số|#|:)?\s*\d+",
            text,
            re.IGNORECASE
        )
    )

    if not require_version:
        return has_source and has_page

    has_version = bool(
        re.search(
            r"(version|ver|v)\s*[:#-]?\s*(\d+|khong ro|không rõ|unknown|n/?a)",
            text,
            re.IGNORECASE
        )
    )

    return has_source and has_page and has_version
