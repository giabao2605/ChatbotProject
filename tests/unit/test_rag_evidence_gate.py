from mech_chatbot.rag.evidence_gate import heuristic_missing_evidence_reason


def test_english_ocr_time_units_count_as_time_evidence():
    context = (
        "Inspection torque: 8 N.m. Hold time: 14 minutes. "
        "Repeat interval: 18 days."
    )

    assert heuristic_missing_evidence_reason(
        "Trong qa-scan.pdf mã QA-4827, mô-men, thời gian giữ tải và chu kỳ kiểm tra là bao nhiêu?",
        context,
    ) is None
