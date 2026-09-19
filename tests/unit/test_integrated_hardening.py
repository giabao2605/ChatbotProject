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
    build_demo_matrix, build_release_matrix,
    classify_provider_outcome,
    evaluate_demo_readiness,
    resolve_demo_flags,
    validate_milestone_decision,
    verify_demo_decision_ledger,
    verify_milestone_decision,
)
from scripts.integrated_eval.results import build_results
from scripts.integrated_eval.contracts import assert_clean_status
from scripts.integrated_eval.compose_gate_metadata import (
    _expected_configurations,
    evaluate_combination_evidence,
    load_matrix_evidence,
)
from scripts.eval.rag_trace_snapshot import build_snapshot
from scripts.eval.milestone_decision import build_decision_artifact
from scripts.eval.provider_smoke import run_provider_smoke


pytestmark = pytest.mark.unit


def _scoped_matrix():
    template = json.loads((Path(__file__).resolve().parents[2]
        / "data/integrated_hardening_v1/matrix.json").read_text())["combinations"][0]
    groups = {
        "crag_claim": {"RAG_CRAG_ENABLED", "RAG_CLAIM_REPAIR_ENABLED"},
        "grounded_math": {"RAG_GROUNDED_MATH_ENABLED"},
        "query_decomposition": {"RAG_QUERY_DECOMPOSITION_ENABLED"},
        "late_interaction": {"RAG_LATE_INTERACTION_ENABLED"},
    }
    rows = dict(groups)
    for name, left, right in (
        ("crag_math", "crag_claim", "grounded_math"),
        ("crag_query", "crag_claim", "query_decomposition"),
        ("crag_late", "crag_claim", "late_interaction"),
        ("math_query", "grounded_math", "query_decomposition"),
        ("math_late", "grounded_math", "late_interaction"),
        ("query_late", "query_decomposition", "late_interaction"),
    ):
        rows[name] = groups[left] | groups[right]
    rows["full_stack"] = set().union(*groups.values())
    return {
        "schema": "integrated-feature-matrix-v1",
        "version": "integrated-v4-scoped",
        "combinations": [{
            "id": name, "prerequisites": ["evaluation_foundation"],
            "flags": {flag: flag in enabled for flag in FEATURE_FLAGS},
            "baseline_flags": {flag: False for flag in FEATURE_FLAGS},
            "versions": dict(template["versions"]),
        } for name, enabled in rows.items()],
    }


def test_scoped_matrix_requires_late_pairs_and_full_stack_without_graph():
    matrix = _scoped_matrix()
    assert validate_combination_matrix(matrix)["passed"] is True
    pending, _ = load_matrix_evidence(
        {"schema": "integrated-matrix-evidence-v1", "combinations": []},
        feature_matrix=matrix,
        release_decisions={"schema": "integrated-release-decisions-v1", "decisions": {}},
    )
    assert pending["feature_matrix_version"] == "integrated-v4-scoped"
    assert pending["checks"]["requested_capabilities_enabled"] is False
    assert pending["passed"] is False
    assert validate_combination_matrix({**matrix, "combinations": matrix["combinations"][:-1]})["passed"] is False
    changed = json.loads(json.dumps(matrix))
    changed["combinations"][-1]["flags"]["RAG_GRAPH_RETRIEVAL_ENABLED"] = True
    assert validate_combination_matrix(changed)["passed"] is False


@pytest.mark.parametrize("change", [
    "unknown_version", "wrong_schema", "duplicate", "extra", "baseline_enabled",
    "missing_version", "missing_flag", "wrong_pair", "missing_prerequisite",
])
def test_scoped_matrix_rejects_incomplete_or_changed_contract(change):
    matrix = _scoped_matrix()
    if change == "unknown_version":
        matrix["version"] = "integrated-v99"
    elif change == "wrong_schema":
        matrix["schema"] = "other"
    elif change == "duplicate":
        matrix["combinations"].append(matrix["combinations"][0])
    elif change == "extra":
        matrix["combinations"].append({**matrix["combinations"][0], "id": "extra"})
    elif change == "baseline_enabled":
        matrix["combinations"][0]["baseline_flags"]["RAG_CRAG_ENABLED"] = True
    elif change == "missing_version":
        matrix["combinations"][0]["versions"].pop("RAG_LATE_INDEX_VERSION")
    elif change == "missing_flag":
        matrix["combinations"][0]["flags"].pop("RAG_LATE_INTERACTION_ENABLED")
    elif change == "wrong_pair":
        matrix["combinations"][4]["flags"]["RAG_LATE_INTERACTION_ENABLED"] = True
    else:
        matrix["combinations"][0]["prerequisites"] = []
    assert validate_combination_matrix(matrix)["passed"] is False
    with pytest.raises(ValueError, match="feature matrix is invalid"):
        load_matrix_evidence({}, feature_matrix=matrix, release_decisions={})


