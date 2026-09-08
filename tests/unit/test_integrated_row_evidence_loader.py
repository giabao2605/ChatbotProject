import hashlib
import json
from pathlib import Path
import subprocess

import pytest

from scripts.eval.rag_trace_snapshot import build_snapshot
from scripts.integrated_eval.load_report import build_integrated_load_report
from scripts.integrated_eval.results import build_results
from scripts.integrated_eval.compose_gate_metadata import load_row_evidence


pytestmark = pytest.mark.unit


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _write_json(path: Path, value: dict) -> dict:
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return {"path": str(path), "sha256": _sha256(path), "schema": value["schema"]}


def _git_sha() -> str:
    return subprocess.check_output(["git", "rev-parse", "HEAD"], text=True).strip()


def _security_rows() -> list[dict]:
    path = Path(__file__).resolve().parents[2] / "data/integrated_hardening_v1/security_matrix.jsonl"
    cases = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()
             if line.strip()]
    return [{**case, "observed_access": case["expected_access"], "leaked": False}
            for case in cases]


def _evaluation(run_label: str, completed_at: str) -> dict:
    pipeline = {"flags": {}, "versions": {}}
    return {
        "schema": "rag-labeled-eval-v4",
        "run_label": run_label,
        "git_sha": _git_sha(),
        "manifest_sha256s": ["manifest"],
        "snapshot_fingerprint": "snapshot",
        "provider_configuration_sha256": "provider",
        "governance_scope_sha256": "scope",
        "benchmark_concurrency": 5,
        "collection": "staging",
        "execution_context": "evaluation",
        "started_at": ("2026-01-01T00:00:01Z" if run_label == "baseline"
                       else "2026-01-01T00:00:03Z"),
        "completed_at": completed_at,
        "total_cases": 1,
        "case_count": 1,
        "total_estimated_cost": 0.1,
        "provider_retries": 0,
        "provider_failure_count": 0,
        "fallback_coverage": {"fallback_rate": 0},
        "pipeline_configuration": pipeline,
        "outcome_confusion": {"wrong_answer": 0, "leakage": 0},
        "claim_evaluation": {
            "applicable_cases": 1,
            "claim_precision": {"value": 1.0},
        },
        "citation_evaluation": {
            "applicable_cases": 1,
            "citation_accuracy": {"value": 1.0},
            "citation_precision": {"value": 1.0},
        },
        "cases": [{
            "id": "budget",
            "combination_id": "crag_claim",
            "planner_count": 0,
            "subquery_count": 0,
            "correction_count": 0,
            "repair_count": 0,
            "calculation_count": 0,
            "graph_edge_count": 0,
            "provider_retries": 0,
            "provider_failure": False,
            "final_generation_count": 1,
            "deadline_exceeded": False,
        }],
    }


def _benchmark(evaluation: dict, deployment_id: str) -> dict:
    identity_fields = (
        "git_sha", "manifest_sha256s", "snapshot_fingerprint",
        "provider_configuration_sha256", "governance_scope_sha256",
        "collection", "execution_context", "pipeline_configuration",
    )
    return {
        "schema": "rag-concurrency-benchmark-v1",
        "runtime_identity": {
            "deployment_id": deployment_id,
            **{field: evaluation[field] for field in identity_fields},
        },
        "results": [
            {
                "summary": {
                    "concurrency": concurrency,
                    "requests": 1,
                    "successful_requests": 1,
                    "first_token_p50_ms": 10,
                    "first_token_p95_ms": 10,
                    "complete_p50_ms": 20,
                    "complete_p95_ms": 20,
                },
            }
            for concurrency in (1, 5)
        ],
    }


