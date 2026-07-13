"""Pure-unit coverage for the operator-supplied pilot and benchmark frameworks."""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path

import pytest
from mech_chatbot.evaluation.outcomes import (
    classify_actual_outcome,
    classify_outcome,
    expected_outcome,
    summarize_outcomes,
)
from mech_chatbot.evaluation.metrics import ranked_retrieval_metrics


pytestmark = pytest.mark.unit


def test_ranked_retrieval_metrics_reward_earlier_relevant_sources():
    metrics = ranked_retrieval_metrics(
        ["other.pdf", "target.pdf", "appendix.pdf"],
        ["target.pdf"],
        cutoffs=(2, 10),
    )

    assert metrics["recall_at_2"] == 1.0
    assert metrics["recall_at_10"] == 1.0
    assert metrics["ndcg_at_10"] == pytest.approx(0.6309297536)


def test_ranked_retrieval_metrics_report_zero_when_source_is_missing():
    metrics = ranked_retrieval_metrics(["other.pdf"], ["target.pdf"], cutoffs=(5, 10))

    assert metrics == {
        "recall_at_5": 0.0,
        "ndcg_at_5": 0.0,
        "recall_at_10": 0.0,
        "ndcg_at_10": 0.0,
    }


def test_eval_runner_keeps_ten_sources_for_recall_at_ten():
    source = Path("scripts/eval/run_eval.py").read_text(encoding="utf-8")

    assert 'debug.get("retrieved_docs", [])[:10]' in source


def _load_script(module_name: str, relative_path: str):
    script = Path(__file__).resolve().parents[2] / relative_path
    spec = importlib.util.spec_from_file_location(module_name, script)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


pilot_gate = _load_script("pilot_rollout_gate", "scripts/eval/pilot_rollout_gate.py")
benchmark = _load_script("benchmark_rag_concurrency", "scripts/eval/benchmark_rag_concurrency.py")
crag_gate = _load_script("crag_rollout_gate", "scripts/eval/crag_rollout_gate.py")


def _record(department: str, index: int) -> dict:
    return {
        "department": department,
        "question": f"Cau hoi da duyet {department} {index}",
        "scenario": "grounded_lookup",
        "expected_file": f"{department}-{index}.pdf",
        "expected_page": 1,
        "version_policy": "current_only",
        "keywords": ["du lieu da duyet"],
        "security_expectation": {"access": "allowed"},
        "refusal_expectation": False,
    }


def _write_jsonl(path: Path, records: list[dict]) -> None:
    path.write_text(
        "\n".join(json.dumps(record, ensure_ascii=False) for record in records),
        encoding="utf-8",
    )


def test_pilot_gate_accepts_complete_real_manifest_shape(tmp_path):
    departments = ["Technical", "Finance", "HR"]
    records = [
        _record(department, index)
        for department in departments
        for index in range(1, 76)
    ]
    manifest = tmp_path / "pilot.jsonl"
    _write_jsonl(manifest, records)

    questions = pilot_gate.read_questions(manifest)
    report = pilot_gate.summarize(
        questions,
        75,
        expected_departments=departments,
        expected_department_count=3,
    )

    assert report["manifest_schema"] == "pilot-eval-v5"
    assert report["passed"] is True
    assert report["departments"]["Technical"]["question_count"] == 75
    assert "question" not in report["departments"]["Technical"]


def test_pilot_gate_requires_explicit_refusal_contract_fields(tmp_path):
    record = _record("Technical", 1)
    record.update(
        {
            "expected_file": None,
            "expected_page": None,
            "keywords": [],
            "refusal_expectation": True,
            "security_expectation": {"access": "denied"},
        }
    )
    manifest = tmp_path / "refusal.jsonl"
    _write_jsonl(manifest, [record])

    parsed = pilot_gate.read_questions(manifest)

    assert parsed[0]["refusal_expected"] is True
    assert parsed[0]["has_expected_document"] is False


def test_pilot_gate_accepts_expected_outcome_and_maps_legacy_refusal(tmp_path):
    answer = _record("Technical", 1)
    answer.pop("refusal_expectation")
    answer["expected_outcome"] = "partial_answer"
    legacy_refusal = _record("Technical", 2)
    legacy_refusal.update(
        {
            "expected_file": None,
            "expected_page": None,
            "keywords": [],
            "refusal_expectation": True,
        }
    )
    manifest = tmp_path / "outcomes.jsonl"
    _write_jsonl(manifest, [answer, legacy_refusal])

    parsed = pilot_gate.read_questions(manifest)

    assert parsed[0]["expected_outcome"] == "partial_answer"
    assert parsed[0]["refusal_expected"] is False
    assert parsed[1]["expected_outcome"] == "insufficient_evidence"
    assert parsed[1]["refusal_expected"] is True


def test_pilot_gate_rejects_missing_mandatory_security_expectation(tmp_path):
    record = _record("Technical", 1)
    record.pop("security_expectation")
    manifest = tmp_path / "bad.jsonl"
    _write_jsonl(manifest, [record])

    with pytest.raises(ValueError, match="security_expectation"):
        pilot_gate.read_questions(manifest)


