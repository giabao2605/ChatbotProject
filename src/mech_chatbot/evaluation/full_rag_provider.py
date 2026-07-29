"""Fail-closed evidence gates for full-RAG rerank-provider evaluation."""

from __future__ import annotations

from typing import Any, Mapping, Sequence

from mech_chatbot.evaluation.rerank_provider import canonical_sha256


EVAL_SCHEMA = "rag-labeled-eval-v4"
TRACE_SCHEMA = "rag-refusal-snapshot-v1"
GATE_SCHEMA = "full-rag-rerank-provider-gate-v1"
PAIR_SCHEMA = "rerank-provider-full-rag-pair-v1"
AUTHORIZATION_SCHEMA = "rerank-provider-technical-authorization-v1"
CASE_IDS = (
    "crag-number-thousands",
    "crag-no-cost-refusal",
    "crag-restricted-denial",
)
QUALITY_METRICS = (
    "recall_at_5",
    "ndcg_at_5",
    "recall_at_10",
    "ndcg_at_10",
    "recall_at_20",
    "mrr",
)
ARM_ORDERS = ("baseline-first", "candidate-first", "baseline-first")
TRACE_FILTER_INVARIANTS = (
    "execution_contexts",
    "excluded_reasons",
    "exclude_empty_reason",
)


def _mapping(value: Any) -> Mapping[str, Any]:
    return value if isinstance(value, Mapping) else {}


def _number(value: Any) -> float | None:
    if isinstance(value, bool):
        return None
    try:
        result = float(value)
    except (TypeError, ValueError):
        return None
    return result if result >= 0 else None


def _count(value: Any) -> int | None:
    number = _number(value)
    if number is None or not number.is_integer():
        return None
    return int(number)


def _ratio(candidate: Any, baseline: Any) -> float | None:
    after = _number(candidate)
    before = _number(baseline)
    if after is None or before is None:
        return None
    if before == 0:
        return 1.0 if after == 0 else None
    return after / before


def _case_map(report: Mapping[str, Any]) -> dict[str, Mapping[str, Any]]:
    rows = report.get("cases")
    if not isinstance(rows, list):
        return {}
    result: dict[str, Mapping[str, Any]] = {}
    for row in rows:
        item = _mapping(row)
        case_id = str(item.get("id") or "")
        if not case_id or case_id in result:
            return {}
        result[case_id] = item
    return result


def _provider_stats(
    trace: Mapping[str, Any],
    provider: str,
) -> Mapping[str, Any]:
    return _mapping(
        _mapping(trace.get("rerank_by_provider")).get(provider)
    )


def _correction_repair_bounded(
    report: Mapping[str, Any],
    cases: Mapping[str, Mapping[str, Any]],
) -> bool:
    budget = _mapping(report.get("budget_counts"))
    for field in ("correction_count", "repair_count"):
        case_counts = [_count(row.get(field)) for row in cases.values()]
        aggregate = _count(budget.get(field))
        if (
            len(case_counts) != 3
            or any(value is None or value > 1 for value in case_counts)
            or aggregate is None
            or aggregate > 3
            or aggregate != sum(value or 0 for value in case_counts)
        ):
            return False
    return True


def _provider_isolated(
    trace: Mapping[str, Any],
    *,
    expected: str,
    absent: str,
) -> bool:
    used = _provider_stats(trace, expected)
    unused = _provider_stats(trace, absent)
    calls = _number(used.get("call_count"))
    successes = _number(used.get("success_count"))
    return (
        calls is not None
        and calls > 0
        and successes == calls
        and all(
            _number(used.get(field)) == 0
            for field in (
                "error_count",
                "fallback_count",
                "error_rate",
                "fallback_rate",
                "retry_attempt_count",
            )
        )
        and _number(unused.get("call_count")) == 0
        and all(
            _number(unused.get(field)) == 0
            for field in (
                "error_count",
                "fallback_count",
                "error_rate",
                "fallback_rate",
                "retry_attempt_count",
            )
        )
    )


