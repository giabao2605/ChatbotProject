from __future__ import annotations

import hashlib
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from scripts.crag_eval.run_diagnostic import (
    build_diagnostic_declaration,
    build_diagnostic_outcome,
    run_diagnostic,
    validate_canonical_manifest,
    validate_preflight_report,
)


pytestmark = pytest.mark.unit


def _arm(
    *,
    total_ms: int,
    cost: float,
    provider_failures: int = 0,
    provider_retries: int = 0,
) -> dict:
    return {
        "eval": {
            "provider_failure_count": provider_failures,
            "provider_retries": provider_retries,
        },
        "latency": {
            "estimated_cost": cost,
            "stage_summary": {
                "retrieval": {"latency_p50_ms": 100},
                "rerank": {"latency_p50_ms": 20},
                "generation": {"latency_p50_ms": 300},
                "total": {"latency_p50_ms": total_ms},
            },
        },
    }


def _case_arm_payload(
    case_id: str,
    label: str,
    *,
    total_ms: int,
    cost: float,
    requires_correction: bool = False,
    requires_repair: bool = False,
    wrong_refusal: int = 0,
) -> dict:
    trace_id = f"eval:{label}:{case_id}"
    correction_count = int(label == "candidate" and requires_correction)
    repair_count = int(label == "candidate" and requires_repair)
    return {
        "eval": _case_evaluation(
            case_id,
            label,
            trace_id,
            total_ms=total_ms,
            cost=cost,
            requires_correction=requires_correction,
            requires_repair=requires_repair,
            correction_count=correction_count,
            repair_count=repair_count,
            wrong_refusal=wrong_refusal,
        ),
        "trace": _case_trace(trace_id, total_ms, cost, correction_count, repair_count),
        "latency": _case_latency(trace_id, total_ms, cost),
        "artifacts": {
            "eval": {"path": f"{label}-eval.json", "sha256": "a" * 64},
            "trace": {"path": f"{label}-trace.json", "sha256": "b" * 64},
            "latency": {"path": f"{label}-latency.json", "sha256": "c" * 64},
        },
    }


def _case_evaluation(
    case_id,
    label,
    trace_id,
    *,
    total_ms,
    cost,
    requires_correction,
    requires_repair,
    correction_count,
    repair_count,
    wrong_refusal,
):
    return {
        "total_cases": 1,
        "passed_cases": 1,
        "provider_failure_count": 0,
        "provider_retries": 0,
        "total_estimated_cost": cost,
        "feature_flags": {
            "crag": str(label == "candidate").lower(),
            "claim_repair": str(label == "candidate").lower(),
            "semantic_cache": "false",
        },
        "outcome_confusion": {
            "wrong_refusal": wrong_refusal,
            "wrong_answer": 0,
            "wrong_refusal_type": 0,
            "leakage": 0,
        },
        "cases": [{
            "id": case_id,
            "trace_id": trace_id,
            "passed": True,
            "provider_failure": False,
            "requires_correction": requires_correction,
            "requires_repair": requires_repair,
            "correction_count": correction_count,
            "repair_count": repair_count,
            "latency_ms": total_ms,
        }],
    }


def _case_trace(trace_id, total_ms, cost, correction_count, repair_count):
    return {"system_metrics": {
        "query_count": 1,
        "latency_p50_ms": total_ms,
        "latency_p95_ms": total_ms,
        "estimated_cost": cost,
        "correction_rate": correction_count,
        "repair_rate": repair_count,
        "max_corrections_per_query": correction_count,
        "max_repairs_per_query": repair_count,
        "correction_trace_ids": [trace_id] if correction_count else [],
        "repair_trace_ids": [trace_id] if repair_count else [],
        "correction_error_count": 0,
        "retry_rate": 0.0,
    }}


def _stage_latency(value):
    return {
        "sample_count": 1,
        "latency_p50_ms": value,
        "latency_p95_ms": value,
        "latency_max_ms": value,
    }


