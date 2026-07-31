"""Deterministic coverage and reviewer-precision report for governed graph data."""

from __future__ import annotations

from collections import Counter

from mech_chatbot.governance.review_governance import (
    distinct_reviewer_count,
    independent_reviewer_diversity_valid,
    normalize_reviewer_identity,
    review_governance_status,
)


def _relation_identity(value):
    return (
        str(value.get("source_key") or "").strip().casefold(),
        str(value.get("relation_type") or "").strip().upper(),
        str(value.get("target_key") or "").strip().casefold(),
    )


def _has_provenance_value(value):
    if isinstance(value, bool):
        return value
    return bool(value.strip()) if isinstance(value, str) else value is not None


def validate_review_samples(
    samples, *, require_independent=False, allowed_edge_ids=None,
    allowed_proposal_ids=None, review_governance=None,
    review_governance_source_commit=None, review_governance_scope=None,
):
    governance = review_governance_status(
        review_governance,
        source_commit=review_governance_source_commit,
        scope=review_governance_scope,
    )
    governed_review = require_independent or review_governance is not None
    if governed_review and not governance.valid:
        raise ValueError("review governance is invalid")
    if require_independent and governance.mode != "multi_reviewer":
        raise ValueError("independent review requires multi_reviewer governance")
    validated = []
    identities = set()
    reviewers = set()
    for index, sample in enumerate(samples or ()):
        identity_type = "edge_id" if sample.get("edge_id") is not None else "proposal_id"
        identity = sample.get(identity_type)
        if identity is None:
            raise ValueError(f"review sample {index} requires edge_id or proposal_id")
        key = (identity_type, str(identity))
        if key in identities:
            raise ValueError(f"duplicate review sample identity: {identity_type}={identity}")
        identities.add(key)
        allowed = allowed_edge_ids if identity_type == "edge_id" else allowed_proposal_ids
        if allowed is not None and str(identity) not in {str(value) for value in allowed}:
            raise ValueError(f"review sample references unknown {identity_type}={identity}")
        reviewer = normalize_reviewer_identity(sample.get("reviewer"))
        if not reviewer:
            raise ValueError(f"review sample {index} requires reviewer")
        if not isinstance(sample.get("expected_correct"), bool):
            raise ValueError(f"review sample {index} expected_correct must be boolean")
        decision = str(sample.get("decision") or "").casefold()
        if decision not in {"approved", "rejected"}:
            raise ValueError(f"review sample {index} has invalid decision")
        if (
            governed_review
            and sample.get("review_source") != governance.review_source
        ):
            if require_independent:
                raise ValueError(f"review sample {index} is not marked independent")
            raise ValueError(
                f"review sample {index} does not match review governance"
            )
        if governed_review and identity_type != "edge_id":
            raise ValueError(f"review sample {index} must reference an approved edge_id")
        if governed_review and decision != "approved":
            raise ValueError(f"review sample {index} decision must match approved serving state")
        if (
            governance.mode == "single_owner"
            and reviewer != normalize_reviewer_identity(governance.owner)
        ):
            raise ValueError(
                f"review sample {index} reviewer does not match owner"
            )
        reviewers.add(reviewer)
        validated.append({**sample, "decision": decision})
    if (
        governed_review
        and governance.mode == "multi_reviewer"
        and validated
        and not independent_reviewer_diversity_valid(
            distinct_reviewer_count(reviewers)
        )
    ):
        raise ValueError(
            "independent review requires at least two distinct reviewers"
        )
    return validated