def test_scoped_matrix_cannot_count_hard_denied_late_as_measured():
    decisions = {flag: {"decision": "accepted"} for flag in FEATURE_FLAGS}
    result, _ = load_matrix_evidence(
        {"schema": "integrated-matrix-evidence-v1", "combinations": []},
        feature_matrix=_scoped_matrix(),
        release_decisions={"schema": "integrated-release-decisions-v1",
                           "decisions": decisions},
    )
    assert result["checks"]["requested_capabilities_enabled"] is False
    assert result["passed"] is False


@pytest.mark.parametrize("fault", ["none", "retry", "missing_c1", "missing_c5", "commit_drift"])
def test_scoped_matrix_loads_all_artifacts_without_accepting_disabled_late(tmp_path, fault):
    from tests.unit.test_integrated_row_evidence_loader import _row_fixture, _write_json

    matrix = _scoped_matrix()
    decisions = {"schema": "integrated-release-decisions-v1", "decisions": {
        flag: {"decision": "accepted"} for flag in FEATURE_FLAGS
    }}
    configurations = _expected_configurations(matrix, decisions)
    budgets = {row["id"]: {flag for flag, value in row["flags"].items() if value}
               for row in matrix["combinations"]}
    rows = []
    for row in matrix["combinations"]:
        directory = tmp_path / row["id"]
        directory.mkdir()
        rows.append(_row_fixture(
            directory, combination=row["id"], budget_combinations=budgets,
            maximum_provider_retries=0, configuration=configurations[row["id"]],
            candidate_changes=({"provider_retries": 1}
                               if fault == "retry" and row["id"] == "full_stack" else None),
        ))
    if fault in {"missing_c1", "missing_c5", "commit_drift"}:
        artifact = "candidate_eval" if fault == "commit_drift" else "candidate_benchmark"
        path = Path(rows[-1][artifact]["path"])
        value = json.loads(path.read_text(encoding="utf-8"))
        if fault == "commit_drift":
            value["git_sha"] = "different-commit"
        else:
            missing = 1 if fault == "missing_c1" else 5
            value["results"] = [run for run in value["results"]
                             if run["summary"]["concurrency"] != missing]
        rows[-1] = {**rows[-1], artifact: _write_json(path, value)}
    manifest = {"schema": "integrated-matrix-evidence-v1", "combinations": rows,
                "primary_combination_id": "full_stack"}
    if fault in {"commit_drift", "missing_c5"}:
        message = ("runtime identity does not match" if fault == "commit_drift"
                   else "exactly one benchmark summary for concurrency 5")
        with pytest.raises(ValueError, match=message):
            load_matrix_evidence(manifest, feature_matrix=matrix,
                                 release_decisions=decisions, root=tmp_path)
        return
    report, references = load_matrix_evidence(
        manifest, feature_matrix=matrix, release_decisions=decisions, root=tmp_path,
    )
    assert len(references) == 110
    assert report["checks"]["combination_ids_exact"] is True
    assert report["checks"]["all_combinations_passed"] is (fault == "none"), report
    full = report["combination_results"][-1]
    if fault.startswith("missing_c"):
        assert full["checks"]["required_load_concurrencies_passed"] is False
    elif fault == "retry":
        assert full["checks"]["candidate_trace_budgets_reconciled"] is False
    assert report["checks"]["requested_capabilities_enabled"] is False
    assert report["passed"] is False
    duplicate, _ = load_matrix_evidence(
        {**manifest, "combinations": [*rows, rows[0]]},
        feature_matrix=matrix, release_decisions=decisions, root=tmp_path,
    )
    assert duplicate["checks"]["combination_ids_unique"] is False
    assert duplicate["passed"] is False


def _json(path):
    return json.loads(Path(path).read_text(encoding="utf-8"))


def _complete_release_ledger(tmp_path, source_commit="abc123"):
    schemas = {
        "RAG_CRAG_ENABLED": ("crag-production-pilot-v1", None),
        "RAG_CLAIM_REPAIR_ENABLED": ("crag-production-pilot-v1", None),
        "RAG_GROUNDED_MATH_ENABLED": ("grounded-math-rollout-run-v1", None),
        "RAG_LATE_INTERACTION_ENABLED": (
            "retrieval-intelligence-gate-v1", "late_interaction",
        ),
        "RAG_QUERY_DECOMPOSITION_ENABLED": (
            "decomposition-rollout-run-v1", None,
        ),
        "RAG_GRAPH_RETRIEVAL_ENABLED": ("graph-rollout-run-v1", None),
        "RAG_GRAPH_COMMUNITY_SUMMARIES_ENABLED": (
            "retrieval-intelligence-gate-v1", "community_summaries",
        ),
    }
    rows = {}
    for flag, (schema, stage) in schemas.items():
        rejected = flag == "RAG_LATE_INTERACTION_ENABLED"
        artifact_commit = "historical-late" if rejected else source_commit
        artifact = {
            "schema": schema,
            "git_sha": artifact_commit,
            "passed": not rejected,
            "production_eligible": not rejected,
            "decision": "rejected" if rejected else "accepted",
        }
        if flag in {"RAG_CRAG_ENABLED", "RAG_CLAIM_REPAIR_ENABLED"}:
            artifact["review_governance"] = {"mode": "multi_reviewer"}
        if stage:
            artifact["stage"] = stage
        path = tmp_path / f"{flag.lower()}.json"
        raw = (json.dumps(artifact) + "\n").encode()
        path.write_bytes(raw)
        rows[flag] = {
            "decision": "rejected" if rejected else "accepted",
            "source_commit": artifact_commit,
            "evidence": {
                "path": str(path),
                "sha256": hashlib.sha256(raw).hexdigest(),
                "schema": schema,
            },
        }
        if rejected:
            rows[flag]["reason"] = "quality gate rejected this release"
    return {
        "schema": "integrated-release-decisions-v1",
        "status": "complete",
        "decisions": rows,
    }


