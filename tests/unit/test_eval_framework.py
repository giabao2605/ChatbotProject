"""Pure-unit coverage for the operator-supplied pilot and benchmark frameworks."""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path

import pytest


pytestmark = pytest.mark.unit


def _load_script(module_name: str, relative_path: str):
    script = Path(__file__).resolve().parents[2] / relative_path
    spec = importlib.util.spec_from_file_location(module_name, script)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


pilot_gate = _load_script("pilot_rollout_gate", "scripts/eval/pilot_rollout_gate.py")
benchmark = _load_script("benchmark_rag_concurrency", "scripts/eval/benchmark_rag_concurrency.py")


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

    assert report["manifest_schema"] == "pilot-eval-v4"
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
