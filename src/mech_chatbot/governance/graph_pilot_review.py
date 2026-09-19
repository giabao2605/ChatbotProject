"""Fail-closed metadata contract for Graph pilot human review."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
import hashlib
from typing import Any


REVIEW_CASE_FIELDS = frozenset({
    "trace_id_sha256",
    "automated_status",
    "reviewer",
    "review_source",
    "answer_correct",
    "citation_correct",
    "safety_correct",
    "decision",
})
APPROVED_GRAPH_REFUSALS = frozenset({
    "evidence_gate",
})


@dataclass(frozen=True, slots=True)
class GraphPilotReviewContract:
    owner: str
    governance_sha256: str


def review_contract(
    artifact: Mapping[str, Any],
    *,
    governance_sha256: str,
    formal_series_sha256: str,
    source_commit: str,
) -> GraphPilotReviewContract | None:
    authority = artifact.get("authoritative_governance")
    formal = artifact.get("formal_graph_review")
    pilot = artifact.get("pilot_human_review")
    if not all((
        isinstance(authority, Mapping),
        isinstance(formal, Mapping),
        isinstance(pilot, Mapping),
    )):
        return None
    if not _contract_fields_valid(
        artifact,
        authority,
        formal,
        pilot,
        governance_sha256,
        formal_series_sha256,
        source_commit,
    ):
        return None
    owner = str(authority.get("owner") or "").strip()
    if not owner or pilot.get("primary_human_reviewer") != owner:
        return None
    return GraphPilotReviewContract(owner, governance_sha256)


def _contract_fields_valid(
    artifact: Mapping[str, Any],
    authority: Mapping[str, Any],
    formal: Mapping[str, Any],
    pilot: Mapping[str, Any],
    governance_sha256: str,
    formal_series_sha256: str,
    source_commit: str,
) -> bool:
    return all((
        artifact.get("schema") == "graph-pilot-review-contract-resolution-v1",
        artifact.get("source_commit") == source_commit,
        artifact.get("scope") == "controlled_demo",
        authority.get("sha256") == governance_sha256,
        authority.get("mode") == "single_owner",
        authority.get("review_source") == "owner_review",
        authority.get("risk_accepted") is True,
        authority.get("validated") is True,
        (formal.get("series_guardrail") or {}).get("sha256")
        == formal_series_sha256,
        formal.get("review_mode") == "multi_reviewer",
        formal.get("review_source") == "independent",
        formal.get("reviewer_count", 0) >= 2,
        formal.get("unchanged_by_pilot_contract") is True,
        pilot.get("stratified_cases") == 20,
        pilot.get("primary_labels_required") == 20,
        pilot.get(
            "failures_access_denied_and_low_confidence_require_owner_review"
        ) is True,
        pilot.get("codex_is_independent_human_reviewer") is False,
        (artifact.get("default_rollout") or {}).get("authorized") is False,
    ))


def review_result_valid(
    artifact: Mapping[str, Any],
    *,
    contract: GraphPilotReviewContract,
    source_commit: str,
    trace_sha256: str,
    required: Mapping[str, str],
) -> bool:
    cases = artifact.get("cases")
    if not isinstance(cases, Sequence) or isinstance(cases, (str, bytes)):
        return False
    rows = tuple(row for row in cases if isinstance(row, Mapping))
    by_id = {
        str(row.get("trace_id_sha256") or ""): row for row in rows
    }
    metadata_valid = _review_metadata_valid(
        artifact,
        contract=contract,
        source_commit=source_commit,
        trace_sha256=trace_sha256,
        required_count=len(required),
        row_count=len(rows),
    )
    return all((
        metadata_valid,
        len(required) >= 20,
        len(rows) == len(cases),
        len(by_id) == len(rows),
        set(by_id) == set(required),
        all(
            _review_case_valid(by_id.get(trace_id), status, contract.owner)
            for trace_id, status in required.items()
        ),
    ))


def select_review_cases(
    events_by_trace: Mapping[str, Sequence[Mapping[str, Any]]],
    candidate_ids: set[str],
) -> dict[str, str]:
    def row_for(trace_id: str) -> tuple[str, str, bool]:
        evidence = next(
            (
                event for event in events_by_trace[trace_id]
                if event.get("event") == "pilot_request_evidence"
                and event.get("route") == "graph_relational"
            ),
            None,
        )
        status = str(
            (evidence.get("graph_result_status") or "invalid")
            if evidence else "graph_error"
        )
        required = (
            evidence is None
            or evidence.get("owner_review_required") is True
            or evidence.get("low_confidence") is True
            or status != "valid"
        )
        digest = hashlib.sha256(trace_id.encode("utf-8")).hexdigest()
        return digest, status, required

    rows = [row_for(trace_id) for trace_id in candidate_ids]
    mandatory = sorted(row for row in rows if row[2])
    optional = sorted(row for row in rows if not row[2])
    return {
        digest: status
        for digest, status, _ in mandatory + optional[:max(0, 20 - len(mandatory))]
    }


def _review_metadata_valid(
    artifact: Mapping[str, Any],
    *,
    contract: GraphPilotReviewContract,
    source_commit: str,
    trace_sha256: str,
    required_count: int,
    row_count: int,
) -> bool:
    return all((
        artifact.get("schema") == "graph-pilot-human-review-result-v1",
        artifact.get("source_commit") == source_commit,
        artifact.get("scope") == "controlled_demo",
        artifact.get("review_mode") == "single_owner",
        artifact.get("review_source") == "owner_review",
        artifact.get("owner") == contract.owner,
        artifact.get("governance_sha256") == contract.governance_sha256,
        artifact.get("trace_sha256") == trace_sha256,
        artifact.get("stratification_method")
        == "graph_result_status_and_owner_review_required",
        artifact.get("raw_content_recorded") is False,
        artifact.get("case_count") == required_count == row_count,
    ))


def _review_case_valid(
    row: Mapping[str, Any] | None,
    expected_status: str,
    owner: str,
) -> bool:
    if row is None or set(row) != REVIEW_CASE_FIELDS:
        return False
    labels = tuple(
        row.get(name)
        for name in ("answer_correct", "citation_correct", "safety_correct")
    )
    return all((
        row.get("automated_status") == expected_status,
        row.get("reviewer") == owner,
        row.get("review_source") == "owner_review",
        all(value is True for value in labels),
        row.get("decision") == "accepted",
    ))


__all__ = [
    "APPROVED_GRAPH_REFUSALS",
    "GraphPilotReviewContract",
    "review_contract",
    "review_result_valid",
    "select_review_cases",
]