def test_combination_matrix_covers_roadmap_and_declares_dependencies():
    matrix = _json("data/integrated_hardening_v1/matrix.json")
    report = validate_combination_matrix(matrix)

    assert report["passed"] is True
    assert set(report["combination_ids"]) == {
        "crag_claim", "grounded_math", "query_decomposition",
        "graph_retrieval", "community_summaries",
    }
    assert report["checks"]["all_flags_explicit"] is True
    assert matrix["version"] == "integrated-v3-selective"
    expected = {
        "crag_claim": (
            {"RAG_CRAG_ENABLED", "RAG_CLAIM_REPAIR_ENABLED"},
            ["evaluation_foundation"],
        ),
        "grounded_math": (
            {"RAG_GROUNDED_MATH_ENABLED"},
            ["evaluation_foundation"],
        ),
        "query_decomposition": (
            {"RAG_QUERY_DECOMPOSITION_ENABLED"},
            ["evaluation_foundation"],
        ),
        "graph_retrieval": (
            {"RAG_GRAPH_RETRIEVAL_ENABLED"},
            ["evaluation_foundation"],
        ),
        "community_summaries": (
            {
                "RAG_GRAPH_RETRIEVAL_ENABLED",
                "RAG_GRAPH_COMMUNITY_SUMMARIES_ENABLED",
            },
            ["graph_retrieval"],
        ),
    }
    for row in matrix["combinations"]:
        enabled, prerequisites = expected[row["id"]]
        assert {
            name for name, value in row["flags"].items() if value == "true"
        } == enabled
        assert row["prerequisites"] == prerequisites

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

    community = next(
        item for item in matrix["combinations"]
        if item["id"] == "community_summaries"
    )
    before = pipeline_namespace({**community["flags"], **community["versions"]})
    after = pipeline_namespace({
        **community["flags"], **community["versions"],
        "RAG_COMMUNITY_SERVING_EPOCH": "community-next",
    })
    assert before != after


def test_release_matrix_disables_rejected_features_without_erasing_requested_flags():
    matrix = _json("data/integrated_hardening_v1/matrix.json")
    decisions = {
        name: {"decision": "accepted"}
        for name in FEATURE_FLAGS
    }
    decisions["RAG_LATE_INTERACTION_ENABLED"] = {"decision": "rejected"}

    resolved = build_release_matrix(matrix, decisions)
    community = next(
        row for row in resolved["combinations"]
        if row["id"] == "community_summaries"
    )

    assert community["requested_flags"]["RAG_LATE_INTERACTION_ENABLED"] is False
    assert community["effective_flags"]["RAG_LATE_INTERACTION_ENABLED"] is False
    assert community["fallback_features"] == ["RAG_LATE_INTERACTION_ENABLED"]
    assert community["unresolved_features"] == []
    assert resolved["decisions_complete"] is True


def test_release_matrix_fails_closed_for_missing_decisions():
    matrix = _json("data/integrated_hardening_v1/matrix.json")
    resolved = build_release_matrix(matrix, {})

    crag = next(row for row in resolved["combinations"] if row["id"] == "crag_claim")
    assert crag["effective_flags"]["RAG_CRAG_ENABLED"] is False
    assert crag["effective_flags"]["RAG_CLAIM_REPAIR_ENABLED"] is False
    assert crag["unresolved_features"] == [
        "RAG_CRAG_ENABLED", "RAG_CLAIM_REPAIR_ENABLED",
    ]
    assert resolved["decisions_complete"] is False


def test_release_matrix_derives_selective_accepted_stack_from_decisions():
    decisions = {
        name: {
            "decision": (
                "accepted"
                if name == "RAG_QUERY_DECOMPOSITION_ENABLED"
                else "rejected"
            )
        }
        for name in FEATURE_FLAGS
    }

    resolved = build_release_matrix(
        _json("data/integrated_hardening_v1/matrix.json"), decisions,
    )

    assert {
        name for name, enabled in resolved["accepted_stack"].items() if enabled
    } == {"RAG_QUERY_DECOMPOSITION_ENABLED"}


