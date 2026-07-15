"""Compose verified evidence for all roadmap 2.9 feature combinations."""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SRC = ROOT / "src"
for value in (ROOT, SRC):
    if str(value) not in sys.path:
        sys.path.insert(0, str(value))

from mech_chatbot.evaluation.integrated_hardening import (
    FEATURE_FLAGS,
    REQUEST_LIMITS,
    REQUIRED_COMBINATIONS,
    validate_combination_matrix,
    compare_load_reports,
    evaluate_request_budgets,
)
from scripts.integrated_eval.contracts import (
    read_json_artifact,
    require_artifact_reference,
)
from scripts.integrated_eval.load_report import build_integrated_load_report
from scripts.integrated_eval.results import build_results
from scripts.eval.rag_trace_snapshot import build_snapshot

CONDITION_FIELDS = (
    "git_sha", "manifest_sha256s", "snapshot_fingerprint",
    "provider_configuration_sha256", "governance_scope_sha256",
    "benchmark_concurrency", "collection", "execution_context",
)
MATRIX_CONDITION_FIELDS = (
    "git_sha", "provider_configuration_sha256", "governance_scope_sha256",
    "benchmark_concurrency", "execution_context",
)
ROW_ARTIFACTS = (
    "baseline_eval", "candidate_eval", "baseline_trace", "candidate_trace",
    "baseline_benchmark", "candidate_benchmark",
    "baseline_load", "candidate_load", "results",
)


def _reference(path: Path, artifact: dict, digest: str) -> dict:
    return {
        "path": str(path.resolve()), "sha256": digest,
        "schema": artifact.get("schema"),
    }


def _conditions_match(baseline: dict, candidate: dict) -> bool:
    return (
        baseline.get("schema") == "rag-labeled-eval-v4"
        and candidate.get("schema") == "rag-labeled-eval-v4"
        and baseline.get("run_label") == "baseline"
        and candidate.get("run_label") == "candidate"
        and all(baseline.get(field) is not None for field in CONDITION_FIELDS)
        and all(baseline.get(field) == candidate.get(field) for field in CONDITION_FIELDS)
    )


def _trace_matches(trace: dict, evaluation: dict) -> bool:
    source = trace.get("source") or {}
    filters = trace.get("filters") or {}
    observed = trace.get("observed_range") or {}
    system = trace.get("system_metrics") or {}
    event_counts = trace.get("event_counts") or {}
    budgets = trace.get("observed_budget_metrics") or {}
    budget_fields = {
        "max_planner_count", "max_subquery_count", "max_correction_count",
        "max_repair_count", "max_calculation_count", "max_graph_edge_count",
        "max_provider_retries",
    }
    try:
        window_start = datetime.fromisoformat(str(filters.get("start")).replace("Z", "+00:00"))
        window_end = datetime.fromisoformat(str(filters.get("end")).replace("Z", "+00:00"))
        eval_start = datetime.fromisoformat(str(evaluation.get("started_at")).replace("Z", "+00:00"))
        eval_end = datetime.fromisoformat(str(evaluation.get("completed_at")).replace("Z", "+00:00"))
        observed_first = datetime.fromisoformat(str(observed.get("first")).replace("Z", "+00:00"))
        observed_last = datetime.fromisoformat(str(observed.get("last")).replace("Z", "+00:00"))
        windows_valid = (
            window_start <= eval_start <= eval_end <= window_end
            and window_start <= observed_first <= observed_last <= window_end
        )
    except (TypeError, ValueError):
        windows_valid = False
    try:
        source_hash_matches = (
            hashlib.sha256(Path(source["path"]).read_bytes()).hexdigest()
            == source.get("sha256")
        )
        rebuilt_snapshot = build_snapshot(
            Path(source["path"]), start=filters.get("start"), end=filters.get("end"),
            execution_contexts=set(filters.get("execution_contexts") or []),
            excluded_reasons=set(filters.get("excluded_reasons") or []),
        )
        snapshot_recomputed = rebuilt_snapshot == trace
    except (KeyError, OSError, TypeError, ValueError):
        source_hash_matches = False
        snapshot_recomputed = False
    query_count = system.get("query_count")
    return (
        trace.get("schema") == "rag-refusal-snapshot-v1"
        and source.get("git_sha") == evaluation.get("git_sha")
        and isinstance(source.get("path"), str) and bool(source["path"].strip())
        and isinstance(source.get("sha256"), str) and len(source["sha256"]) == 64
        and all(character in "0123456789abcdef" for character in source["sha256"].casefold())
        and source_hash_matches
        and snapshot_recomputed
        and filters.get("execution_contexts") == ["evaluation"]
        and windows_valid
        and trace.get("parse_errors") == 0
        and trace.get("legacy_reason_events") == 0
        and isinstance(trace.get("denominator"), int) and trace["denominator"] >= 0
        and isinstance(query_count, int) and query_count > 0
        and query_count == evaluation.get("total_cases")
        and event_counts.get("rag_end") == query_count
        and set(budgets) == budget_fields
        and all(isinstance(value, int) and value >= 0 for value in budgets.values())
    )


