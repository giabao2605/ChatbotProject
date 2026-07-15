import json
from pathlib import Path

import pytest

from mech_chatbot.rag.community_summaries import (
    build_pending_summary,
    detect_communities,
    evaluate_summary_serving,
)
from scripts.community_eval.preflight import build_readiness, validate_manifest_groups


pytestmark = pytest.mark.unit


def _edge(edge_id, source, target, status="approved"):
    return {
        "edge_id": edge_id,
        "source_key": source,
        "target_key": target,
        "relation_type": "RELATED_TO",
        "serving_status": status,
        "doc_id": edge_id,
        "page": 1,
        "version": 2,
    }


def _source(
    doc_id=1, *, department="Technical", site="HQ", version=2,
    node_keys=None, edge_ids=None,
):
    return {
        "doc_id": doc_id,
        "page": 1,
        "version": version,
        "department": department,
        "site": site,
        "security_level": "internal",
        "node_keys": node_keys or [f"document:{doc_id}", f"part:{doc_id}"],
        "edge_ids": edge_ids or [doc_id],
    }


def _current_edge(edge_id=1, doc_id=1, *, node_keys=None, version=2):
    nodes = node_keys or [f"document:{doc_id}", f"part:{doc_id}"]
    return {
        "edge_id": edge_id,
        "source_key": nodes[0],
        "target_key": nodes[1],
        "serving_status": "approved",
        "doc_id": doc_id,
        "page": 1,
        "version": version,
        "department": "Technical" if doc_id == 1 else "Production",
        "site": "HQ",
        "security_level": "internal",
    }


def _version(node_keys=None, **overrides):
    value = {
        "status": "approved",
        "prerequisite_graph_gate_passed": True,
        "structured_coverage": 1.0,
        "reviewed_edge_precision": 0.96,
        "min_global_answer_gain": 0.10,
        "serving_epoch": "community-v1",
        "detection_version": "connected-components-v1",
        "graph_fingerprint": "graph-sha-1",
        "node_keys": node_keys or ["document:1", "part:1"],
    }
    value.update(overrides)
    return value


def test_detection_is_versioned_and_ignores_unapproved_edges():
    result = detect_communities(
        [
            _edge(1, "document:1", "part:a"),
            _edge(2, "part:a", "material:steel"),
            _edge(3, "secret:x", "secret:y", status="pending"),
        ],
        detection_version="connected-components-v1",
        graph_fingerprint="graph-sha-1",
    )

    assert result["schema"] == "graph-community-detection-v1"
    assert result["detection_version"] == "connected-components-v1"
    assert result["graph_fingerprint"] == "graph-sha-1"
    assert result["communities"] == [{
        "community_key": "community:0001",
        "node_keys": ["document:1", "material:steel", "part:a"],
        "edge_ids": [1, 2],
    }]


def test_generated_summary_is_always_pending_and_requires_complete_provenance():
    proposal = build_pending_summary(
        community_key="community:0001",
        summary_text="Approved graph facts summarized by the model.",
        detection_version="connected-components-v1",
        serving_epoch="community-v1",
        graph_fingerprint="graph-sha-1",
        node_keys=["document:1", "part:1"],
        edge_ids=[1],
        sources=[_source()],
        generated_by="proxyllm:gpt-5.4",
    )

    assert proposal["status"] == "pending"
    assert proposal["source_provenance"] == [_source()]
    assert "prompt" not in str(proposal).lower()

    with pytest.raises(ValueError, match="source provenance"):
        build_pending_summary(
            community_key="community:0001",
            summary_text="Missing provenance",
            detection_version="connected-components-v1",
            serving_epoch="community-v1",
            graph_fingerprint="graph-sha-1",
            node_keys=["document:1"],
            edge_ids=[1],
            sources=[{"doc_id": 1, "page": 1}],
        )