def _case_latency(trace_id, total_ms, cost):
    stages = {"retrieval": 100, "generation": total_ms - 100, "total": total_ms}
    return {
        "query_count": 1,
        "parse_errors": 0,
        "estimated_cost": cost,
        "stage_summary": {
            name: _stage_latency(value) for name, value in stages.items()
        },
        "traces": [{
            "trace_id_sha256": hashlib.sha256(trace_id.encode("utf-8")).hexdigest(),
            "stages_ms": stages,
            "estimated_cost": cost,
        }],
    }


def _recording_case_pair(calls):
    def run_case_pair(
        _context,
        *,
        series_id,
        ordinal,
        case_id,
        arm_order,
        arm_starts,
    ):
        del _context
        calls.append((series_id, ordinal, case_id, arm_order))
        requirements = {
            "requires_correction": case_id == "correction",
            "requires_repair": case_id == "repair",
        }
        pair = {
            "case_id": case_id,
            "baseline": _case_arm_payload(
                case_id,
                "baseline",
                total_ms=1000 + ordinal,
                cost=1.0,
                wrong_refusal=int(case_id == "correction"),
                **requirements,
            ),
            "candidate": _case_arm_payload(
                case_id,
                "candidate",
                total_ms=1100 + ordinal,
                cost=1.1,
                **requirements,
            ),
        }
        return pair, (*arm_starts, f"{series_id}:{case_id}"), False

    return run_case_pair


def _retrying_case_pair(calls):
    def run_case_pair(
        _context,
        *,
        series_id,
        ordinal,
        case_id,
        arm_order,
        arm_starts,
    ):
        del _context
        calls.append((series_id, ordinal, case_id, arm_order))
        arm = _case_arm_payload(case_id, "candidate", total_ms=1000, cost=1.0)
        arm = {**arm, "eval": {**arm["eval"], "provider_retries": 1}}
        return {"case_id": case_id, "candidate": arm}, (*arm_starts, "start"), True

    return run_case_pair


def test_case_paired_series_aggregates_before_applying_unchanged_gate():
    from scripts.crag_eval.diagnostic_aggregation import build_series_summary

    case_pairs = []
    for case_id, baseline_ms, candidate_ms, correction, repair in (
        ("correction", 1000, 1100, True, False),
        ("repair", 2000, 2200, False, True),
    ):
        case_pairs.append({
            "case_id": case_id,
            "baseline": _case_arm_payload(
                case_id,
                "baseline",
                total_ms=baseline_ms,
                cost=1.0,
                requires_correction=correction,
                requires_repair=repair,
                wrong_refusal=int(case_id == "correction"),
            ),
            "candidate": _case_arm_payload(
                case_id,
                "candidate",
                total_ms=candidate_ms,
                cost=1.1,
                requires_correction=correction,
                requires_repair=repair,
            ),
        })

    report = build_series_summary(
        series_id="series-01",
        arm_order="candidate-first",
        case_pairs=case_pairs,
    )

    assert report["case_count"] == 2
    assert report["case_ids"] == ["correction", "repair"]
    assert report["gate"]["passed"] is True
    assert report["gate"]["checks"]["correction_fixture_present"] is True
    assert report["gate"]["checks"]["repair_fixture_present"] is True
    assert report["baseline"]["latency"]["stage_summary"]["total"][
        "latency_p95_ms"
    ] == 2000
    assert report["candidate"]["latency"]["stage_summary"]["total"][
        "latency_p95_ms"
    ] == 2200
    assert report["candidate"]["latency"]["estimated_cost"] == pytest.approx(2.2)
    assert "cases" not in report["candidate"]["eval"]
    assert "system_metrics" not in report["candidate"]


def test_case_paired_series_rejects_wrong_singleton_trace_identity():
    from scripts.crag_eval.diagnostic_aggregation import build_series_summary

    baseline = _case_arm_payload(
        "case-1", "baseline", total_ms=1000, cost=1.0
    )
    candidate = _case_arm_payload(
        "case-1", "candidate", total_ms=1100, cost=1.1
    )
    candidate["latency"]["traces"][0]["trace_id_sha256"] = "f" * 64

    with pytest.raises(ValueError, match="trace.*selected case"):
        build_series_summary(
            series_id="series-01",
            arm_order="candidate-first",
            case_pairs=[{
                "case_id": "case-1",
                "baseline": baseline,
                "candidate": candidate,
            }],
        )


