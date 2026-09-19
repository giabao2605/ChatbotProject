from __future__ import annotations

import json

import pytest

from mech_chatbot.evaluation.decomposition import (
    DecompositionManifestError,
    evaluate_decomposition_case,
    load_decomposition_manifest,
    summarize_decomposition_evaluation,
    summarize_decomposition_usage,
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


def test_scoped_manifest_requires_explicit_rendered_branch_citations(tmp_path):
    case = _case(evaluation_scope="query_only")
    path = tmp_path / "manifest.jsonl"
    path.write_text(json.dumps(case) + "\n", encoding="utf-8")

    with pytest.raises(
        DecompositionManifestError,
        match="expected_rendered_citations",
    ):
        load_decomposition_manifest(path)


def test_scoped_manifest_rejects_wildcard_retrieval_citations(tmp_path):
    case = _case(
        evaluation_scope="query_only",
        expected_branches=[{
            "branch_id": "torque",
            "expected_outcome": "full_answer",
            "expected_citations": [{}],
            "expected_rendered_citations": [],
        }],
    )
    path = tmp_path / "manifest.jsonl"
    path.write_text(json.dumps(case) + "\n", encoding="utf-8")

    with pytest.raises(
        DecompositionManifestError,
        match="citation source_id is required",
    ):
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


def test_branch_evaluator_rejects_plans_that_do_not_cover_every_detected_intent():
    debug = {
        "planner_count": 1,
        "subquery_count": 2,
        "correction_count": 0,
        "final_generation_count": 1,
        "deadline_exceeded": False,
        "decomposition_intent_count": 2,
        "decomposition_intent_coverage": [True, False],
        "decomposition_branches": [
            {
                "branch_id": "torque",
                "outcome": "full_answer",
                "citations": [{"document": "technical_effective_core.md", "doc_id": 32}],
                "rendered_source_ids": [],
            },
            {"branch_id": "cost", "outcome": "insufficient_evidence", "citations": []},
        ],
    }

    result = evaluate_decomposition_case(_case(), debug)

    assert result["passed"] is False
    assert result["budget_checks"]["intent_coverage"] is False


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


def test_terminal_refusal_separates_retrieved_from_rendered_branch_citations():
    citation = {
        "document": "technical_effective_core.md",
        "doc_id": 32,
        "source_id": "D32P1",
    }
    case = _case(
        expected_outcome="insufficient_evidence",
        expected_branches=[{
            "branch_id": "torque",
            "expected_outcome": "full_answer",
            "expected_citations": [citation],
            "expected_rendered_citations": [],
        }],
    )
    debug = {
        "planner_count": 1,
        "subquery_count": 1,
        "correction_count": 0,
        "final_generation_count": 0,
        "deadline_exceeded": False,
        "decomposition_branches": [{
            "branch_id": "torque",
            "outcome": "full_answer",
            "citations": [citation],
            "rendered_source_ids": [],
        }],
    }

    assert evaluate_decomposition_case(case, debug)["passed"] is True

    duplicate_chunk_debug = {
        **debug,
        "decomposition_branches": [{
            **debug["decomposition_branches"][0],
            "citations": [
                citation,
                {**citation, "chunk_index": 2},
            ],
        }],
    }
    assert (
        evaluate_decomposition_case(case, duplicate_chunk_debug)["passed"]
        is True
    )

    rendered_debug = {
        **debug,
        "decomposition_branches": [{
            **debug["decomposition_branches"][0],
            "rendered_source_ids": ["D32P1"],
        }],
    }
    assert evaluate_decomposition_case(case, rendered_debug)["passed"] is False

    missing_retrieval_debug = {
        **debug,
        "decomposition_branches": [{
            **debug["decomposition_branches"][0],
            "citations": [],
        }],
    }
    assert (
        evaluate_decomposition_case(case, missing_retrieval_debug)["passed"]
        is False
    )

    extra_retrieval_debug = {
        **debug,
        "decomposition_branches": [{
            **debug["decomposition_branches"][0],
            "citations": [
                citation,
                {
                    "document": "unexpected.md",
                    "doc_id": 999,
                    "source_id": "D999P1",
                },
            ],
        }],
    }
    assert (
        evaluate_decomposition_case(case, extra_retrieval_debug)["passed"]
        is False
    )


def test_terminal_refusal_rejects_factual_text_without_rendered_source():
    citation = {
        "document": "technical_effective_core.md",
        "doc_id": 32,
        "source_id": "D32P1",
    }
    case = _case(
        expected_outcome="insufficient_evidence",
        expected_terminal_claim_count=0,
        expected_terminal_rendered_source_count=0,
        expected_branches=[{
            "branch_id": "torque",
            "expected_outcome": "full_answer",
            "expected_citations": [citation],
            "expected_rendered_citations": [],
        }],
    )
    debug = {
        "planner_count": 1,
        "subquery_count": 1,
        "correction_count": 0,
        "final_generation_count": 0,
        "deadline_exceeded": False,
        "decomposition_branches": [{
            "branch_id": "torque",
            "outcome": "full_answer",
            "citations": [citation],
            "rendered_source_ids": [],
        }],
    }

    result = evaluate_decomposition_case(
        case,
        debug,
        answer=(
            "Tài liệu nội bộ hiện có không đề cập đến chi phí. "
            "Giá trị định mức là 1,500."
        ),
    )

    assert result["passed"] is False
    assert result["terminal_answer_passed"] is False
    assert result["terminal_claim_count"] == 1

    result = evaluate_decomposition_case(
        case,
        debug,
        answer=(
            "Tài liệu nội bộ hiện có không đề cập đến chi phí. "
            "[Nguồn: unrelated.md; SourceID: D999P1]"
        ),
    )

    assert result["passed"] is False
    assert result["terminal_answer_passed"] is False
    assert result["terminal_rendered_source_count"] == 1

    result = evaluate_decomposition_case(
        case,
        debug,
        answer=(
            "Tài liệu hiện tại không ghi thông tin đủ để trả lời câu hỏi này "
            "(partial_branch_coverage).\n\n"
            "Mình sẽ không tự ước lượng hoặc tự bịa số liệu. Để trả lời được, "
            "bạn cần bổ sung tài liệu có dữ kiện trực tiếp liên quan, ví dụ "
            "thời gian gia công cho 1 sản phẩm, năng suất theo giờ/ca, định "
            "mức sản xuất, chi phí hoặc tiêu chuẩn kiểm tra tương ứng."
        ),
    )

    assert result["passed"] is True
    assert result["terminal_answer_passed"] is True
    assert result["terminal_claim_count"] == 0
    assert result["terminal_rendered_source_count"] == 0

    result = evaluate_decomposition_case(case, debug)

    assert result["passed"] is False
    assert result["terminal_answer_violations"] == [
        {"kind": "terminal_answer_missing", "value": ""}
    ]


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


def test_summary_counts_terminal_answer_violations():
    rows = [
        {
            "evaluation_group": "complex",
            "planner_count": 1,
            "decomposition_evaluation": {
                "applicable": True,
                "passed": False,
                "branch_accuracy": 1.0,
                "citation_accuracy": 1.0,
                "budget_passed": True,
                "terminal_answer_passed": False,
            },
        },
    ]

    summary = summarize_decomposition_evaluation(rows)

    assert summary["terminal_answer_violations"] == 1



def test_usage_summary_reconciles_priced_stages_without_double_counting_context():
    rows = [
        {
            "estimated_cost": 0.7,
            "decomposition_usage": {
                "schema": "rag-decomposition-usage-v1",
                "planner": {
                    "calls": 1,
                    "input_tokens": 10,
                    "output_tokens": 2,
                    "estimated_cost": 0.1,
                },
                "branches": [
                    {
                        "branch_id": "branch-1",
                        "retrieval": {
                            "document_count": 2,
                            "estimated_input_tokens": 20,
                            "estimated_cost": None,
                            "cost_status": "unpriced",
                        },
                        "correction": {
                            "attempted": True,
                            "input_tokens": 4,
                            "output_tokens": 1,
                            "estimated_cost": 0.2,
                        },
                    }
                ],
                "final_context": {
                    "estimated_input_tokens": 30,
                    "estimated_input_cost": 0.3,
                    "included_in_final_generation": True,
                },
                "final_generation": {
                    "calls": 1,
                    "input_tokens": 40,
                    "output_tokens": 5,
                    "estimated_cost": 0.4,
                },
            },
        }
    ]

    summary = summarize_decomposition_usage(rows)

    assert summary["cases"] == 1
    assert summary["planner"]["estimated_cost"] == pytest.approx(0.1)
    assert summary["branch_retrieval"] == {
        "branches": 1,
        "document_count": 2,
        "estimated_input_tokens": 20,
        "priced_branches": 0,
        "unpriced_branches": 1,
    }
    assert summary["branch_correction"]["estimated_cost"] == pytest.approx(0.2)
    assert summary["final_context"]["estimated_input_cost"] == pytest.approx(0.3)
    assert summary["final_generation"]["estimated_cost"] == pytest.approx(0.4)
    assert summary["legacy_total_estimated_cost"] == pytest.approx(0.7)
    assert summary["attributed_estimated_cost"] == pytest.approx(0.7)
    assert summary["cost_reconciled"] is True
