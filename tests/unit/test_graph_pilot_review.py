"""Direct contracts for Graph pilot single-owner review governance."""

from __future__ import annotations

import hashlib

import pytest

from mech_chatbot.governance.graph_pilot_review import (
    review_contract,
    review_result_valid,
    select_review_cases,
)


pytestmark = pytest.mark.unit


def _contract_artifact(**updates):
    base = {
        "schema": "graph-pilot-review-contract-resolution-v1",
        "source_commit": "a" * 40,
        "scope": "controlled_demo",
        "authoritative_governance": {
            "sha256": "b" * 64,
            "mode": "single_owner",
            "review_source": "owner_review",
            "owner": "bao.nguyen",
            "risk_accepted": True,
            "validated": True,
        },
        "formal_graph_review": {
            "series_guardrail": {"sha256": "c" * 64},
            "review_mode": "multi_reviewer",
            "review_source": "independent",
            "reviewer_count": 2,
            "unchanged_by_pilot_contract": True,
        },
        "pilot_human_review": {
            "stratified_cases": 20,
            "primary_human_reviewer": "bao.nguyen",
            "primary_labels_required": 20,
            "failures_access_denied_and_low_confidence_require_owner_review": True,
            "codex_is_independent_human_reviewer": False,
        },
        "default_rollout": {"authorized": False},
    }
    return {**base, **updates}


def _contract():
    return review_contract(
        _contract_artifact(),
        governance_sha256="b" * 64,
        formal_series_sha256="c" * 64,
        source_commit="a" * 40,
    )


def _review_case(trace_id: str, status: str = "valid") -> dict:
    return {
        "trace_id_sha256": trace_id,
        "automated_status": status,
        "reviewer": "bao.nguyen",
        "review_source": "owner_review",
        "answer_correct": True,
        "citation_correct": True,
        "safety_correct": True,
        "decision": "accepted",
    }


def _review_result(required: dict[str, str], **updates) -> dict:
    base = {
        "schema": "graph-pilot-human-review-result-v1",
        "source_commit": "a" * 40,
        "scope": "controlled_demo",
        "review_mode": "single_owner",
        "review_source": "owner_review",
        "owner": "bao.nguyen",
        "governance_sha256": "b" * 64,
        "trace_sha256": "d" * 64,
        "stratification_method": "graph_result_status_and_owner_review_required",
        "raw_content_recorded": False,
        "case_count": len(required),
        "cases": [_review_case(key, value) for key, value in required.items()],
    }
    return {**base, **updates}


def test_review_contract_preserves_formal_independence_and_graph_owner():
    contract = _contract()

    assert contract is not None
    assert contract.owner == "bao.nguyen"


@pytest.mark.parametrize(
    "artifact",
    [
        _contract_artifact(
            authoritative_governance={
                **_contract_artifact()["authoritative_governance"],
                "owner": "other.owner",
            }
        ),
        _contract_artifact(default_rollout={"authorized": True}),
        _contract_artifact(
            formal_graph_review={
                **_contract_artifact()["formal_graph_review"],
                "review_mode": "single_owner",
            }
        ),
    ],
)
def test_review_contract_rejects_scope_or_independence_drift(artifact):
    assert review_contract(
        artifact,
        governance_sha256="b" * 64,
        formal_series_sha256="c" * 64,
        source_commit="a" * 40,
    ) is None


def test_review_result_accepts_exact_metadata_only_owner_labels():
    required = {hashlib.sha256(str(i).encode()).hexdigest(): "valid" for i in range(20)}

    assert review_result_valid(
        _review_result(required),
        contract=_contract(),
        source_commit="a" * 40,
        trace_sha256="d" * 64,
        required=required,
    )


@pytest.mark.parametrize("drift", ["duplicate", "extra", "raw"])
def test_review_result_rejects_case_set_or_raw_content_drift(drift):
    required = {hashlib.sha256(str(i).encode()).hexdigest(): "valid" for i in range(20)}
    artifact = _review_result(required)
    cases = artifact["cases"]
    if drift == "duplicate":
        cases = [*cases[:-1], cases[0]]
    elif drift == "extra":
        cases = [*cases, _review_case("e" * 64)]
    else:
        cases = [{**cases[0], "answer": "private"}, *cases[1:]]
    artifact = {**artifact, "cases": cases, "case_count": len(cases)}

    assert not review_result_valid(
        artifact,
        contract=_contract(),
        source_commit="a" * 40,
        trace_sha256="d" * 64,
        required=required,
    )


def test_selection_includes_all_failures_refusals_and_low_confidence_cases():
    events = {}
    for index in range(25):
        trace_id = f"private-trace-{index}"
        if index == 0:
            rows = [{"event": "graph_retrieval", "error": "unavailable"}]
        else:
            rows = [{
                "event": "pilot_request_evidence",
                "route": "graph_relational",
                "graph_result_status": "safe_refusal" if index == 1 else "valid",
                "owner_review_required": index in {1, 2},
                "low_confidence": index == 2,
            }]
        events = {**events, trace_id: rows}
    selected = select_review_cases(events, set(events))
    mandatory = {
        hashlib.sha256(f"private-trace-{index}".encode()).hexdigest()
        for index in range(3)
    }

    assert len(selected) == 20
    assert mandatory <= set(selected)
    assert all("private-trace" not in key for key in selected)