def test_serving_fails_closed_for_pending_stale_epoch_rbac_and_source_drift():
    summary = build_pending_summary(
        community_key="community:0001",
        summary_text="Cross-domain summary",
        detection_version="connected-components-v1",
        serving_epoch="community-v1",
        graph_fingerprint="graph-sha-1",
        node_keys=["document:1", "part:1", "document:2", "part:2"],
        edge_ids=[1, 2],
        sources=[_source(1), _source(2, department="Production")],
    )
    access = {
        "roles": ["viewer"],
        "allowed_departments": ["Technical", "Production"],
        "allowed_sites": ["HQ"],
        "max_security_level": "internal",
    }
    current = [
        {**_source(1), "servable": True, "is_current": True,
         "publication_state": "published", "lifecycle_status": "published",
         "review_status": "approved"},
        {**_source(2, department="Production"), "servable": True, "is_current": True,
         "publication_state": "published", "lifecycle_status": "published",
         "review_status": "approved"},
    ]
    current_edges = [_current_edge(1, 1), _current_edge(2, 2)]
    version = _version(node_keys=summary["node_keys"])

    assert evaluate_summary_serving(
        summary, serving_epoch="community-v1", graph_fingerprint="graph-sha-1",
        access_context=access, current_sources=current, current_edges=current_edges,
        community_version=version,
    ).reason == "summary_not_approved"

    approved = {**summary, "status": "approved"}
    assert evaluate_summary_serving(
        approved, serving_epoch="community-v2", graph_fingerprint="graph-sha-1",
        access_context=access, current_sources=current, current_edges=current_edges,
        community_version=version,
    ).reason == "serving_epoch_mismatch"
    assert evaluate_summary_serving(
        approved, serving_epoch="community-v1", graph_fingerprint="graph-sha-2",
        access_context=access, current_sources=current, current_edges=current_edges,
        community_version=version,
    ).reason == "graph_fingerprint_stale"
    assert evaluate_summary_serving(
        approved, serving_epoch="community-v1", graph_fingerprint="graph-sha-1",
        access_context={**access, "allowed_departments": ["Technical"]},
        current_sources=current, current_edges=current_edges,
        community_version=version,
    ).reason == "source_access_denied"
    assert evaluate_summary_serving(
        approved, serving_epoch="community-v1", graph_fingerprint="graph-sha-1",
        access_context=access,
        current_sources=[current[0], {**current[1], "version": 3}],
        current_edges=current_edges,
        community_version=version,
    ).reason == "source_provenance_stale"
    assert evaluate_summary_serving(
        approved, serving_epoch="community-v1", graph_fingerprint="graph-sha-1",
        access_context=access,
        current_sources=[current[0], {**current[1], "department": "HR"}],
        current_edges=current_edges,
        community_version=version,
    ).reason == "source_governance_drift"

    decision = evaluate_summary_serving(
        approved, serving_epoch="community-v1", graph_fingerprint="graph-sha-1",
        access_context=access, current_sources=current, current_edges=current_edges,
        community_version=version,
    )
    assert decision.allowed is True
    assert decision.reason == "approved_current_authorized"


def test_summary_rejects_unmapped_or_stale_edge_provenance():
    with pytest.raises(ValueError, match="map every community node and edge"):
        build_pending_summary(
            community_key="community:0001", summary_text="Unmapped edge",
            detection_version="connected-components-v1",
            serving_epoch="community-v1", graph_fingerprint="graph-sha-1",
            node_keys=["document:1", "part:1"], edge_ids=[1, 2],
            sources=[_source()],
        )

    summary = build_pending_summary(
        community_key="community:0001", summary_text="Mapped edge",
        detection_version="connected-components-v1",
        serving_epoch="community-v1", graph_fingerprint="graph-sha-1",
        node_keys=["document:1", "part:1"], edge_ids=[1], sources=[_source()],
    )
    source = {
        **_source(), "servable": True, "is_current": True,
        "publication_state": "published", "lifecycle_status": "published",
        "review_status": "approved",
    }
    decision = evaluate_summary_serving(
        {**summary, "status": "approved"}, serving_epoch="community-v1",
        graph_fingerprint="graph-sha-1",
        access_context={"roles": ["admin"]}, current_sources=[source],
        current_edges=[{**_current_edge(), "serving_status": "disabled"}],
        community_version=_version(),
    )
    assert decision.reason == "source_edge_stale"


