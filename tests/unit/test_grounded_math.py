from decimal import Decimal
from types import SimpleNamespace

import pytest

from mech_chatbot.rag.grounded_math import (
    CalculationPlan,
    GroundedFact,
    build_calculation_plan,
    derive_claim,
    make_calculation_provenance,
    render_grounded_calculation_answer,
    validate_grounded_calculation_answer,
)


pytestmark = pytest.mark.unit


def fact(value, unit="kg", source_id="BOM-1", version=12, doc_id=41, label="", page=1):
    return GroundedFact(
        value=Decimal(value),
        unit=unit,
        doc_id=doc_id,
        page=page,
        version=version,
        source_id=source_id,
        label=label,
    )


def test_calculation_planner_selects_operation_and_ordered_bom_operands():
    facts = (
        fact("2", "cái", "BOM-1", label="PART-A"),
        fact("5", "cái", "BOM-2", label="PART-B"),
    )

    total = build_calculation_plan("Tổng BOM là bao nhiêu?", facts)
    grand_total = build_calculation_plan("Tổng cộng BOM là bao nhiêu?", facts)
    difference = build_calculation_plan("Lấy PART-B trừ PART-A", facts)

    assert total == CalculationPlan("sum", facts)
    assert grand_total == CalculationPlan("sum", facts)
    assert difference == CalculationPlan("subtract", (facts[1], facts[0]))


@pytest.mark.parametrize(
    ("question", "operation", "source_ids"),
    [
        ("Cộng PART-A và PART-B", "add", ("BOM-1", "BOM-2")),
        ("Tỷ lệ PART-A so với PART-B", "ratio", ("BOM-1", "BOM-2")),
        ("Tỉ lệ PART-A so với PART-B", "ratio", ("BOM-1", "BOM-2")),
        ("PART-A chiếm bao nhiêu phần trăm PART-B?", "percent", ("BOM-1", "BOM-2")),
        ("Lấy PART-A nhân FACTOR-X", "multiply", ("BOM-1", "BOM-3")),
        ("Lấy PART-B chia FACTOR-X", "divide", ("BOM-2", "BOM-3")),
    ],
)
def test_calculation_planner_supports_the_bounded_operation_set(question, operation, source_ids):
    facts = (
        fact("2", "cái", "BOM-1", label="PART-A"),
        fact("5", "cái", "BOM-2", label="PART-B"),
        fact("2", "", "BOM-3", label="FACTOR-X"),
    )

    plan = build_calculation_plan(question, facts)

    assert plan is not None
    assert plan.operation == operation
    assert tuple(item.source_id for item in plan.operands) == source_ids


def test_calculation_planner_keeps_missing_operand_fail_closed():
    plan = build_calculation_plan(
        "Lấy PART-A chia PART-MISSING",
        (fact("2", "cái", "BOM-1", label="PART-A"),),
    )

    assert plan is not None
    assert derive_claim(plan).status == "missing_operand"


def test_addition_requires_at_least_two_grounded_operands():
    claim = derive_claim(CalculationPlan(
        "add",
        (fact("2", "cái", "BOM-1", label="PART-A"),),
    ))

    assert claim.status == "missing_operand"


def test_calculation_planner_matches_complete_bom_labels_not_substrings():
    facts = (
        fact("1", "cái", "BOM-1", label="PART-A"),
        fact("5", "cái", "BOM-2", label="PART-AB"),
        fact("2", "cái", "BOM-3", label="PART-B"),
    )

    plan = build_calculation_plan("Lấy PART-AB trừ PART-B", facts)

    assert tuple(item.source_id for item in plan.operands) == ("BOM-2", "BOM-3")


def test_duplicate_named_operands_fail_as_ambiguous_provenance():
    facts = (
        fact("1", "cái", "BOM-1", label="PART-A"),
        fact("2", "cái", "BOM-2", label="PART-A"),
        fact("3", "cái", "BOM-3", label="PART-B"),
    )
    plan = build_calculation_plan("Cộng PART-A và PART-B", facts)

    claim = derive_claim(plan)

    assert claim.status == "ambiguous_provenance"
    assert claim.provenance == facts


def test_bom_total_deduplicates_source_rows_and_preserves_provenance():
    claim = derive_claim(CalculationPlan("sum", (fact("1.5"), fact("1.5"), fact("2", source_id="BOM-2"))))

    assert claim.status == "valid"
    assert claim.value == Decimal("3.5")
    assert claim.unit == "kg"
    assert claim.source_ids == ("BOM-1", "BOM-2")
    assert claim.formula == "1.5 + 2 = 3.5 kg"


def test_grounded_math_rejects_mixed_units_versions_and_zero_division():
    assert derive_claim(CalculationPlan("sum", (fact("1", "kg"), fact("2", "m", "BOM-2")))).status == "ambiguous_unit"
    assert derive_claim(CalculationPlan("sum", (fact("1"), fact("2", source_id="BOM-2", version=11)))).status == "mixed_version"
    assert derive_claim(CalculationPlan("divide", (fact("1"), fact("0", source_id="BOM-2")))).status == "division_by_zero"


def test_ratio_and_percentage_are_dimensionally_safe():
    ratio = derive_claim(CalculationPlan("ratio", (fact("1"), fact("4", source_id="BOM-2"))))
    percent = derive_claim(CalculationPlan("percent", (fact("1"), fact("3", source_id="BOM-2"))))

    assert ratio.value == Decimal("0.25")
    assert ratio.unit == ""
    assert percent.display_value == "33.3333"
    assert percent.approximate is True
    assert percent.unit == "%"


