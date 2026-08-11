from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from scripts.graph_eval.run_diagnostic import (
    build_diagnostic_declaration,
    build_diagnostic_outcome,
    run_diagnostic,
    validate_canonical_manifest,
    validate_preflight_report,
)


pytestmark = pytest.mark.unit


def _arm(
    *,
    latency_p95_ms: int,
    cost: float,
    provider_failures: int = 0,
    provider_retries: int = 0,
    generation_p95_ms: int = 300,
) -> dict:
    return {
        "eval": {
            "provider_failure_count": provider_failures,
            "provider_retries": provider_retries,
            "latency_p95_ms": latency_p95_ms,
            "total_estimated_cost": cost,
        },
        "latency": {
            "stage_summary": {
                "retrieval": {"latency_p95_ms": 100},
                "rerank": {"latency_p95_ms": 20},
                "generation": {"latency_p95_ms": generation_p95_ms},
                "total": {"latency_p95_ms": latency_p95_ms},
            },
        },
    }


def test_declaration_precommits_reversed_pairs_as_non_formal_evidence():
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

    assert report["scope"] == "supporting_diagnostic_only"
    assert report["formal_evidence"] is False
    assert report["pair_order"] == [
        {"id": "pair-01", "arm_order": "candidate-first"},
        {"id": "pair-02", "arm_order": "baseline-first"},
    ]
    assert report["limits"] == {
        "max_latency_p95_ratio": 1.25,
        "max_cost_ratio": 1.5,
        "max_pair_latency_ratio_spread": 0.10,
        "max_pair_cost_ratio_spread": 0.10,
        "provider_errors": 0,
        "provider_retries": 0,
    }
    assert report["review_requirement"] == {
        "mode": "multi_reviewer",
        "source": "independent",
    }
    assert report["formal_window_authorized"] is False
    assert report["feature_enablement_authorized"] is False


def test_outcome_passes_only_stable_quality_green_pairs_with_p95_margin():
    pairs = [
        {
            "id": "pair-01",
            "arm_order": "candidate-first",
            "gate": {"passed": True, "checks": {"quality": True}},
            "baseline": _arm(latency_p95_ms=1000, cost=1.0),
            "candidate": _arm(
                latency_p95_ms=1200,
                cost=1.4,
                generation_p95_ms=500,
            ),
        },
        {
            "id": "pair-02",
            "arm_order": "baseline-first",
            "gate": {"passed": True, "checks": {"quality": True}},
            "baseline": _arm(latency_p95_ms=1100, cost=1.0),
            "candidate": _arm(
                latency_p95_ms=1280,
                cost=1.4,
                generation_p95_ms=500,
            ),
        },
    ]

    report = build_diagnostic_outcome(
        pairs,
        source_commit="a" * 40,
        declaration_sha256="b" * 64,
    )

    assert report["status"] == "passed"
    assert report["diagnostic_target_met"] is True
    assert report["latency_p95_ratio"] == pytest.approx(
        ((1200 / 1000) + (1280 / 1100)) / 2
    )
    assert report["cost_ratio"] == pytest.approx(1.4)
    assert report["stage_p95_deltas_ms"]["generation"] == 200
    assert report["dominant_overhead_stage"] == "generation"
    assert report["formal_window_authorized"] is False
    assert report["feature_enablement_authorized"] is False


@pytest.mark.parametrize("failure_field", ["provider_failures", "provider_retries"])
def test_outcome_is_inconclusive_on_provider_variance(failure_field):
    arm_kwargs = {failure_field: 1}
    pair = {
        "id": "pair-01",
        "arm_order": "candidate-first",
        "gate": {"passed": False},
        "baseline": _arm(latency_p95_ms=1000, cost=1.0, **arm_kwargs),
        "candidate": None,
    }

    report = build_diagnostic_outcome(
        [pair],
        source_commit="a" * 40,
        declaration_sha256="b" * 64,
    )

    assert report["status"] == "inconclusive"
    assert report["diagnostic_target_met"] is False
    assert report["latency_p95_ratio"] is None
    assert report["formal_window_authorized"] is False


