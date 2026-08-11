from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from scripts.graph_eval.run_diagnostic import (
    build_case_plan,
    build_diagnostic_declaration,
    build_diagnostic_outcome,
    probe_qdrant_health,
    run_diagnostic,
    validate_canonical_manifest,
    validate_preflight_report,
)


pytestmark = pytest.mark.unit
SINGLE_CASE_PLAN = [
    {"ordinal": 1, "case_id": "case-1", "arm_order": "candidate-first"},
]


def _arm(
    *,
    latency_p95_ms: int,
    cost: float,
    provider_failures: int = 0,
    provider_retries: int = 0,
    generation_p95_ms: int | None = 300,
    trace_errors: int = 0,
    trace_fallbacks: int = 0,
    trace_retries: int = 0,
) -> dict:
    return {
        "eval": {
            "provider_failure_count": provider_failures,
            "provider_retries": provider_retries,
            "total_cases": 1,
            "passed_cases": 1,
            "latency_p95_ms": latency_p95_ms,
            "total_estimated_cost": cost,
        },
        "latency": {
            "stage_summary": {
                "retrieval": {"latency_p95_ms": 100},
                "rerank": {"latency_p95_ms": 20},
                **(
                    {"generation": {"latency_p95_ms": generation_p95_ms}}
                    if generation_p95_ms is not None
                    else {}
                ),
                "total": {"latency_p95_ms": latency_p95_ms},
            },
        },
        "trace": {
            "error_event_count": trace_errors,
            "fallback_event_count": trace_fallbacks,
            "retry_event_count": trace_retries,
        },
    }


def test_case_plan_alternates_arm_order_in_canonical_order():
    report = build_case_plan(
        [
            {"id": "case-a", "value": 1},
            {"id": "case-b", "value": 2},
        ]
    )

    assert report == [
        {
            "ordinal": 1,
            "case_id": "case-a",
            "arm_order": "candidate-first",
        },
        {
            "ordinal": 2,
            "case_id": "case-b",
            "arm_order": "baseline-first",
        },
    ]


def test_declaration_precommits_interleaved_case_pairs_as_non_formal_evidence():
    case_plan = [
        {
            "ordinal": 1,
            "case_id": "graph-case-01",
            "arm_order": "candidate-first",
        },
        {
            "ordinal": 2,
            "case_id": "graph-case-02",
            "arm_order": "baseline-first",
        },
    ]
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
        case_plan=case_plan,
    )

    assert report["schema"] == "graph-latency-diagnostic-declaration-v2"
    assert report["scope"] == "supporting_diagnostic_only"
    assert report["formal_evidence"] is False
    assert report["measurement_design"] == "case_paired_interleaved"
    assert report["case_plan"] == case_plan
    assert report["health_gate"] == {
        "before_each_case_pair": True,
        "collection": "MechChatbot_Graph_Eval_v1",
        "required_status": "passed",
    }
    assert report["limits"] == {
        "max_latency_p95_ratio": 1.25,
        "max_cost_ratio": 1.5,
        "max_arm_order_latency_ratio_spread": 0.10,
        "provider_errors": 0,
        "provider_retries": 0,
    }
    assert report["review_requirement"] == {
        "mode": "multi_reviewer",
        "source": "independent",
    }
    assert report["stage_breakdown"] == ["retrieval", "generation"]
    assert report["stage_evidence_requirement"] == {
        "retrieval": "every_case_both_arms",
        "generation": "same_case_presence_both_arms_and_at_least_one_pair",
    }
    assert "fallback" in " ".join(report["stop_rules"])
    assert report["formal_window_authorized"] is False
    assert report["feature_enablement_authorized"] is False