def test_disabled_or_mismatched_community_version_cannot_serve():
    summary = build_pending_summary(
        community_key="community:0001", summary_text="Mapped edge",
        detection_version="connected-components-v1",
        serving_epoch="community-v1", graph_fingerprint="graph-sha-1",
        node_keys=["document:1", "part:1"], edge_ids=[1], sources=[_source()],
    )
    source = {
        **_source(), "servable": True, "is_current": True,
        "publication_state": "published", "lifecycle_status": "published",
        "review_status": "approved",
    }
    common = dict(
        summary={**summary, "status": "approved"},
        serving_epoch="community-v1", graph_fingerprint="graph-sha-1",
        access_context={"roles": ["admin"]}, current_sources=[source],
        current_edges=[_current_edge()],
    )
    assert evaluate_summary_serving(
        **common, community_version=_version(status="disabled")
    ).reason == "community_version_not_servable"
    assert evaluate_summary_serving(
        **common, community_version=_version(node_keys=["document:1", "part:other"])
    ).reason == "community_membership_mismatch"


def test_summary_hash_uses_canonical_stored_text():
    common = dict(
        community_key="community:0001", detection_version="connected-components-v1",
        serving_epoch="community-v1", graph_fingerprint="graph-sha-1",
        node_keys=["document:1", "part:1"], edge_ids=[1], sources=[_source()],
    )
    padded = build_pending_summary(summary_text="  Same summary  ", **common)
    canonical = build_pending_summary(summary_text="Same summary", **common)
    assert padded["summary_text"] == "Same summary"
    assert padded["summary_sha256"] == canonical["summary_sha256"]


def test_community_summary_migration_is_additive_and_pending_by_default():
    migration = Path(
        "database/migrations/V0036__governed_graph_community_summaries.sql"
    ).read_text(encoding="utf-8")

    assert "GraphCommunityVersion" in migration
    assert "GraphCommunityMembership" in migration
    assert "GraphCommunitySummary" in migration
    assert "DEFAULT 'pending'" in migration
    assert "SourceProvenanceJson" in migration
    assert "ServingEpoch" in migration
    assert "PrerequisiteGraphGatePassed" in migration
    assert "ReviewedEdgePrecision >= 0.95000" in migration


def test_repository_never_inserts_generated_summary_as_approved():
    source = Path(
        "src/mech_chatbot/db/repositories/community_summaries.py"
    ).read_text(encoding="utf-8")

    producer = source[
        source.index("def propose_community_summary"):
        source.index("def list_community_summaries")
    ]
    assert "GraphCommunitySummary" in producer
    assert ":sources, 'pending', :generated_by" in producer
    assert "Status, GeneratedBy" in producer
    assert "v.Status='approved'" in producer
    assert "v.PrerequisiteGraphGatePassed=1" in producer
    assert "raw_prompt" not in producer


def test_repository_serving_version_preserves_exact_membership():
    from mech_chatbot.db.repositories.community_summaries import _serving_version

    row = {
        "VersionStatus": "approved", "PrerequisiteGraphGatePassed": 1,
        "StructuredCoverage": 1, "ReviewedEdgePrecision": 0.96,
        "MinGlobalAnswerGain": 0.1, "ServingEpoch": "community-v1",
        "DetectionVersion": "connected-components-v1",
        "GraphFingerprint": "graph-sha-1",
    }
    version = _serving_version(row, {"part:1", "document:1"})
    assert version["node_keys"] == ["document:1", "part:1"]