def _identity(
    baseline: Mapping[str, Any],
    candidate: Mapping[str, Any],
) -> dict[str, Any]:
    manifests = baseline.get("manifest_sha256s")
    return {
        "source_commit": str(baseline.get("git_sha") or ""),
        "manifest_sha256": (
            str(manifests[0])
            if isinstance(manifests, list) and len(manifests) == 1
            else ""
        ),
        "snapshot_fingerprint": str(
            baseline.get("snapshot_fingerprint") or ""
        ),
        "governance_scope_sha256": str(
            baseline.get("governance_scope_sha256") or ""
        ),
        "collection": str(baseline.get("collection") or ""),
        "benchmark_concurrency": baseline.get("benchmark_concurrency"),
        "case_ids": list(CASE_IDS),
        "baseline_provider_configuration_sha256": str(
            baseline.get("provider_configuration_sha256") or ""
        ),
        "candidate_provider_configuration_sha256": str(
            candidate.get("provider_configuration_sha256") or ""
        ),
    }


def compare_full_rag_provider_reports(
    baseline_eval: Mapping[str, Any],
    candidate_eval: Mapping[str, Any],
    baseline_trace: Mapping[str, Any],
    candidate_trace: Mapping[str, Any],
    max_latency_ratio: float = 1.25,
    max_cost_ratio: float = 1.5,
) -> dict[str, Any]:
    """Compare Voyage and Jina full-RAG arms without authorizing release."""
    baseline = _mapping(baseline_eval)
    candidate = _mapping(candidate_eval)
    before_trace = _mapping(baseline_trace)
    after_trace = _mapping(candidate_trace)
    baseline_cases = _case_map(baseline)
    candidate_cases = _case_map(candidate)
    expected_case_set = set(CASE_IDS)
    latency_ratio = _ratio(
        candidate.get("latency_p95_ms"),
        baseline.get("latency_p95_ms"),
    )
    cost_ratio = _ratio(
        candidate.get("total_estimated_cost"),
        baseline.get("total_estimated_cost"),
    )
    latency_limit = _number(max_latency_ratio)
    cost_limit = _number(max_cost_ratio)
    identity_fields = (
        "git_sha",
        "manifest_sha256s",
        "snapshot_fingerprint",
        "governance_scope_sha256",
        "benchmark_concurrency",
        "collection",
        "execution_context",
    )
    same_identity = all(
        baseline.get(field) == candidate.get(field)
        and baseline.get(field) not in (None, "", [])
        for field in identity_fields
    )
    trace_identity = (
        _mapping(before_trace.get("source")).get("git_sha")
        == _mapping(after_trace.get("source")).get("git_sha")
        == baseline.get("git_sha")
        and bool(_mapping(before_trace.get("source")).get("sha256"))
        and bool(_mapping(after_trace.get("source")).get("sha256"))
        and all(
            _mapping(before_trace.get("filters")).get(field)
            == _mapping(after_trace.get("filters")).get(field)
            and _mapping(before_trace.get("filters")).get(field) is not None
            for field in TRACE_FILTER_INVARIANTS
        )
        and _mapping(before_trace.get("filters")).get("execution_contexts")
        == ["evaluation"]
    )
    quality_before = _mapping(baseline.get("ranked_retrieval"))
    quality_after = _mapping(candidate.get("ranked_retrieval"))
    quality_non_inferior = all(
        _number(quality_before.get(metric)) is not None
        and _number(quality_after.get(metric)) is not None
        and float(quality_after[metric]) >= float(quality_before[metric])
        for metric in QUALITY_METRICS
    )
    all_rows = [*baseline_cases.values(), *candidate_cases.values()]
    baseline_confusion = _mapping(baseline.get("outcome_confusion"))
    candidate_confusion = _mapping(candidate.get("outcome_confusion"))
    baseline_wrong = (
        _number(baseline_confusion.get("wrong_answer", 0))
        if baseline_confusion
        else None
    )
    candidate_wrong = (
        _number(candidate_confusion.get("wrong_answer", 0))
        if candidate_confusion
        else None
    )
    checks = {
        "schemas_valid": (
            baseline.get("schema") == EVAL_SCHEMA
            and candidate.get("schema") == EVAL_SCHEMA
            and before_trace.get("schema") == TRACE_SCHEMA
            and after_trace.get("schema") == TRACE_SCHEMA
        ),
        "identical_evidence_identity": (
            same_identity
            and baseline.get("execution_context") == "evaluation"
            and trace_identity
        ),
        "identical_pipeline_configuration": (
            baseline.get("feature_flags") == candidate.get("feature_flags")
            and baseline.get("pipeline_configuration")
            == candidate.get("pipeline_configuration")
            and bool(baseline.get("feature_flags"))
            and bool(baseline.get("pipeline_configuration"))
        ),
        "provider_configuration_isolated": (
            bool(baseline.get("provider_configuration_sha256"))
            and bool(candidate.get("provider_configuration_sha256"))
            and baseline.get("provider_configuration_sha256")
            != candidate.get("provider_configuration_sha256")
        ),
        "exact_case_set": (
            set(baseline_cases) == expected_case_set
            and set(candidate_cases) == expected_case_set
            and _count(baseline.get("total_cases")) == 3
            and _count(candidate.get("total_cases")) == 3
        ),
        "all_cases_passed": (
            len(all_rows) == 6
            and all(row.get("passed") is True for row in all_rows)
            and _count(baseline.get("passed_cases")) == 3
            and _count(candidate.get("passed_cases")) == 3
            and all(row.get("provider_failure") is False for row in all_rows)
        ),
        "no_leakage": (
            bool(baseline_confusion)
            and bool(candidate_confusion)
            and _number(baseline_confusion.get("leakage", 0)) == 0
            and _number(candidate_confusion.get("leakage", 0)) == 0
            and all(row.get("leaked") is False for row in all_rows)
        ),
        "wrong_answers_not_increased": (
            baseline_wrong is not None
            and candidate_wrong is not None
            and candidate_wrong <= baseline_wrong
        ),
        "provider_retries_zero": (
            _number(baseline.get("provider_retries")) == 0
            and _number(candidate.get("provider_retries")) == 0
            and all(_number(row.get("provider_retries")) == 0 for row in all_rows)
        ),
        "correction_repair_bounded": (
            _correction_repair_bounded(baseline, baseline_cases)
            and _correction_repair_bounded(candidate, candidate_cases)
        ),
        "quality_non_inferior": quality_non_inferior,
        "latency_within_limit": (
            latency_limit is not None
            and latency_ratio is not None
            and latency_ratio <= latency_limit
        ),
        "cost_within_limit": (
            cost_limit is not None
            and cost_ratio is not None
            and cost_ratio <= cost_limit
        ),
        "trace_contract": all(
            _count(trace.get("parse_errors")) == 0
            and _count(
                _mapping(trace.get("system_metrics")).get("query_count")
            )
            == 3
            for trace in (before_trace, after_trace)
        ),
        "baseline_voyage_isolated": _provider_isolated(
            before_trace,
            expected="voyage",
            absent="jina",
        ),
        "candidate_jina_isolated": _provider_isolated(
            after_trace,
            expected="jina",
            absent="voyage",
        ),
    }
    return {
        "schema": GATE_SCHEMA,
        "passed": all(checks.values()),
        "release_authorized": False,
        "max_latency_ratio": latency_limit,
        "max_cost_ratio": cost_limit,
        "latency_ratio": latency_ratio,
        "cost_ratio": cost_ratio,
        "checks": checks,
        "failed_checks": [
            name for name, passed in checks.items() if not passed
        ],
        "identity": _identity(baseline, candidate),
        "artifact_sha256s": {
            "baseline_eval_sha256": canonical_sha256(baseline),
            "candidate_eval_sha256": canonical_sha256(candidate),
            "baseline_trace_sha256": canonical_sha256(before_trace),
            "candidate_trace_sha256": canonical_sha256(after_trace),
        },
    }


