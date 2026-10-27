import pytest

from mech_chatbot.rag.evidence_gate import (
    find_unsupported_numbers,
    has_unsupported_numbers,
    heuristic_missing_evidence_reason,
)


pytestmark = pytest.mark.unit


@pytest.mark.parametrize("answer,expected", [
    ("Có 999 nhân viên.", ["999"]),
    ("Không phải 999, đúng là 120 nhân viên.", []),
    ("Không phải 999 piece, đúng là 120 piece.", []),
    ("Ngưỡng là 120 piece, không phải 999 piece.", []),
    ("Không. Theo **DEMO-WH-001**, ngưỡng tồn tối thiểu là **120 piece**, không phải **999 piece**.  \n[Nguồn: warehouse_demo_process_v1.md, Trang 1, Version 1, SourceID D20P1]", []),
    ("Không phải 999 USD, đúng là 120 piece.", ["999"]),
    ("Có 120 piece. Bộ phận khác không phải 999 piece.", ["999"]),
    ("Không phải 999, đúng là 120. [Nguồn: D41P4, Version 2]", []),
    ("Not 999, but 120 employees.", []),
    ("Không có tài liệu khác. Có 999 nhân viên.", ["999"]),
    ("Không phải 999, đúng là 888 nhân viên.", ["999", "888"]),
    ("Không phải 999. Có 120 nhân viên.", ["999"]),
    ("Không phải 999, đúng là 120. Bộ phận khác có 999.", ["999"]),
])
def test_question_numbers_are_only_allowed_in_a_direct_supported_correction(answer, expected):
    violations = find_unsupported_numbers(
        answer, "Số nhân viên là 120. Version 1.", "Có phải 999 nhân viên không?", strict_mode=True,
    )
    assert [item.normalized for item in violations] == expected


def test_direct_correction_cannot_introduce_a_new_rejected_value():
    violations = find_unsupported_numbers(
        "Không phải 555, đúng là 120.", "Có 120 nhân viên.", "Có bao nhiêu người?", strict_mode=True,
    )
    assert [item.normalized for item in violations] == ["555"]


def test_citations_and_verified_computation_values_remain_allowed():
    assert find_unsupported_numbers(
        "Tổng là 5. [Nguồn: D41P4, Version 2]", "<verified_computation>5</verified_computation>",
        "", strict_mode=True,
    ) == []


@pytest.mark.parametrize("source,answer", [("1,500", "1500"), ("1.500", "1500"), ("12,50", "12.5")])
def test_number_grounding_accepts_equivalent_formatting(source, answer):
    assert find_unsupported_numbers(answer, source, "Chi phí bao nhiêu?", strict_mode=True) == []
    assert has_unsupported_numbers(answer, source, "Chi phí bao nhiêu?", strict_mode=True) is False


def test_number_grounding_returns_positioned_violations():
    violations = find_unsupported_numbers(
        "Chi phí là 2500 USD.",
        "Chi phí được duyệt là 1500 USD.",
        "Chi phí bao nhiêu?",
        strict_mode=True,
    )

    assert [(item.raw, item.normalized, item.start, item.end) for item in violations] == [
        ("2500", "2500", 11, 15)
    ]


def test_number_grounding_does_not_exempt_small_derived_values():
    violations = find_unsupported_numbers(
        "Tong BOM la 5.",
        "Dong A co 2 va dong B co 3.",
        "Tong BOM la bao nhieu?",
        strict_mode=True,
    )

    assert [item.normalized for item in violations] == ["5"]


def test_cost_heuristic_does_not_treat_gia_tri_as_a_cost_question():
    assert heuristic_missing_evidence_reason(
        "Gia tri dinh muc CRAG-EVAL-NUM-001 la bao nhieu?",
        "Gia tri dinh muc CRAG-EVAL-NUM-001 la 1,500.",
    ) is None


def test_cost_heuristic_still_requires_direct_cost_evidence():
    assert heuristic_missing_evidence_reason(
        "Gia CRAG-EVAL-PART-C la bao nhieu?",
        "Tai lieu chi mo ta quy trinh lap rap.",
    ) == "tai lieu khong ghi chi phi/don gia/gia thanh"


@pytest.mark.parametrize("question,context", [
    ("Đánh giá thử việc thực hiện trước ngày nào?", "Đánh giá thử việc trước ngày thứ 55."),
    ("Nhà cung cấp được đánh giá lại bao lâu một lần?", "Đánh giá lại mỗi 12 tháng."),
    ("Đánh giá nội bộ được thực hiện bao lâu một lần?", "Đánh giá nội bộ mỗi 6 tháng."),
    ("Chu kỳ đánh giá là bao lâu?", "Chu kỳ đánh giá mỗi năm."),
    ("Thời gian tham gia đào tạo là bao lâu?", "Đào tạo trong 2 tuần."),
    ("Thời gian gia công là bao lâu?", "Gia công trong 35 giây."),
    ("Kiểm tra định kỳ bao lâu một lần?", "Kiểm tra mỗi quý."),
    ("Kiểm tra định kỳ bao lâu một lần?", "Kiểm tra vào thứ sáu hằng tuần."),
])
def test_non_cost_evaluation_and_calendar_questions_accept_direct_evidence(question, context):
    assert heuristic_missing_evidence_reason(question, context) is None


@pytest.mark.parametrize("question,context,reason", [
    ("Chi phí đánh giá là bao nhiêu?", "Đánh giá mỗi 6 tháng.", "chi phi/don gia/gia thanh"),
    ("Giá bán của dịch vụ đánh giá là bao nhiêu?", "Đánh giá mỗi 6 tháng.", "chi phi/don gia/gia thanh"),
    ("Đánh giá nội bộ bao lâu một lần?", "Tài liệu chỉ nêu người phụ trách.", "thoi gian gia cong/nang suat/dinh muc san xuat"),
])
def test_evaluation_terms_do_not_exempt_missing_cost_or_time_evidence(question, context, reason):
    assert heuristic_missing_evidence_reason(question, context) == "tai lieu khong ghi " + reason


def test_document_supersedes_question_is_not_treated_as_material_substitution():
    assert heuristic_missing_evidence_reason(
        "GRAPH-EVAL-ASM-001 phiên bản 2 thay thế tài liệu nào?",
        "Quan he duoc duyet: Assembly v2 --SUPERSEDES--> Assembly v1",
    ) is None


def test_material_substitution_still_requires_direct_evidence():
    assert heuristic_missing_evidence_reason(
        "Vật liệu steel thay thế bằng vật liệu nào?",
        "Tài liệu chỉ ghi kích thước chi tiết.",
    ) == "tai lieu khong ghi thong tin vat lieu thay the/tuong duong"


@pytest.mark.parametrize("question", [
    "Nhóm tài liệu nào thay thế bản cũ?",
    "Động cơ nào thay thế phiên bản cũ?",
])
def test_document_terms_do_not_trigger_material_substitution(question):
    assert heuristic_missing_evidence_reason(
        question,
        "Quan he duoc duyet: v2 --SUPERSEDES--> v1",
    ) is None
