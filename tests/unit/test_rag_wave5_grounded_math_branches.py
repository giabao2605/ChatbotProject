from decimal import Decimal
from types import SimpleNamespace

import pytest

from mech_chatbot.rag.grounded_math import (
    CalculationPlan,
    GroundedFact,
    derive_claim,
    make_calculation_provenance,
    render_grounded_calculation_answer,
    select_grounded_answer_citation_documents,
    select_grounded_bom_document_ids,
    solve_grounded_calculation,
    validate_grounded_calculation_answer,
)


pytestmark = pytest.mark.unit


def _fact(
    value,
    unit="kg",
    source_id="ROW-1",
    *,
    label="A",
    page=3,
):
    return GroundedFact(
        value=Decimal(value),
        unit=unit,
        doc_id=41,
        page=page,
        version=12,
        source_id=source_id,
        label=label,
    )


def _document(provenance, *, page=3, file_name="bom.pdf"):
    return SimpleNamespace(
        metadata={
            "doc_id": 41,
            "trang_so": page,
            "version_no": 12,
            "file_goc": file_name,
            "calculation_provenance": provenance,
        }
    )


def test_bom_document_selection_ignores_invalid_ids_and_fails_closed_on_a_tie():
    only_valid = [
        SimpleNamespace(metadata={"doc_id": "invalid"}, page_content="ignored"),
        SimpleNamespace(metadata={"doc_id": "41"}, page_content="pump alpha"),
    ]
    tied = [
        SimpleNamespace(metadata={"doc_id": 41}, page_content="pump alpha"),
        SimpleNamespace(metadata={"doc_id": 42}, page_content="pump alpha"),
    ]

    assert select_grounded_bom_document_ids(only_valid) == [41]
    assert select_grounded_bom_document_ids(tied, "pump alpha") == []


def test_answer_citation_selection_ignores_unanswerable_and_malformed_branches():
    calculation = SimpleNamespace(
        metadata={"doc_id": 41, "calculation_provenance": {"status": "valid"}}
    )
    unrelated = SimpleNamespace(metadata={"doc_id": 42, "trang_so": 2})
    branches = [
        {"outcome": "no_answer", "citations": [{"doc_id": 42, "page": 2}]},
        {"outcome": "partial_answer", "citations": [{"doc_id": "bad"}]},
    ]

    assert select_grounded_answer_citation_documents(
        [calculation, unrelated], branches
    ) == [calculation]


@pytest.mark.parametrize(
    ("operation", "facts", "status"),
    [
        ("subtract", (_fact("2"),), "missing_operand"),
        (
            "subtract",
            (_fact("2", "kg"), _fact("1", "m", "ROW-2", label="B")),
            "ambiguous_unit",
        ),
        (
            "ratio",
            (_fact("2"),),
            "missing_operand",
        ),
        (
            "ratio",
            (_fact("2", "kg"), _fact("1", "m", "ROW-2", label="B")),
            "ambiguous_unit",
        ),
        (
            "ratio",
            (_fact("2"), _fact("0", source_id="ROW-2", label="B")),
            "division_by_zero",
        ),
        ("multiply", (_fact("2"),), "missing_operand"),
    ],
)
def test_derived_claim_rejects_incomplete_or_dimensionally_unsafe_plans(
    operation, facts, status
):
    assert derive_claim(CalculationPlan(operation, facts)).status == status


def test_subtraction_and_reciprocal_division_preserve_the_requested_unit():
    subtraction = derive_claim(
        CalculationPlan(
            "subtract",
            (_fact("5"), _fact("2", source_id="ROW-2", label="B")),
        )
    )
    reciprocal = derive_claim(
        CalculationPlan(
            "divide",
            (
                _fact("6", "", label="A"),
                _fact("2", "piece", "ROW-2", label="B"),
            ),
        )
    )

    assert (subtraction.value, subtraction.formula) == (
        Decimal("3"),
        "5 - 2 = 3 kg",
    )
    assert (reciprocal.value, reciprocal.unit) == (Decimal("3"), "1/piece")


def test_invalid_decimal_arithmetic_fails_closed():
    claim = derive_claim(
        CalculationPlan(
            "sum",
            (_fact("sNaN"), _fact("1", source_id="ROW-2", label="B")),
        )
    )

    assert claim.status == "invalid_number"


def test_solver_reports_that_no_calculation_was_requested():
    result = solve_grounded_calculation("Cho tôi thông tin tài liệu", (_fact("1"),))

    assert result.status == "plan_missing"
    assert result.plan is None
    assert result.claim is None


@pytest.mark.parametrize(
    "sources",
    [
        "not-a-list",
        [{"doc_id": 41}],
        [{"doc_id": 41}, "not-a-source"],
    ],
)
def test_renderer_rejects_malformed_serialized_provenance(sources):
    provenance = {
        "operation": "sum",
        "status": "valid",
        "sources": sources,
    }
    documents = [_document(provenance)]

    assert render_grounded_calculation_answer(documents) is None
    assert validate_grounded_calculation_answer("anything", documents) == (
        "calculation_provenance"
    )


def test_empty_source_provenance_uses_the_document_citation_fallback():
    plan = CalculationPlan("sum", ())
    provenance = make_calculation_provenance(plan, derive_claim(plan))
    documents = [_document(provenance)]

    answer = render_grounded_calculation_answer(documents, language="en")

    assert answer == (
        "Partial answer: BOM rows were found, but the calculation cannot be "
        "completed because an operand is missing. "
        "[Source: bom.pdf, Page 3, Version 12, SourceID D41P3]"
    )
    assert validate_grounded_calculation_answer(answer, documents) is None
    assert validate_grounded_calculation_answer(
        "[Source: bom.pdf, Page 3, Version 12, SourceID D41P3]", documents
    ) == "partial_or_citation"


def test_grounded_answer_requires_complete_citation_metadata():
    plan = CalculationPlan(
        "sum",
        (_fact("2"), _fact("3", source_id="ROW-2", label="B")),
    )
    provenance = make_calculation_provenance(plan, derive_claim(plan))
    missing_file = _document(provenance, file_name="")

    assert render_grounded_calculation_answer([missing_file]) is None
    assert validate_grounded_calculation_answer(
        "Kết quả tính có kiểm soát: 5 kg;", [missing_file]
    ) == "citation_provenance"


def test_validation_without_calculation_evidence_is_a_noop():
    assert validate_grounded_calculation_answer("ordinary answer", []) is None
