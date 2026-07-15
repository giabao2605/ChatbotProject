from __future__ import annotations

from copy import deepcopy

import pytest

from mech_chatbot.evaluation.grounded_math import evaluate_grounded_calculation
from mech_chatbot.evaluation.schema import (
    CURRENT_MANIFEST_SCHEMA,
    validate_manifest_ground_truth,
)


pytestmark = pytest.mark.unit


def expected_calculation(status="valid"):
    return {
        "operation": "sum",
        "status": status,
        "exact_value": "5" if status == "valid" else None,
        "display_value": "5" if status == "valid" else "",
        "formula": "2 + 3 = 5 kg" if status == "valid" else "",
        "unit": "kg" if status == "valid" else "",
        "sources": [
            {"doc_id": 41, "page": 3, "version": 12, "source_id": "BOM-101", "value": "2", "unit": "kg"},
            {"doc_id": 41, "page": 3, "version": 12, "source_id": "BOM-102", "value": "3", "unit": "kg"},
        ],
        "allowed_numbers": [] if status == "valid" else ["0"],
    }


def actual_calculation(status="valid"):
    value = expected_calculation(status)
    value["source_ids"] = [source["source_id"] for source in value["sources"]]
    value["approximate"] = False
    return value


def test_exact_grounded_calculation_checks_decimal_formula_unit_and_row_provenance():
    report = evaluate_grounded_calculation(
        expected_calculation(),
        [actual_calculation()],
        answer=(
            "Kết quả tính có kiểm soát: 5 kg. Công thức: 2 + 3 = 5 kg. "
            "[Nguồn: bom.md, Trang 3, Version 12, SourceID D41P3]"
        ),
    )

    assert report["passed"] is True
    assert report["calculation_count"] == 1
    assert report["checks"] == {
        "single_plan": True,
        "status": True,
        "operation": True,
        "exact_decimal": True,
        "display_value": True,
        "formula": True,
        "unit": True,
        "provenance": True,
        "unsupported_numbers_zero": True,
    }


@pytest.mark.parametrize(
    ("field", "bad_value", "failed_check"),
    [
        ("exact_value", "5.1", "exact_decimal"),
        ("formula", "2 + 4 = 6 kg", "formula"),
        ("unit", "m", "unit"),
        ("sources", [{"doc_id": 41, "page": 4, "version": 12, "source_id": "BOM-101", "value": "2", "unit": "kg"}], "provenance"),
    ],
)
def test_grounded_calculation_evaluator_fails_each_exact_contract(field, bad_value, failed_check):
    actual = actual_calculation()
    actual[field] = bad_value

    report = evaluate_grounded_calculation(
        expected_calculation(), [actual], answer="2 + 3 = 5 kg"
    )

    assert report["passed"] is False
    assert report["checks"][failed_check] is False


def test_grounded_calculation_evaluator_accepts_expected_partial_status_without_a_result():
    expected = expected_calculation("division_by_zero")
    actual = actual_calculation("division_by_zero")

    report = evaluate_grounded_calculation(
        expected,
        [actual],
        answer=(
            "Tôi trả lời được một phần: không thể tính vì mẫu số bằng 0. "
            "[Nguồn: bom.md, Trang 3, Version 12, SourceID D41P3]"
        ),
    )

    assert report["passed"] is True
    assert report["checks"]["exact_decimal"] is True


def test_grounded_calculation_evaluator_allows_operand_value_in_partial_reason():
    expected = expected_calculation("division_by_zero")
    expected["allowed_numbers"] = []
    expected["sources"][1]["value"] = "0"
    actual = actual_calculation("division_by_zero")
    actual["sources"][1]["value"] = "0"

    report = evaluate_grounded_calculation(
        expected, [actual], answer="Không thể tính vì mẫu số bằng 0."
    )

    assert report["passed"] is True
    assert report["unsupported_numbers"] == []


def test_grounded_calculation_evaluator_rejects_numbers_not_in_expected_formula_or_sources():
    report = evaluate_grounded_calculation(
        expected_calculation(),
        [actual_calculation()],
        answer="Kết quả là 6 kg. Công thức 2 + 3 = 5 kg. Trang 3 Version 12 DocID 41.",
    )

    assert report["passed"] is False
    assert report["unsupported_numbers"] == ["6"]


def test_grounded_math_manifest_requires_a_complete_expected_calculation_contract():
    case = {
        "manifest_schema": CURRENT_MANIFEST_SCHEMA,
        "evaluation_group": "grounded_math",
        "expected_claims": [{
            "id": "claim-1",
            "required_terms": ["5 kg"],
            "allowed_source_ids": ["D41P3"],
        }],
        "expected_citations": [{
            "document": "bom.md",
            "doc_id": 41,
            "page": 3,
            "version": 12,
            "source_id": "D41P3",
        }],
    }

    with pytest.raises(ValueError, match="expected_calculation"):
        validate_manifest_ground_truth(case, expected_outcome="full_answer")

    case["expected_calculation"] = expected_calculation()
    validate_manifest_ground_truth(case, expected_outcome="full_answer")

    without_citations = deepcopy(case)
    without_citations["expected_citations"] = []
    with pytest.raises(ValueError, match="expected_citations"):
        validate_manifest_ground_truth(without_citations, expected_outcome="full_answer")

    broken = deepcopy(case)
    broken["expected_calculation"]["sources"][0].pop("source_id")
    with pytest.raises(ValueError, match="calculation source"):
        validate_manifest_ground_truth(broken, expected_outcome="full_answer")