def test_driver_executes_every_case_in_two_mirrored_adjacent_series(
    monkeypatch,
    tmp_path,
):
    from scripts.crag_eval import run_diagnostic as diagnostic

    case_ids = ("correction", "repair")
    context = SimpleNamespace(
        output=tmp_path / "diagnostic",
        case_ids=case_ids,
        source_commit="a" * 40,
    )
    monkeypatch.setattr(diagnostic, "_prepare_context", lambda *args: context)
    monkeypatch.setattr(diagnostic, "_write_declaration", lambda _context: "b" * 64)
    calls = []
    monkeypatch.setattr(
        diagnostic,
        "_run_case_pair",
        _recording_case_pair(calls),
        raising=False,
    )

    report = diagnostic.run_diagnostic(
        manifest=tmp_path / "manifest.jsonl",
        preflight=tmp_path / "preflight.json",
        provider_smoke=tmp_path / "smoke.json",
        output=context.output,
        trace=tmp_path / "trace.jsonl",
    )

    assert calls == [
        ("series-01", 1, "correction", "candidate-first"),
        ("series-01", 2, "repair", "candidate-first"),
        ("series-02", 1, "correction", "baseline-first"),
        ("series-02", 2, "repair", "baseline-first"),
    ]
    assert report["schema"] == "crag-stage-latency-diagnostic-outcome-v3"
    assert report["status"] == "passed"
    assert report["series_count"] == 2
    assert report["case_pair_count"] == 4
    assert report["controlled_demo_pilot_authorized"] is False
    assert report["default_rollout_authorized"] is False


def test_driver_stops_and_counts_retry_before_series_aggregation(
    monkeypatch,
    tmp_path,
):
    from scripts.crag_eval import run_diagnostic as diagnostic

    context = SimpleNamespace(
        output=tmp_path / "diagnostic",
        case_ids=("case-1", "case-2"),
        source_commit="a" * 40,
    )
    monkeypatch.setattr(diagnostic, "_prepare_context", lambda *args: context)
    monkeypatch.setattr(diagnostic, "_write_declaration", lambda _context: "b" * 64)
    calls = []
    monkeypatch.setattr(
        diagnostic,
        "_run_case_pair",
        _retrying_case_pair(calls),
    )

    report = diagnostic.run_diagnostic(
        manifest=tmp_path / "manifest.jsonl",
        preflight=tmp_path / "preflight.json",
        provider_smoke=tmp_path / "smoke.json",
        output=context.output,
        trace=tmp_path / "trace.jsonl",
    )

    assert calls == [("series-01", 1, "case-1", "candidate-first")]
    assert report["status"] == "inconclusive"
    assert report["series_count"] == 0
    assert report["case_pair_count"] == 0
    assert report["arm_run_count"] == 1
    assert report["provider_retry_count"] == 1
    assert report["formal_window_authorized"] is False


def test_driver_records_execution_error_type_without_secret_message(
    monkeypatch,
    tmp_path,
):
    from scripts.crag_eval import run_diagnostic as diagnostic

    context = SimpleNamespace(
        output=tmp_path / "diagnostic",
        case_ids=("case-1",),
        source_commit="a" * 40,
    )
    monkeypatch.setattr(diagnostic, "_prepare_context", lambda *args: context)
    monkeypatch.setattr(diagnostic, "_write_declaration", lambda _context: "b" * 64)

    def fail(*args, **kwargs):
        raise RuntimeError("Bearer must-not-appear")

    monkeypatch.setattr(diagnostic, "_run_case_pair", fail)

    report = diagnostic.run_diagnostic(
        manifest=tmp_path / "manifest.jsonl",
        preflight=tmp_path / "preflight.json",
        provider_smoke=tmp_path / "smoke.json",
        output=context.output,
        trace=tmp_path / "trace.jsonl",
    )

    assert report["status"] == "inconclusive"
    assert report["execution_failure_count"] == 1
    assert report["execution_failure"] == {"error_type": "RuntimeError"}
    assert "must-not-appear" not in (context.output / "outcome.json").read_text(
        encoding="utf-8"
    )