def test_qdrant_health_probe_is_read_only_and_secret_safe():
    observed = {}

    class Client:
        def scroll(self, **kwargs):
            observed.update(kwargs)
            return [object()], None

    class Runtime:
        client = Client()

        def close(self):
            observed["closed"] = True

    def build_runtime(settings, **kwargs):
        observed["collection"] = settings.collection
        observed.update(kwargs)
        return Runtime()

    settings = SimpleNamespace(
        QDRANT_URL="https://secret-qdrant.example",
        QDRANT_API_KEY="secret-key",
        QDRANT_COLLECTION="production",
        EMBEDDING_MODEL="model",
        EMBEDDING_DEVICE="cpu",
        EMBEDDING_DIM=1024,
    )

    report = probe_qdrant_health(settings, runtime_builder=build_runtime)

    assert report["passed"] is True
    assert report["collection"] == "MechChatbot_Graph_Eval_v1"
    assert report["point_observed"] is True
    assert observed == {
        "collection": "MechChatbot_Graph_Eval_v1",
        "timeout_seconds": 10,
        "collection_name": "MechChatbot_Graph_Eval_v1",
        "limit": 1,
        "with_payload": False,
        "with_vectors": False,
        "closed": True,
    }
    assert "secret" not in json.dumps(report)

    def fail_runtime(unused_settings, **unused_kwargs):
        raise RuntimeError("secret endpoint transport details")

    failed = probe_qdrant_health(settings, runtime_builder=fail_runtime)
    assert failed["passed"] is False
    assert failed["error_type"] == "RuntimeError"
    assert "secret" not in json.dumps(failed)