def test_outcome_is_inconclusive_when_arm_order_changes_effect_size():
    pairs = [
        {
            "id": "pair-01",
            "arm_order": "candidate-first",
            "gate": {"passed": True, "checks": {"quality": True}},
            "baseline": _arm(latency_p95_ms=1000, cost=1.0),
            "candidate": _arm(latency_p95_ms=1200, cost=1.2),
        },
        {
            "id": "pair-02",
            "arm_order": "baseline-first",
            "gate": {"passed": False, "checks": {"quality": True}},
            "baseline": _arm(latency_p95_ms=1000, cost=1.0),
            "candidate": _arm(latency_p95_ms=1600, cost=1.2),
        },
    ]

    report = build_diagnostic_outcome(
        pairs,
        source_commit="a" * 40,
        declaration_sha256="b" * 64,
    )

    assert report["status"] == "inconclusive"
    assert report["arm_order_consistent"] is False
    assert report["pair_latency_p95_ratios"] == [1.2, 1.6]
    assert report["diagnostic_target_met"] is False
    assert "new declaration" in report["next_action"]


def test_preflight_binds_graph_collection_case_count_and_review_report():
    report = {
        "schema": "graph-fixture-preflight-v1",
        "passed": True,
        "batch": "graph-eval-v1",
        "collection": "MechChatbot_Graph_Eval_v1",
        "checked_cases": 17,
        "fixture_fingerprint": "a" * 64,
        "graph_report": {
            "schema": "graph-readiness-v1",
            "review_mode": "multi_reviewer",
            "review_sample_source": "independent",
            "reviewer_count": 2,
        },
    }

    validate_preflight_report(report, expected_case_count=17)

    with pytest.raises(ValueError, match="collection"):
        validate_preflight_report(
            {**report, "collection": "production"},
            expected_case_count=17,
        )
    with pytest.raises(ValueError, match="case count"):
        validate_preflight_report(report, expected_case_count=18)
    with pytest.raises(ValueError, match="graph readiness"):
        validate_preflight_report(
            {**report, "graph_report": {}},
            expected_case_count=17,
        )
    with pytest.raises(ValueError, match="independent multi-reviewer"):
        validate_preflight_report(
            {
                **report,
                "graph_report": {
                    **report["graph_report"],
                    "review_mode": "single_owner",
                    "review_sample_source": "owner_review",
                },
            },
            expected_case_count=17,
        )


def test_driver_accepts_only_the_canonical_graph_manifest(tmp_path):
    canonical = (
        Path(__file__).resolve().parents[2]
        / "data"
        / "graph_eval_v1"
        / "eval_manifest.jsonl"
    )
    validate_canonical_manifest(canonical)

    copied = tmp_path / "eval_manifest.jsonl"
    copied.write_bytes(canonical.read_bytes())
    with pytest.raises(ValueError, match="canonical Graph manifest path"):
        validate_canonical_manifest(copied)


def test_driver_requires_runner_to_exist_in_source_commit(monkeypatch):
    from scripts.graph_eval import run_diagnostic as diagnostic

    calls = []

    def fake_run(command, **kwargs):
        calls.append(command)
        return SimpleNamespace(returncode=1)

    monkeypatch.setattr(diagnostic.subprocess, "run", fake_run)

    with pytest.raises(RuntimeError, match="runner must be committed"):
        diagnostic._require_runner_in_source_commit()

    assert calls == [
        [
            "git",
            "cat-file",
            "-e",
            "HEAD:scripts/graph_eval/run_diagnostic.py",
        ]
    ]


