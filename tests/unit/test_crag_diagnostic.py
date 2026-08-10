from __future__ import annotations

from pathlib import Path

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


def test_declaration_precommits_two_opposite_arm_orders():
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
        runner={"path": "run_diagnostic.py", "sha256": "2" * 64},
    )

    assert report["formal_evidence"] is False
    assert report["pair_order"] == [
        {"id": "pair-01", "arm_order": "candidate-first"},
        {"id": "pair-02", "arm_order": "baseline-first"},
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
