"""Failure-family contracts and aggregate evaluation for robust RAG fixes."""

from __future__ import annotations

from collections import defaultdict

from mech_chatbot.evaluation.outcomes import VALID_OUTCOMES


FAILURE_FAMILIES = frozenset({
    "ACCESS_POLICY_ERROR",
    "EVIDENCE_POLICY_ERROR",
    "CORRECTION_ROUTING_ERROR",
    "UNSUPPORTED_CLAIM",
    "STRUCTURED_FACT_MISSING",
    "CALCULATION_PLAN_ERROR",
    "MULTI_INTENT_COVERAGE_ERROR",
    "BRANCH_CITATION_ERROR",
    "GRAPH_RELATION_ERROR",
    "RERANK_REGRESSION",
    "PROVIDER_FAILURE",
    "LATENCY_BUDGET_ERROR",
})

EVIDENCE_STATES = frozenset({"SUFFICIENT", "AMBIGUOUS", "INSUFFICIENT"})
_CONTRACT_FIELDS = frozenset({
    "failure_family",
    "seed_case_id",
    "expected_policy",
    "invariants",
    "mutation_axes",
    "holdout",
})


def _nonempty_strings(value: object) -> bool:
    return (
        isinstance(value, list)
        and bool(value)
        and all(isinstance(item, str) and item.strip() for item in value)
    )


def validate_failure_contract(case: dict) -> None:
    """Validate an optional family contract without changing legacy manifests."""
    present = _CONTRACT_FIELDS.intersection(case)
    if not present:
        return None
    family = case.get("failure_family")
    if family not in FAILURE_FAMILIES:
        raise ValueError(f"unsupported failure_family: {family}")
    if not isinstance(case.get("seed_case_id"), str) or not case["seed_case_id"].strip():
        raise ValueError("seed_case_id must be a non-empty string")
    if not _nonempty_strings(case.get("invariants")):
        raise ValueError("invariants must be a non-empty string list")
    axes = case.get("mutation_axes")
    if not isinstance(axes, list) or not all(
        isinstance(item, str) and item.strip() for item in axes
    ):
        raise ValueError("mutation_axes must be a string list")
    if not isinstance(case.get("holdout"), bool):
        raise ValueError("holdout must be a boolean")

    policy = case.get("expected_policy")
    valid_policy = (
        isinstance(policy, dict)
        and policy.get("outcome") in VALID_OUTCOMES
        and str(policy.get("evidence_state") or "").upper() in EVIDENCE_STATES
        and isinstance(policy.get("correction_allowed"), bool)
    )
    if not valid_policy:
        raise ValueError(
            "expected_policy requires valid outcome/evidence_state/correction_allowed"
        )
    if policy["outcome"] == "access_denied" and policy["correction_allowed"]:
        raise ValueError("expected_policy cannot correct an access_denied outcome")
    return None


def _fraction(rows: list[dict]) -> float | None:
    if not rows:
        return None
    return sum(bool(row.get("passed")) for row in rows) / len(rows)


def _family_report(family: str, rows: list[dict]) -> dict:
    provider_failures = [row for row in rows if row.get("provider_failure")]
    quality_rows = [row for row in rows if not row.get("provider_failure")]
    seed_rows = [
        row for row in quality_rows
        if str(row.get("id")) == str(row.get("seed_case_id"))
    ]
    development = [
        row for row in quality_rows
        if not row.get("holdout")
        and str(row.get("id")) != str(row.get("seed_case_id"))
    ]
    holdout = [row for row in quality_rows if row.get("holdout")]
    leakage_count = sum(bool(row.get("leaked")) for row in rows)
    sample_complete = bool(seed_rows) and len(development) >= 4 and len(holdout) >= 2
    seed_passed = bool(seed_rows) and all(bool(row.get("passed")) for row in seed_rows)
    common_pass_rate = _fraction(development)
    holdout_pass_rate = _fraction(holdout)

    quality_passed = (
        sample_complete
        and seed_passed
        and common_pass_rate is not None
        and common_pass_rate >= 0.9
        and holdout_pass_rate == 1.0
    )
    if leakage_count:
        decision = "rejected"
    elif provider_failures or not sample_complete:
        decision = "inconclusive"
    else:
        decision = "accepted" if quality_passed else "rejected"

    return {
        "family": family,
        "case_count": len(rows),
        "seed_count": len(seed_rows),
        "seed_passed": seed_passed,
        "development_variant_count": len(development),
        "holdout_variant_count": len(holdout),
        "common_pass_rate": common_pass_rate,
        "holdout_pass_rate": holdout_pass_rate,
        "provider_failure_count": len(provider_failures),
        "leakage_count": leakage_count,
        "sample_complete": sample_complete,
        "quality_passed": quality_passed,
        "decision": decision,
    }


def build_failure_family_report(rows: list[dict]) -> dict:
    """Aggregate family-level evidence without turning provider errors into quality failures."""
    grouped: dict[str, list[dict]] = defaultdict(list)
    for row in rows:
        family = row.get("failure_family")
        if family is None:
            continue
        if family not in FAILURE_FAMILIES:
            raise ValueError(f"unsupported failure_family: {family}")
        grouped[family].append(row)

    families = {
        family: _family_report(family, family_rows)
        for family, family_rows in sorted(grouped.items())
    }
    decisions = {value["decision"] for value in families.values()}
    if not families:
        decision = None
    elif "rejected" in decisions:
        decision = "rejected"
    elif decisions == {"accepted"}:
        decision = "accepted"
    else:
        decision = "inconclusive"
    return {
        "schema": "failure-family-eval-v1",
        "applicable": bool(families),
        "decision": decision,
        "labeled_case_count": sum(len(value) for value in grouped.values()),
        "provider_failure_count": sum(
            value["provider_failure_count"] for value in families.values()
        ),
        "leakage_count": sum(value["leakage_count"] for value in families.values()),
        "families": families,
    }