def test_pilot_gate_reports_missing_or_unexpected_pilot_departments():
    questions = [
        {"department": "Technical", "scenario": "grounded_lookup", "refusal_expected": False},
        {"department": "Sales", "scenario": "grounded_lookup", "refusal_expected": False},
    ]

    report = pilot_gate.summarize(
        questions,
        75,
        expected_departments=["Technical", "Finance", "HR"],
        expected_department_count=3,
    )

    assert report["passed"] is False
    assert report["missing_departments"] == ["Finance", "HR"]
    assert report["unexpected_departments"] == ["Sales"]


def test_benchmark_defaults_and_sse_trace_stage_summary():
    assert benchmark.parse_concurrency_levels("1,5,10") == [1, 5, 10]
    samples = [
        {
            "ok": True,
            "first_token_ms": 100,
            "complete_ms": 300,
            "stage_metrics": {"dense_retrieval": 12, "bm25_retrieval": 4, "rrf_grouping": 2},
        },
        {
            "ok": True,
            "first_token_ms": 200,
            "complete_ms": 500,
            "stage_metrics": {"dense_retrieval": 22, "bm25_retrieval": 6, "rrf_grouping": 3},
        },
    ]

    summary = benchmark.summarize(samples, 5)

    assert summary["first_token_p50_ms"] == 100.0
    assert summary["complete_p95_ms"] == 500.0
    assert summary["stage_latency"]["sources"] == ["sse_trace_stages"]
    assert summary["stage_latency"]["stages"]["dense_retrieval"]["p95_ms"] == 22.0


def test_outcome_metrics_separate_wrong_refusal_wrong_answer_and_leakage():
    assert expected_outcome({"should_refuse": True}) == "insufficient_evidence"
    assert classify_outcome("full_answer", "insufficient_evidence", answer_correct=False, leaked=False) == "wrong_refusal"
    assert classify_outcome("insufficient_evidence", "full_answer", answer_correct=False, leaked=False) == "wrong_answer"
    assert classify_outcome("access_denied", "access_denied", answer_correct=True, leaked=True) == "leakage"
    assert classify_outcome("partial_answer", "full_answer", answer_correct=True, leaked=False) == "wrong_answer"
    assert classify_actual_outcome("Tôi trả lời được một phần; phần còn lại chưa đủ dữ kiện.") == "partial_answer"
    assert classify_actual_outcome("Bạn muốn so sánh với phiên bản nào? Vui lòng chỉ định.") == "clarification_required"
    assert classify_actual_outcome("Tài liệu không công bố chi phí hoặc đơn giá.") == "insufficient_evidence"
    assert classify_actual_outcome(
        "Tài liệu CRAG-EVAL-BOM-001 không có tổng số lượng BOM được phê duyệt trong tài liệu này. "
        "[Nguồn: bom.md, Trang 1, Version 1, SourceID D1P1]"
    ) == "insufficient_evidence"
    assert classify_actual_outcome(
        "Tài liệu CRAG-EVAL-BOM-001 không có tổng BOM được phê duyệt trong tài liệu này. "
        "[Nguồn: bom.md, Trang 1, Version 1, SourceID D1P1]"
    ) == "insufficient_evidence"
    assert classify_actual_outcome(
        "Rất tiếc, mình không tìm thấy mã số 'secret-001' nào trong hệ thống bản vẽ hiện tại. "
        "Vui lòng kiểm tra lại mã hoặc mô tả rõ hơn."
    ) == "insufficient_evidence"
    assert classify_actual_outcome(
        "Tài liệu không có tổng số lượng BOM được phê duyệt, nhưng tổng tính từ BOM là 999."
    ) == "full_answer"
    assert classify_actual_outcome(
        "Tài liệu CRAG-EVAL-BOM-001 không có tổng BOM được phê duyệt trong tài liệu này, "
        "nhưng tổng là 999."
    ) == "full_answer"
    assert classify_actual_outcome(
        "Không có tổng BOM được phê duyệt trong tài liệu này.\n\n"
        "| Mã | Số lượng |\n|---|---:|\n| PART-A | 2 |\n| PART-B | 3 |\n\n"
        "[Nguồn: bom.md, Trang 1, Version 1, SourceID D1P1]"
    ) == "insufficient_evidence"
    assert classify_actual_outcome(
        "Không có tổng BOM được phê duyệt trong tài liệu này, nhưng tổng là 999.\n\n"
        "| Mã | Số lượng |\n|---|---:|\n| PART-A | 2 |\n| PART-B | 3 |"
    ) == "full_answer"
    assert classify_actual_outcome(
        "Không có tổng BOM được phê duyệt trong tài liệu này.\n\n"
        "| Mã | Số lượng |\n|---|---:|\n| Tổng | 5 |\n\n"
        "[Nguồn: bom.md, Trang 1, Version 1, SourceID D1P1]"
    ) == "full_answer"
    assert classify_actual_outcome(
        "Không có tổng BOM được phê duyệt trong tài liệu này.\n\n"
        "| Mã | Số lượng |\n|---|---:|\n| Tổng cộng | 5 |\n\n"
        "[Nguồn: bom.md, Trang 1, Version 1, SourceID D1P1]"
    ) == "full_answer"
    assert classify_actual_outcome(
        "Không có tổng BOM được phê duyệt trong tài liệu này.\n\n"
        "| Mã | Số lượng |\n|---|---:|\n| Grand total | 5 |\n\n"
        "[Nguồn: bom.md, Trang 1, Version 1, SourceID D1P1]"
    ) == "full_answer"
    assert classify_actual_outcome(
        "Mình không tìm thấy mã số cũ; mã thay thế là CRAG-EVAL-NEW-001."
    ) == "full_answer"
    assert classify_actual_outcome("Mình không thể hỗ trợ yêu cầu này do chính sách truy cập.") == "access_denied"
    assert summarize_outcomes([
        {"expected": "full_answer", "actual": "full_answer", "answer_correct": True, "legacy_admin_bypass": True}
    ])["legacy_admin_exception"] == 1


