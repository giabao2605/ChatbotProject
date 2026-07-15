"""Deterministic coverage and reviewer-precision report for governed graph data."""

from __future__ import annotations

from collections import Counter


def _relation_identity(value):
    return (
        str(value.get("source_key") or "").strip().casefold(),
        str(value.get("relation_type") or "").strip().upper(),
        str(value.get("target_key") or "").strip().casefold(),
    )


def validate_review_samples(samples, *, require_independent=False):
    validated = []
    identities = set()
    for index, sample in enumerate(samples or ()):
        identity_type = "edge_id" if sample.get("edge_id") is not None else "proposal_id"
        identity = sample.get(identity_type)
        if identity is None:
            raise ValueError(f"review sample {index} requires edge_id or proposal_id")
        key = (identity_type, str(identity))
        if key in identities:
            raise ValueError(f"duplicate review sample identity: {identity_type}={identity}")
        identities.add(key)
        if not str(sample.get("reviewer") or "").strip():
            raise ValueError(f"review sample {index} requires reviewer")
        if not isinstance(sample.get("expected_correct"), bool):
            raise ValueError(f"review sample {index} expected_correct must be boolean")
        decision = str(sample.get("decision") or "").casefold()
        if decision not in {"approved", "rejected"}:
            raise ValueError(f"review sample {index} has invalid decision")
        if require_independent and sample.get("review_source") != "independent":
            raise ValueError(f"review sample {index} is not marked independent")
        validated.append({**sample, "decision": decision})
    return validated


def build_graph_report(
    *, nodes, edges, proposals, expected_relations, review_samples, expected_domains,
    review_sample_source="independent",
):
    approved_edges = [
        edge for edge in edges or ()
        if str(edge.get("serving_status") or "").casefold() == "approved"
    ]
    available = {_relation_identity(edge) for edge in approved_edges}
    expected = {_relation_identity(relation) for relation in expected_relations or ()}
    matched = expected & available
    reviewed = validate_review_samples(
        review_samples, require_independent=review_sample_source == "independent",
    )
    correct_reviews = sum(
        (bool(sample.get("expected_correct")) and sample.get("decision") == "approved")
        or (not bool(sample.get("expected_correct")) and sample.get("decision") == "rejected")
        for sample in reviewed
    )
    node_domains = {str(node.get("department") or "") for node in nodes or ()}
    edge_domains = {str(edge.get("department") or "") for edge in approved_edges}
    domains = list(dict.fromkeys(str(value) for value in expected_domains or ()))
    provenance_fields = ("doc_id", "page", "version", "department", "site", "security_level")
    provenance_complete = sum(
        all(edge.get(field) not in (None, "") for field in provenance_fields)
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
        "reviewed_edge_precision": correct_reviews / len(reviewed) if reviewed else 0.0,
        "provenance_complete_count": provenance_complete,
        "provenance_completeness": provenance_complete / len(approved_edges) if approved_edges else 0.0,
        "domain_coverage": {
            domain: domain in node_domains and domain in edge_domains for domain in domains
        },
    }