def _arm_run_context(tmp_path):
    return SimpleNamespace(
        manifest=tmp_path / "manifest.jsonl",
        provider_smoke=tmp_path / "smoke.json",
        provider_configuration_sha256="provider",
        router_mode="offline",
        governance_scope_sha256="scope",
        provider_environment={},
        preflight_report={"fixture_fingerprint": "f" * 64},
    )


def _write_arm_fixture(run_dir, payload):
    (run_dir / "eval.json").write_text(
        json.dumps(payload["eval"]), encoding="utf-8"
    )
    trace_report = {
        **payload["trace"],
        "error_event_count": 0,
        "fallback_event_count": 0,
        "retry_event_count": 0,
    }
    (run_dir / "trace.json").write_text(
        json.dumps(trace_report), encoding="utf-8"
    )
    preflight = {"fixture_fingerprint": "f" * 64, "checked_cases": 1}
    (run_dir / "preflight.json").write_text(
        json.dumps(preflight), encoding="utf-8"
    )


def _write_arm_trace(trace, trace_id):
    event = {
        "ts": "2026-08-11T00:00:01Z",
        "execution_context": "evaluation",
        "trace_id": trace_id,
        "event": "rag_end",
        "final_latency_ms": 1000,
        "estimated_cost": 1.0,
    }
    trace.write_text(json.dumps(event) + "\n", encoding="utf-8")


def _fake_arm_runner(trace_logs):
    def run_arm(
        label,
        manifest,
        output,
        trace,
        *,
        case_id,
        started_at,
        **kwargs,
    ):
        del manifest, kwargs
        trace_logs.append(trace)
        assert not (output / label).exists()
        payload = _case_arm_payload(
            case_id, label, total_ms=1000, cost=1.0
        )
        run_dir = output / label
        run_dir.mkdir(parents=True)
        _write_arm_fixture(run_dir, payload)
        _write_arm_trace(trace, payload["eval"]["cases"][0]["trace_id"])
        return {
            "started_at": started_at,
            "completed_at": "2026-08-11T00:00:02Z",
        }

    return run_arm


def test_case_arms_use_separate_trace_logs(monkeypatch, tmp_path):
    from scripts.crag_eval import run_diagnostic as diagnostic

    context = _arm_run_context(tmp_path)
    monkeypatch.setattr(diagnostic, "_require_inputs_unchanged", lambda _context: None)
    monkeypatch.setattr(
        diagnostic,
        "validate_provider_smoke_for_arms",
        lambda *args, **kwargs: None,
    )
    trace_logs = []
    monkeypatch.setattr(diagnostic, "_run", _fake_arm_runner(trace_logs))

    diagnostic._run_arm(
        context,
        tmp_path / "series-01" / "case-001",
        label="candidate",
        enabled=True,
        arm_starts=("2026-08-11T00:00:00Z",),
        case_id="case-1",
    )
    diagnostic._run_arm(
        context,
        tmp_path / "series-01" / "case-001",
        label="baseline",
        enabled=False,
        arm_starts=("2026-08-11T00:00:00Z",),
        case_id="case-1",
    )

    assert trace_logs == [
        tmp_path / "series-01" / "case-001" / "rag-traces" / "candidate.jsonl",
        tmp_path / "series-01" / "case-001" / "rag-traces" / "baseline.jsonl",
    ]


def test_declaration_precommits_two_mirrored_case_paired_series():
    report = build_diagnostic_declaration(
        source_commit="a" * 40,
        manifest={"path": "manifest.jsonl", "sha256": "b" * 64},
        preflight={
            "path": "preflight.json",
            "sha256": "c" * 64,
            "fixture_fingerprint": "d" * 64,
        },
        provider_configuration_sha256="e" * 64,
        provider_smoke={"path": "smoke.json", "sha256": "f" * 64},
        governance_scope_sha256="1" * 64,
        runner={
            "diagnostic": {"path": "run_diagnostic.py", "sha256": "2" * 64},
            "rollout": {"path": "run_rollout.py", "sha256": "3" * 64},
        },
        case_ids=("case-1", "case-2"),
    )

    assert report["schema"] == "crag-stage-latency-diagnostic-declaration-v3"
    assert report["formal_evidence"] is False
    assert report["measurement_design"] == "mirrored_case_paired_interleaved"
    assert report["trace_strategy"] == "one_private_log_per_series_case_arm"
    assert report["controlled_demo_pilot_authorized"] is False
    assert report["default_rollout_authorized"] is False
    assert report["series_plan"] == [
        {
            "id": "series-01",
            "arm_order": "candidate-first",
            "case_ids": ["case-1", "case-2"],
        },
        {
            "id": "series-02",
            "arm_order": "baseline-first",
            "case_ids": ["case-1", "case-2"],
        },
    ]
    assert report["limits"] == {
        "max_latency_ratio": 1.25,
        "max_cost_ratio": 1.5,
        "max_pair_latency_ratio_spread": 0.10,
        "max_pair_cost_ratio_spread": 0.10,
        "provider_errors": 0,
        "provider_retries": 0,
    }