def test_multiplication_requires_one_dimensionless_operand():
    invalid = derive_claim(CalculationPlan("multiply", (fact("2", "kg"), fact("3", "m", "BOM-2"))))
    valid = derive_claim(CalculationPlan("multiply", (fact("2", "kg"), fact("3", "", "BOM-2"))))

    assert invalid.status == "ambiguous_unit"
    assert valid.value == Decimal("6")
    assert valid.unit == "kg"


def test_plain_division_requires_a_dimensionless_operand():
    invalid = derive_claim(CalculationPlan("divide", (fact("4", "kg"), fact("2", "kg", "BOM-2"))))
    valid = derive_claim(CalculationPlan("divide", (fact("4", "kg"), fact("2", "", "BOM-2"))))

    assert invalid.status == "ambiguous_unit"
    assert valid.value == Decimal("2")
    assert valid.unit == "kg"


def test_percent_formula_records_the_required_multiplication():
    claim = derive_claim(CalculationPlan(
        "percent",
        (fact("1", source_id="BOM-1"), fact("4", source_id="BOM-2")),
    ))

    assert claim.display_value == "25"
    assert claim.unit == "%"
    assert claim.formula == "1 / 4 * 100 = 25 %"


def test_tiny_rounded_result_displays_zero_and_marks_approximation():
    claim = derive_claim(CalculationPlan(
        "divide",
        (
            fact("0.00001", "kg", "BOM-1"),
            fact("2", "", "BOM-2"),
        ),
    ))

    assert claim.value == Decimal("0.000005")
    assert claim.display_value == "0"
    assert claim.approximate is True


def calculation_doc(provenance, *, page=3):
    return SimpleNamespace(metadata={
        "doc_id": 41,
        "trang_so": page,
        "version_no": 12,
        "file_goc": "bom-v12.pdf",
        "calculation_provenance": provenance,
    })


def test_grounded_calculation_renderer_emits_verified_formula_and_citation():
    plan = CalculationPlan(
        "add",
        (
            fact("2", "cái", "BOM-1", label="PART-A", page=3),
            fact("5", "cái", "BOM-2", label="PART-B", page=3),
        ),
    )
    claim = derive_claim(plan)
    docs = [calculation_doc(make_calculation_provenance(plan, claim))]

    answer = render_grounded_calculation_answer(docs, language="vi")

    assert answer == (
        "Kết quả tính có kiểm soát: 7 cái. Công thức: 2 + 5 = 7 cái. "
        "[Nguồn: bom-v12.pdf, Trang 3, Version 12, SourceID D41P3]"
    )
    assert validate_grounded_calculation_answer(answer, docs) is None
    assert validate_grounded_calculation_answer("Kết quả là 7 cái.", docs) == "result_or_unit"
    assert validate_grounded_calculation_answer(
        answer.replace(
            "Kết quả tính có kiểm soát: 7 cái.",
            "Kết quả tính có kiểm soát: 700 kg.",
        ),
        docs,
    ) == "result_or_unit"


@pytest.mark.parametrize(
    ("field", "tampered"),
    [
        ("formula", "2 + 5 = 700 cái"),
        ("display_value", "700"),
        ("unit", "kg"),
        ("exact_value", "700"),
    ],
)
def test_grounded_calculation_recomputes_provenance_before_render(field, tampered):
    plan = CalculationPlan(
        "add",
        (
            fact("2", "cái", "BOM-1", label="PART-A", page=3),
            fact("5", "cái", "BOM-2", label="PART-B", page=3),
        ),
    )
    provenance = make_calculation_provenance(plan, derive_claim(plan))
    provenance[field] = tampered
    docs = [calculation_doc(provenance)]

    assert render_grounded_calculation_answer(docs, language="vi") is None


def test_grounded_calculation_renderer_returns_explicit_partial_answer_for_invalid_claim():
    plan = CalculationPlan(
        "add",
        (
            fact("2", "kg", "BOM-1", label="PART-A", page=3),
            fact("5", "m", "BOM-2", label="PART-B", page=3),
        ),
    )
    docs = [calculation_doc(make_calculation_provenance(plan, derive_claim(plan)))]

    answer = render_grounded_calculation_answer(docs, language="vi")

    assert answer.startswith("Tôi trả lời được một phần:")
    assert "không thể tính" in answer
    assert "đơn vị không tương thích" in answer
    assert "ambiguous_unit" not in answer
    assert "SourceID D41P3" in answer
    assert validate_grounded_calculation_answer(answer, docs) is None


def test_grounded_calculation_requires_citation_for_every_operand_page():
    plan = CalculationPlan(
        "sum",
        (
            fact("2", "cái", "BOM-1", label="PART-A", page=3),
            fact("5", "cái", "BOM-2", label="PART-B", page=4),
        ),
    )
    docs = [
        calculation_doc(make_calculation_provenance(plan, derive_claim(plan)), page=3),
        calculation_doc(None, page=4),
    ]

    answer = render_grounded_calculation_answer(docs, language="vi")

    assert "SourceID D41P3" in answer
    assert "SourceID D41P4" in answer
    assert validate_grounded_calculation_answer(answer, docs) is None
    assert validate_grounded_calculation_answer(
        answer.replace(" [Nguồn: bom-v12.pdf, Trang 4, Version 12, SourceID D41P4]", ""),
        docs,
    ) == "formula_or_citation"
    assert render_grounded_calculation_answer(docs[:1], language="vi") is None
