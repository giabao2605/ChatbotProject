"""Worked examples for the shared evaluation foundation in roadmap 2.2."""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path

import pytest

from mech_chatbot.evaluation.grounding import (
    evaluate_citations,
    evaluate_claims,
    extract_claims,
    select_rendered_citations,
)
from mech_chatbot.evaluation.adjudication import resolve_case_reviews
from mech_chatbot.evaluation.risk_coverage import build_risk_coverage_report


pytestmark = pytest.mark.unit


def test_claim_evaluator_separates_precision_recall_and_faithfulness():
    answer = (
        "Giá trị định mức là 1,500. "
        "[Nguồn: numbers.md, Trang 1, Version 12, SourceID D41P1]\n"
        "Chi phí ước tính là 10 USD."
    )
    expected = [
        {
            "id": "nominal-value",
            "required_terms": ["1,500"],
            "allowed_source_ids": ["D41P1"],
        }
    ]

    report = evaluate_claims(
        extract_claims(answer),
        expected,
        accessible_source_ids={"D41P1"},
    )

    assert report["applicable"] is True
    assert report["claim_precision"] == 0.5
    assert report["expected_claim_recall"] == 1.0
    assert report["faithfulness"] == 0.5
    assert report["violations"] == [
        {"claim_index": 2, "reason": "unexpected_claim"}
    ]


def test_claim_evaluator_does_not_treat_inaccessible_source_as_support():
    claims = extract_claims(
        "Giá trị định mức là 1,500. "
        "[Nguồn: numbers.md, Trang 1, Version 12, SourceID D41P1]"
    )
    expected = [
        {
            "id": "nominal-value",
            "required_terms": ["1,500"],
            "allowed_source_ids": ["D41P1"],
        }
    ]

    report = evaluate_claims(claims, expected, accessible_source_ids=set())

    assert report["claim_precision"] == 1.0
    assert report["faithfulness"] == 0.0
    assert report["violations"] == [
        {"claim_index": 1, "reason": "inaccessible_source"}
    ]


def test_claim_evaluator_accepts_equivalent_number_formatting():
    claims = extract_claims(
        "Giá trị định mức là 1500. "
        "[Nguồn: numbers.md, Trang 1, Version 12, SourceID D41P1]"
    )
    expected = [
        {
            "id": "nominal-value",
            "required_terms": ["1,500"],
            "allowed_source_ids": ["D41P1"],
        }
    ]

    report = evaluate_claims(
        claims, expected, accessible_source_ids={"D41P1"}
    )

    assert report["claim_precision"] == 1.0
    assert report["faithfulness"] == 1.0


def test_claim_evaluator_does_not_match_number_as_substring():
    claims = extract_claims(
        "Giá trị định mức là 11,500. "
        "[Nguồn: numbers.md, Trang 1, Version 12, SourceID D41P1]"
    )
    expected = [{
        "id": "nominal-value", "required_terms": ["1,500"],
        "allowed_source_ids": ["D41P1"],
    }]

    report = evaluate_claims(claims, expected, accessible_source_ids={"D41P1"})

    assert report["claim_precision"] == 0.0
    assert report["expected_claim_recall"] == 0.0


def test_citation_evaluator_checks_source_page_version_and_rendering():
    actual = [
        {
            "file_goc": "numbers.md",
            "doc_id": 41,
            "trang": 1,
            "version_no": 12,
            "source_id": "D41P1",
        }
    ]
    expected = [
        {
            "document": "numbers.md",
            "doc_id": 41,
            "page": 1,
            "version": 12,
            "source_id": "D41P1",
        }
    ]
    rendered = (
        "[Nguồn: numbers.md, Trang 1, Version 12, SourceID D41P1]"
    )

    report = evaluate_citations(
        actual,
        expected,
        accessible_source_ids={"D41P1"},
        rendered_text=rendered,
    )

    assert report["citation_accuracy"] == 1.0
    assert report["citation_precision"] == 1.0
    assert report["violations"] == []