def test_release_matrix_fails_closed_on_invalid_accepted_dependencies():
    decisions = {
        name: {"decision": "rejected"}
        for name in FEATURE_FLAGS
    }
    decisions["RAG_CRAG_ENABLED"] = {"decision": "accepted"}
    decisions["RAG_GRAPH_COMMUNITY_SUMMARIES_ENABLED"] = {"decision": "accepted"}

    resolved = build_release_matrix(
        _json("data/integrated_hardening_v1/matrix.json"), decisions,
    )

    assert all(
        row["effective_flags"]["RAG_CRAG_ENABLED"] is False
        and row["effective_flags"]["RAG_CLAIM_REPAIR_ENABLED"] is False
        and row["effective_flags"]["RAG_GRAPH_COMMUNITY_SUMMARIES_ENABLED"] is False
        for row in resolved["combinations"]
    )


def test_integrated_evidence_uses_effective_release_flags():
    matrix = _json("data/integrated_hardening_v1/matrix.json")
    decisions = {
        name: {"decision": "accepted"}
        for name in FEATURE_FLAGS
    }
    decisions["RAG_GROUNDED_MATH_ENABLED"] = {"decision": "rejected"}

    configurations = _expected_configurations(
        matrix,
        {
            "schema": "integrated-release-decisions-v1",
            "decisions": decisions,
        },
    )

    assert configurations["grounded_math"]["flags"][
        "RAG_GROUNDED_MATH_ENABLED"
    ] is False
    assert configurations["grounded_math"]["versions"] == next(
        row["versions"]
        for row in matrix["combinations"]
        if row["id"] == "grounded_math"
    )
    assert not any(configurations["query_decomposition"]["baseline_flags"].values())
    assert {
        name
        for name, enabled in configurations["community_summaries"][
            "baseline_flags"
        ].items()
        if enabled
    } == {"RAG_GRAPH_RETRIEVAL_ENABLED"}


def test_repository_release_ledger_records_late_interaction_as_rejected():
    ledger = _json("data/integrated_hardening_v1/release_decisions.json")
    late = ledger["decisions"]["RAG_LATE_INTERACTION_ENABLED"]
    evidence = Path(late["evidence"]["path"])

    assert late["decision"] == "rejected"
    assert late["source_commit"] == "757b9392275b22a826269959797c43d9d169bd8b"
    assert hashlib.sha256(evidence.read_bytes()).hexdigest() == late["evidence"]["sha256"]
    resolved = build_release_matrix(
        _json("data/integrated_hardening_v1/matrix.json"),
        ledger["decisions"],
    )
    assert all(
        row["effective_flags"]["RAG_LATE_INTERACTION_ENABLED"] is False
        for row in resolved["combinations"]
    )


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