def test_outcome_uses_suite_p95_and_separate_stage_metrics():
    case_plan = [
        {"ordinal": 1, "case_id": "case-1", "arm_order": "candidate-first"},
        {"ordinal": 2, "case_id": "case-2", "arm_order": "baseline-first"},
    ]
    pairs = [
        {
            "id": "case-1",
            "ordinal": 1,
            "arm_order": "candidate-first",
            "health": {"passed": True},
            "baseline": _arm(latency_p95_ms=1000, cost=1.0),
            "candidate": _arm(
                latency_p95_ms=1200,
                cost=1.4,
                generation_p95_ms=500,
            ),
        },
        {
            "id": "case-2",
            "ordinal": 2,
            "arm_order": "baseline-first",
            "health": {"passed": True},
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
        case_plan=case_plan,
        source_commit="a" * 40,
        declaration_sha256="b" * 64,
    )

    assert report["status"] == "passed"
    assert report["diagnostic_target_met"] is True
    assert report["latency_p95_ratio"] == pytest.approx(1280 / 1100)
    assert report["paired_latency_ratio_median"] == pytest.approx(
        ((1200 / 1000) + (1280 / 1100)) / 2
    )
    assert report["cost_ratio"] == pytest.approx(1.4)
    assert report["stage_metrics"]["generation"] == {
        "baseline_sample_count": 2,
        "candidate_sample_count": 2,
        "baseline_p95_ms": 300.0,
        "candidate_p95_ms": 500.0,
        "delta_p95_ms": 200.0,
        "ratio": pytest.approx(500 / 300),
    }
    assert report["stage_metrics"]["retrieval"]["delta_p95_ms"] == 0.0
    assert report["dominant_overhead_stage"] == "generation"
    assert report["formal_window_authorized"] is False
    assert report["feature_enablement_authorized"] is False


@pytest.mark.parametrize("failure_field", ["provider_failures", "provider_retries"])
def test_outcome_is_inconclusive_on_provider_variance(failure_field):
    arm_kwargs = {failure_field: 1}
    pair = {
        "id": "case-1",
        "ordinal": 1,
        "arm_order": "candidate-first",
        "health": {"passed": True},
        "baseline": _arm(latency_p95_ms=1000, cost=1.0, **arm_kwargs),
        "candidate": None,
    }

    report = build_diagnostic_outcome(
        [pair],
        case_plan=SINGLE_CASE_PLAN,
        source_commit="a" * 40,
        declaration_sha256="b" * 64,
    )

    assert report["status"] == "inconclusive"
    assert report["diagnostic_target_met"] is False
    assert report["latency_p95_ratio"] is None
    assert report["formal_window_authorized"] is False


def test_outcome_is_inconclusive_on_retrieval_transport_error():
    pair = {
        "id": "case-1",
        "ordinal": 1,
        "arm_order": "candidate-first",
        "health": {"passed": True},
        "baseline": _arm(
            latency_p95_ms=1000,
            cost=1.0,
            trace_errors=1,
        ),
        "candidate": None,
    }

    report = build_diagnostic_outcome(
        [pair],
        case_plan=SINGLE_CASE_PLAN,
        source_commit="a" * 40,
        declaration_sha256="b" * 64,
    )

    assert report["status"] == "inconclusive"
    assert report["provider_failure_count"] == 1
    assert report["diagnostic_target_met"] is False


@pytest.mark.parametrize(
    ("arm_kwargs", "count_field"),
    [
        ({"trace_fallbacks": 1}, "provider_failure_count"),
        ({"trace_retries": 1}, "provider_retry_count"),
    ],
)
def test_outcome_is_inconclusive_on_trace_fallback_or_retry(
    arm_kwargs,
    count_field,
):
    pair = {
        "id": "case-1",
        "ordinal": 1,
        "arm_order": "candidate-first",
        "health": {"passed": True},
        "baseline": _arm(latency_p95_ms=1000, cost=1.0, **arm_kwargs),
        "candidate": None,
    }

    report = build_diagnostic_outcome(
        [pair],
        case_plan=SINGLE_CASE_PLAN,
        source_commit="a" * 40,
        declaration_sha256="b" * 64,
    )

    assert report["status"] == "inconclusive"
    assert report[count_field] == 1


def test_outcome_is_inconclusive_when_arm_order_changes_effect_size():
    pairs = [
        {
            "id": "case-1",
            "ordinal": 1,
            "arm_order": "candidate-first",
            "health": {"passed": True},
            "baseline": _arm(latency_p95_ms=1000, cost=1.0),
            "candidate": _arm(latency_p95_ms=1200, cost=1.2),
        },
        {
            "id": "case-2",
            "ordinal": 2,
            "arm_order": "baseline-first",
            "health": {"passed": True},
            "baseline": _arm(latency_p95_ms=1000, cost=1.0),
            "candidate": _arm(latency_p95_ms=1600, cost=1.2),
        },
    ]

    report = build_diagnostic_outcome(
        pairs,
        case_plan=[
            {"ordinal": 1, "case_id": "case-1", "arm_order": "candidate-first"},
            {"ordinal": 2, "case_id": "case-2", "arm_order": "baseline-first"},
        ],
        source_commit="a" * 40,
        declaration_sha256="b" * 64,
    )

    assert report["status"] == "inconclusive"
    assert report["arm_order_consistent"] is False
    assert report["paired_latency_ratios"] == [1.2, 1.6]
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
        "case_fixture_fingerprints": {
            f"case-{index}": "a" * 64
            for index in range(17)
        },
        "graph_report": {
            "schema": "graph-readiness-v1",
            "review_mode": "multi_reviewer",
            "review_sample_source": "independent",
            "reviewer_count": 2,
        },
    }

    expected_case_ids = set(report["case_fixture_fingerprints"])
    validate_preflight_report(
        report,
        expected_case_count=17,
        expected_case_ids=expected_case_ids,
    )

    with pytest.raises(ValueError, match="case ids"):
        validate_preflight_report(
            report,
            expected_case_count=17,
            expected_case_ids={*expected_case_ids, "unexpected"},
        )

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


