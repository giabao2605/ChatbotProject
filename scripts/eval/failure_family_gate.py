"""Gate matched baseline/candidate runs for deterministic failure families."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path


_PAIR_FIELDS = (
    "git_sha",
    "manifest_sha256s",
    "snapshot_fingerprint",
    "provider_configuration_sha256",
    "governance_scope_sha256",
    "benchmark_concurrency",
    "collection",
    "execution_context",
)


def _ratio(candidate: float, baseline: float) -> float:
    if baseline == 0:
        return 1.0 if candidate == 0 else float("inf")
    return candidate / baseline


def _request_budgets_respected(cases) -> bool:
    for case in cases or ():
        if any(int(case.get(field) or 0) > 1 for field in (
            "correction_count", "repair_count", "planner_count",
            "calculation_count", "graph_traversal_count",
        )):
            return False
        if int(case.get("subquery_count") or 0) > 3:
            return False
        if int(case.get("final_generation_count") or 0) > 1:
            return False
        if int(case.get("graph_max_hops") or 0) > 2:
            return False
        if int(case.get("graph_edge_count") or 0) > 50:
            return False
    return True


def compare_failure_family_pair(
    baseline: dict,
    candidate: dict,
    *,
    rollback: dict,
    max_latency_ratio: float = 1.5,
    max_cost_ratio: float = 1.5,
) -> dict:
    family = candidate.get("failure_family_evaluation") or {}
    baseline_outcomes = baseline.get("outcome_confusion") or {}
    candidate_outcomes = candidate.get("outcome_confusion") or {}
    provider_failures = int(family.get("provider_failure_count") or 0)
    leakage = max(
        int(family.get("leakage_count") or 0),
        int(candidate_outcomes.get("leakage") or 0),
    )
    conditions_match = all(
        baseline.get(field) not in (None, "", [])
        and baseline.get(field) == candidate.get(field)
        for field in _PAIR_FIELDS
    )
    family_rows = family.get("families") or {}
    family_contract_complete = (
        family.get("schema") == "failure-family-eval-v1"
        and family.get("applicable") is True
        and bool(family_rows)
        and all(
            value.get("sample_complete") is True
            for value in family_rows.values()
        )
    )
    total_cases = max(1, int(candidate.get("total_cases") or len(candidate.get("cases") or ())))
    checks = {
        "benchmark_conditions_match": conditions_match,
        "family_contract_complete": family_contract_complete,
        "all_families_accepted": (
            family.get("decision") == "accepted"
            and all(value.get("decision") == "accepted" for value in family_rows.values())
        ),
        "wrong_answer_not_increased": int(candidate_outcomes.get("wrong_answer") or 0)
        <= int(baseline_outcomes.get("wrong_answer") or 0),
        "wrong_refusal_not_increased": (
            int(candidate_outcomes.get("wrong_refusal") or 0)
            + int(candidate_outcomes.get("wrong_refusal_type") or 0)
        ) <= (
            int(baseline_outcomes.get("wrong_refusal") or 0)
            + int(baseline_outcomes.get("wrong_refusal_type") or 0)
        ),
        "leakage_zero": leakage == 0,
        "request_budgets_respected": _request_budgets_respected(candidate.get("cases")),
        "provider_retry_budget": int(candidate.get("provider_retries") or 0)
        <= total_cases * 2,
        "latency_within_budget": _ratio(
            float(candidate.get("latency_p95_ms") or 0),
            float(baseline.get("latency_p95_ms") or 0),
        ) <= max_latency_ratio,
        "cost_within_budget": _ratio(
            float(candidate.get("total_estimated_cost") or 0),
            float(baseline.get("total_estimated_cost") or 0),
        ) <= max_cost_ratio,
        "rollback_verified": (
            rollback.get("schema") == "feature-rollback-verification-v1"
            and rollback.get("passed") is True
        ),
    }
    safety_or_quality_checks = (
        "all_families_accepted",
        "wrong_answer_not_increased",
        "wrong_refusal_not_increased",
        "leakage_zero",
        "request_budgets_respected",
        "provider_retry_budget",
        "latency_within_budget",
        "cost_within_budget",
    )
    if leakage:
        decision, reason = "rejected", "safety_regression"
    elif provider_failures:
        decision, reason = "inconclusive", "provider_failure"
    elif not conditions_match or not family_contract_complete or not checks["rollback_verified"]:
        decision, reason = "inconclusive", "evidence_incomplete_or_unmatched"
    elif all(checks[name] for name in safety_or_quality_checks):
        decision, reason = "accepted", "all_controlled_demo_gates_passed"
    else:
        decision, reason = "rejected", "quality_or_budget_regression"
    return {
        "schema": "failure-family-rollout-gate-v1",
        "scope": "controlled_demo",
        "passed": decision == "accepted",
        "decision": decision,
        "reason": reason,
        "checks": checks,
        "limits": {
            "max_latency_ratio": max_latency_ratio,
            "max_cost_ratio": max_cost_ratio,
            "max_corrections": 1,
            "max_repairs": 1,
            "max_planners": 1,
            "max_calculations": 1,
            "max_graph_traversals": 1,
            "max_subqueries": 3,
            "max_graph_hops": 2,
            "max_graph_edges": 50,
        },
    }


def _read(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def _reference(path: Path) -> dict:
    artifact = _read(path)
    return {
        "path": str(path),
        "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
        "schema": artifact.get("schema"),
    }


def main(argv=None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("baseline", type=Path)
    parser.add_argument("candidate", type=Path)
    parser.add_argument("rollback", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args(argv)
    if args.output.exists():
        parser.error(f"output already exists: {args.output}")
    report = compare_failure_family_pair(
        _read(args.baseline),
        _read(args.candidate),
        rollback=_read(args.rollback),
    )
    report["source_artifacts"] = [
        _reference(args.baseline),
        _reference(args.candidate),
        _reference(args.rollback),
    ]
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    return 0 if report["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