def test_outcome_aggregates_interleaved_pairs_and_identifies_stage_overhead():
    pairs = [
        {
            "id": "pair-01",
            "gate": {"passed": True},
            "baseline": _arm(total_ms=1000, cost=1.0),
            "candidate": _arm(total_ms=1200, cost=1.4),
        },
        {
            "id": "pair-02",
            "gate": {"passed": True},
            "baseline": _arm(total_ms=1100, cost=1.0),
            "candidate": _arm(total_ms=1250, cost=1.4),
        },
    ]
    for pair in pairs:
        pair["candidate"]["latency"]["stage_summary"]["generation"][
            "latency_p50_ms"
        ] = 500

    report = build_diagnostic_outcome(
        pairs,
        source_commit="a" * 40,
        declaration_sha256="b" * 64,
    )

    assert report["status"] == "passed"
    assert report["diagnostic_target_met"] is True
    assert report["latency_ratio"] == pytest.approx(1225 / 1050)
    assert report["cost_ratio"] == pytest.approx(1.4)
    assert report["stage_deltas_ms"]["generation"] == 200
    assert report["dominant_overhead_stage"] == "generation"


def test_outcome_is_inconclusive_and_fail_closed_on_provider_failure():
    pair = {
        "id": "pair-01",
        "gate": {"passed": False},
        "baseline": _arm(total_ms=1000, cost=1.0, provider_failures=1),
        "candidate": None,
    }

    report = build_diagnostic_outcome(
        [pair],
        source_commit="a" * 40,
        declaration_sha256="b" * 64,
    )

    assert report["status"] == "inconclusive"
    assert report["diagnostic_target_met"] is False
    assert report["latency_ratio"] is None
    assert report["dominant_overhead_stage"] == "unavailable"
    assert report["formal_window_authorized"] is False
    assert report["feature_enablement_authorized"] is False


def test_outcome_is_inconclusive_and_fail_closed_on_provider_retry():
    pairs = [
        {
            "id": "pair-01",
            "gate": {"passed": True},
            "baseline": _arm(total_ms=1000, cost=1.0),
            "candidate": _arm(
                total_ms=1100,
                cost=1.2,
                provider_retries=1,
            ),
        },
        {
            "id": "pair-02",
            "gate": {"passed": True},
            "baseline": _arm(total_ms=1000, cost=1.0),
            "candidate": _arm(total_ms=1100, cost=1.2),
        },
    ]

    report = build_diagnostic_outcome(
        pairs,
        source_commit="a" * 40,
        declaration_sha256="b" * 64,
    )

    assert report["status"] == "inconclusive"
    assert report["provider_retry_count"] == 1
    assert report["diagnostic_target_met"] is False


def test_outcome_does_not_prescribe_code_fix_when_arm_order_changes_result():
    pairs = [
        {
            "id": "pair-01",
            "gate": {"passed": True},
            "baseline": _arm(total_ms=1000, cost=1.0),
            "candidate": _arm(total_ms=1200, cost=1.2),
        },
        {
            "id": "pair-02",
            "gate": {"passed": False},
            "baseline": _arm(total_ms=1000, cost=1.0),
            "candidate": _arm(total_ms=1300, cost=1.2),
        },
    ]

    report = build_diagnostic_outcome(
        pairs,
        source_commit="a" * 40,
        declaration_sha256="b" * 64,
    )

    assert report["status"] == "inconclusive"
    assert report["arm_order_consistent"] is False
    assert report["diagnostic_target_met"] is False
    assert "new declaration" in report["next_action"]


