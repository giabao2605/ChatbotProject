import hashlib
import json
import subprocess
from pathlib import Path

import pytest

from mech_chatbot.evaluation.integrated_hardening import (
    FEATURE_FLAGS,
    compare_load_reports,
    evaluate_integrated_readiness,
    evaluate_request_budgets,
    evaluate_security_results,
    execute_security_manifest,
    validate_combination_matrix,
    validate_security_manifest,
)
from mech_chatbot.rag.semantic_cache import pipeline_namespace
from mech_chatbot.config.settings import Settings
from scripts.integrated_eval.load_report import build_integrated_load_report
from scripts.integrated_eval.preflight import build_preflight, _prerequisites
from mech_chatbot.evaluation.milestone_decisions import (
    build_demo_matrix,
    classify_provider_outcome,
    evaluate_demo_readiness,
    resolve_demo_flags,
    validate_milestone_decision,
    verify_demo_decision_ledger,
    verify_milestone_decision,
)
from scripts.integrated_eval.results import build_results
from scripts.integrated_eval.contracts import assert_clean_status
from scripts.integrated_eval.compose_gate_metadata import evaluate_combination_evidence
from scripts.eval.rag_trace_snapshot import build_snapshot
from scripts.eval.milestone_decision import build_decision_artifact
from scripts.eval.provider_smoke import run_provider_smoke


pytestmark = pytest.mark.unit


def _json(path):
    return json.loads(Path(path).read_text(encoding="utf-8"))


def test_combination_matrix_covers_roadmap_and_declares_dependencies():
    report = validate_combination_matrix(
        _json("data/integrated_hardening_v1/matrix.json")
    )

    assert report["passed"] is True
    assert set(report["combination_ids"]) == {
        "crag_repair", "crag_grounded_math", "crag_late_interaction",
        "crag_query_decomposition", "crag_graph_retrieval",
        "decomposition_graph", "decomposition_late_interaction",
    }
    assert report["checks"]["all_flags_explicit"] is True

    matrix = _json("data/integrated_hardening_v1/matrix.json")
    matrix["combinations"] = matrix["combinations"][:-1]
    assert validate_combination_matrix(matrix)["passed"] is False

    matrix = _json("data/integrated_hardening_v1/matrix.json")
    matrix["combinations"][0]["flags"]["RAG_GRAPH_RETRIEVAL_ENABLED"] = "true"
    assert validate_combination_matrix(matrix)["passed"] is False


def test_every_combination_and_version_has_a_distinct_cache_namespace():
    matrix = _json("data/integrated_hardening_v1/matrix.json")
    namespaces = {
        item["id"]: pipeline_namespace({**item["flags"], **item["versions"]})
        for item in matrix["combinations"]
    }
    assert len(set(namespaces.values())) == len(namespaces)

    late = next(item for item in matrix["combinations"] if item["id"] == "crag_late_interaction")
    before = pipeline_namespace({**late["flags"], **late["versions"]})
    after = pipeline_namespace({
        **late["flags"], **late["versions"], "RAG_LATE_INDEX_VERSION": "late-next",
    })
    assert before != after


def test_all_integrated_feature_flags_default_disabled():
    defaults = {
        name: Settings.model_fields[name].default
        for name in (
            "RAG_CRAG_ENABLED", "RAG_CLAIM_REPAIR_ENABLED",
            "RAG_GROUNDED_MATH_ENABLED", "RAG_LATE_INTERACTION_ENABLED",
            "RAG_QUERY_DECOMPOSITION_ENABLED", "RAG_GRAPH_RETRIEVAL_ENABLED",
            "RAG_GRAPH_COMMUNITY_SUMMARIES_ENABLED",
        )
    }
    assert set(defaults.values()) == {False}