def test_citation_evaluator_reports_wrong_version_and_inaccessible_source():
    actual = [
        {
            "file_goc": "numbers.md",
            "doc_id": 42,
            "trang": 1,
            "version_no": 11,
            "source_id": "D41P1",
        }
    ]
    expected = [
        {
            "document": "numbers.md",
            "doc_id": 41,
            "page": 1,
            "version": 12,
            "source_id": "D41P1",
        }
    ]

    report = evaluate_citations(
        actual,
        expected,
        accessible_source_ids=set(),
        rendered_text="numbers.md Trang 1 Version 11 SourceID D41P1",
    )

    assert report["citation_accuracy"] == 0.0
    assert {item["reason"] for item in report["violations"]} == {
        "wrong_version",
        "wrong_doc_id",
        "inaccessible_source",
        "rendered_citation_mismatch",
    }


def test_rendered_citation_selection_excludes_uncited_retrieval_candidates():
    candidates = [
        {
            "file_goc": "target.md",
            "doc_id": 41,
            "trang": 1,
            "version_no": 12,
            "source_id": "D41P1",
        },
        {
            "file_goc": "unrelated.md",
            "doc_id": 99,
            "trang": 2,
            "version_no": 3,
            "source_id": "D99P2",
        },
    ]
    rendered = "**target.md** (Trang 1) - Technical · _v12 | DocID 41_"

    selected = select_rendered_citations(candidates, rendered)

    assert selected == [candidates[0]]


def test_rendered_citation_selection_deduplicates_the_same_source_identity():
    candidate = {
        "file_goc": "target.md", "doc_id": 41, "trang": 1,
        "version_no": 12, "source_id": "D41P1",
    }

    selected = select_rendered_citations(
        [candidate, dict(candidate)],
        "[Nguồn: target.md, Trang 1, Version 12, SourceID D41P1]",
    )

    assert selected == [candidate]


def test_citation_evaluator_matches_multiple_pages_from_same_document_one_to_one():
    actual = [
        {
            "file_goc": "manual.md", "doc_id": 41, "trang": 1,
            "version_no": 12, "source_id": "D41P1",
        },
        {
            "file_goc": "manual.md", "doc_id": 41, "trang": 2,
            "version_no": 12, "source_id": "D41P2",
        },
    ]
    expected = [
        {
            "document": "manual.md", "doc_id": 41, "page": 1,
            "version": 12, "source_id": "D41P1",
        },
        {
            "document": "manual.md", "doc_id": 41, "page": 2,
            "version": 12, "source_id": "D41P2",
        },
    ]
    rendered = (
        "manual.md Trang 1 Version 12 SourceID D41P1; "
        "manual.md Trang 2 Version 12 SourceID D41P2"
    )

    report = evaluate_citations(
        actual,
        expected,
        accessible_source_ids={"D41P1", "D41P2"},
        rendered_text=rendered,
    )

    assert report["citation_accuracy"] == 1.0
    assert report["citation_precision"] == 1.0
    assert report["violations"] == []


def test_rendered_citation_identity_cannot_be_assembled_across_blocks():
    actual = [{
        "file_goc": "manual.md", "doc_id": 41, "trang": 1,
        "version_no": 12, "source_id": "D41P1",
    }]
    expected = [{
        "document": "manual.md", "doc_id": 41, "page": 1,
        "version": 12, "source_id": "D41P1",
    }]
    rendered = (
        "[Nguồn: manual.md, Trang 2, Version 12, SourceID D41P1]; "
        "[Nguồn: other.md, Trang 1, Version 12, SourceID D99P1]"
    )

    report = evaluate_citations(
        actual, expected, accessible_source_ids={"D41P1"}, rendered_text=rendered
    )

    assert report["citation_accuracy"] == 0.0
    assert {item["reason"] for item in report["violations"]} == {
        "rendered_citation_mismatch"
    }


@pytest.mark.parametrize(
    ("rendered", "expected_page", "expected_version"),
    [
        ("manual.md Trang 10 Version 1 SourceID D41P1", 1, 1),
        ("manual.md Trang 1 Version 12 SourceID D41P1", 1, 1),
    ],
)
def test_rendered_citation_identity_uses_numeric_boundaries(
    rendered, expected_page, expected_version
):
    actual = [{
        "file_goc": "manual.md", "doc_id": 41, "trang": expected_page,
        "version_no": expected_version, "source_id": "D41P1",
    }]
    expected = [{
        "document": "manual.md", "doc_id": 41, "page": expected_page,
        "version": expected_version, "source_id": "D41P1",
    }]

    report = evaluate_citations(
        actual,
        expected,
        accessible_source_ids={"D41P1"},
        rendered_text=rendered,
    )

    assert report["citation_accuracy"] == 0.0
    assert report["violations"] == [
        {"citation_index": 1, "reason": "rendered_citation_mismatch"}
    ]