def test_request_budget_enforces_selective_feature_boundaries():
    valid = {
        "id": "case-ok", "combination_id": "query_decomposition",
        "planner_count": 1, "subquery_count": 3, "correction_count": 0,
        "repair_count": 0, "calculation_count": 0,
        "graph_edge_count": 0, "provider_retries": 2,
        "final_generation_count": 1, "deadline_exceeded": False,
    }
    report = evaluate_request_budgets([valid])
    assert report["passed"] is True
    assert report["maxima"]["subquery_count"] == 3

    correction = {**valid, "id": "query-used-crag", "correction_count": 1}
    report = evaluate_request_budgets([correction])
    assert report["passed"] is False
    assert report["violations"][0]["field"] == "correction_count"

    inactive = {
        **valid, "id": "inactive-feature", "combination_id": "crag_claim",
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
            "community_summaries": False,
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
            "query_decomposition", "graph_retrieval", "community_summaries",
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
            "query_decomposition", "graph_retrieval", "community_summaries",
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
    assert artifact["release_decisions_complete"] is False
    assert all(
        not any(row["effective_flags"].values())
        for row in artifact["release_matrix"]["combinations"]
    )


def test_integrated_preflight_uses_effective_release_flags_and_keeps_late_off(tmp_path):
    matrix = _json("data/integrated_hardening_v1/matrix.json")
    cases = [
        json.loads(line)
        for line in Path("data/integrated_hardening_v1/security_matrix.jsonl")
        .read_text(encoding="utf-8").splitlines() if line.strip()
    ]
    decisions = _complete_release_ledger(tmp_path)
    artifact = build_preflight(
        matrix=matrix,
        security_cases=cases,
        prerequisites={name: True for name in (
            "crag", "grounded_math", "late_interaction",
            "query_decomposition", "graph_retrieval", "community_summaries",
        )},
        offline_evidence={
            "schema": "integrated-offline-verification-v1",
            "git_sha": "abc123", "passed": True,
            "cache_isolation_passed": True,
            "strict_stream_passed": True,
            "rollback_passed": True,
        },
        git_sha="abc123",
        release_decisions=decisions,
    )

    matrix_rows = artifact["release_matrix"]["combinations"]
    assert artifact["release_decisions_complete"] is True
    assert artifact["ready_for_live_matrix"] is True
    assert all(
        row["effective_flags"]["RAG_LATE_INTERACTION_ENABLED"] is False
        and row["fallback_features"] == ["RAG_LATE_INTERACTION_ENABLED"]
        for row in matrix_rows
    )


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
            "query_decomposition", "graph_retrieval", "community_summaries",
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
            "id": "safe-case", "combination_id": "crag_claim",
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
    identity = {
        "deployment_id": "candidate-1",
        "git_sha": "a" * 40,
        "manifest_sha256s": ["b" * 64],
        "snapshot_fingerprint": "c" * 64,
        "provider_configuration_sha256": "d" * 64,
        "governance_scope_sha256": "e" * 64,
        "collection": "fixture",
        "execution_context": "evaluation",
        "pipeline_configuration": {
            "flags": {"RAG_CRAG_ENABLED": True},
            "versions": {"RAG_PLANNER_VERSION": "planner-v1"},
        },
    }
    summary = {
        "concurrency": 5, "requests": 30, "successful_requests": 30,
        "first_token_p50_ms": 100, "first_token_p95_ms": 200,
        "complete_p50_ms": 400, "complete_p95_ms": 800,
    }
    benchmark = {
        "schema": "rag-concurrency-benchmark-v1",
        "runtime_identity": dict(identity),
        "results": [{"summary": summary, "samples": []}],
    }
    evaluation = {
        "schema": "rag-labeled-eval-v4", "case_count": 30,
        "total_estimated_cost": 0.3, "provider_retries": 3,
        "fallback_coverage": {"fallback_rate": 0.05},
        **{key: value for key, value in identity.items() if key != "deployment_id"},
    }
    report = build_integrated_load_report(benchmark, evaluation, concurrency=5)
    assert report["schema"] == "integrated-load-report-v1"
    assert report["cost_per_query"] == pytest.approx(0.01)
    assert report["provider_retry_rate"] == pytest.approx(0.1)
    assert report["fallback_rate"] == pytest.approx(0.05)
    with pytest.raises(ValueError, match="concurrency 10"):
        build_integrated_load_report(benchmark, evaluation, concurrency=10)
    benchmark["runtime_identity"] = {
        **identity,
        "snapshot_fingerprint": "f" * 64,
    }
    with pytest.raises(ValueError, match="runtime identity"):
        build_integrated_load_report(benchmark, evaluation, concurrency=5)


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
        "community_summaries": "retrieval-intelligence-gate-v1",
    }
    stages = {}
    for name, schema in schemas.items():
        artifact = tmp_path / f"{name}.json"
        payload = {"schema": schema, "git_sha": "abc", "passed": True}
        if name == "crag":
            payload["decision"] = "accepted"
        if name in {"grounded_math", "graph_retrieval"}:
            payload["production_eligible"] = True
        if name == "late_interaction":
            payload["stage"] = "late_interaction"
        if name == "community_summaries":
            payload["stage"] = "community_summaries"
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

    graph_path = Path(stages["graph_retrieval"]["artifact_path"])
    graph_payload = json.loads(graph_path.read_text(encoding="utf-8"))
    graph_payload["production_eligible"] = False
    graph_raw = (json.dumps(graph_payload) + "\n").encode()
    graph_path.write_bytes(graph_raw)
    stages["graph_retrieval"]["artifact_sha256"] = hashlib.sha256(
        graph_raw
    ).hexdigest()
    assert _prerequisites(payload, "abc")[0]["graph_retrieval"] is False

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