def _trace_budget_matches(trace: dict, evaluation: dict) -> bool:
    report = evaluate_request_budgets(evaluation.get("cases") or [])
    if not report["passed"]:
        return False
    observed = trace.get("observed_budget_metrics") or {}
    mapping = {
        "max_planner_count": "planner_count",
        "max_subquery_count": "subquery_count",
        "max_correction_count": "correction_count",
        "max_repair_count": "repair_count",
        "max_calculation_count": "calculation_count",
        "max_graph_edge_count": "graph_edge_count",
        "max_provider_retries": "provider_retries",
    }
    return all(
        observed.get(trace_field) == report["maxima"].get(eval_field)
        and observed.get(trace_field) <= REQUEST_LIMITS[eval_field]
        for trace_field, eval_field in mapping.items()
    )


def _metric(report, section, metric):
    return (((report.get(section) or {}).get(metric) or {}).get("value"))


def _metric_not_decreased(baseline, candidate, section, metric) -> bool:
    before = _metric(baseline, section, metric)
    after = _metric(candidate, section, metric)
    return before is not None and after is not None and float(after) >= float(before)


def _enabled(value) -> bool:
    return str(value).strip().casefold() in {"1", "true", "yes", "y", "on"}


def evaluate_combination_evidence(
    combination_id, artifacts, digests, *, expected_configuration=None,
) -> dict:
    baseline = artifacts["baseline_eval"]
    candidate = artifacts["candidate_eval"]
    results = artifacts["results"]
    checks = {
        "benchmark_conditions_match": _conditions_match(baseline, candidate),
        "baseline_trace_bound": _trace_matches(artifacts["baseline_trace"], baseline),
        "candidate_trace_bound": _trace_matches(artifacts["candidate_trace"], candidate),
        "baseline_trace_budgets_reconciled": _trace_budget_matches(
            artifacts["baseline_trace"], baseline
        ),
        "candidate_trace_budgets_reconciled": _trace_budget_matches(
            artifacts["candidate_trace"], candidate
        ),
        "pipeline_configuration_bound": (
            expected_configuration is None
            or (
                baseline.get("pipeline_configuration") == {
                    "flags": {name: False for name in FEATURE_FLAGS},
                    "versions": expected_configuration["versions"],
                }
                and candidate.get("pipeline_configuration") == expected_configuration
            )
        ),
        "load_concurrency_bound": (
            artifacts["baseline_load"].get("concurrency")
            == baseline.get("benchmark_concurrency")
            == candidate.get("benchmark_concurrency")
            == artifacts["candidate_load"].get("concurrency")
        ),
        "baseline_load_bound": (
            artifacts["baseline_load"].get("source_eval_sha256")
            == digests["baseline_eval"]
            and artifacts["baseline_load"].get("source_benchmark_sha256")
            == digests["baseline_benchmark"]
        ),
        "candidate_load_bound": (
            artifacts["candidate_load"].get("source_eval_sha256")
            == digests["candidate_eval"]
            and artifacts["candidate_load"].get("source_benchmark_sha256")
            == digests["candidate_benchmark"]
        ),
        "results_bound": (
            results.get("source_eval_sha256s") == [digests["candidate_eval"]]
        ),
        "request_and_security_results_passed": (
            results.get("passed") is True
            and (results.get("budget_report") or {}).get("combination_ids")
            == [combination_id]
        ),
        "load_passed": compare_load_reports(
            artifacts["baseline_load"], artifacts["candidate_load"]
        )["passed"],
        "wrong_answer_not_increased": int(
            (candidate.get("outcome_confusion") or {}).get("wrong_answer") or 0
        ) <= int(
            (baseline.get("outcome_confusion") or {}).get("wrong_answer") or 0
        ),
        "leakage_zero": int(
            (candidate.get("outcome_confusion") or {}).get("leakage") or 0
        ) == 0,
        "claim_precision_not_decreased": _metric_not_decreased(
            baseline, candidate, "claim_evaluation", "claim_precision"
        ),
        "citation_accuracy_not_decreased": _metric_not_decreased(
            baseline, candidate, "citation_evaluation", "citation_accuracy"
        ),
        "citation_precision_not_decreased": _metric_not_decreased(
            baseline, candidate, "citation_evaluation", "citation_precision"
        ),
    }
    return {
        "schema": "integrated-combination-evidence-result-v1",
        "combination_id": combination_id,
        "passed": all(checks.values()),
        "checks": checks,
        "gate_inputs": {
            "baseline_eval_sha256": digests["baseline_eval"],
            "candidate_eval_sha256": digests["candidate_eval"],
            "baseline_trace_sha256": digests["baseline_trace"],
            "candidate_trace_sha256": digests["candidate_trace"],
        },
        "benchmark_conditions": {
            field: baseline.get(field) for field in CONDITION_FIELDS
        },
    }


