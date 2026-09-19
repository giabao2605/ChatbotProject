from __future__ import annotations

import pytest

from mech_chatbot.evaluation.failure_families import (
    build_failure_family_report,
    validate_failure_contract,
)


pytestmark = pytest.mark.unit


def _contract(**overrides):
    value = {
        "id": "evidence-seed",
        "failure_family": "EVIDENCE_POLICY_ERROR",
        "seed_case_id": "evidence-seed",
        "expected_policy": {
            "outcome": "partial_answer",
            "evidence_state": "AMBIGUOUS",
            "correction_allowed": True,
        },
        "invariants": ["leakage_zero", "governance_unchanged"],
        "mutation_axes": ["paraphrase", "intent_order"],
        "holdout": False,
    }
    value.update(overrides)
    return value


def _row(case_id, *, holdout=False, passed=True, provider_failure=False, leaked=False):
    return {
        "id": case_id,
        "failure_family": "EVIDENCE_POLICY_ERROR",
        "seed_case_id": "evidence-seed",
        "holdout": holdout,
        "passed": passed,
        "provider_failure": provider_failure,
        "leaked": leaked,
    }


def test_failure_contract_is_optional_for_legacy_manifest_cases():
    assert validate_failure_contract({"id": "legacy-case"}) is None


def test_failure_contract_rejects_incomplete_or_unknown_family():
    with pytest.raises(ValueError, match="unsupported failure_family"):
        validate_failure_contract(_contract(failure_family="UNKNOWN"))

    incomplete = _contract()
    incomplete.pop("invariants")
    with pytest.raises(ValueError, match="invariants"):
        validate_failure_contract(incomplete)


def test_failure_contract_rejects_invalid_expected_policy():
    invalid = _contract(expected_policy={
        "outcome": "maybe",
        "evidence_state": "UNKNOWN",
        "correction_allowed": "yes",
    })

    with pytest.raises(ValueError, match="expected_policy"):
        validate_failure_contract(invalid)


def test_failure_family_report_accepts_complete_seed_and_unseen_variants():
    rows = [_row("evidence-seed")]
    rows.extend(_row(f"dev-{index}") for index in range(1, 5))
    rows.extend(_row(f"holdout-{index}", holdout=True) for index in range(1, 3))

    report = build_failure_family_report(rows)
    family = report["families"]["EVIDENCE_POLICY_ERROR"]

    assert report["schema"] == "failure-family-eval-v1"
    assert report["decision"] == "accepted"
    assert family["decision"] == "accepted"
    assert family["seed_passed"] is True
    assert family["development_variant_count"] == 4
    assert family["holdout_variant_count"] == 2
    assert family["common_pass_rate"] == 1.0


def test_failure_family_report_is_inconclusive_when_sample_or_provider_is_invalid():
    incomplete = build_failure_family_report([
        _row("evidence-seed"),
        _row("dev-1"),
        _row("provider-error", provider_failure=True, passed=False),
    ])

    assert incomplete["decision"] == "inconclusive"
    assert incomplete["provider_failure_count"] == 1
    assert incomplete["families"]["EVIDENCE_POLICY_ERROR"]["sample_complete"] is False


def test_failure_family_report_rejects_quality_or_safety_regression():
    rows = [_row("evidence-seed")]
    rows.extend(_row(f"dev-{index}") for index in range(1, 5))
    rows.extend([
        _row("holdout-1", holdout=True),
        _row("holdout-2", holdout=True, passed=False, leaked=True),
    ])

    report = build_failure_family_report(rows)
    family = report["families"]["EVIDENCE_POLICY_ERROR"]

    assert report["decision"] == "rejected"
    assert family["decision"] == "rejected"
    assert family["leakage_count"] == 1
    assert family["holdout_pass_rate"] == 0.5
