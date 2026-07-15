from __future__ import annotations

import json

import pytest

from mech_chatbot.evaluation.decomposition import (
    DecompositionManifestError,
    evaluate_decomposition_case,
    load_decomposition_manifest,
    summarize_decomposition_evaluation,
)


pytestmark = pytest.mark.unit


def _identity():
    return {
        "user_department": "Technical",
        "user_roles": ["viewer"],
        "allowed_departments": ["Technical"],
        "allowed_sites": ["HQ"],
        "max_security_level": "internal",
    }


def _case(**overrides):
    value = {
        "manifest_schema": "rag-eval-manifest-v2",
        "id": "complex-partial",
        "scenario": "complex_partial",
        "evaluation_group": "complex",
        "question": "Cho biết mô-men siết và chi phí của TK-100-V2?",
        **_identity(),
        "expected_outcome": "partial_answer",
        "expected_sources": [{"document": "technical_effective_core.md", "doc_id": 32}],
        "expected_branches": [
            {
                "branch_id": "torque",
                "expected_outcome": "full_answer",
                "expected_citations": [{"document": "technical_effective_core.md", "doc_id": 32}],
            },
            {
                "branch_id": "cost",
                "expected_outcome": "insufficient_evidence",
                "expected_citations": [],
            },
        ],
    }
    value.update(overrides)
    return value


def test_manifest_requires_explicit_branch_outcomes_and_citations(tmp_path):
    case = _case()
    case["expected_branches"][1].pop("expected_citations")
    path = tmp_path / "manifest.jsonl"
    path.write_text(json.dumps(case) + "\n", encoding="utf-8")

    with pytest.raises(DecompositionManifestError, match="expected_citations"):
        load_decomposition_manifest(path)


def test_branch_evaluator_checks_outcome_and_accessible_citation_identity():
    debug = {
        "planner_count": 1,
        "subquery_count": 2,
        "correction_count": 1,
        "final_generation_count": 1,
        "deadline_exceeded": False,
        "decomposition_branches": [
            {
                "branch_id": "torque",
                "outcome": "full_answer",
                "citations": [{"document": "technical_effective_core.md", "doc_id": 32}],
                "rendered_source_ids": [],
            },
            {
                "branch_id": "cost",
                "outcome": "insufficient_evidence",
                "citations": [],
            },
        ],
    }

    result = evaluate_decomposition_case(_case(), debug)

    assert result["applicable"] is True
    assert result["passed"] is True
    assert result["branch_accuracy"] == 1.0
    assert result["citation_accuracy"] == 1.0
    assert result["budget_passed"] is True


def test_access_denied_branch_fails_if_restricted_source_is_exposed():
    case = _case(
        id="denied",
        expected_branches=[{
            "branch_id": "restricted",
            "expected_outcome": "access_denied",
            "expected_citations": [],
        }],
    )
    debug = {
        "planner_count": 1,
        "subquery_count": 1,
        "correction_count": 0,
        "final_generation_count": 1,
        "deadline_exceeded": False,
        "decomposition_branches": [{
            "branch_id": "restricted",
            "outcome": "access_denied",
            "citations": [{"document": "restricted.md", "doc_id": 999}],
            "rendered_source_ids": [],
        }],
    }

    result = evaluate_decomposition_case(case, debug)

    assert result["passed"] is False
    assert result["citation_accuracy"] == 0.0


def test_expected_branch_citation_can_be_a_subset_of_accessible_branch_evidence():
    debug = {
        "planner_count": 1, "subquery_count": 2, "correction_count": 0,
        "final_generation_count": 1, "deadline_exceeded": False,
        "decomposition_branches": [
            {"branch_id": "torque", "outcome": "full_answer", "citations": [
                {"document": "technical_effective_core.md", "doc_id": 32},
                {"document": "another_accessible.md", "doc_id": 33},
            ], "rendered_source_ids": []},
            {"branch_id": "cost", "outcome": "insufficient_evidence", "citations": []},
        ],
    }

    assert evaluate_decomposition_case(_case(), debug)["passed"] is True


def test_branch_evaluator_does_not_accept_a_wrong_id_by_position():
    debug = {
        "planner_count": 1, "subquery_count": 2, "correction_count": 0,
        "final_generation_count": 1, "deadline_exceeded": False,
        "decomposition_branches": [
            {"branch_id": "wrong-id", "outcome": "full_answer", "citations": [
                {"document": "technical_effective_core.md", "doc_id": 32},
            ], "rendered_source_ids": []},
            {"branch_id": "cost", "outcome": "insufficient_evidence", "citations": []},
        ],
    }

    result = evaluate_decomposition_case(_case(), debug)

    assert result["passed"] is False
    assert result["branch_accuracy"] == 0.5


def test_branch_citation_requires_source_id_to_be_rendered():
    case = _case(expected_branches=[{
        "branch_id": "torque", "expected_outcome": "full_answer",
        "expected_citations": [{
            "document": "technical_effective_core.md", "doc_id": 32,
            "source_id": "D32P1",
        }],
    }])
    debug = {
        "planner_count": 1, "subquery_count": 1, "correction_count": 0,
        "final_generation_count": 1, "deadline_exceeded": False,
        "decomposition_branches": [{
            "branch_id": "torque", "outcome": "full_answer",
            "citations": [{
                "document": "technical_effective_core.md", "doc_id": 32,
                "source_id": "D32P1",
            }],
            "rendered_source_ids": [],
        }],
    }

    result = evaluate_decomposition_case(case, debug)

    assert result["passed"] is False
    assert result["citation_accuracy"] == 0.0


def test_summary_reports_simple_planner_calls_and_all_request_budgets():
    rows = [
        {
            "evaluation_group": "simple",
            "planner_count": 0,
            "decomposition_evaluation": {"applicable": True, "passed": True, "branch_accuracy": 1.0, "citation_accuracy": 1.0, "budget_passed": True},
        },
        {
            "evaluation_group": "complex",
            "planner_count": 1,
            "decomposition_evaluation": {"applicable": True, "passed": True, "branch_accuracy": 1.0, "citation_accuracy": 1.0, "budget_passed": True},
        },
    ]

    summary = summarize_decomposition_evaluation(rows)

    assert summary["simple_planner_calls"] == 0
    assert summary["branch_accuracy"] == 1.0
    assert summary["citation_accuracy"] == 1.0
    assert summary["budget_violations"] == 0