def test_latency_report_requires_complete_parse_clean_trace_coverage():
    from scripts.graph_eval import run_diagnostic as diagnostic

    diagnostic._validate_latency_report(
        {
            "query_count": 17,
            "parse_errors": 0,
            "stage_summary": {
                "retrieval": {"sample_count": 17},
                "generation": {"sample_count": 15},
            },
        },
        expected_case_count=17,
    )
    with pytest.raises(RuntimeError, match="query count"):
        diagnostic._validate_latency_report(
            {"query_count": 0, "parse_errors": 0},
            expected_case_count=17,
        )
    with pytest.raises(RuntimeError, match="parse errors"):
        diagnostic._validate_latency_report(
            {
                "query_count": 17,
                "parse_errors": 1,
                "stage_summary": {"retrieval": {"sample_count": 17}},
            },
            expected_case_count=17,
        )
    with pytest.raises(RuntimeError, match="retrieval stage"):
        diagnostic._validate_latency_report(
            {"query_count": 17, "parse_errors": 0, "stage_summary": {}},
            expected_case_count=17,
        )


def test_outcome_requires_balanced_generation_stage_evidence():
    no_generation = _arm(
        latency_p95_ms=1000,
        cost=1.0,
        generation_p95_ms=None,
    )
    pair = {
        "id": "case-1",
        "ordinal": 1,
        "arm_order": "candidate-first",
        "health": {"passed": True},
        "baseline": no_generation,
        "candidate": no_generation,
    }

    missing = build_diagnostic_outcome(
        [pair],
        case_plan=SINGLE_CASE_PLAN,
        source_commit="a" * 40,
        declaration_sha256="b" * 64,
    )

    assert missing["status"] == "inconclusive"
    assert missing["stage_evidence_complete"] is False
    assert missing["diagnostic_target_met"] is False

    asymmetric = {
        **pair,
        "candidate": _arm(latency_p95_ms=1000, cost=1.0),
    }
    report = build_diagnostic_outcome(
        [asymmetric],
        case_plan=SINGLE_CASE_PLAN,
        source_commit="a" * 40,
        declaration_sha256="b" * 64,
    )

    assert report["status"] == "inconclusive"
    assert report["stage_evidence_complete"] is False


