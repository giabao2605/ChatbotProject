"""
Generic extractor — trich xuat metadata cho tai lieu PHI CO KHI
(ke_toan, nhan_su, chung...). Khong ep schema co khi; chi bat cac truong
hanh chinh pho bien bang regex nhe, luu vao bang DocumentAttributes.

Duoc goi boi pipeline khi domain.extractor != 'mechanical'.
"""
import re

# Cac mau regex pho bien cho tai lieu hanh chinh / ke toan / nhan su.
# Co tinh "best-effort": khong match duoc cung khong sao (quality_generic
# khong tru diem khi thieu thuoc tinh).
_PATTERNS = {
    "so_hop_dong":   r"(?:so\s*h[\u01a1o]p\s*[\u0111d][\u1ed3o]ng|hop dong so|contract no\.?)\s*[:#]?\s*([A-Za-z0-9\-/\.]+)",
    "so_quyet_dinh": r"(?:quy[\u1ebfe]t\s*[\u0111d][i\u1ecb]nh\s*s[\u1ed1o]|qd\s*so)\s*[:#]?\s*([A-Za-z0-9\-/\.]+)",
    "so_hoa_don":    r"(?:h[\u00f3o]a\s*[\u0111d][\u01a1o]n\s*s[\u1ed1o]|invoice no\.?)\s*[:#]?\s*([A-Za-z0-9\-/\.]+)",
    "ma_nhan_vien":  r"(?:m[\u00e3a]\s*nh[\u00e2a]n\s*vi[\u00eae]n|employee id|ma nv)\s*[:#]?\s*([A-Za-z0-9\-]+)",
    "ngay":          r"\b(\d{1,2}[/-]\d{1,2}[/-]\d{2,4})\b",
    "so_tien":       r"([0-9][0-9\.,]{3,})\s*(?:vnd|\u0111|dong|usd)",
}


def extract_generic_attributes(text, domain=None):
    """Tra ve list dict {key, value, confidence, extracted_by} tu text.

    Args:
        text: noi dung van ban da gop (text layer + tom tat).
        domain: domain key (chi de log/ghi chu, khong bat buoc).
    """
    if not text:
        return []
    out = []
    seen = set()
    for key, pat in _PATTERNS.items():
        try:
            matches = re.finditer(pat, text, re.IGNORECASE)
        except re.error:
            continue
        for m in matches:
            val = (m.group(1) or "").strip()
            if not val:
                continue
            dedup = (key, val.lower())
            if dedup in seen:
                continue
            seen.add(dedup)
            out.append({
                "key": key,
                "value": val[:500],
                "confidence": 0.6,
                "extracted_by": "regex",
            })
    return out