@pytest.mark.parametrize("role", ["knowledge_approver", "reviewer", "admin"])
def test_summary_review_endpoint_is_role_gated_and_audited(monkeypatch, role):
    from mech_chatbot.api import app_server

    with pytest.raises(app_server.HTTPException) as denied:
        app_server.community_summary_approve(
            5, {}, {"roles": ["viewer"], "username": "alice"}
        )
    assert denied.value.status_code == 403

    audits = []
    monkeypatch.setattr(
        app_server,
        "review_community_summary",
        lambda summary_id, action, reviewer, note=None: {
            "ok": True, "summary_id": summary_id, "status": "approved",
        },
    )
    monkeypatch.setattr(app_server, "write_audit_log", lambda **kwargs: audits.append(kwargs))

    result = app_server.community_summary_approve(
        5, {"note": "provenance checked"},
        {"roles": [role], "username": "bob", "user_id": 9},
    )

    assert result["status"] == "approved"
    assert audits[0]["entity_type"] == "graph_community_summary"
    assert "prompt" not in str(audits[0]).lower()


def test_community_summary_feature_flag_defaults_off(monkeypatch):
    from mech_chatbot.rag.community_summaries import enabled

    monkeypatch.delenv("RAG_GRAPH_COMMUNITY_SUMMARIES_ENABLED", raising=False)
    assert enabled() is False


def test_community_flag_and_epoch_isolate_semantic_cache(monkeypatch):
    from mech_chatbot.rag.semantic_cache import pipeline_namespace

    monkeypatch.delenv("RAG_GRAPH_COMMUNITY_SUMMARIES_ENABLED", raising=False)
    monkeypatch.setenv("RAG_COMMUNITY_SERVING_EPOCH", "community-v1")
    baseline = pipeline_namespace()
    monkeypatch.setenv("RAG_GRAPH_COMMUNITY_SUMMARIES_ENABLED", "true")
    enabled_namespace = pipeline_namespace()
    monkeypatch.setenv("RAG_COMMUNITY_SERVING_EPOCH", "community-v2")
    next_epoch = pipeline_namespace()

    assert baseline != enabled_namespace
    assert enabled_namespace != next_epoch


def test_readiness_requires_graph_gate_review_precision_and_all_eval_groups():
    cases = [
        json.loads(line)
        for line in Path("data/community_eval_v1/manifest.jsonl")
        .read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    groups = validate_manifest_groups(cases)
    readiness = build_readiness(
        graph_gate={"schema": "retrieval-intelligence-gate-v1", "passed": False},
        graph_readiness={
            "schema": "graph-readiness-v1",
            "structured_coverage": 1.0,
            "reviewed_edge_precision": 0.0,
        },
        detection_report={
            "schema": "graph-community-detection-v1",
            "serving_edge_validation_passed": True,
            "provenance_completeness": 1.0,
            "indexing_latency_ms": 10.0,
        },
        manifest_groups=groups,
        detection_version="connected-components-v1",
        serving_epoch="community-v1",
        min_global_answer_gain=0.10,
    )

    assert readiness["capability_passed"] is True
    assert readiness["ready_for_generation"] is False
    assert readiness["ready_for_serving"] is False
    assert set(readiness["blockers"]) >= {
        "graph_gate_not_passed", "reviewed_edge_precision_below_95_percent",
    }
    assert readiness["provenance_completeness"] == 1.0
    assert readiness["serving_epoch_valid"] is True
    assert readiness["indexing_latency_ms"] == 10.0
    assert readiness["max_indexing_latency_ms"] == 60000.0


def test_readiness_can_generate_after_prerequisites_but_cannot_serve_without_review():
    readiness = build_readiness(
        graph_gate={"schema": "retrieval-intelligence-gate-v1", "passed": True},
        graph_readiness={
            "schema": "graph-readiness-v1",
            "structured_coverage": 0.9,
            "reviewed_edge_precision": 0.96,
        },
        detection_report={
            "schema": "graph-community-detection-v1",
            "serving_edge_validation_passed": True,
            "provenance_completeness": 1.0,
            "indexing_latency_ms": 10.0,
        },
        manifest_groups={"global": 2, "local": 2, "relational": 2},
        detection_version="connected-components-v1",
        serving_epoch="community-v1",
        min_global_answer_gain=0.10,
        approved_summary_count=0,
    )

    assert readiness["ready_for_generation"] is True
    assert readiness["ready_for_serving"] is False
    assert readiness["blockers"] == ["no_reviewed_summary_available"]
