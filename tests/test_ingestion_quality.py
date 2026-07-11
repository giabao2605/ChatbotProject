import pytest

from mech_chatbot.ingestion.domain_handlers import quality_generic, quality_mechanical
from mech_chatbot.ingestion.pdf.quality import QUALITY_POLICY_VERSION, calculate_quality_status, evaluate_quality


def report(*, pages=20, extracted=20, chunks=10, metadata_failed=0, status="success", attrs=0):
    return {
        "status": status,
        "total_pages": pages,
        "total_chunks": chunks,
        "pages_text_extracted": list(range(1, extracted + 1)),
        "pages_table_extracted": [],
        "pages_vision_success": [],
        "failed_pages": list(range(extracted + 1, pages + 1)),
        "metadata_llm_failed_pages": list(range(1, metadata_failed + 1)),
        "technical_attributes_count": attrs,
    }


@pytest.mark.parametrize(("extracted", "expected_score", "expected_status"), [
    (18, 59, "blocked"),
    (20, 60, "needs_review"),
    (68, 84, "needs_review"),
    (70, 85, "ready_for_review"),
])
def test_balanced_threshold_boundaries(extracted, expected_score, expected_status):
    result = evaluate_quality(report(pages=100, extracted=extracted), "generic")
    assert result["score"] == expected_score
    assert result["status"] == expected_status


def test_partial_page_failure_is_scored_instead_of_hard_blocked():
    result = evaluate_quality(report(pages=10, extracted=8), "generic")
    assert result["score"] == 90
    assert result["status"] == "ready_for_review"
    assert result["hard_blocked"] is False
    assert "partial_extraction" in result["reason_codes"]


@pytest.mark.parametrize("broken", [
    report(pages=0, extracted=0, chunks=0),
    report(pages=2, extracted=0, chunks=2),
    report(pages=2, extracted=2, chunks=0),
    report(pages=2, extracted=2, chunks=2, status="error"),
])
def test_hard_blockers_are_file_unprocessable(broken):
    result = evaluate_quality(broken, "generic")
    assert result["status"] == "blocked"
    assert result["hard_blocked"] is True
    assert result["label"] == "File không thể xử lý"


def test_domain_signal_and_report_explanation():
    mechanical = evaluate_quality(report(attrs=0), "mechanical")
    generic = evaluate_quality(report(attrs=0), "generic")
    assert mechanical["components"]["domain_signal"] == 0
    assert generic["components"]["domain_signal"] == 15
    extraction_report = report()
    assert calculate_quality_status(extraction_report, "generic") == quality_generic(report())
    assert quality_mechanical(report(attrs=1)) == (100, "ready_for_review")
    assert extraction_report["quality_policy_version"] == QUALITY_POLICY_VERSION
    assert extraction_report["quality_label"] == "Đạt chất lượng trích xuất"
    assert set(extraction_report["quality_components"]) == {"extraction_coverage", "chunks", "metadata_classification", "domain_signal"}