def load_matrix_evidence(
    manifest: dict, *, feature_matrix: dict, root: Path = ROOT,
) -> tuple[dict, list]:
    matrix_validation = validate_combination_matrix(feature_matrix)
    if not matrix_validation["passed"]:
        raise ValueError("feature matrix is invalid")
    matrix_by_id = {
        row["id"]: {
            "flags": {name: _enabled(value) for name, value in row["flags"].items()},
            "versions": row["versions"],
        }
        for row in feature_matrix["combinations"]
    }
    rows = manifest.get("combinations") or []
    ids = [str(row.get("id") or "") for row in rows]
    reports = []
    references = []
    for row in rows:
        artifacts = {}
        digests = {}
        for name in ROW_ARTIFACTS:
            reference = row.get(name) or {}
            artifact = require_artifact_reference(reference, root=root)
            artifacts[name] = artifact
            digests[name] = reference["sha256"]
            references.append(reference)
        security_reference = row.get("security_results") or {}
        security_path = Path(str(security_reference.get("path") or ""))
        if not security_path.is_absolute():
            security_path = root / security_path
        security_raw = security_path.read_bytes()
        security_digest = hashlib.sha256(security_raw).hexdigest()
        if security_digest != security_reference.get("sha256"):
            raise ValueError(f"artifact hash mismatch: {security_path}")
        security_rows = [
            json.loads(line) for line in security_raw.decode("utf-8").splitlines()
            if line.strip()
        ]
        if not security_rows:
            raise ValueError("security results must be non-empty JSONL")
        references.append({
            "path": str(security_path.resolve()), "sha256": security_digest,
            "format": "jsonl",
        })
        if artifacts["results"].get("security_results_sha256") != security_digest:
            raise ValueError("integrated results are not bound to security results")
        report = evaluate_combination_evidence(
            row["id"], artifacts, digests,
            expected_configuration=matrix_by_id.get(row["id"]),
        )
        expected_baseline_load = build_integrated_load_report(
            artifacts["baseline_benchmark"], artifacts["baseline_eval"],
            concurrency=artifacts["baseline_load"].get("concurrency"),
            source_benchmark_sha256=digests["baseline_benchmark"],
            source_eval_sha256=digests["baseline_eval"],
        )
        expected_candidate_load = build_integrated_load_report(
            artifacts["candidate_benchmark"], artifacts["candidate_eval"],
            concurrency=artifacts["candidate_load"].get("concurrency"),
            source_benchmark_sha256=digests["candidate_benchmark"],
            source_eval_sha256=digests["candidate_eval"],
        )
        expected_results = build_results(
            [artifacts["candidate_eval"]], security_rows,
            source_eval_sha256s=[digests["candidate_eval"]],
            security_results_sha256=security_digest,
        )
        result_fields = (
            "schema", "passed", "eval_schemas_valid", "budget_report",
            "security_report", "source_eval_sha256s", "security_results_sha256",
        )
        report["checks"]["derived_artifacts_recomputed"] = (
            artifacts["baseline_load"] == expected_baseline_load
            and artifacts["candidate_load"] == expected_candidate_load
            and all(
                artifacts["results"].get(field) == expected_results.get(field)
                for field in result_fields
            )
        )
        report["passed"] = all(report["checks"].values())
        reports.append(report)
    primary = str(manifest.get("primary_combination_id") or "")
    by_id = {row["combination_id"]: row for row in reports}
    checks = {
        "schema_valid": manifest.get("schema") == "integrated-matrix-evidence-v1",
        "combination_ids_exact": set(ids) == set(REQUIRED_COMBINATIONS),
        "combination_ids_unique": len(ids) == len(set(ids)),
        "all_combinations_passed": bool(reports) and all(row["passed"] for row in reports),
        "primary_combination_valid": primary in by_id,
        "global_conditions_match": (
            bool(reports)
            and len({
                json.dumps({
                    field: row["benchmark_conditions"].get(field)
                    for field in MATRIX_CONDITION_FIELDS
                }, sort_keys=True)
                for row in reports
            }) == 1
        ),
    }
    return ({
        "schema": "integrated-matrix-evidence-result-v1",
        "passed": all(checks.values()), "checks": checks,
        "combination_results": reports,
        "primary_combination_id": primary,
        "primary_gate_inputs": (by_id.get(primary) or {}).get("gate_inputs") or {},
    }, references)


