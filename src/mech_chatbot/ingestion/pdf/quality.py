# -*- coding: utf-8 -*-
"""Canonical ingestion quality policy and small PDF helpers."""

import re

from mech_chatbot.config.constants import SHARE_ALL_DEPARTMENT

QUALITY_POLICY_VERSION = "balanced-v1"
QUALITY_READY_THRESHOLD = 85
QUALITY_REVIEW_THRESHOLD = 60
QUALITY_LABELS = {
    "ready_for_review": "Đạt chất lượng trích xuất",
    "needs_review": "Cần rà soát thủ công",
    "blocked": "Không đạt chất lượng trích xuất",
    "file_unprocessable": "File không thể xử lý",
}


def _extracted_pages(report):
    pages = set(report.get("pages_text_extracted", []) or [])
    pages.update(report.get("pages_table_extracted", []) or [])
    pages.update(report.get("pages_vision_success", []) or [])
    return pages


def _reason(code, component, message):
    return {"code": code, "component": component, "message": message}


def evaluate_quality(report, domain="generic"):
    """Return a JSON-serializable explanation of the balanced 85/60 gate."""
    total_pages = max(0, int(report.get("total_pages", 0) or 0))
    total_chunks = max(0, int(report.get("total_chunks", 0) or 0))
    extracted_count = min(total_pages, len(_extracted_pages(report))) if total_pages else 0
    metadata_failures = set(report.get("metadata_llm_failed_pages", []) or [])
    report_status = str(report.get("status", "success") or "success").lower()

    hard_reasons = []
    if total_pages <= 0:
        hard_reasons.append(_reason("no_pages", "extraction", "Không đọc được trang nào từ file."))
    if report_status == "error":
        hard_reasons.append(_reason("pipeline_error", "extraction", "Pipeline xử lý file đã thất bại."))
    if total_pages > 0 and extracted_count <= 0:
        hard_reasons.append(_reason("no_extracted_pages", "extraction", "Không trích xuất thành công trang nào."))
    if total_chunks <= 0:
        hard_reasons.append(_reason("no_chunks", "chunks", "Không tạo được chunk nội dung hợp lệ."))

    coverage_ratio = (extracted_count / total_pages) if total_pages else 0.0
    extraction_score = round(50 * coverage_ratio, 2)
    chunks_score = 20 if total_chunks > 0 else 0
    metadata_success = max(0, total_pages - len(metadata_failures))
    metadata_ratio = (metadata_success / total_pages) if total_pages else 0.0
    metadata_score = 0 if report.get("classification_failed") else round(15 * metadata_ratio, 2)
    normalized_domain = str(domain or "generic").strip().lower()
    has_domain_signal = (
        int(report.get("technical_attributes_count", 0) or 0) > 0
        if normalized_domain == "mechanical"
        else bool(report.get("domain_signal_present", True))
    )
    domain_score = 15 if has_domain_signal else 0
    components = {
        "extraction_coverage": extraction_score,
        "chunks": chunks_score,
        "metadata_classification": metadata_score,
        "domain_signal": domain_score,
    }
    score = round(sum(components.values()), 2)
    reasons = list(hard_reasons)
    if coverage_ratio < 1:
        reasons.append(_reason("partial_extraction", "extraction", f"Trích xuất thành công {extracted_count}/{total_pages} trang."))
    if metadata_score < 15:
        reasons.append(_reason("metadata_incomplete", "metadata_classification", "Metadata hoặc phân loại chưa hoàn chỉnh."))
    if not has_domain_signal:
        reasons.append(_reason("missing_domain_signal", "domain_signal", "Thiếu tín hiệu đặc thù của loại tài liệu."))

    if hard_reasons:
        status, label = "blocked", QUALITY_LABELS["file_unprocessable"]
    elif score >= QUALITY_READY_THRESHOLD:
        status, label = "ready_for_review", QUALITY_LABELS["ready_for_review"]
    elif score >= QUALITY_REVIEW_THRESHOLD:
        status, label = "needs_review", QUALITY_LABELS["needs_review"]
    else:
        status, label = "blocked", QUALITY_LABELS["blocked"]
    return {
        "policy_version": QUALITY_POLICY_VERSION,
        "score": score,
        "status": status,
        "label": label,
        "components": components,
        "reason_codes": [item["code"] for item in reasons],
        "reasons": reasons,
        "hard_blocked": bool(hard_reasons),
    }


def calculate_quality_status(report, domain="generic"):
    """Apply the canonical policy to a report and keep the legacy tuple API."""
    result = evaluate_quality(report, domain)
    report["quality_policy_version"] = result["policy_version"]
    report["quality_components"] = result["components"]
    report["quality_reason_codes"] = result["reason_codes"]
    report["quality_reasons"] = result["reasons"]
    report["quality_label"] = result["label"]
    report["quality_hard_blocked"] = result["hard_blocked"]
    return result["score"], result["status"]


def _quality_mechanical(report):
    return calculate_quality_status(report, "mechanical")


def _quality_generic(report):
    return calculate_quality_status(report, "generic")


_QUALITY_FUNCS = {"quality_mechanical": _quality_mechanical, "quality_generic": _quality_generic}


def has_mechanical_signal(text):
    if not text:
        return False
    patterns = [r"\b\d+\.\d+\.\d+\b", r"\b\d{3}-\d{3}\b", r"±\s*\d+", r"\bSUS\s*\d+\b", r"\bSS400\b", r"\bSPCC\b", r"\bInox\b", r"\b\d+(?:\.\d+)?\s[xX×]\s\d+", r"Ø\s*\d+", r"\bR\s*\d+"]
    return any(re.search(pattern, text, re.IGNORECASE) for pattern in patterns)


def _normalize_phong_ban_quyen(thu_muc, phong_ban_override=None):
    result = []

    def _add(value):
        if value is None:
            return
        if isinstance(value, (list, tuple, set)):
            for item in value:
                _add(item)
            return
        for part in str(value).split(","):
            part = part.strip()
            if part and part not in result:
                result.append(part)

    _add(thu_muc)
    _add(phong_ban_override)
    return result or [SHARE_ALL_DEPARTMENT]


__all__ = ["QUALITY_POLICY_VERSION", "QUALITY_READY_THRESHOLD", "QUALITY_REVIEW_THRESHOLD", "QUALITY_LABELS", "evaluate_quality", "_quality_mechanical", "_quality_generic", "_QUALITY_FUNCS", "calculate_quality_status", "has_mechanical_signal", "_normalize_phong_ban_quyen"]