def test_risk_coverage_reports_each_operating_point_without_selecting_threshold():
    rows = [
        {
            "id": "correct",
            "confidence": 0.90,
            "evidence_state": "SUFFICIENT",
            "expected_outcome": "full_answer",
            "answer_correct": True,
            "leaked": False,
        },
        {
            "id": "wrong",
            "confidence": 0.80,
            "evidence_state": "AMBIGUOUS",
            "expected_outcome": "full_answer",
            "answer_correct": False,
            "leaked": False,
        },
        {
            "id": "denied",
            "confidence": 0.70,
            "evidence_state": "INSUFFICIENT",
            "expected_outcome": "access_denied",
            "answer_correct": False,
            "leaked": True,
        },
    ]

    report = build_risk_coverage_report(
        rows,
        operating_points=(0.75, 0.85),
        baseline_wrong_answers=0,
    )

    assert report["automatic_threshold_selection"] is False
    assert report["selected_threshold"] is None
    assert report["operating_points"][0] == {
        "threshold": 0.75,
        "served": 2,
        "coverage": pytest.approx(2 / 3),
        "wrong_refusal": 0,
        "wrong_answer": 1,
        "leakage": 0,
        "serving_allowed": False,
    }
    assert report["operating_points"][1]["serving_allowed"] is True


def _review(reviewer_id, *, outcome="full_answer", citation=True, role="reviewer"):
    return {
        "reviewer_id": reviewer_id,
        "role": role,
        "outcome_label": outcome,
        "answer_correct": True,
        "citation_correct": citation,
        "reason_code": "evidence_supported",
    }


def test_adjudication_requires_third_reviewer_when_independent_labels_disagree():
    reviews = [
        _review("reviewer-a"),
        _review("reviewer-b", outcome="partial_answer"),
    ]

    with pytest.raises(ValueError, match="third adjudicator"):
        resolve_case_reviews("case-1", reviews)

    resolved = resolve_case_reviews(
        "case-1",
        reviews + [_review("reviewer-c", role="adjudicator")],
    )
    assert resolved["disagreement"] is True
    assert resolved["resolved_by"] == "reviewer-c"
    assert resolved["outcome_label"] == "full_answer"


def test_adjudication_accepts_two_matching_independent_reviews():
    resolved = resolve_case_reviews(
        "case-1",
        [_review("reviewer-a"), _review("reviewer-b")],
    )

    assert resolved["disagreement"] is False
    assert resolved["resolved_by"] == "consensus"
    assert resolved["reviewer_ids"] == ["reviewer-a", "reviewer-b"]


def test_adjudication_cli_artifact_is_reproducible(tmp_path):
    reviews = tmp_path / "reviews.jsonl"
    records = [
        {"case_id": "case-1", **_review("reviewer-a")},
        {"case_id": "case-1", **_review("reviewer-b")},
    ]
    reviews.write_text(
        "\n".join(json.dumps(record) for record in records) + "\n",
        encoding="utf-8",
    )
    spec = importlib.util.spec_from_file_location(
        "evaluation_adjudicate_cli", Path("scripts/eval/adjudicate.py")
    )
    module = importlib.util.module_from_spec(spec)
    assert spec.loader
    spec.loader.exec_module(module)
    first_json = tmp_path / "first.json"
    first_md = tmp_path / "first.md"
    second_json = tmp_path / "second.json"
    second_md = tmp_path / "second.md"

    assert module.main([
        str(reviews), "--json-output", str(first_json),
        "--markdown-output", str(first_md),
    ]) == 0
    assert module.main([
        str(reviews), "--json-output", str(second_json),
        "--markdown-output", str(second_md),
    ]) == 0

    assert first_json.read_bytes() == second_json.read_bytes()
    assert first_md.read_bytes() == second_md.read_bytes()
    artifact = json.loads(first_json.read_text(encoding="utf-8"))
    assert artifact["schema"] == "evaluation-adjudication-artifact-v1"
    assert artifact["protocol"]["raw_prompt_recorded"] is False