def build_graph_report(
    *, nodes, edges, proposals, expected_relations, review_samples, expected_domains,
    review_sample_source="independent", review_governance=None,
    review_governance_source_commit=None, review_governance_scope=None,
):
    approved_edges = [
        edge for edge in edges or ()
        if str(edge.get("serving_status") or "").casefold() == "approved"
    ]
    available = {_relation_identity(edge) for edge in approved_edges}
    expected = {_relation_identity(relation) for relation in expected_relations or ()}
    matched = expected & available
    governance = review_governance_status(
        review_governance,
        source_commit=review_governance_source_commit,
        scope=review_governance_scope,
    )
    governed_source = review_sample_source in {"independent", "owner_review"}
    review_governance_valid = (
        governed_source
        and governance.valid
        and review_sample_source == governance.review_source
    )
    if governed_source and not review_governance_valid:
        raise ValueError("review governance does not match sample source")
    if not governed_source and review_governance is not None:
        raise ValueError("review governance requires a governed review source")
    reviewed = validate_review_samples(
        review_samples, require_independent=review_sample_source == "independent",
        allowed_edge_ids={edge.get("edge_id") for edge in approved_edges},
        allowed_proposal_ids={item.get("proposal_id") for item in proposals or ()},
        review_governance=review_governance,
        review_governance_source_commit=review_governance_source_commit,
        review_governance_scope=review_governance_scope,
    )
    if review_sample_source == "independent":
        correct_reviews = sum(bool(sample.get("expected_correct")) for sample in reviewed)
    else:
        correct_reviews = sum(
            (bool(sample.get("expected_correct")) and sample.get("decision") == "approved")
            or (not bool(sample.get("expected_correct")) and sample.get("decision") == "rejected")
            for sample in reviewed
        )
    node_domains = {str(node.get("department") or "") for node in nodes or ()}
    edge_domains = {str(edge.get("department") or "") for edge in approved_edges}
    domains = list(dict.fromkeys(str(value) for value in expected_domains or ()))
    provenance_fields = (
        "doc_id", "page", "version", "department", "site", "security_level",
        "source_quote", "source_evidence_matches",
    )
    provenance_complete = sum(
        all(_has_provenance_value(edge.get(field)) for field in provenance_fields)
        for edge in approved_edges
    )
    return {
        "schema": "graph-readiness-v1",
        "node_count": len(nodes or ()), "approved_edge_count": len(approved_edges),
        "proposal_count": len(proposals or ()),
        "nodes_by_type": dict(sorted(Counter(str(node.get("node_type") or "unknown") for node in nodes or ()).items())),
        "edges_by_type": dict(sorted(Counter(str(edge.get("relation_type") or "unknown") for edge in approved_edges).items())),
        "nodes_by_department": dict(sorted(Counter(str(node.get("department") or "unknown") for node in nodes or ()).items())),
        "nodes_by_site": dict(sorted(Counter(str(node.get("site") or "unknown") for node in nodes or ()).items())),
        "nodes_by_security": dict(sorted(Counter(str(node.get("security_level") or "unknown") for node in nodes or ()).items())),
        "nodes_by_version": dict(sorted(Counter(str(node.get("version") or "unknown") for node in nodes or ()).items())),
        "edges_by_department": dict(sorted(Counter(str(edge.get("department") or "unknown") for edge in approved_edges).items())),
        "edges_by_site": dict(sorted(Counter(str(edge.get("site") or "unknown") for edge in approved_edges).items())),
        "edges_by_security": dict(sorted(Counter(str(edge.get("security_level") or "unknown") for edge in approved_edges).items())),
        "edges_by_version": dict(sorted(Counter(str(edge.get("version") or "unknown") for edge in approved_edges).items())),
        "proposals_by_status": dict(sorted(Counter(str(item.get("status") or "unknown") for item in proposals or ()).items())),
        "coverage_numerator": len(matched), "coverage_denominator": len(expected),
        "structured_coverage": len(matched) / len(expected) if expected else 0.0,
        "review_sample_count": len(reviewed),
        "review_sample_source": review_sample_source,
        "review_mode": governance.mode if review_governance_valid else None,
        "review_governance_valid": review_governance_valid,
        "reviewer_count": distinct_reviewer_count(
            sample.get("reviewer") for sample in reviewed
        ),
        "reviewed_edge_precision": correct_reviews / len(reviewed) if reviewed else 0.0,
        "provenance_complete_count": provenance_complete,
        "provenance_completeness": provenance_complete / len(approved_edges) if approved_edges else 0.0,
        "domain_coverage": {
            domain: domain in node_domains and domain in edge_domains for domain in domains
        },
    }
