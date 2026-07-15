import pytest

from mech_chatbot.rag.evidence_gate import (
    find_unsupported_numbers,
    has_unsupported_numbers,
    heuristic_missing_evidence_reason,
)


pytestmark = pytest.mark.unit


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