_RELEASE_SCHEMAS = {
    "RAG_CRAG_ENABLED": "crag-production-pilot-v1",
    "RAG_CLAIM_REPAIR_ENABLED": "crag-production-pilot-v1",
    "RAG_GROUNDED_MATH_ENABLED": "grounded-math-rollout-run-v1",
    "RAG_LATE_INTERACTION_ENABLED": "retrieval-intelligence-gate-v1",
    "RAG_QUERY_DECOMPOSITION_ENABLED": "decomposition-rollout-run-v1",
    "RAG_GRAPH_RETRIEVAL_ENABLED": "graph-rollout-run-v1",
    "RAG_GRAPH_COMMUNITY_SUMMARIES_ENABLED": "retrieval-intelligence-gate-v1",
}


def _release_evidence_matches(flag, row, artifact) -> bool:
    if artifact.get("schema") != _RELEASE_SCHEMAS[flag]:
        return False
    if flag == "RAG_LATE_INTERACTION_ENABLED" and artifact.get("stage") != "late_interaction":
        return False
    if flag == "RAG_GRAPH_COMMUNITY_SUMMARIES_ENABLED" and artifact.get("stage") != "community_summaries":
        return False
    decision = row.get("decision")
    if decision == "accepted":
        return (
            artifact.get("passed") is True
            and artifact.get("production_eligible", True) is True
            and artifact.get("decision", "accepted") == "accepted"
        )
    return (
        decision == "rejected"
        and (
            artifact.get("passed") is False
            or artifact.get("production_eligible") is False
            or artifact.get("decision") == "rejected"
        )
    )