def test_rejected_late_interaction_prerequisite_keeps_historical_evidence(tmp_path):
    schemas = {
        "crag": "crag-production-pilot-v1",
        "grounded_math": "grounded-math-rollout-run-v1",
        "late_interaction": "retrieval-intelligence-gate-v1",
        "query_decomposition": "decomposition-rollout-run-v1",
        "graph_retrieval": "graph-rollout-run-v1",
        "community_summaries": "retrieval-intelligence-gate-v1",
    }
    stages = {}
    for name, schema in schemas.items():
        source_commit = "historical-late" if name == "late_interaction" else "abc"
        artifact = tmp_path / f"{name}.json"
        artifact_payload = {
            "schema": schema,
            "git_sha": source_commit,
            "passed": name != "late_interaction",
        }
        if name == "crag":
            artifact_payload["decision"] = "accepted"
        if name in {"grounded_math", "graph_retrieval"}:
            artifact_payload["production_eligible"] = True
        if name in {"late_interaction", "community_summaries"}:
            artifact_payload["stage"] = name
        if name == "late_interaction":
            artifact_payload["decision"] = "rejected"
        raw = (json.dumps(artifact_payload) + "\n").encode()
        artifact.write_bytes(raw)
        stages[name] = {
            "complete": True,
            "artifact_path": str(artifact),
            "artifact_sha256": hashlib.sha256(raw).hexdigest(),
            "artifact_schema": schema,
            "decision": "rejected" if name == "late_interaction" else "accepted",
        }

    statuses, verification = _prerequisites(
        {"schema": "integrated-prerequisites-v1", "stages": stages},
        "abc",
    )

    assert all(statuses.values())
    assert verification["late_interaction"]["artifact_verified"] is True

    graph = tmp_path / "graph_retrieval.json"
    graph_payload = {
        "schema": "graph-rollout-run-v1",
        "git_sha": "historical-graph",
        "passed": False,
        "decision": "rejected",
    }
    graph_raw = (json.dumps(graph_payload) + "\n").encode()
    graph.write_bytes(graph_raw)
    stages["graph_retrieval"].update({
        "artifact_path": str(graph),
        "artifact_sha256": hashlib.sha256(graph_raw).hexdigest(),
        "decision": "rejected",
    })

    statuses, _ = _prerequisites(
        {"schema": "integrated-prerequisites-v1", "stages": stages},
        "abc",
    )
    assert statuses["late_interaction"] is True
    assert statuses["graph_retrieval"] is False


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


def test_nested_source_artifact_tampering_invalidates_decision(tmp_path):
    source = tmp_path / "source.json"
    source_raw = b'{"schema":"raw-gate-v1","git_sha":"historical","passed":true}\n'
    source.write_bytes(source_raw)
    wrapper = tmp_path / "wrapper.json"
    wrapper_payload = {
        "schema": "controlled-demo-evidence-v1",
        "git_sha": "historical",
        "source_artifacts": [{
            "path": str(source),
            "sha256": hashlib.sha256(source_raw).hexdigest(),
            "schema": "raw-gate-v1",
        }],
    }
    wrapper_raw = (json.dumps(wrapper_payload) + "\n").encode()
    wrapper.write_bytes(wrapper_raw)
    decision = {
        "schema": "milestone-decision-v2",
        "milestone": "crag",
        "scope": "controlled_demo",
        "decision": "accepted",
        "source_commit": "historical",
        "evidence": [{
            "path": str(wrapper),
            "sha256": hashlib.sha256(wrapper_raw).hexdigest(),
            "schema": "controlled-demo-evidence-v1",
        }],
        "reason": "The controlled demo gate passed.",
        "reviewer_signoff": {
            "reviewer": "qa", "signed_at": "2026-07-16T00:00:00Z",
        },
    }
    assert verify_milestone_decision(decision, root=tmp_path)["passed"] is True

    source.write_text(
        '{"schema":"raw-gate-v1","git_sha":"historical","passed":false}\n',
        encoding="utf-8",
    )

    report = verify_milestone_decision(decision, root=tmp_path)
    assert report["passed"] is False
    assert report["evidence"][0]["nested_artifacts_passed"] is False


def test_commitless_evidence_cannot_trust_reference_source_commit(tmp_path):
    evidence = tmp_path / "gate.json"
    raw = b'{"schema":"raw-gate-v1","passed":false}\n'
    evidence.write_bytes(raw)
    decision = {
        "schema": "milestone-decision-v2",
        "milestone": "late_interaction",
        "scope": "controlled_demo",
        "decision": "rejected",
        "source_commit": "caller-supplied",
        "evidence": [{
            "path": str(evidence),
            "sha256": hashlib.sha256(raw).hexdigest(),
            "schema": "raw-gate-v1",
            "source_commit": "caller-supplied",
        }],
        "reason": "No quality gain.",
        "reviewer_signoff": {
            "reviewer": "qa", "signed_at": "2026-07-16T00:00:00Z",
        },
    }

    report = verify_milestone_decision(decision, root=tmp_path)

    assert report["passed"] is False
    assert report["source_commit_matches"] is False


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


def test_demo_matrix_keeps_all_rows_and_pins_rejected_features_off():
    matrix = _json("data/integrated_hardening_v1/matrix.json")
    decisions = {
        "late_interaction": {"scope": "controlled_demo", "decision": "rejected"},
        "graph_retrieval": {"scope": "controlled_demo", "decision": "inconclusive"},
    }
    demo = build_demo_matrix(matrix, decisions)
    assert demo["schema"] == "integrated-demo-feature-matrix-v1"
    assert len(demo["combinations"]) == 5
    assert all(
        row["requested_flags"]["RAG_LATE_INTERACTION_ENABLED"] is False
        and row["effective_flags"]["RAG_LATE_INTERACTION_ENABLED"] is False
        for row in demo["combinations"]
    )
    community = next(
        row for row in demo["combinations"]
        if row["id"] == "community_summaries"
    )
    assert community["baseline_flags"]["RAG_GRAPH_RETRIEVAL_ENABLED"] is False


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