def _pair_identity(pair: Mapping[str, Any]) -> dict[str, Any]:
    gate_identity = _mapping(_mapping(pair.get("gate")).get("identity"))
    profiles = _mapping(pair.get("profile_sha256s"))
    return {
        "source_commit": str(gate_identity.get("source_commit") or ""),
        "manifest_sha256": str(gate_identity.get("manifest_sha256") or ""),
        "snapshot_fingerprint": str(
            gate_identity.get("snapshot_fingerprint") or ""
        ),
        "governance_scope_sha256": str(
            gate_identity.get("governance_scope_sha256") or ""
        ),
        "baseline_profile_sha256": str(profiles.get("baseline") or ""),
        "candidate_profile_sha256": str(profiles.get("candidate") or ""),
        "baseline_provider_configuration_sha256": str(
            gate_identity.get("baseline_provider_configuration_sha256") or ""
        ),
        "candidate_provider_configuration_sha256": str(
            gate_identity.get("candidate_provider_configuration_sha256") or ""
        ),
    }


def build_provider_technical_authorization(
    pairs: Sequence[Mapping[str, Any]],
    approval_ref: str,
) -> dict[str, Any]:
    """Authorize a bounded pilot from three independent passing pairs."""
    rows = [_mapping(pair) for pair in pairs]
    pair_hashes = [canonical_sha256(pair) for pair in rows]
    identities = [_pair_identity(pair) for pair in rows]
    reference = identities[0] if identities else {}
    identity_fields = tuple(reference)
    gates = [_mapping(pair.get("gate")) for pair in rows]
    evidence_hash_sets = [
        _mapping(gate.get("artifact_sha256s")) for gate in gates
    ]
    evidence_hash_tuples = [
        tuple(sorted((str(key), str(value)) for key, value in hashes.items()))
        for hashes in evidence_hash_sets
    ]
    checks = {
        "exactly_three_unique_pairs": (
            len(rows) == 3 and len(set(pair_hashes)) == 3
        ),
        "pair_contracts_valid": (
            len(rows) == 3
            and [pair.get("pair_index") for pair in rows] == [1, 2, 3]
            and all(pair.get("schema") == PAIR_SCHEMA for pair in rows)
        ),
        "independent_evidence_artifacts": (
            len(evidence_hash_sets) == 3
            and all(
                set(hashes)
                == {
                    "baseline_eval_sha256",
                    "candidate_eval_sha256",
                    "baseline_trace_sha256",
                    "candidate_trace_sha256",
                }
                and all(bool(str(value)) for value in hashes.values())
                for hashes in evidence_hash_sets
            )
            and len(set(evidence_hash_tuples)) == 3
        ),
        "arm_orders_predeclared": (
            tuple(pair.get("arm_order") for pair in rows) == ARM_ORDERS
        ),
        "all_gates_passed": (
            len(gates) == 3
            and all(
                gate.get("schema") == GATE_SCHEMA
                and gate.get("passed") is True
                for gate in gates
            )
        ),
        "stable_evidence_identity": (
            len(identities) == 3
            and all(
                identity == reference
                and all(identity.get(field) for field in identity_fields)
                for identity in identities
            )
            and all(
                pair.get("source_commit") == reference.get("source_commit")
                and pair.get("manifest_sha256")
                == reference.get("manifest_sha256")
                and pair.get("snapshot_fingerprint")
                == reference.get("snapshot_fingerprint")
                for pair in rows
            )
        ),
        "approval_reference_valid": (
            isinstance(approval_ref, str)
            and approval_ref.startswith("codex-thread:")
            and bool(approval_ref.removeprefix("codex-thread:").strip())
        ),
        "no_release_authorization": (
            len(rows) == 3
            and all(
                pair.get("release_authorized") is False
                and gate.get("release_authorized") is False
                for pair, gate in zip(rows, gates, strict=True)
            )
        ),
    }
    accepted = all(checks.values())
    return {
        "schema": AUTHORIZATION_SCHEMA,
        "decision": "accepted" if accepted else "rejected",
        "technical_authorized": accepted,
        "pilot_eligible": accepted,
        "production_authorized": False,
        "release_authorized": False,
        "approval_ref": approval_ref,
        "checks": checks,
        "failed_checks": [
            name for name, passed in checks.items() if not passed
        ],
        "pair_sha256s": pair_hashes,
        "identity": reference,
    }


__all__ = [
    "AUTHORIZATION_SCHEMA",
    "CASE_IDS",
    "GATE_SCHEMA",
    "PAIR_SCHEMA",
    "build_provider_technical_authorization",
    "compare_full_rag_provider_reports",
]