def _release_decisions_complete(decisions: dict) -> bool:
    rows = decisions.get("decisions") or {}
    if not (
        decisions.get("schema") == "integrated-release-decisions-v1"
        and set(rows) == set(FEATURE_FLAGS)
        and all(
            isinstance(row, dict) and row.get("decision") in {"accepted", "rejected"}
            and isinstance(row.get("evidence"), dict) for row in rows.values()
        )
    ):
        return False
    try:
        evidence = [require_artifact_reference(row["evidence"], root=ROOT) for row in rows.values()]
    except (OSError, ValueError, json.JSONDecodeError):
        return False
    return all(
        _release_evidence_matches(flag, rows[flag], artifact)
        for flag, artifact in zip(rows, evidence)
    )


def compose_gate_metadata(
    *, readiness, offline, decisions, matrix_report, references,
) -> dict:
    commits = {readiness.get("git_sha"), offline.get("git_sha")}
    matrix_commits = {
        row.get("benchmark_conditions", {}).get("git_sha")
        for row in matrix_report.get("combination_results") or []
    }
    integrity = {
        "passed": (
            len(commits) == 1 and None not in commits
            and matrix_commits == commits
            and matrix_report.get("passed") is True
        ),
        "artifact_references": references,
        "benchmark_conditions_match": all(
            row["checks"]["benchmark_conditions_match"]
            for row in matrix_report.get("combination_results") or []
        ),
        "git_sha": next(iter(commits)) if len(commits) == 1 else None,
    }
    return {
        "schema": "integrated-gate-metadata-v1",
        "artifact_integrity": integrity,
        "matrix_validation": readiness.get("matrix_validation") or {},
        "combination_matrix_evidence": matrix_report,
        "primary_gate_inputs": matrix_report.get("primary_gate_inputs") or {},
        "strict_stream_evidence": {"passed": offline.get("strict_stream_passed") is True},
        "cache_isolation": {"passed": offline.get("cache_isolation_passed") is True},
        "rollback_evidence": {"passed": offline.get("rollback_passed") is True},
        "prerequisites": readiness.get("prerequisites") or {},
        "release_decisions_complete": _release_decisions_complete(decisions),
    }


def main(argv=None):
    parser = argparse.ArgumentParser()
    parser.add_argument("--readiness", type=Path, required=True)
    parser.add_argument("--offline-evidence", type=Path, required=True)
    parser.add_argument("--release-decisions", type=Path, required=True)
    parser.add_argument("--matrix-evidence", type=Path, required=True)
    parser.add_argument("--feature-matrix", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args(argv)
    top = {}
    references = []
    for name, path in (
        ("readiness", args.readiness), ("offline", args.offline_evidence),
        ("decisions", args.release_decisions),
    ):
        artifact, digest = read_json_artifact(path)
        top[name] = artifact
        references.append(_reference(path, artifact, digest))
    matrix_manifest, digest = read_json_artifact(args.matrix_evidence)
    references.append(_reference(args.matrix_evidence, matrix_manifest, digest))
    feature_matrix, digest = read_json_artifact(args.feature_matrix)
    references.append(_reference(args.feature_matrix, feature_matrix, digest))
    matrix_report, nested_references = load_matrix_evidence(
        matrix_manifest, feature_matrix=feature_matrix,
        root=args.matrix_evidence.parent,
    )
    references.extend(nested_references)
    metadata = compose_gate_metadata(
        readiness=top["readiness"], offline=top["offline"],
        decisions=top["decisions"], matrix_report=matrix_report,
        references=references,
    )
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(metadata, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(metadata, ensure_ascii=False, indent=2))
    return 0 if metadata["artifact_integrity"]["passed"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
