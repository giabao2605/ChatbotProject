from decimal import Decimal

import pytest

from mech_chatbot.rag.grounded_math import (
    CalculationPlan,
    GroundedFact,
    derive_claim,
)


pytestmark = pytest.mark.unit


def fact(value, unit="kg", source_id="BOM-1", version=12, doc_id=41):
    return GroundedFact(
        value=Decimal(value),
        unit=unit,
        doc_id=doc_id,
        page=1,
        version=version,
        source_id=source_id,
    )


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