def _row_fixture(root: Path, *, combination="crag_claim", budget_combinations=None,
                 maximum_provider_retries=2, baseline_changes=None, candidate_changes=None,
                 case_ids=None, conditions=None, configuration=None) -> dict:
    raw_trace = root / "raw-trace.jsonl"
    raw_trace.write_text("\n".join((
        json.dumps({
            "ts": "2026-01-01T00:00:02Z",
            "event": "rag_end",
            "trace_id": "baseline",
            "execution_context": "evaluation",
            "refusal": False,
            "final_latency_ms": 10,
        }),
        json.dumps({
            "ts": "2026-01-01T00:00:04Z",
            "event": "rag_end",
            "trace_id": "candidate",
            "execution_context": "evaluation",
            "refusal": False,
            "final_latency_ms": 10,
        }),
    )) + "\n", encoding="utf-8")

    baseline = _evaluation("baseline", "2026-01-01T00:00:02Z")
    candidate = _evaluation("candidate", "2026-01-01T00:00:04Z")
    baseline = {**baseline, "cases": [{**case, "combination_id": combination,
                                      **(baseline_changes or {})}
                                     for case in baseline["cases"]]}
    candidate = {**candidate, "cases": [{**case, "combination_id": combination,
                                        **(candidate_changes or {})}
                                       for case in candidate["cases"]]}
    if case_ids is not None:
        baseline = {**baseline, **(conditions or {}), "total_cases": len(case_ids),
                    "case_count": len(case_ids),
                    "cases": [{**baseline["cases"][0], "id": value} for value in case_ids]}
        candidate = {**candidate, **(conditions or {}), "total_cases": len(case_ids),
                     "case_count": len(case_ids),
                     "cases": [{**candidate["cases"][0], "id": value} for value in case_ids]}
    if configuration is not None:
        baseline = {**baseline, "pipeline_configuration": {
            "flags": configuration["baseline_flags"], "versions": configuration["versions"]}}
        candidate = {**candidate, "pipeline_configuration": {
            "flags": configuration["flags"], "versions": configuration["versions"]}}
    extra_events = []
    for evaluation in (baseline, candidate):
        case = evaluation["cases"][0]
        identity = {"ts": evaluation["completed_at"], "trace_id": evaluation["run_label"],
                    "execution_context": "evaluation"}
        extra_events += [
            {**identity, "event": "query_decomposition", "planner_count": case["planner_count"],
             "subquery_count": case["subquery_count"]},
            {**identity, "event": "grounded_math_generation", "calculations": case["calculation_count"]},
            *[{**identity, "event": "llm_retry"} for _ in range(case["provider_retries"])],
        ]
        extra_events += [{**identity, "event": "rag_end", "trace_id": case["id"],
                          "refusal": False, "final_latency_ms": 10}
                         for case in evaluation["cases"][1:]]
    raw_trace.write_text(raw_trace.read_text(encoding="utf-8")
                         + "\n".join(json.dumps(event) for event in extra_events) + "\n",
                         encoding="utf-8")
    if case_ids is not None:
        baseline = {**baseline, "cases": [{**case, "trace_id": f"eval:baseline:{case['id']}"}
                                         for case in baseline["cases"]]}
        candidate = {**candidate, "cases": [{**case, "trace_id": f"eval:candidate:{case['id']}"}
                                           for case in candidate["cases"]]}
        events = [json.loads(line) for line in raw_trace.read_bytes().splitlines()]
        mapped = []
        for event in events:
            label = "baseline" if event["ts"] == baseline["completed_at"] else "candidate"
            identifier = event["trace_id"]
            identifier = case_ids[0] if identifier in ("baseline", "candidate") else identifier
            mapped.append({**event, "trace_id": f"eval:{label}:{identifier}"})
        raw_trace.write_text("\n".join(json.dumps(event) for event in mapped) + "\n", encoding="utf-8")
    baseline_benchmark = _benchmark(baseline, "baseline-1")
    candidate_benchmark = _benchmark(candidate, "candidate-1")
    references = {
        "baseline_eval": _write_json(root / "baseline-eval.json", baseline),
        "candidate_eval": _write_json(root / "candidate-eval.json", candidate),
        "baseline_benchmark": _write_json(root / "baseline-benchmark.json", baseline_benchmark),
        "candidate_benchmark": _write_json(root / "candidate-benchmark.json", candidate_benchmark),
    }
    traces = {
        "baseline_trace": build_snapshot(
            raw_trace,
            start="2026-01-01T00:00:00Z",
            end="2026-01-01T00:00:02Z",
            execution_contexts={"evaluation"},
        ),
        "candidate_trace": build_snapshot(
            raw_trace,
            start="2026-01-01T00:00:03Z",
            end="2026-01-01T00:00:05Z",
            execution_contexts={"evaluation"},
        ),
    }
    references.update({
        name: _write_json(root / f"{name}.json", artifact)
        for name, artifact in traces.items()
    })
    baseline_load = build_integrated_load_report(
        baseline_benchmark,
        baseline,
        concurrency=baseline["benchmark_concurrency"],
        source_benchmark_sha256=references["baseline_benchmark"]["sha256"],
        source_eval_sha256=references["baseline_eval"]["sha256"],
    )
    candidate_load = build_integrated_load_report(
        candidate_benchmark,
        candidate,
        concurrency=candidate["benchmark_concurrency"],
        source_benchmark_sha256=references["candidate_benchmark"]["sha256"],
        source_eval_sha256=references["candidate_eval"]["sha256"],
    )
    references["baseline_load"] = _write_json(root / "baseline-load.json", baseline_load)
    references["candidate_load"] = _write_json(root / "candidate-load.json", candidate_load)
    security = root / "security-results.jsonl"
    security.write_text(
        "\n".join(json.dumps(row, ensure_ascii=False) for row in _security_rows()) + "\n",
        encoding="utf-8",
    )
    security_sha = _sha256(security)
    results = build_results(
        [candidate],
        _security_rows(),
        source_eval_sha256s=[references["candidate_eval"]["sha256"]],
        security_results_sha256=security_sha,
        combinations=budget_combinations,
        maximum_provider_retries=maximum_provider_retries,
    )
    references["results"] = _write_json(root / "results.json", results)
    return {
        "id": combination,
        **references,
        "security_results": {"path": str(security), "sha256": security_sha},
    }