def test_outcome_detects_material_effect_size_variance_when_gates_agree():
    pairs = [
        {
            "id": "pair-01",
            "gate": {"passed": False, "checks": {"latency_within_budget": False}},
            "baseline": _arm(total_ms=1000, cost=1.0),
            "candidate": _arm(total_ms=1300, cost=1.2),
        },
        {
            "id": "pair-02",
            "gate": {"passed": False, "checks": {"latency_within_budget": False}},
            "baseline": _arm(total_ms=1000, cost=1.0),
            "candidate": _arm(total_ms=1700, cost=1.2),
        },
    ]

    report = build_diagnostic_outcome(
        pairs,
        source_commit="a" * 40,
        declaration_sha256="b" * 64,
    )

    assert report["status"] == "inconclusive"
    assert report["arm_order_consistent"] is False
    assert report["pair_latency_ratios"] == [1.3, 1.7]
    assert report["latency_ratio_spread"] == pytest.approx(0.4)


def test_preflight_must_bind_the_crag_collection_and_manifest_case_count():
    report = {
        "schema": "crag-fixture-preflight-v1",
        "passed": True,
        "collection": "MechChatbot_CRAG_Eval_v1",
        "checked_cases": 9,
        "fixture_fingerprint": "a" * 64,
    }

    validate_preflight_report(report, expected_case_count=9)

    with pytest.raises(ValueError, match="collection"):
        validate_preflight_report(
            {**report, "collection": "production"},
            expected_case_count=9,
        )
    with pytest.raises(ValueError, match="case count"):
        validate_preflight_report(report, expected_case_count=10)


def test_driver_accepts_only_the_reviewed_canonical_manifest(tmp_path):
    canonical = (
        Path(__file__).resolve().parents[2]
        / "data"
        / "crag_eval_v1"
        / "eval_manifest.jsonl"
    )
    validate_canonical_manifest(canonical)

    copied = tmp_path / "eval_manifest.jsonl"
    copied.write_bytes(canonical.read_bytes())
    with pytest.raises(ValueError, match="canonical CRAG manifest path"):
        validate_canonical_manifest(copied)


def test_driver_requires_explicit_opt_in(monkeypatch, tmp_path):
    monkeypatch.delenv("RAG_CRAG_DIAGNOSTIC_OPT_IN", raising=False)

    with pytest.raises(RuntimeError, match="RAG_CRAG_DIAGNOSTIC_OPT_IN"):
        run_diagnostic(
            manifest=tmp_path / "missing-manifest.jsonl",
            preflight=tmp_path / "missing-preflight.json",
            provider_smoke=tmp_path / "missing-smoke.json",
            output=tmp_path / "output",
            trace=tmp_path / "trace.jsonl",
        )


def test_driver_refuses_to_overwrite_output_or_trace(monkeypatch, tmp_path):
    monkeypatch.setenv("RAG_CRAG_DIAGNOSTIC_OPT_IN", "1")
    manifest = (
        Path(__file__).resolve().parents[2]
        / "data"
        / "crag_eval_v1"
        / "eval_manifest.jsonl"
    )
    preflight = tmp_path / "preflight.json"
    smoke = tmp_path / "smoke.json"
    for path in (preflight, smoke):
        path.write_text("{}\n", encoding="utf-8")
    output = tmp_path / "output"
    output.mkdir()
    (output / "existing.json").write_text("{}\n", encoding="utf-8")

    with pytest.raises(ValueError, match="overwrite"):
        run_diagnostic(
            manifest=manifest,
            preflight=preflight,
            provider_smoke=smoke,
            output=output,
            trace=tmp_path / "trace.jsonl",
        )

    (output / "existing.json").unlink()
    trace = tmp_path / "trace.jsonl"
    trace.write_text("existing evidence\n", encoding="utf-8")
    with pytest.raises(ValueError, match="trace must be new or empty"):
        run_diagnostic(
            manifest=manifest,
            preflight=preflight,
            provider_smoke=smoke,
            output=output,
            trace=trace,
        )