def test_benchmark_accepts_done_trace_stage_contract_and_safe_trace_jsonl(tmp_path):
    payload = {
        "trace_stages": {
            "dense_retrieval": {"latency_ms": 13},
            "bm25_retrieval_ms": 5,
            "rrf_grouping": {"ms": 2},
        }
    }
    assert benchmark.extract_trace_stages(payload) == {
        "dense_retrieval": 13.0,
        "bm25_retrieval": 5.0,
        "rrf_grouping": 2.0,
    }

    trace_path = tmp_path / "rag_trace.jsonl"
    trace_path.write_text(
        "\n".join(
            [
                json.dumps({"ts": "2026-07-11T00:00:00+00:00", "trace_id": "t1", "event": "dense_retrieval", "latency_ms": 9}),
                json.dumps({"ts": "2026-07-11T00:00:01+00:00", "trace_id": "t1", "event": "rrf_grouping", "latency_ms": 3}),
            ]
        ),
        encoding="utf-8",
    )

    stages, metadata = benchmark.read_trace_jsonl(trace_path)

    assert stages == {"dense_retrieval": [9.0], "rrf_grouping": [3.0]}
    assert metadata["correlation"] == "time_window_only"
    assert "t1" not in json.dumps(metadata)


def test_crag_rollout_gate_blocks_wrong_answers_leakage_and_excess_cost():
    baseline_eval = {"outcome_confusion": {"wrong_refusal": 5, "wrong_answer": 1, "leakage": 0}}
    candidate_eval = {
        "outcome_confusion": {"wrong_refusal": 3, "wrong_answer": 1, "leakage": 0},
        "total_cases": 2, "passed_cases": 2,
        "feature_flags": {"crag": "true", "claim_repair": "true", "semantic_cache": "false"},
        "cases": [
            {"trace_id": "eval:candidate:ambiguous", "requires_correction": True},
            {"trace_id": "eval:candidate:repair", "requires_repair": True},
        ],
    }
    baseline_trace = {"system_metrics": {"latency_p95_ms": 1000, "estimated_cost": 1.0, "correction_rate": 0.0, "repair_rate": 0.0, "retry_rate": 0.0}}
    candidate_trace = {"system_metrics": {"latency_p95_ms": 1200, "estimated_cost": 1.2, "correction_rate": 0.2, "repair_rate": 0.1, "retry_rate": 0.3, "correction_trace_ids": ["eval:candidate:ambiguous"], "repair_trace_ids": ["eval:candidate:repair"]}}

    report = crag_gate.compare_reports(baseline_eval, candidate_eval, baseline_trace, candidate_trace)
    assert report["passed"] is True

    candidate_eval["outcome_confusion"]["wrong_answer"] = 2
    assert crag_gate.compare_reports(baseline_eval, candidate_eval, baseline_trace, candidate_trace)["passed"] is False


def test_crag_rollout_gate_blocks_more_than_one_correction_or_repair_per_query():
    eval_report = {
        "outcome_confusion": {"wrong_refusal": 0, "wrong_answer": 0, "leakage": 0},
        "total_cases": 1, "passed_cases": 1,
        "feature_flags": {"crag": "true", "claim_repair": "true", "semantic_cache": "false"},
        "cases": [
            {"trace_id": "eval:candidate:ambiguous", "requires_correction": True},
            {"trace_id": "eval:candidate:repair", "requires_repair": True},
        ],
    }
    baseline_trace = {"system_metrics": {"latency_p95_ms": 100, "estimated_cost": 1}}
    candidate_trace = {"system_metrics": {
        "latency_p95_ms": 100, "estimated_cost": 1, "correction_rate": 0.5,
        "repair_rate": 0.5, "retry_rate": 0, "max_corrections_per_query": 2,
        "max_repairs_per_query": 1, "correction_trace_ids": ["eval:candidate:ambiguous"],
        "repair_trace_ids": ["eval:candidate:repair"],
    }}
    report = crag_gate.compare_reports(eval_report, eval_report, baseline_trace, candidate_trace)
    assert report["checks"]["correction_budget"] is False
    assert report["passed"] is False