def test_driver_requires_both_diagnostic_and_fixture_opt_in(monkeypatch, tmp_path):
    monkeypatch.delenv("RAG_GRAPH_DIAGNOSTIC_OPT_IN", raising=False)
    monkeypatch.delenv("RUN_GRAPH_EVAL_FIXTURE", raising=False)

    with pytest.raises(RuntimeError, match="RAG_GRAPH_DIAGNOSTIC_OPT_IN"):
        run_diagnostic(
            manifest=tmp_path / "missing-manifest.jsonl",
            preflight=tmp_path / "missing-preflight.json",
            provider_smoke=tmp_path / "missing-smoke.json",
            output=tmp_path / "output",
            trace=tmp_path / "trace.jsonl",
        )

    monkeypatch.setenv("RAG_GRAPH_DIAGNOSTIC_OPT_IN", "1")
    with pytest.raises(RuntimeError, match="RUN_GRAPH_EVAL_FIXTURE"):
        run_diagnostic(
            manifest=tmp_path / "missing-manifest.jsonl",
            preflight=tmp_path / "missing-preflight.json",
            provider_smoke=tmp_path / "missing-smoke.json",
            output=tmp_path / "output",
            trace=tmp_path / "trace.jsonl",
        )


def test_driver_refuses_to_overwrite_output_or_trace(monkeypatch, tmp_path):
    monkeypatch.setenv("RAG_GRAPH_DIAGNOSTIC_OPT_IN", "1")
    monkeypatch.setenv("RUN_GRAPH_EVAL_FIXTURE", "1")
    manifest = (
        Path(__file__).resolve().parents[2]
        / "data"
        / "graph_eval_v1"
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


def test_driver_executes_exact_predeclared_arm_order(monkeypatch, tmp_path):
    from scripts.graph_eval import run_diagnostic as diagnostic

    context = SimpleNamespace(
        output=tmp_path / "output",
        source_commit="a" * 40,
    )
    observed = []

    monkeypatch.setattr(diagnostic, "_prepare_context", lambda *args: context)
    monkeypatch.setattr(diagnostic, "_write_declaration", lambda unused: "b" * 64)

    def fake_run_arm(unused_context, pair_dir, *, label, enabled, arm_starts):
        observed.append((pair_dir.name, label, enabled))
        return _arm(
            latency_p95_ms=1200 if enabled else 1000,
            cost=1.4 if enabled else 1.0,
            generation_p95_ms=500 if enabled else 300,
        )

    monkeypatch.setattr(diagnostic, "_run_arm", fake_run_arm)
    monkeypatch.setattr(
        diagnostic,
        "_run_gate",
        lambda unused_context, unused_pair_dir: {
            "passed": True,
            "checks": {"quality": True},
        },
    )

    report = run_diagnostic(
        manifest=tmp_path / "manifest.jsonl",
        preflight=tmp_path / "preflight.json",
        provider_smoke=tmp_path / "smoke.json",
        output=context.output,
        trace=tmp_path / "trace.jsonl",
    )

    assert observed == [
        ("pair-01", "candidate", True),
        ("pair-01", "baseline", False),
        ("pair-02", "baseline", False),
        ("pair-02", "candidate", True),
    ]
    assert report["status"] == "passed"
    assert (context.output / "pair-01" / "summary.json").is_file()
    assert (context.output / "pair-02" / "summary.json").is_file()
    assert (context.output / "outcome.json").is_file()


def test_driver_stops_without_carry_forward_on_provider_retry(
    monkeypatch,
    tmp_path,
):
    from scripts.graph_eval import run_diagnostic as diagnostic

    context = SimpleNamespace(
        output=tmp_path / "output",
        source_commit="a" * 40,
    )
    observed = []
    monkeypatch.setattr(diagnostic, "_prepare_context", lambda *args: context)
    monkeypatch.setattr(diagnostic, "_write_declaration", lambda unused: "b" * 64)

    def fake_run_arm(unused_context, pair_dir, *, label, enabled, arm_starts):
        observed.append((pair_dir.name, label))
        return _arm(
            latency_p95_ms=1200,
            cost=1.4,
            provider_retries=1,
        )

    monkeypatch.setattr(diagnostic, "_run_arm", fake_run_arm)
    monkeypatch.setattr(
        diagnostic,
        "_run_gate",
        lambda *args: pytest.fail("gate ran after provider retry"),
    )

    report = run_diagnostic(
        manifest=tmp_path / "manifest.jsonl",
        preflight=tmp_path / "preflight.json",
        provider_smoke=tmp_path / "smoke.json",
        output=context.output,
        trace=tmp_path / "trace.jsonl",
    )

    assert observed == [("pair-01", "candidate")]
    assert report["status"] == "inconclusive"
    assert report["provider_retry_count"] == 1
    assert not (context.output / "pair-02").exists()


def test_arm_execution_binds_smoke_preflight_and_privacy_safe_metrics(
    monkeypatch,
    tmp_path,
):
    from scripts.graph_eval import run_diagnostic as diagnostic

    graph_report = {"schema": "graph-readiness-v1", "review_sample_count": 21}
    context = SimpleNamespace(
        provider_smoke=tmp_path / "smoke.json",
        provider_configuration_sha256="a" * 64,
        manifest=tmp_path / "manifest.jsonl",
        trace=tmp_path / "trace.jsonl",
        governance_scope_sha256="b" * 64,
        provider_environment={"GPT_MODEL_NAME": "test-model"},
        preflight_report={
            "fixture_fingerprint": "c" * 64,
            "graph_report": graph_report,
        },
    )
    context.trace.write_text("", encoding="utf-8")
    pair_dir = tmp_path / "pair-01"
    smoke_calls = []
    monkeypatch.setattr(diagnostic, "_require_inputs_unchanged", lambda unused: None)
    monkeypatch.setattr(
        diagnostic,
        "validate_provider_smoke_for_arms",
        lambda *args, **kwargs: smoke_calls.append(kwargs["arm_started_at"]),
    )

    def fake_run(label, manifest, output, trace, **kwargs):
        run_dir = output / label
        run_dir.mkdir(parents=True)
        (run_dir / "eval.json").write_text(
            """{
              "provider_failure_count": 0,
              "provider_retries": 0,
              "total_cases": 17,
              "passed_cases": 17,
              "latency_p95_ms": 1200,
              "total_estimated_cost": 1.4
            }""",
            encoding="utf-8",
        )
        (run_dir / "trace.json").write_text(
            '{"schema":"rag-refusal-snapshot-v1"}',
            encoding="utf-8",
        )
        (run_dir / "preflight.json").write_text(
            json.dumps({
                "fixture_fingerprint": "c" * 64,
                "graph_report": graph_report,
            }),
            encoding="utf-8",
        )
        return {
            "started_at": kwargs["started_at"],
            "completed_at": "2026-08-10T00:01:00Z",
        }

    monkeypatch.setattr(diagnostic, "_run", fake_run)
    monkeypatch.setattr(
        diagnostic,
        "build_latency_breakdown",
        lambda *args, **kwargs: {
            "schema": "crag-latency-breakdown-v1",
            "stage_summary": {
                "generation": {"latency_p95_ms": 500},
                "total": {"latency_p95_ms": 1200},
            },
        },
    )

    arm = diagnostic._run_arm(
        context,
        pair_dir,
        label="candidate",
        enabled=True,
        arm_starts=("2026-08-10T00:00:00Z",),
    )

    assert smoke_calls == [("2026-08-10T00:00:00Z",)]
    assert arm["eval"] == {
        "provider_failure_count": 0,
        "provider_retries": 0,
        "total_cases": 17,
        "passed_cases": 17,
        "latency_p95_ms": 1200,
        "total_estimated_cost": 1.4,
    }
    assert arm["latency"]["stage_summary"]["generation"] == {
        "latency_p95_ms": 500
    }
    assert set(arm["artifacts"]) == {"eval", "trace", "latency"}