def test_provider_smoke_rejects_error_text_without_persisting_response():
    response = "[Error] Service unavailable. private-provider-detail"
    artifact = run_provider_smoke(lambda *args, **kwargs: response)
    assert artifact["passed"] is False
    assert artifact["successful_requests"] == 0
    assert response not in json.dumps(artifact)


def test_provider_smoke_accepts_adapter_message_acknowledgement():
    from langchain_core.messages import AIMessage

    artifact = run_provider_smoke(lambda *args, **kwargs: AIMessage(content=" OK\n"))
    assert artifact["passed"] is True


@pytest.mark.parametrize("response", [None, "", {"content": "OK"}])
def test_provider_smoke_rejects_missing_or_wrong_response_type(response):
    artifact = run_provider_smoke(lambda *args, **kwargs: response)
    assert artifact["passed"] is False
    assert artifact["successful_requests"] == 0
    assert artifact["request_count"] == 1


def test_provider_smoke_rejects_wrong_request_count_before_dispatch():
    def invoke(*args, **kwargs):
        pytest.fail("invalid smoke must not dispatch")

    with pytest.raises(ValueError, match="exactly five"):
        run_provider_smoke(invoke, request_count=4)


def test_provider_smoke_handles_malformed_provider_exception_metadata():
    class LastAttempt:
        def exception(self):
            return None

    class ProviderError(Exception):
        status_code = "unknown"
        last_attempt = LastAttempt()

    def invoke(*args, **kwargs):
        raise ProviderError("503 service_unavailable private-detail")

    artifact = run_provider_smoke(invoke)
    assert artifact["passed"] is False
    assert artifact["request_count"] == 1
    assert artifact["status_codes"] == [503]
    assert artifact["error_categories"] == ["capacity"]
    assert "private-detail" not in json.dumps(artifact)


@pytest.mark.parametrize("failure", ["invalid", "exception", "retry"])
def test_provider_smoke_stops_before_next_request_after_failure(failure):
    calls = []

    def invoke(*args, **kwargs):
        calls.append(1)
        if len(calls) == 1:
            return "OK"
        if failure == "exception":
            raise RuntimeError("provider failed")
        if failure == "retry":
            kwargs["retry_counter"]["count"] = 1
            return "OK"
        return "[Error] Service unavailable."

    artifact = run_provider_smoke(invoke)
    assert len(calls) == 2
    assert artifact["request_count"] == 2
    assert artifact["passed"] is False


def test_provider_smoke_unwraps_retry_error_without_persisting_raw_message():
    class LastAttempt:
        @staticmethod
        def exception():
            return RuntimeError("503 service_unavailable no_capacity secret-detail")

    class FakeRetryError(Exception):
        last_attempt = LastAttempt()

    def invoke(_messages, **kwargs):
        kwargs["retry_counter"]["count"] = 3
        raise FakeRetryError("opaque retry wrapper")

    artifact = run_provider_smoke(invoke, request_count=5)

    assert artifact["passed"] is False
    assert artifact["provider_outcome"]["decision"] == "inconclusive"
    assert artifact["root_error_types"] == ["RuntimeError"]
    assert artifact["status_codes"] == [503]
    assert artifact["error_categories"] == ["capacity"]
    assert "secret-detail" not in json.dumps(artifact)


@pytest.mark.parametrize("message,category,status", [
    ("401 private-detail", "authentication_or_authorization", 401),
    ("request timed out private-detail", "timeout", None),
    ("502 private-detail", "http_error", 502),
])
def test_provider_smoke_classifies_first_failure_without_leaking_message(message, category, status):
    def invoke(*args, **kwargs):
        raise RuntimeError(message)

    artifact = run_provider_smoke(invoke)
    assert artifact["request_count"] == 1
    assert artifact["error_categories"] == [category]
    assert artifact["status_codes"] == [status]
    assert artifact["passed"] is False
    assert "private-detail" not in json.dumps(artifact)


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


def test_repository_demo_ledger_enables_only_accepted_grounded_math():
    root = Path.cwd()
    ledger = _json("data/integrated_hardening_v1/demo_decisions.json")
    report = verify_demo_decision_ledger(ledger, root=root, current_commit="current")
    assert report["passed"] is True
    assert report["ready_for_demo_matrix"] is True
    assert report["ready_for_live_matrix"] is False
    matrix = build_demo_matrix(
        _json("data/integrated_hardening_v1/matrix.json"),
        report["decisions"],
    )
    assert len(matrix["combinations"]) == 5
    effective_by_id = {
        row["id"]: row["effective_flags"]
        for row in matrix["combinations"]
    }
    assert effective_by_id["grounded_math"]["RAG_GROUNDED_MATH_ENABLED"] is True
    assert all(
        not enabled
        for combination_id, flags in effective_by_id.items()
        for flag, enabled in flags.items()
        if not (
            combination_id == "grounded_math"
            and flag == "RAG_GROUNDED_MATH_ENABLED"
        )
    )