def test_request_budget_is_shared_across_combined_features():
    valid = {
        "id": "case-ok", "combination_id": "decomposition_graph",
        "planner_count": 1, "subquery_count": 3, "correction_count": 1,
        "repair_count": 0, "calculation_count": 0,
        "graph_edge_count": 12, "provider_retries": 2,
        "final_generation_count": 1, "deadline_exceeded": False,
    }
    report = evaluate_request_budgets([valid])
    assert report["passed"] is True
    assert report["maxima"]["correction_count"] == 1

    invalid = {**valid, "id": "case-over", "correction_count": 2}
    report = evaluate_request_budgets([valid, invalid])
    assert report["passed"] is False
    assert report["violations"][0]["field"] == "correction_count"

    inactive = {
        **valid, "id": "inactive-feature", "combination_id": "crag_repair",
        "planner_count": 1, "subquery_count": 1, "graph_edge_count": 1,
    }
    report = evaluate_request_budgets([inactive])
    assert report["passed"] is False
    assert {row["field"] for row in report["violations"]} >= {
        "planner_count", "subquery_count", "graph_edge_count",
    }

    missing = dict(valid)
    missing.pop("provider_retries")
    assert evaluate_request_budgets([missing])["passed"] is False


def test_security_matrix_covers_every_dimension_and_leakage_fails_closed():
    cases = [
        json.loads(line)
        for line in Path("data/integrated_hardening_v1/security_matrix.jsonl")
        .read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    manifest = validate_security_manifest(cases)
    assert manifest["passed"] is True
    assert set(manifest["dimensions"]) == {
        "role", "department", "site", "clearance", "lifecycle",
        "publication", "current_version",
    }
    assert execute_security_manifest(cases)["passed"] is True

    results = [
        {**case, "observed_access": case["expected_access"], "leaked": False}
        for case in cases
    ]
    assert evaluate_security_results(results)["passed"] is True
    results[0]["leaked"] = True
    failed = evaluate_security_results(results)
    assert failed["passed"] is False
    assert failed["leakage_count"] == 1

    spoofed = dict(results[0])
    spoofed["leaked"] = True
    spoofed["admin_exception"] = True
    spoofed["identity"] = {**spoofed["identity"], "roles": ["viewer"]}
    assert evaluate_security_results([spoofed])["leakage_count"] == 1


def test_load_comparison_requires_first_token_completion_cost_retry_and_fallback():
    baseline = {
        "schema": "integrated-load-report-v1", "concurrency": 5, "requests": 30,
        "successful_requests": 30, "first_token_p50_ms": 100,
        "first_token_p95_ms": 200, "complete_p50_ms": 400,
        "complete_p95_ms": 800, "cost_per_query": 0.01,
        "provider_retry_rate": 0.02, "fallback_rate": 0.03,
    }
    candidate = {
        **baseline, "first_token_p95_ms": 280, "complete_p95_ms": 1100,
        "cost_per_query": 0.014, "provider_retry_rate": 0.02,
        "fallback_rate": 0.04,
    }
    assert compare_load_reports(baseline, candidate)["passed"] is True

    missing = dict(candidate)
    missing.pop("first_token_p95_ms")
    assert compare_load_reports(baseline, missing)["passed"] is False


def test_integrated_readiness_distinguishes_offline_capability_from_live_gate():
    matrix = validate_combination_matrix(
        _json("data/integrated_hardening_v1/matrix.json")
    )
    security = validate_security_manifest([
        json.loads(line)
        for line in Path("data/integrated_hardening_v1/security_matrix.jsonl")
        .read_text(encoding="utf-8").splitlines() if line.strip()
    ])
    blocked = evaluate_integrated_readiness(
        matrix_report=matrix, security_manifest_report=security,
        cache_isolation_passed=True, strict_stream_passed=True,
        rollback_passed=True,
        prerequisites={
            "crag": False, "grounded_math": False, "late_interaction": False,
            "query_decomposition": False, "graph_retrieval": False,
        },
    )
    assert blocked["capability_passed"] is True
    assert blocked["ready_for_live_matrix"] is False
    assert "prerequisite_milestones_incomplete" in blocked["blockers"]

    ready = evaluate_integrated_readiness(
        matrix_report=matrix, security_manifest_report=security,
        cache_isolation_passed=True, strict_stream_passed=True,
        rollback_passed=True,
        prerequisites={name: True for name in (
            "crag", "grounded_math", "late_interaction",
            "query_decomposition", "graph_retrieval",
        )},
    )
    assert ready["ready_for_live_matrix"] is True


def test_integrated_preflight_is_commit_pinned_and_fails_closed_on_current_dependencies():
    matrix = _json("data/integrated_hardening_v1/matrix.json")
    cases = [
        json.loads(line)
        for line in Path("data/integrated_hardening_v1/security_matrix.jsonl")
        .read_text(encoding="utf-8").splitlines() if line.strip()
    ]
    artifact = build_preflight(
        matrix=matrix,
        security_cases=cases,
        prerequisites={name: False for name in (
            "crag", "grounded_math", "late_interaction",
            "query_decomposition", "graph_retrieval",
        )},
        offline_evidence={
            "schema": "integrated-offline-verification-v1",
            "git_sha": "abc123", "passed": True,
            "cache_isolation_passed": True,
            "strict_stream_passed": True,
            "rollback_passed": True,
        },
        git_sha="abc123",
    )
    assert artifact["capability_passed"] is True
    assert artifact["ready_for_live_matrix"] is False
    assert artifact["offline_evidence_commit_matches"] is True


def test_integrated_preflight_can_be_demo_ready_without_becoming_live_ready():
    matrix = _json("data/integrated_hardening_v1/matrix.json")
    cases = [
        json.loads(line)
        for line in Path("data/integrated_hardening_v1/security_matrix.jsonl")
        .read_text(encoding="utf-8").splitlines() if line.strip()
    ]
    artifact = build_preflight(
        matrix=matrix,
        security_cases=cases,
        prerequisites={name: False for name in (
            "crag", "grounded_math", "late_interaction",
            "query_decomposition", "graph_retrieval",
        )},
        offline_evidence={
            "schema": "integrated-offline-verification-v1",
            "git_sha": "abc123", "passed": True,
            "cache_isolation_passed": True,
            "strict_stream_passed": True,
            "rollback_passed": True,
        },
        git_sha="abc123",
        demo_decision_verification={
            "passed": True, "ready_for_demo_matrix": True,
            "fallback_milestones": ["late_interaction"],
        },
    )
    assert artifact["ready_for_demo_matrix"] is True
    assert artifact["ready_for_live_matrix"] is False
    assert artifact["demo_fallback_milestones"] == ["late_interaction"]


def test_integrated_results_aggregate_budget_and_security_without_raw_prompts():
    eval_report = {
        "schema": "rag-labeled-eval-v4",
        "cases": [{
            "id": "safe-case", "combination_id": "crag_repair",
            "planner_count": 0, "subquery_count": 0, "correction_count": 1,
            "repair_count": 1, "calculation_count": 0,
            "graph_edge_count": 0, "provider_retries": 0,
            "final_generation_count": 1, "deadline_exceeded": False,
        }],
    }
    security_cases = [
        {**case, "observed_access": case["expected_access"], "leaked": False}
        for case in [
            json.loads(line)
            for line in Path("data/integrated_hardening_v1/security_matrix.jsonl")
            .read_text(encoding="utf-8").splitlines() if line.strip()
        ]
    ]
    artifact = build_results([eval_report], security_cases)
    assert artifact["passed"] is True
    assert artifact["budget_report"]["case_count"] == 1
    assert "question" not in json.dumps(artifact).lower()


def test_load_report_joins_latency_with_cost_retry_and_fallback_metrics():
    summary = {
        "concurrency": 5, "requests": 30, "successful_requests": 30,
        "first_token_p50_ms": 100, "first_token_p95_ms": 200,
        "complete_p50_ms": 400, "complete_p95_ms": 800,
    }
    benchmark = {
        "schema": "rag-concurrency-benchmark-v1",
        "results": [{"summary": summary, "samples": []}],
    }
    evaluation = {
        "schema": "rag-labeled-eval-v4", "case_count": 30,
        "total_estimated_cost": 0.3, "provider_retries": 3,
        "fallback_coverage": {"fallback_rate": 0.05},
    }
    report = build_integrated_load_report(benchmark, evaluation, concurrency=5)
    assert report["schema"] == "integrated-load-report-v1"
    assert report["cost_per_query"] == pytest.approx(0.01)
    assert report["provider_retry_rate"] == pytest.approx(0.1)
    assert report["fallback_rate"] == pytest.approx(0.05)
    with pytest.raises(ValueError, match="concurrency 10"):
        build_integrated_load_report(benchmark, evaluation, concurrency=10)


def test_clean_worktree_contract_fails_closed():
    assert_clean_status("")
    with pytest.raises(RuntimeError, match="clean git worktree"):
        assert_clean_status(" M changed.py\n")


def test_prerequisite_completion_requires_hashed_decision_artifact(tmp_path):
    import hashlib
    schemas = {
        "crag": "crag-production-pilot-v1",
        "grounded_math": "grounded-math-rollout-run-v1",
        "late_interaction": "retrieval-intelligence-gate-v1",
        "query_decomposition": "decomposition-rollout-run-v1",
        "graph_retrieval": "graph-rollout-run-v1",
    }
    stages = {}
    for name, schema in schemas.items():
        artifact = tmp_path / f"{name}.json"
        payload = {"schema": schema, "git_sha": "abc", "passed": True}
        if name == "crag":
            payload["decision"] = "accepted"
        if name == "grounded_math":
            payload["production_eligible"] = True
        if name == "late_interaction":
            payload["stage"] = "late_interaction"
        raw = (json.dumps(payload) + "\n").encode()
        artifact.write_bytes(raw)
        stages[name] = {
            "complete": True, "artifact_path": str(artifact),
            "artifact_sha256": hashlib.sha256(raw).hexdigest(),
            "artifact_schema": schema, "decision": "accepted",
        }
    payload = {
        "schema": "integrated-prerequisites-v1",
        "stages": stages,
    }
    statuses, verification = _prerequisites(payload, "abc")
    assert all(statuses.values())
    assert verification["crag"]["artifact_verified"] is True
    payload["stages"]["crag"]["artifact_sha256"] = "0" * 64
    assert _prerequisites(payload, "abc")[0]["crag"] is False

    arbitrary = tmp_path / "arbitrary.json"
    raw = b'{"schema":"arbitrary","git_sha":"abc","passed":true}\n'
    arbitrary.write_bytes(raw)
    payload["stages"]["crag"].update({
        "artifact_path": str(arbitrary),
        "artifact_sha256": hashlib.sha256(raw).hexdigest(),
        "artifact_schema": "arbitrary",
    })
    assert _prerequisites(payload, "abc")[0]["crag"] is False


def test_controlled_demo_decision_does_not_complete_default_rollout(tmp_path):
    evidence = tmp_path / "crag-gate.json"
    raw = b'{"schema":"crag-production-pilot-v1","git_sha":"old","passed":true}\n'
    evidence.write_bytes(raw)
    decision = {
        "schema": "milestone-decision-v2",
        "milestone": "crag",
        "scope": "controlled_demo",
        "decision": "accepted",
        "source_commit": "old",
        "evidence": [{
            "path": str(evidence),
            "sha256": hashlib.sha256(raw).hexdigest(),
            "schema": "crag-production-pilot-v1",
        }],
        "reason": "Demo gate passed.",
        "reviewer_signoff": {"reviewer": "qa", "signed_at": "2026-07-16T00:00:00Z"},
    }
    report = validate_milestone_decision(decision)
    assert report["passed"] is True
    assert report["completes_controlled_demo"] is True
    assert report["completes_default_rollout"] is False


def test_historical_decision_is_verified_against_source_commit_not_current_head(tmp_path):
    evidence = tmp_path / "late-gate.json"
    raw = b'{"schema":"retrieval-intelligence-gate-v1","git_sha":"historical","stage":"late_interaction","passed":false}\n'
    evidence.write_bytes(raw)
    decision = {
        "schema": "milestone-decision-v2",
        "milestone": "late_interaction",
        "scope": "controlled_demo",
        "decision": "rejected",
        "source_commit": "historical",
        "evidence": [{
            "path": str(evidence),
            "sha256": hashlib.sha256(raw).hexdigest(),
            "schema": "retrieval-intelligence-gate-v1",
        }],
        "reason": "No nDCG improvement.",
        "reviewer_signoff": {"reviewer": "qa", "signed_at": "2026-07-16T00:00:00Z"},
    }
    verified = verify_milestone_decision(decision, root=tmp_path, current_commit="new-head")
    assert verified["passed"] is True
    assert verified["source_commit_matches"] is True
    assert verified["current_commit_matches"] is False


def test_rejected_demo_feature_is_disabled_without_blocking_fallback_matrix():
    flags = {name: True for name in FEATURE_FLAGS}
    decisions = {
        "late_interaction": {"scope": "controlled_demo", "decision": "rejected"},
        "graph_retrieval": {"scope": "controlled_demo", "decision": "inconclusive"},
    }
    result = resolve_demo_flags(flags, decisions)
    assert result["flags"]["RAG_LATE_INTERACTION_ENABLED"] is False
    assert result["flags"]["RAG_GRAPH_RETRIEVAL_ENABLED"] is False
    assert result["fallback_milestones"] == ["graph_retrieval", "late_interaction"]
    assert result["blocked"] is False


def test_demo_matrix_keeps_seven_rows_and_pins_rejected_features_off():
    matrix = _json("data/integrated_hardening_v1/matrix.json")
    decisions = {
        "late_interaction": {"scope": "controlled_demo", "decision": "rejected"},
        "graph_retrieval": {"scope": "controlled_demo", "decision": "inconclusive"},
    }
    demo = build_demo_matrix(matrix, decisions)
    assert demo["schema"] == "integrated-demo-feature-matrix-v1"
    assert len(demo["combinations"]) == 7
    late = next(row for row in demo["combinations"] if row["id"] == "crag_late_interaction")
    assert late["requested_flags"]["RAG_LATE_INTERACTION_ENABLED"] is True
    assert late["effective_flags"]["RAG_LATE_INTERACTION_ENABLED"] is False
    assert late["fallback_milestones"] == ["late_interaction"]


def test_provider_capacity_failure_is_inconclusive_not_quality_rejection():
    report = classify_provider_outcome([
        "503 service_unavailable no_capacity",
        "503 service_unavailable no_capacity",
    ])
    assert report == {
        "decision": "inconclusive",
        "provider_blocked": True,
        "quality_evaluated": False,
        "reason": "provider_capacity_unavailable",
    }


def test_provider_smoke_requires_five_clean_requests_and_records_no_prompt():
    calls = []

    def invoke(_messages, **kwargs):
        calls.append(kwargs)
        return "OK"

    artifact = run_provider_smoke(invoke, request_count=5)
    assert artifact["schema"] == "provider-smoke-v1"
    assert artifact["passed"] is True
    assert artifact["request_count"] == 5
    assert artifact["successful_requests"] == 5
    assert "prompt" not in artifact
    assert "response" not in artifact
    assert {call["surface"] for call in calls} == {"generation"}


def test_demo_readiness_is_separate_from_live_readiness():
    reports = {
        name: {
            "passed": True,
            "scope": "controlled_demo",
            "decision": decision,
        }
        for name, decision in {
            "crag": "accepted",
            "grounded_math": "accepted",
            "late_interaction": "rejected",
            "query_decomposition": "accepted",
            "graph_retrieval": "inconclusive",
            "community_summaries": "inconclusive",
        }.items()
    }
    readiness = evaluate_demo_readiness(
        capability_passed=True,
        decision_reports=reports,
    )
    assert readiness["ready_for_demo_matrix"] is True
    assert readiness["ready_for_live_matrix"] is False
    assert readiness["fallback_milestones"] == [
        "community_summaries", "graph_retrieval", "late_interaction",
    ]


def test_graph_acceptance_without_reviewer_signoff_fails_closed():
    decision = {
        "schema": "milestone-decision-v2",
        "milestone": "graph_retrieval",
        "scope": "controlled_demo",
        "decision": "accepted",
        "source_commit": "abc",
        "evidence": [{"path": "graph.json", "sha256": "0" * 64, "schema": "graph-rollout-run-v1"}],
        "reason": "Graph gate passed.",
        "reviewer_signoff": {},
    }
    assert validate_milestone_decision(decision)["passed"] is False


def test_demo_ledger_verifies_scoped_decisions_without_mutating_live_state(tmp_path):
    refs = {}
    for milestone in (
        "crag", "grounded_math", "late_interaction", "query_decomposition",
        "graph_retrieval", "community_summaries",
    ):
        evidence = tmp_path / f"{milestone}-evidence.json"
        evidence_payload = {
            "schema": f"{milestone}-evidence-v1", "git_sha": "historical",
        }
        evidence_raw = (json.dumps(evidence_payload) + "\n").encode()
        evidence.write_bytes(evidence_raw)
        decision = tmp_path / f"{milestone}-decision.json"
        decision_payload = {
            "schema": "milestone-decision-v2", "milestone": milestone,
            "scope": "controlled_demo", "decision": "rejected",
            "source_commit": "historical",
            "evidence": [{
                "path": str(evidence),
                "sha256": hashlib.sha256(evidence_raw).hexdigest(),
                "schema": evidence_payload["schema"],
            }],
            "reason": "Evidence-first demo decision.",
            "reviewer_signoff": {
                "reviewer": "qa", "signed_at": "2026-07-16T00:00:00Z",
            },
        }
        decision_raw = (json.dumps(decision_payload) + "\n").encode()
        decision.write_bytes(decision_raw)
        refs[milestone] = {
            "path": str(decision),
            "sha256": hashlib.sha256(decision_raw).hexdigest(),
        }
    ledger = {"schema": "controlled-demo-decision-ledger-v2", "decisions": refs}
    report = verify_demo_decision_ledger(ledger, root=tmp_path, current_commit="new")
    assert report["passed"] is True
    assert report["ready_for_demo_matrix"] is True
    assert report["ready_for_live_matrix"] is False


def test_decision_builder_binds_historical_artifact_without_rewriting_it(tmp_path):
    gate = tmp_path / "gate.json"
    raw = b'{"schema":"retrieval-intelligence-gate-v1","stage":"late_interaction","passed":false}\n'
    gate.write_bytes(raw)
    decision = build_decision_artifact(
        milestone="late_interaction",
        scope="controlled_demo",
        decision="rejected",
        source_commit="757b939",
        evidence_paths=[gate],
        reason="No nDCG improvement.",
        reviewer="qa",
        signed_at="2026-07-16T00:00:00Z",
    )
    assert decision["evidence"] == [{
        "path": str(gate),
        "sha256": hashlib.sha256(raw).hexdigest(),
        "schema": "retrieval-intelligence-gate-v1",
        "source_commit": "757b939",
    }]
    assert gate.read_bytes() == raw
    assert verify_milestone_decision(decision, root=tmp_path)["passed"] is True

def test_combination_evidence_binds_eval_trace_load_and_results(tmp_path):
    git_sha = subprocess.check_output(
        ["git", "rev-parse", "HEAD"], text=True
    ).strip()
    conditions = {
        "schema": "rag-labeled-eval-v4", "git_sha": git_sha,
        "manifest_sha256s": ["manifest"], "snapshot_fingerprint": "snapshot",
        "provider_configuration_sha256": "provider",
        "governance_scope_sha256": "scope", "benchmark_concurrency": 5,
        "collection": "staging", "execution_context": "evaluation",
    }
    load = {
        "schema": "integrated-load-report-v1", "concurrency": 5, "requests": 1,
        "successful_requests": 1, "first_token_p50_ms": 10,
        "first_token_p95_ms": 10, "complete_p50_ms": 20,
        "complete_p95_ms": 20, "cost_per_query": .1,
        "provider_retry_rate": 0, "fallback_rate": 0,
    }
    metric = {"applicable_cases": 1, "claim_precision": {"value": 1.0}}
    citation = {
        "applicable_cases": 1, "citation_accuracy": {"value": 1.0},
        "citation_precision": {"value": 1.0},
    }
    baseline = {**conditions, "run_label": "baseline",
                "started_at": "2026-01-01T00:00:01Z",
                "completed_at": "2026-01-01T00:00:02Z", "total_cases": 1,
                "outcome_confusion": {"wrong_answer": 0, "leakage": 0},
                "claim_evaluation": metric, "citation_evaluation": citation}
    candidate = {**conditions, "run_label": "candidate",
                 "started_at": "2026-01-01T00:00:03Z",
                 "completed_at": "2026-01-01T00:00:04Z", "total_cases": 1,
                 "outcome_confusion": {"wrong_answer": 0, "leakage": 0},
                 "claim_evaluation": metric, "citation_evaluation": citation}
    budget_case = {
        "id": "budget", "combination_id": "crag_repair",
        "planner_count": 0, "subquery_count": 0, "correction_count": 0,
        "repair_count": 0, "calculation_count": 0, "graph_edge_count": 0,
        "provider_retries": 0, "final_generation_count": 1,
        "deadline_exceeded": False,
    }
    baseline["cases"] = [dict(budget_case)]
    candidate["cases"] = [dict(budget_case)]
    raw_trace = tmp_path / "raw-trace.jsonl"
    raw_trace.write_text("\n".join((
        json.dumps({
            "ts": "2026-01-01T00:00:02Z", "event": "rag_end",
            "trace_id": "baseline", "execution_context": "evaluation",
            "refusal": False, "final_latency_ms": 10,
        }),
        json.dumps({
            "ts": "2026-01-01T00:00:04Z", "event": "rag_end",
            "trace_id": "candidate", "execution_context": "evaluation",
            "refusal": False, "final_latency_ms": 10,
        }),
    )) + "\n", encoding="utf-8")
    baseline_trace = build_snapshot(
        raw_trace, start="2026-01-01T00:00:00Z", end="2026-01-01T00:00:02Z",
        execution_contexts={"evaluation"},
    )
    candidate_trace = build_snapshot(
        raw_trace, start="2026-01-01T00:00:03Z", end="2026-01-01T00:00:05Z",
        execution_contexts={"evaluation"},
    )
    digests = {name: name for name in (
        "baseline_eval", "candidate_eval", "baseline_trace", "candidate_trace",
        "baseline_benchmark", "candidate_benchmark",
        "baseline_load", "candidate_load", "results",
    )}
    load.update({
        "source_eval_sha256": "baseline_eval",
        "source_benchmark_sha256": "baseline_benchmark",
    })
    candidate_load = {
        **load, "source_eval_sha256": "candidate_eval",
        "source_benchmark_sha256": "candidate_benchmark",
    }
    artifacts = {
        "baseline_eval": baseline, "candidate_eval": candidate,
        "baseline_trace": baseline_trace,
        "candidate_trace": candidate_trace,
        "baseline_benchmark": {}, "candidate_benchmark": {},
        "baseline_load": load, "candidate_load": candidate_load,
        "results": {"passed": True, "source_eval_sha256s": ["candidate_eval"],
                    "budget_report": {"combination_ids": ["crag_repair"]}},
    }
    report = evaluate_combination_evidence("crag_repair", artifacts, digests)
    assert report["passed"] is True
    artifacts["candidate_eval"]["snapshot_fingerprint"] = "other"
    assert evaluate_combination_evidence("crag_repair", artifacts, digests)["passed"] is False
    artifacts["candidate_eval"]["snapshot_fingerprint"] = "snapshot"
    artifacts["candidate_eval"]["outcome_confusion"]["leakage"] = 1
    assert evaluate_combination_evidence("crag_repair", artifacts, digests)["passed"] is False

    artifacts["candidate_eval"]["outcome_confusion"]["leakage"] = 0
    artifacts["candidate_load"]["concurrency"] = 1
    assert evaluate_combination_evidence("crag_repair", artifacts, digests)["passed"] is False
    artifacts["candidate_load"]["concurrency"] = 5

    original_sha = artifacts["candidate_trace"]["source"].pop("sha256")
    assert evaluate_combination_evidence("crag_repair", artifacts, digests)["passed"] is False
    artifacts["candidate_trace"]["source"]["sha256"] = original_sha

    artifacts["candidate_trace"]["observed_budget_metrics"][
        "max_correction_count"
    ] = 2
    assert evaluate_combination_evidence("crag_repair", artifacts, digests)["passed"] is False
    artifacts["candidate_trace"]["observed_budget_metrics"][
        "max_correction_count"
    ] = 0

    matrix_row = _json("data/integrated_hardening_v1/matrix.json")["combinations"][0]
    expected = {
        "flags": {
            name: str(value).casefold() == "true"
            for name, value in matrix_row["flags"].items()
        },
        "versions": matrix_row["versions"],
    }
    artifacts["baseline_eval"]["pipeline_configuration"] = {
        "flags": {name: False for name in expected["flags"]},
        "versions": expected["versions"],
    }
    artifacts["candidate_eval"]["pipeline_configuration"] = {
        "flags": dict(expected["flags"]), "versions": dict(expected["versions"]),
    }
    assert evaluate_combination_evidence(
        "crag_repair", artifacts, digests, expected_configuration=expected
    )["passed"] is True
    artifacts["candidate_eval"]["pipeline_configuration"]["flags"][
        "RAG_CRAG_ENABLED"
    ] = False
    assert evaluate_combination_evidence(
        "crag_repair", artifacts, digests, expected_configuration=expected
    )["passed"] is False