def test_load_row_evidence_rejects_changed_artifact_bytes(tmp_path):
    row = _row_fixture(tmp_path)
    report, references = load_row_evidence(
        row,
        expected_configuration=None,
        root=tmp_path,
    )
    assert report["passed"] is True, report["checks"]
    assert len(references) == 10

    Path(row["candidate_eval"]["path"]).write_text("{}", encoding="utf-8")

    with pytest.raises(ValueError, match="artifact hash mismatch"):
        load_row_evidence(row, expected_configuration=None, root=tmp_path)


def test_load_row_evidence_rejects_missing_referenced_artifact(tmp_path):
    row = _row_fixture(tmp_path)
    Path(row["baseline_load"]["path"]).unlink()

    with pytest.raises(FileNotFoundError):
        load_row_evidence(row, expected_configuration=None, root=tmp_path)


@pytest.mark.parametrize("artifact,field,value,check", [
    ("baseline_load", "requests", 999, "derived_artifacts_recomputed"),
    ("results", "eval_schemas_valid", False, "derived_artifacts_recomputed"),
    ("candidate_trace", "parse_errors", 1, "candidate_trace_bound"),
])
def test_rehashed_self_report_cannot_replace_recomputed_evidence(
    tmp_path, artifact, field, value, check,
):
    row = _row_fixture(tmp_path)
    path = Path(row[artifact]["path"])
    changed = {**json.loads(path.read_bytes()), field: value}
    row = {**row, artifact: _write_json(path, changed)}
    report, _ = load_row_evidence(row, expected_configuration=None, root=tmp_path)
    assert report["passed"] is False
    assert report["checks"][check] is False


def test_raw_trace_drift_invalidates_precomputed_snapshot(tmp_path):
    row = _row_fixture(tmp_path)
    (tmp_path / "raw-trace.jsonl").write_text("{}\n", encoding="utf-8")
    report, _ = load_row_evidence(row, expected_configuration=None, root=tmp_path)
    assert report["passed"] is False
    assert report["checks"]["baseline_trace_bound"] is False


def test_security_results_must_match_the_hash_bound_to_derived_results(tmp_path):
    row = _row_fixture(tmp_path)
    path = Path(row["security_results"]["path"])
    path.write_text(path.read_text(encoding="utf-8") + "\n", encoding="utf-8")
    with pytest.raises(ValueError, match="artifact hash mismatch"):
        load_row_evidence(row, expected_configuration=None, root=tmp_path)
    changed = {**row, "security_results": {"path": str(path), "sha256": _sha256(path)}}
    with pytest.raises(ValueError, match="not bound to security results"):
        load_row_evidence(changed, expected_configuration=None, root=tmp_path)


def test_interaction_row_recomputes_results_under_zero_retry_contract(tmp_path):
    combinations = {"math_query": frozenset({"RAG_GROUNDED_MATH_ENABLED",
                                            "RAG_QUERY_DECOMPOSITION_ENABLED"})}
    row = _row_fixture(tmp_path, combination="math_query",
                       budget_combinations=combinations, maximum_provider_retries=0,
                       candidate_changes={"planner_count": 1, "subquery_count": 2,
                                          "calculation_count": 1})
    report, _ = load_row_evidence(
        row, expected_configuration=None, root=tmp_path,
        baseline_combinations={"math_query": frozenset()},
        candidate_combinations=combinations, maximum_provider_retries=0)
    assert report["passed"] is True, report["checks"]
    legacy, _ = load_row_evidence(row, expected_configuration=None, root=tmp_path)
    assert legacy["passed"] is False
    assert legacy["checks"]["derived_artifacts_recomputed"] is False


@pytest.mark.parametrize("label,changes", [
    ("baseline", {"planner_count": 1}),
    ("baseline", {"calculation_count": 1}),
    ("baseline", {"provider_retries": 1}),
    ("candidate", {"provider_retries": 1}),
])
def test_arm_policy_is_enforced_even_when_every_artifact_is_rehashed(tmp_path, label, changes):
    combinations = {"math_query": frozenset({"RAG_GROUNDED_MATH_ENABLED",
                                            "RAG_QUERY_DECOMPOSITION_ENABLED"})}
    row = _row_fixture(tmp_path, combination="math_query", budget_combinations=combinations,
                       maximum_provider_retries=0, **{label + "_changes": changes})
    report, _ = load_row_evidence(
        row, expected_configuration=None, root=tmp_path,
        baseline_combinations={"math_query": frozenset()}, candidate_combinations=combinations,
        maximum_provider_retries=0)
    assert report["passed"] is False
    assert report["checks"][label + "_trace_budgets_reconciled"] is False
