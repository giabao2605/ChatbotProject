"""Fail-closed domain contracts for governed graph community summaries."""

from __future__ import annotations

import hashlib
import os
from dataclasses import dataclass


_LEVELS = {"public": 0, "internal": 1, "confidential": 2}
_SOURCE_FIELDS = (
    "doc_id", "page", "version", "department", "site", "security_level",
    "node_keys", "edge_ids",
)


@dataclass(frozen=True)
class SummaryServingDecision:
    allowed: bool
    reason: str


def enabled() -> bool:
    return os.getenv(
        "RAG_GRAPH_COMMUNITY_SUMMARIES_ENABLED", "false"
    ).strip().lower() in {"1", "true", "yes", "y", "on"}


def detect_communities(edges, *, detection_version: str, graph_fingerprint: str) -> dict:
    """Return deterministic connected components from approved serving edges."""
    detection_version = str(detection_version or "").strip()
    graph_fingerprint = str(graph_fingerprint or "").strip()
    if not detection_version or not graph_fingerprint:
        raise ValueError("detection version and graph fingerprint are required")

    adjacency: dict[str, set[str]] = {}
    edge_ids_by_nodes: list[tuple[object, str, str]] = []
    for edge in edges or ():
        if str(edge.get("serving_status") or "").lower() != "approved":
            continue
        source = str(edge.get("source_key") or "").strip()
        target = str(edge.get("target_key") or "").strip()
        edge_id = edge.get("edge_id")
        if not source or not target or edge_id is None:
            continue
        adjacency.setdefault(source, set()).add(target)
        adjacency.setdefault(target, set()).add(source)
        edge_ids_by_nodes.append((edge_id, source, target))

    components = []
    unvisited = set(adjacency)
    while unvisited:
        seed = min(unvisited, key=str.casefold)
        stack = [seed]
        nodes = set()
        while stack:
            node = stack.pop()
            if node in nodes:
                continue
            nodes.add(node)
            unvisited.discard(node)
            stack.extend(adjacency.get(node, ()) - nodes)
        edge_ids = sorted(
            edge_id for edge_id, source, target in edge_ids_by_nodes
            if source in nodes and target in nodes
        )
        components.append((sorted(nodes, key=str.casefold), edge_ids))

    components.sort(key=lambda item: tuple(value.casefold() for value in item[0]))
    return {
        "schema": "graph-community-detection-v1",
        "detection_version": detection_version,
        "graph_fingerprint": graph_fingerprint,
        "communities": [
            {
                "community_key": f"community:{index:04d}",
                "node_keys": nodes,
                "edge_ids": edge_ids,
            }
            for index, (nodes, edge_ids) in enumerate(components, 1)
        ],
    }


def _validate_sources(sources) -> list[dict]:
    normalized = []
    for raw in sources or ():
        source = dict(raw or {})
        if any(source.get(field) in (None, "") for field in _SOURCE_FIELDS):
            raise ValueError("complete source provenance is required")
        try:
            source["doc_id"] = int(source["doc_id"])
            source["page"] = int(source["page"])
            source["version"] = int(source["version"])
        except (TypeError, ValueError) as exc:
            raise ValueError("complete source provenance is required") from exc
        if source["page"] <= 0 or source["version"] <= 0:
            raise ValueError("complete source provenance is required")
        source["department"] = str(source["department"]).strip()
        source["site"] = str(source["site"]).strip()
        source["security_level"] = str(source["security_level"]).strip().lower()
        if source["security_level"] not in _LEVELS:
            raise ValueError("complete source provenance is required")
        source["node_keys"] = sorted({
            str(value).strip() for value in source["node_keys"]
            if str(value).strip()
        }, key=str.casefold)
        try:
            source["edge_ids"] = sorted({int(value) for value in source["edge_ids"]})
        except (TypeError, ValueError) as exc:
            raise ValueError("complete source provenance is required") from exc
        if not source["node_keys"] or not source["edge_ids"]:
            raise ValueError("complete source provenance is required")
        normalized.append(source)
    if not normalized:
        raise ValueError("complete source provenance is required")
    return normalized


def build_pending_summary(
    *, community_key, summary_text, detection_version, serving_epoch,
    graph_fingerprint, node_keys, edge_ids, sources, generated_by="community-generator",
) -> dict:
    """Create a non-serving summary proposal without retaining a raw prompt."""
    required = {
        "community_key": community_key,
        "summary_text": summary_text,
        "detection_version": detection_version,
        "serving_epoch": serving_epoch,
        "graph_fingerprint": graph_fingerprint,
    }
    if any(not str(value or "").strip() for value in required.values()):
        raise ValueError("community summary identity is required")
    nodes = sorted({str(value).strip() for value in node_keys or () if str(value).strip()})
    edges = sorted({int(value) for value in edge_ids or ()})
    if not nodes or not edges:
        raise ValueError("node and edge provenance is required")
    source_provenance = _validate_sources(sources)
    mapped_nodes = {value for source in source_provenance for value in source["node_keys"]}
    mapped_edges = {value for source in source_provenance for value in source["edge_ids"]}
    if mapped_nodes != set(nodes) or mapped_edges != set(edges):
        raise ValueError("source provenance must map every community node and edge")
    canonical_text = str(summary_text).strip()
    summary_hash = hashlib.sha256(canonical_text.encode("utf-8")).hexdigest()
    return {
        "schema": "graph-community-summary-v1",
        **{key: str(value).strip() for key, value in required.items()},
        "node_keys": nodes,
        "edge_ids": edges,
        "source_provenance": source_provenance,
        "summary_sha256": summary_hash,
        "generated_by": str(generated_by or "community-generator")[:255],
        "status": "pending",
    }