def test_decision_builder_binds_commit_stamped_historical_artifact_without_rewriting_it(tmp_path):
    gate = tmp_path / "gate.json"
    raw = b'{"schema":"retrieval-intelligence-gate-v1","git_sha":"757b939","stage":"late_interaction","passed":false}\n'
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
        "fallback_coverage": {"fallback_rate": 0},
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
    initial_pipeline = {"flags": {}, "versions": {}}
    baseline["pipeline_configuration"] = initial_pipeline
    candidate["pipeline_configuration"] = initial_pipeline
    budget_case = {
        "id": "budget", "combination_id": "crag_claim",
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
    benchmark = {
        "schema": "rag-concurrency-benchmark-v1",
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
    identity_fields = (
        "git_sha", "manifest_sha256s", "snapshot_fingerprint",
        "provider_configuration_sha256", "governance_scope_sha256",
        "collection", "execution_context", "pipeline_configuration",
    )
    baseline_benchmark = {
        **benchmark,
        "runtime_identity": {
            "deployment_id": "baseline-1",
            **{field: baseline[field] for field in identity_fields},
        },
    }
    candidate_benchmark = {
        **benchmark,
        "runtime_identity": {
            "deployment_id": "candidate-1",
            **{field: candidate[field] for field in identity_fields},
        },
    }
    artifacts = {
        "baseline_eval": baseline, "candidate_eval": candidate,
        "baseline_trace": baseline_trace,
        "candidate_trace": candidate_trace,
        "baseline_benchmark": json.loads(json.dumps(baseline_benchmark)),
        "candidate_benchmark": json.loads(json.dumps(candidate_benchmark)),
        "baseline_load": load, "candidate_load": candidate_load,
        "results": {"passed": True, "source_eval_sha256s": ["candidate_eval"],
                    "budget_report": {"combination_ids": ["crag_claim"]}},
    }
    report = evaluate_combination_evidence("crag_claim", artifacts, digests)
    assert report["passed"] is True
    baseline_results = artifacts["baseline_benchmark"]["results"]
    artifacts["baseline_benchmark"]["results"] = baseline_results[1:]
    assert evaluate_combination_evidence(
        "crag_claim", artifacts, digests
    )["passed"] is False
    artifacts["baseline_benchmark"]["results"] = baseline_results
    artifacts["candidate_eval"]["snapshot_fingerprint"] = "other"
    assert evaluate_combination_evidence("crag_claim", artifacts, digests)["passed"] is False
    artifacts["candidate_eval"]["snapshot_fingerprint"] = "snapshot"
    artifacts["candidate_eval"]["outcome_confusion"]["leakage"] = 1
    assert evaluate_combination_evidence("crag_claim", artifacts, digests)["passed"] is False

    artifacts["candidate_eval"]["outcome_confusion"]["leakage"] = 0
    artifacts["candidate_load"]["concurrency"] = 1
    assert evaluate_combination_evidence("crag_claim", artifacts, digests)["passed"] is False
    artifacts["candidate_load"]["concurrency"] = 5

    original_sha = artifacts["candidate_trace"]["source"].pop("sha256")
    assert evaluate_combination_evidence("crag_claim", artifacts, digests)["passed"] is False
    artifacts["candidate_trace"]["source"]["sha256"] = original_sha

    artifacts["candidate_trace"]["observed_budget_metrics"][
        "max_correction_count"
    ] = 2
    assert evaluate_combination_evidence("crag_claim", artifacts, digests)["passed"] is False
    artifacts["candidate_trace"]["observed_budget_metrics"][
        "max_correction_count"
    ] = 0

    matrix_row = _json("data/integrated_hardening_v1/matrix.json")["combinations"][0]
    expected = {
        "flags": {
            name: str(value).casefold() == "true"
            for name, value in matrix_row["flags"].items()
        },
        "baseline_flags": {
            name: str(value).casefold() == "true"
            for name, value in matrix_row["baseline_flags"].items()
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
    artifacts["baseline_benchmark"]["runtime_identity"][
        "pipeline_configuration"
    ] = artifacts["baseline_eval"]["pipeline_configuration"]
    artifacts["candidate_benchmark"]["runtime_identity"][
        "pipeline_configuration"
    ] = artifacts["candidate_eval"]["pipeline_configuration"]
    assert evaluate_combination_evidence(
        "crag_claim", artifacts, digests, expected_configuration=expected
    )["passed"] is True
    artifacts["candidate_eval"]["pipeline_configuration"]["flags"][
        "RAG_CRAG_ENABLED"
    ] = False
    assert evaluate_combination_evidence(
        "crag_claim", artifacts, digests, expected_configuration=expected
    )["passed"] is False
