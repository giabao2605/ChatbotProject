# -*- coding: utf-8 -*-
"""Auto-split tu ingestion/pdf_processor.py (P1.3). Giu nguyen logic goc; chi tach file + import."""

import re
from mech_chatbot.config.constants import SHARE_ALL_DEPARTMENT


def _quality_mechanical(report):
    """Tinh diem chat luong cho tai lieu co khi (logic cu, giu nguyen)."""
    total_pages = report.get("total_pages", 0)
    failed_pages = report.get("failed_pages", [])
    chunks = report.get("total_chunks", 0)
    attrs = report.get("technical_attributes_count", 0)
    if total_pages <= 0:
        return 0, "blocked"
    if failed_pages:
        return 0, "blocked"
    if chunks <= 0:
        return 0, "blocked"
    score = 100
    if attrs == 0:
        score -= 30
    if len(report.get("pages_text_extracted", [])) == 0 and len(report.get("pages_local_ocr_success", [])) == 0 and len(report.get("pages_gemini_success", [])) == 0:
        score -= 40
    if report.get("vision_failed_pages"):
        score -= 30
    if score >= 90:
        return score, "ready_for_review"
    elif score >= 70:
        return score, "needs_review"
    else:
        return score, "blocked"


def _quality_generic(report):
    """Tinh diem chat luong cho tai lieu phi co khi (ke_toan, nhan_su, chung).
    KHONG phat khi thieu thuoc tinh ky thuat (attrs == 0).
    """
    total_pages = report.get("total_pages", 0)
    failed_pages = report.get("failed_pages", [])
    chunks = report.get("total_chunks", 0)
    if total_pages <= 0 or chunks <= 0:
        return 0, "blocked"
    if failed_pages:
        return 0, "blocked"
    score = 100
    # Chi phat khi KHONG trich duoc text nao
    if (len(report.get("pages_text_extracted", [])) == 0
        and len(report.get("pages_local_ocr_success", [])) == 0
        and len(report.get("pages_gemini_success", [])) == 0):
        score -= 50
    if score >= 90:
        return score, "ready_for_review"
    elif score >= 70:
        return score, "needs_review"
    else:
        return score, "blocked"


_QUALITY_FUNCS = {
    'quality_mechanical': _quality_mechanical,
    'quality_generic': _quality_generic,
}


def calculate_quality_status(report, domain='generic'):
    """Tinh diem chat luong theo domain.
    
    Args:
        report: dict bao cao ingest
        domain: domain key ('mechanical', 'tabular', 'generic')
    """
    from mech_chatbot.ingestion.domain_handlers import get_handler
    return get_handler(domain).quality(report)


def has_mechanical_signal(text):
    if not text:
        return False
    patterns = [
        r"\b\d+\.\d+\.\d+\b",             # mã dạng 9.3.03951
        r"\b\d{3}-\d{3}\b",               # mã dạng 975-123
        r"±\s*\d+",                       # dung sai
        r"\bSUS\s*\d+\b",
        r"\bSS400\b",
        r"\bSPCC\b",
        r"\bInox\b",
        r"\b\d+(?:\.\d+)?\s[xX×]\s\d+",   # kích thước 10x604
        r"Ø\s*\d+",
        r"\bR\s*\d+",
    ]
    return any(re.search(p, text, re.IGNORECASE) for p in patterns)


def _normalize_phong_ban_quyen(thu_muc, phong_ban_override=None):
    # GD5 muc 4: phong_ban_quyen la DANH SACH phong ban duoc quyen doc tai lieu.
    # Luon co thu_muc (phong chu / folder) dau tien, roi cac phong chia se them.
    # phong_ban_override: list hoac chuoi ngan cach bang dau phay; deo trung lap.
    result = []

    def _add(v):
        if v is None:
            return
        if isinstance(v, (list, tuple, set)):
            for x in v:
                _add(x)
            return
        for part in str(v).split(","):
            p = part.strip()
            if p and p not in result:
                result.append(p)

    _add(thu_muc)
    _add(phong_ban_override)
    if not result:
        result = [SHARE_ALL_DEPARTMENT]
    return result

__all__ = [
    '_quality_mechanical',
    '_quality_generic',
    '_QUALITY_FUNCS',
    'calculate_quality_status',
    'has_mechanical_signal',
    '_normalize_phong_ban_quyen',
]