def evaluate_summary_serving(
    summary: dict,
    *,
    serving_epoch: str,
    graph_fingerprint: str,
    access_context: dict,
    current_sources,
    current_edges,
    community_version,
) -> SummaryServingDecision:
    """Authorize an approved summary only while every source remains servable."""
    if str(summary.get("status") or "").lower() != "approved":
        return SummaryServingDecision(False, "summary_not_approved")
    version = community_version or {}
    if not (
        str(version.get("status") or "").lower() == "approved"
        and version.get("prerequisite_graph_gate_passed") is True
        and float(version.get("structured_coverage") or 0) >= 0.80
        and float(version.get("reviewed_edge_precision") or 0) >= 0.95
        and float(version.get("min_global_answer_gain") or 0) > 0
    ):
        return SummaryServingDecision(False, "community_version_not_servable")
    if (
        str(version.get("serving_epoch") or "") != str(summary.get("serving_epoch") or "")
        or str(version.get("graph_fingerprint") or "")
        != str(summary.get("graph_fingerprint") or "")
        or str(version.get("detection_version") or "")
        != str(summary.get("detection_version") or "")
    ):
        return SummaryServingDecision(False, "community_version_identity_mismatch")
    if {str(value) for value in version.get("node_keys") or ()} != {
        str(value) for value in summary.get("node_keys") or ()
    }:
        return SummaryServingDecision(False, "community_membership_mismatch")
    if str(summary.get("serving_epoch") or "") != str(serving_epoch or ""):
        return SummaryServingDecision(False, "serving_epoch_mismatch")
    if str(summary.get("graph_fingerprint") or "") != str(graph_fingerprint or ""):
        return SummaryServingDecision(False, "graph_fingerprint_stale")
    try:
        provenance = _validate_sources(summary.get("source_provenance"))
    except ValueError:
        return SummaryServingDecision(False, "source_provenance_invalid")
    summary_nodes = {str(value) for value in summary.get("node_keys") or ()}
    summary_edges = {int(value) for value in summary.get("edge_ids") or ()}
    mapped_nodes = {value for source in provenance for value in source["node_keys"]}
    mapped_edges = {value for source in provenance for value in source["edge_ids"]}
    if mapped_nodes != summary_nodes or mapped_edges != summary_edges:
        return SummaryServingDecision(False, "source_provenance_mapping_invalid")

    current = {}
    for item in current_sources or ():
        try:
            key = (int(item["doc_id"]), int(item["page"]), int(item["version"]))
        except (KeyError, TypeError, ValueError):
            continue
        current[key] = item
    context = access_context or {}
    roles = {str(value).strip().lower() for value in context.get("roles") or ()}
    is_admin = "admin" in roles
    departments = {str(value).strip() for value in context.get("allowed_departments") or ()}
    sites = {str(value).strip() for value in context.get("allowed_sites") or ()}
    max_level = _LEVELS.get(
        str(context.get("max_security_level") or "public").lower(), 0
    )
    edges = {}
    for item in current_edges or ():
        try:
            edges[int(item["edge_id"])] = item
        except (KeyError, TypeError, ValueError):
            continue
    for source in provenance:
        for edge_id in source["edge_ids"]:
            edge = edges.get(edge_id)
            if not edge or str(edge.get("serving_status") or "").lower() != "approved":
                return SummaryServingDecision(False, "source_edge_stale")
            edge_nodes = {
                str(edge.get("source_key") or ""), str(edge.get("target_key") or "")
            }
            if not edge_nodes <= set(source["node_keys"]):
                return SummaryServingDecision(False, "source_edge_mapping_invalid")
            if (
                int(edge.get("doc_id") or 0) != source["doc_id"]
                or int(edge.get("page") or 0) != source["page"]
                or int(edge.get("version") or 0) != source["version"]
                or str(edge.get("department") or "").strip() != source["department"]
                or str(edge.get("site") or "").strip() != source["site"]
                or str(edge.get("security_level") or "").strip().lower()
                != source["security_level"]
            ):
                return SummaryServingDecision(False, "source_edge_provenance_drift")
    for source in provenance:
        key = (source["doc_id"], source["page"], source["version"])
        record = current.get(key)
        if not record or not all((
            bool(record.get("servable")),
            bool(record.get("is_current")),
            str(record.get("publication_state") or "").lower() == "published",
            str(record.get("lifecycle_status") or "").lower() == "published",
            str(record.get("review_status") or "").lower() == "approved",
        )):
            return SummaryServingDecision(False, "source_provenance_stale")
        record_department = str(record.get("department") or "").strip()
        record_site = str(record.get("site") or "").strip()
        record_security = str(
            record.get("security_level") or "confidential"
        ).strip().lower()
        if (
            record_department != source["department"]
            or record_site != source["site"]
            or record_security != source["security_level"]
        ):
            return SummaryServingDecision(False, "source_governance_drift")
        if not is_admin and (
            record_department not in departments
            or record_site not in sites
            or _LEVELS.get(record_security, 2) > max_level
        ):
            return SummaryServingDecision(False, "source_access_denied")
    return SummaryServingDecision(True, "approved_current_authorized")


__all__ = [
    "SummaryServingDecision", "build_pending_summary", "detect_communities",
    "enabled", "evaluate_summary_serving",
]