def test_arm_preflight_allows_only_case_scoped_coverage_changes(tmp_path):
    from scripts.graph_eval import run_diagnostic as diagnostic

    common = {
        "schema": "graph-readiness-v1",
        "approved_edge_count": 21,
        "approved_edge_ids": list(range(1, 22)),
        "review_mode": "multi_reviewer",
        "review_sample_source": "independent",
        "reviewer_count": 2,
        "reviewed_edge_precision": 1.0,
        "provenance_completeness": 1.0,
        "pending_serving_edges": 0,
        "workflow_fixture_passed": True,
    }
    full_graph = {
        **common,
        "coverage_numerator": 10,
        "coverage_denominator": 10,
        "structured_coverage": 1.0,
        "domain_coverage": {
            "Technical": True,
            "Production": True,
            "Maintenance": True,
        },
    }
    case_graph = {
        **common,
        "coverage_numerator": 1,
        "coverage_denominator": 1,
        "structured_coverage": 1.0,
        "domain_coverage": {"Technical": True},
    }
    context = SimpleNamespace(
        preflight_report={
            "fixture_fingerprint": "a" * 64,
            "case_fixture_fingerprints": {"case-a": "b" * 64},
            "graph_report": full_graph,
        },
    )
    path = tmp_path / "preflight.json"
    report = {
        "schema": "graph-fixture-preflight-v1",
        "passed": True,
        "batch": "graph-eval-v1",
        "collection": "MechChatbot_Graph_Eval_v1",
        "checked_cases": 1,
        "fixture_fingerprint": "b" * 64,
        "case_fixture_fingerprints": {"case-a": "b" * 64},
        "case_resolutions": {"case-a": {}},
        "graph_report": case_graph,
    }
    path.write_text(json.dumps(report), encoding="utf-8")

    diagnostic._verify_arm_preflight(context, path, case_id="case-a")

    report["graph_report"] = {**case_graph, "reviewer_count": 1}
    path.write_text(json.dumps(report), encoding="utf-8")
    with pytest.raises(RuntimeError, match="governance"):
        diagnostic._verify_arm_preflight(context, path, case_id="case-a")

    report["graph_report"] = case_graph
    report["fixture_fingerprint"] = "c" * 64
    report["case_fixture_fingerprints"] = {"case-a": "c" * 64}
    path.write_text(json.dumps(report), encoding="utf-8")
    with pytest.raises(RuntimeError, match="fixture snapshot"):
        diagnostic._verify_arm_preflight(context, path, case_id="case-a")


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
    returncodes = iter((0, 1))

    def fake_run(command, **kwargs):
        calls.append(command)
        return SimpleNamespace(returncode=next(returncodes))

    monkeypatch.setattr(diagnostic.subprocess, "run", fake_run)

    with pytest.raises(RuntimeError, match="runner must be committed"):
        diagnostic._require_runner_in_source_commit()

    assert calls == [
        [
            "git",
            "cat-file",
            "-e",
            "HEAD:scripts/graph_eval/run_diagnostic.py",
        ],
        [
            "git",
            "cat-file",
            "-e",
            "HEAD:scripts/graph_eval/diagnostic_metrics.py",
        ],
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


def test_driver_stops_before_case_arms_when_qdrant_health_fails(
    monkeypatch,
    tmp_path,
):
    from scripts.graph_eval import run_diagnostic as diagnostic

    context = SimpleNamespace(
        output=tmp_path / "output",
        source_commit="a" * 40,
        cases=({"id": "case-a", "value": 1},),
    )
    observed = []
    monkeypatch.setattr(diagnostic, "_prepare_context", lambda *args: context)
    monkeypatch.setattr(
        diagnostic,
        "_write_declaration",
        lambda unused_context, unused_plan: "b" * 64,
    )
    monkeypatch.setattr(
        diagnostic,
        "_run_case_health",
        lambda unused_context, case_dir: (
            observed.append((case_dir.name, "health"))
            or {"passed": False, "reason": "qdrant_health_failed"}
        ),
        raising=False,
    )
    monkeypatch.setattr(
        diagnostic,
        "_run_arm",
        lambda *args, **kwargs: pytest.fail("arm ran after failed health gate"),
    )

    report = run_diagnostic(
        manifest=tmp_path / "manifest.jsonl",
        preflight=tmp_path / "preflight.json",
        provider_smoke=tmp_path / "smoke.json",
        output=context.output,
        trace=tmp_path / "trace.jsonl",
    )

    assert observed == [("case-001", "health")]
    assert report["status"] == "inconclusive"
    assert report["health_failure_count"] == 1
    assert not (context.output / "case-002").exists()


def test_driver_executes_exact_predeclared_arm_order(monkeypatch, tmp_path):
    from scripts.graph_eval import run_diagnostic as diagnostic

    context = SimpleNamespace(
        output=tmp_path / "output",
        source_commit="a" * 40,
        cases=({"id": "case-a"}, {"id": "case-b"}),
    )
    observed = []

    monkeypatch.setattr(diagnostic, "_prepare_context", lambda *args: context)
    monkeypatch.setattr(
        diagnostic,
        "_write_declaration",
        lambda unused_context, unused_plan: "b" * 64,
    )
    monkeypatch.setattr(
        diagnostic,
        "_run_case_health",
        lambda unused_context, case_dir: (
            observed.append((case_dir.name, "health", None))
            or {"passed": True}
        ),
    )

    def fake_run_arm(
        unused_context,
        pair_dir,
        *,
        label,
        enabled,
        arm_starts,
        case_id,
    ):
        observed.append((pair_dir.name, label, enabled))
        return _arm(
            latency_p95_ms=1200 if enabled else 1000,
            cost=1.4 if enabled else 1.0,
            generation_p95_ms=500 if enabled else 300,
        )

    monkeypatch.setattr(diagnostic, "_run_arm", fake_run_arm)

    report = run_diagnostic(
        manifest=tmp_path / "manifest.jsonl",
        preflight=tmp_path / "preflight.json",
        provider_smoke=tmp_path / "smoke.json",
        output=context.output,
        trace=tmp_path / "trace.jsonl",
    )

    assert observed == [
        ("case-001", "health", None),
        ("case-001", "candidate", True),
        ("case-001", "baseline", False),
        ("case-002", "health", None),
        ("case-002", "baseline", False),
        ("case-002", "candidate", True),
    ]
    assert report["status"] == "passed"
    assert report["full_quality_gate_executed"] is False
    assert (context.output / "case-001" / "summary.json").is_file()
    assert (context.output / "case-002" / "summary.json").is_file()
    assert (context.output / "outcome.json").is_file()


def test_driver_stops_without_carry_forward_on_provider_retry(
    monkeypatch,
    tmp_path,
):
    from scripts.graph_eval import run_diagnostic as diagnostic

    context = SimpleNamespace(
        output=tmp_path / "output",
        source_commit="a" * 40,
        cases=({"id": "case-a"}, {"id": "case-b"}),
    )
    observed = []
    monkeypatch.setattr(diagnostic, "_prepare_context", lambda *args: context)
    monkeypatch.setattr(
        diagnostic,
        "_write_declaration",
        lambda unused_context, unused_plan: "b" * 64,
    )
    monkeypatch.setattr(
        diagnostic,
        "_run_case_health",
        lambda unused_context, unused_dir: {"passed": True},
    )

    def fake_run_arm(
        unused_context,
        pair_dir,
        *,
        label,
        enabled,
        arm_starts,
        case_id,
    ):
        observed.append((pair_dir.name, label))
        return _arm(
            latency_p95_ms=1200,
            cost=1.4,
            provider_retries=1,
        )

    monkeypatch.setattr(diagnostic, "_run_arm", fake_run_arm)

    report = run_diagnostic(
        manifest=tmp_path / "manifest.jsonl",
        preflight=tmp_path / "preflight.json",
        provider_smoke=tmp_path / "smoke.json",
        output=context.output,
        trace=tmp_path / "trace.jsonl",
    )

    assert observed == [("case-001", "candidate")]
    assert report["status"] == "inconclusive"
    assert report["provider_retry_count"] == 1
    assert not (context.output / "case-002").exists()


def test_driver_persists_secret_safe_inconclusive_arm_failure(
    monkeypatch,
    tmp_path,
):
    from scripts.graph_eval import run_diagnostic as diagnostic

    context = SimpleNamespace(
        output=tmp_path / "output",
        source_commit="a" * 40,
        cases=({"id": "case-a"}, {"id": "case-b"}),
    )
    monkeypatch.setattr(diagnostic, "_prepare_context", lambda *args: context)
    monkeypatch.setattr(
        diagnostic,
        "_write_declaration",
        lambda unused_context, unused_plan: "b" * 64,
    )
    monkeypatch.setattr(
        diagnostic,
        "_run_case_health",
        lambda unused_context, unused_dir: {"passed": True},
    )
    monkeypatch.setattr(
        diagnostic,
        "_run_arm",
        lambda *args, **kwargs: (_ for _ in ()).throw(
            ValueError("secret provider endpoint")
        ),
    )

    report = run_diagnostic(
        manifest=tmp_path / "manifest.jsonl",
        preflight=tmp_path / "preflight.json",
        provider_smoke=tmp_path / "smoke.json",
        output=context.output,
        trace=tmp_path / "trace.jsonl",
    )

    assert report["status"] == "inconclusive"
    assert report["execution_failure_count"] == 1
    summary = (context.output / "case-001" / "summary.json").read_text(
        encoding="utf-8"
    )
    assert '"error_type": "ValueError"' in summary
    assert "secret provider endpoint" not in summary
    assert not (context.output / "case-002").exists()


def test_arm_execution_binds_smoke_preflight_and_privacy_safe_metrics(
    monkeypatch,
    tmp_path,
):
    from scripts.graph_eval import run_diagnostic as diagnostic

    graph_report = {
        "schema": "graph-readiness-v1",
        "review_sample_count": 21,
        "review_mode": "multi_reviewer",
        "review_sample_source": "independent",
    }
    context = SimpleNamespace(
        provider_smoke=tmp_path / "smoke.json",
        provider_configuration_sha256="a" * 64,
        manifest=tmp_path / "manifest.jsonl",
        trace=tmp_path / "trace.jsonl",
        governance_scope_sha256="b" * 64,
        provider_environment={"GPT_MODEL_NAME": "test-model"},
        preflight_report={
            "fixture_fingerprint": "c" * 64,
            "case_fixture_fingerprints": {
                "graph-case-01": "c" * 64,
            },
            "graph_report": graph_report,
        },
    )
    context.trace.write_text("", encoding="utf-8")
    pair_dir = tmp_path / "pair-01"
    smoke_calls = []
    run_environment = {}
    monkeypatch.setattr(diagnostic, "_require_inputs_unchanged", lambda unused: None)
    monkeypatch.setattr(
        diagnostic,
        "validate_provider_smoke_for_arms",
        lambda *args, **kwargs: smoke_calls.append(kwargs["arm_started_at"]),
    )

    def fake_run(label, manifest, output, trace, **kwargs):
        run_environment.update(kwargs["provider_environment"])
        run_environment["case_id"] = kwargs["case_id"]
        run_dir = output / label
        run_dir.mkdir(parents=True)
        (run_dir / "eval.json").write_text(
            """{
              "provider_failure_count": 0,
              "provider_retries": 0,
              "total_cases": 1,
              "passed_cases": 1,
              "latency_p95_ms": 1200,
              "total_estimated_cost": 1.4
            }""",
            encoding="utf-8",
        )
        (run_dir / "trace.json").write_text(
            """{
              "schema":"rag-refusal-snapshot-v1",
              "error_event_count":0,
              "fallback_event_count":0,
              "retry_event_count":0
            }""",
            encoding="utf-8",
        )
        (run_dir / "preflight.json").write_text(
            json.dumps({
                "schema": "graph-fixture-preflight-v1",
                "passed": True,
                "batch": "graph-eval-v1",
                "collection": "MechChatbot_Graph_Eval_v1",
                "checked_cases": 1,
                "fixture_fingerprint": "c" * 64,
                "case_fixture_fingerprints": {
                    "graph-case-01": "c" * 64,
                },
                "case_resolutions": {"graph-case-01": {}},
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
            "query_count": 1,
            "parse_errors": 0,
            "stage_summary": {
                "retrieval": {
                    "sample_count": 1,
                    "latency_p95_ms": 300,
                },
                "generation": {
                    "sample_count": 1,
                    "latency_p95_ms": 500,
                },
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
        case_id="graph-case-01",
    )

    assert smoke_calls == [("2026-08-10T00:00:00Z",)]
    assert run_environment["RAG_TRACE_LOG_FILE"] == str(context.trace.resolve())
    assert run_environment["case_id"] == "graph-case-01"
    assert arm["eval"] == {
        "provider_failure_count": 0,
        "provider_retries": 0,
        "total_cases": 1,
        "passed_cases": 1,
        "latency_p95_ms": 1200,
        "total_estimated_cost": 1.4,
    }
    assert arm["latency"]["stage_summary"]["generation"] == {
        "sample_count": 1,
        "latency_p95_ms": 500,
    }
    assert arm["trace"] == {
        "error_event_count": 0,
        "error_events": {},
        "fallback_event_count": 0,
        "fallback_events": {},
        "retry_event_count": 0,
        "retry_events": {},
    }
    assert set(arm["artifacts"]) == {"eval", "trace", "latency"}
