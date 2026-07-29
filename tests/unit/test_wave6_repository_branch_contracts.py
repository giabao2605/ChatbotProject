from __future__ import annotations

from datetime import date, datetime, timedelta

import pytest

from mech_chatbot.db.repositories import (
    community_summaries,
    external_ai,
    graph,
    lifecycle,
    material,
    rollout,
)


pytestmark = pytest.mark.unit


class _Result:
    def __init__(self, *, rows=(), row=None, scalar_value=None, rowcount=0):
        self._rows = list(rows)
        self._row = row
        self._scalar = scalar_value
        self.rowcount = rowcount

    def fetchall(self):
        return list(self._rows)

    def fetchone(self):
        return self._row

    def scalar(self):
        return self._scalar

    def mappings(self):
        return self

    def all(self):
        return list(self._rows)

    def first(self):
        return self._row if self._row is not None else (self._rows[0] if self._rows else None)


class _Connection:
    def __init__(self, outcomes=()):
        self.outcomes = list(outcomes)
        self.calls = []

    def execute(self, statement, params=None):
        self.calls.append((str(statement), dict(params or {})))
        result = self.outcomes.pop(0) if self.outcomes else _Result()
        if isinstance(result, BaseException):
            raise result
        return result

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False


class _Engine:
    def __init__(self, connection):
        self.connection = connection

    def connect(self):
        return self.connection

    def begin(self):
        return self.connection


def _install(monkeypatch, module, *outcomes):
    connection = _Connection(outcomes)
    monkeypatch.setattr(module, "engine", _Engine(connection))
    monkeypatch.setattr(module, "_ensure_engine", lambda: None)
    return connection


def _community_row(**overrides):
    row = {
        "SummaryID": 7,
        "CommunityVersionID": 11,
        "CommunityKey": "community:0001",
        "SummaryText": "Summary",
        "SummarySha256": "sha",
        "NodeKeysJson": '["n1"]',
        "EdgeIDsJson": "[3]",
        "SourceProvenanceJson": '[{"doc_id": 9, "page": 2, "version": 1, "department": "Tech", "site": "HQ", "security_level": "public", "node_keys": ["n1"], "edge_ids": [3]}]',
        "Status": "approved",
        "VersionStatus": "approved",
        "DetectionVersion": "v1",
        "ServingEpoch": "e1",
        "GraphFingerprint": "g1",
        "PrerequisiteGraphGatePassed": 1,
        "StructuredCoverage": 0.9,
        "ReviewedEdgePrecision": 0.99,
        "MinGlobalAnswerGain": 0.2,
    }
    row.update(overrides)
    return row


def _edge_row(**overrides):
    row = {
        "edge_id": 3,
        "source_key": "n1",
        "target_key": "n2",
        "serving_status": "approved",
        "doc_id": 9,
        "page": 2,
        "version": 1,
        "department": "Tech",
        "site": "HQ",
        "security_level": "public",
    }
    row.update(overrides)
    return row


def _source_row(**overrides):
    row = {
        "doc_id": 9,
        "page": 2,
        "version": 1,
        "department": "Tech",
        "site": "HQ",
        "security_level": "public",
        "servable": 1,
        "is_current": 1,
        "publication_state": "published",
        "lifecycle_status": "published",
        "review_status": "approved",
    }
    row.update(overrides)
    return row


def _valid_summary_values():
    return {
        "community_key": "community:0001",
        "summary_text": "Summary",
        "detection_version": "v1",
        "serving_epoch": "e1",
        "graph_fingerprint": "g1",
        "node_keys": ["n1"],
        "edge_ids": [3],
        "sources": [{
            "doc_id": 9, "page": 2, "version": 1,
            "department": "Tech", "site": "HQ", "security_level": "public",
            "node_keys": ["n1"], "edge_ids": [3],
        }],
    }


def test_community_propose_and_list_contracts(monkeypatch):
    conn = _install(monkeypatch, community_summaries, _Result(row={"SummaryID": 4, "Status": "pending"}))
    assert community_summaries.propose_community_summary(11, generated_by="alice", **_valid_summary_values()) == {
        "ok": True, "summary_id": 4, "status": "pending",
    }
    assert conn.calls[0][1]["nodes"] == '["n1"]'

    _install(monkeypatch, community_summaries, _Result(row=None))
    assert community_summaries.propose_community_summary(11, generated_by="alice", **_valid_summary_values()) == {
        "ok": False, "reason": "community_version_mismatch",
    }

    conn = _install(monkeypatch, community_summaries, _Result(rows=[{"SummaryID": 1, "Status": "pending"}]))
    listed = community_summaries.list_community_summaries("PENDING", 0)
    assert listed == [{"SummaryID": 1, "Status": "pending"}]
    assert conn.calls[0][1] == {"limit": 1, "status": "pending"}
    with pytest.raises(ValueError, match="invalid community summary status"):
        community_summaries.list_community_summaries("unknown")


def test_load_servable_community_summaries_skips_bad_json_and_accepts_current_source(monkeypatch):
    monkeypatch.setattr(
        community_summaries,
        "expand_seed_keys",
        lambda keys: [str(item).lower() for item in keys],
    )
    raw = _community_row()
    conn = _install(
        monkeypatch,
        community_summaries,
        _Result(rows=[dict(raw, NodeKeysJson="not-json")]),
        _Result(rows=[("n1",)]),
        _Result(rows=[_edge_row(target_key="n1")]),
        _Result(rows=[_source_row()]),
    )
    assert community_summaries.load_servable_community_summaries(
        node_keys=["N1"], access_context={"roles": ["admin"]},
        serving_epoch="e1", graph_fingerprint="g1",
    ) == []
    assert len(conn.calls) == 1

    conn = _install(
        monkeypatch,
        community_summaries,
        _Result(rows=[raw]),
        _Result(rows=[("n1",)]),
        _Result(rows=[_edge_row(target_key="n1")]),
        _Result(rows=[_source_row()]),
    )
    loaded = community_summaries.load_servable_community_summaries(
        node_keys=["N1"], access_context={"roles": ["admin"]},
        serving_epoch="e1", graph_fingerprint="g1", limit=99,
    )
    assert loaded[0]["summary_id"] == 7
    assert conn.calls[0][1]["limit"] == 20


@pytest.mark.parametrize(
    ("action", "row", "expected"),
    [
        ("approve", None, {"ok": False, "reason": "not_found"}),
        ("approve", _community_row(Status="approved"), {"ok": False, "reason": "already_reviewed", "status": "approved"}),
        ("approve", _community_row(Status="pending", VersionStatus="pending"), {"ok": False, "reason": "community_version_not_approved"}),
        ("approve", _community_row(Status="pending", PrerequisiteGraphGatePassed=0), {"ok": False, "reason": "community_prerequisite_not_met"}),
    ],
)
def test_community_review_rejects_unsafe_states(monkeypatch, action, row, expected):
    _install(monkeypatch, community_summaries, _Result(row=row))
    assert community_summaries.review_community_summary(7, action, "reviewer") == expected
    with pytest.raises(ValueError, match="action must be approve"):
        community_summaries.review_community_summary(7, "publish", "reviewer")


def test_community_review_reject_path_and_approve_provenance(monkeypatch):
    conn = _install(monkeypatch, community_summaries, _Result(row=_community_row(Status="pending")), _Result())
    assert community_summaries.review_community_summary(7, "reject", "") == {
        "ok": True, "summary_id": 7, "status": "rejected",
    }
    assert conn.calls[-1][1]["reviewer"] == "System"

    # Approval with malformed provenance is fail-closed before any write.
    conn = _install(monkeypatch, community_summaries, _Result(row=_community_row(Status="pending", NodeKeysJson="bad")))
    assert community_summaries.review_community_summary(7, "approve", "r") == {
        "ok": False, "reason": "community_provenance_invalid",
    }

    # Approval with a valid snapshot reaches the membership and serving checks.
    conn = _install(
        monkeypatch,
        community_summaries,
        _Result(row=_community_row(Status="pending")),
        _Result(rows=[_edge_row(target_key="n1")]),
        _Result(rows=[_source_row()]),
        _Result(rows=[("n1",)]),
        _Result(),
    )
    result = community_summaries.review_community_summary(7, "approve", "r", "ok")
    assert result == {"ok": True, "summary_id": 7, "status": "approved"}


def _graph_review_row(**overrides):
    row = {
        "ProposalID": 8,
        "SourceNodeID": 1,
        "TargetNodeID": 2,
        "RelationType": "HAS_PAGE",
        "SourceDocID": 9,
        "SourcePage": 2,
        "SourceVersion": 1,
        "Confidence": 0.9,
        "Status": "pending",
        "ThuMuc": "Tech",
        "Site": "HQ",
        "SecurityLevel": "public",
        "SourceQuote": "verified quote",
    }
    row.update(overrides)
    return row


def test_graph_proposal_validation_duplicate_and_provenance(monkeypatch):
    values = {
        "doc_id": 9, "page": 2, "version": 1,
        "evidence": {"source_quote": "quote", "prompt": "secret"},
    }
    _install(monkeypatch, graph)
    assert graph.propose_graph_edge(1, 2, "RELATED_TO", **values) == {
        "ok": False, "reason": "ambiguous_or_redundant_relation",
    }
    assert graph.propose_graph_edge(1, 2, "HAS_PAGE", **dict(values, evidence={})) == {
        "ok": False, "reason": "source_quote_required",
    }

    _install(monkeypatch, graph, _Result(row=(3,)))
    assert graph.propose_graph_edge(1, 2, "HAS_PAGE", **values) == {
        "ok": False, "reason": "duplicate_serving_edge",
    }
    conn = _install(monkeypatch, graph, _Result(row=None), _Result(row=None))
    assert graph.propose_graph_edge(1, 2, "HAS_PAGE", **values) == {
        "ok": False, "reason": "invalid_provenance",
    }
    conn = _install(monkeypatch, graph, _Result(row=None), _Result(row={"ProposalID": 5, "Status": "pending"}))
    result = graph.propose_graph_edge(1, 2, "HAS_PAGE", **values)
    assert result == {"ok": True, "proposal_id": 5, "status": "pending"}
    assert "prompt" not in conn.calls[-1][1]["evidence"]


def test_graph_list_review_and_traverse_public_contracts(monkeypatch):
    with pytest.raises(ValueError, match="invalid graph proposal status"):
        _install(monkeypatch, graph)
        graph.list_graph_proposals("bad")
    _install(monkeypatch, graph, _Result(rows=[{"ProposalID": 1}]))
    assert graph.list_graph_proposals("approved", 0) == [{"ProposalID": 1}]

    for row, expected in [
        (None, {"ok": False, "reason": "not_found"}),
        (_graph_review_row(Status="approved"), {"ok": False, "reason": "already_reviewed", "status": "approved"}),
        (_graph_review_row(SourceQuote=""), {"ok": False, "reason": "invalid_provenance"}),
    ]:
        _install(monkeypatch, graph, _Result(row=row))
        assert graph.review_graph_proposal(8, "approve", "r") == expected
    with pytest.raises(ValueError, match="action must be approve"):
        _install(monkeypatch, graph, _Result(row=_graph_review_row()))
        graph.review_graph_proposal(8, "hold", "r")

    conn = _install(monkeypatch, graph, _Result(row=_graph_review_row()), _Result(), _Result())
    assert graph.review_graph_proposal(8, "approve", "") == {
        "ok": True, "proposal_id": 8, "status": "approved",
    }
    assert conn.calls[-1][1]["reviewer"] == "System"

    assert graph.traverse_knowledge_graph([], {}, 0, 0) == []
    monkeypatch.setattr(graph, "expand_seed_keys", lambda keys: ["n1"])
    conn = _install(monkeypatch, graph, _Result(rows=[{"edge_id": 3, "source_key": "n1"}]))
    rows = graph.traverse_knowledge_graph(
        ["N1"], {"roles": ["admin"], "max_security_level": "confidential"}, max_hops=99, limit=99,
    )
    assert rows == [{"edge_id": 3, "source_key": "n1"}]
    assert conn.calls[0][1]["is_admin"] == 1
    assert conn.calls[0][1]["max_hops"] == 2


def test_material_dictionary_crud_covers_empty_duplicate_insert_and_failures(monkeypatch):
    monkeypatch.setattr(material, "_refresh_material_cache", lambda: None)
    conn = _install(
        monkeypatch,
        material,
        _Result(rows=[(1, "M1", "Material 1", "metal", 1)]),
        _Result(rows=[(10, 1, "alias", 1), (11, 2, "inactive", 0)]),
    )
    assert material.list_materials() == [{
        "material_id": 1, "code": "M1", "display": "Material 1", "category": "metal",
        "is_active": True, "synonyms": [{"synonym_id": 10, "synonym": "alias", "is_active": True}],
    }]
    assert conn.calls
    assert material.upsert_material(" ") is False

    conn = _install(monkeypatch, material, _Result())
    assert material.upsert_material("M1", material_id=1, is_active=False) is True
    assert conn.calls[0][1] == {"c": "M1", "d": "M1", "cat": None, "a": 0, "id": 1}

    conn = _install(monkeypatch, material, _Result(row=(4,)), _Result())
    assert material.upsert_material("M2", display="Two") is True
    assert any("UPDATE dbo.MaterialDictionary" in sql for sql, _ in conn.calls)
    conn = _install(monkeypatch, material, _Result(row=None), _Result())
    assert material.upsert_material("M3", category="x") is True
    assert any("INSERT INTO dbo.MaterialDictionary" in sql for sql, _ in conn.calls)
    _install(monkeypatch, material, RuntimeError("write failed"))
    assert material.upsert_material("M4") is False

    _install(monkeypatch, material, _Result())
    assert material.delete_material(1) is True
    _install(monkeypatch, material, RuntimeError("delete failed"))
    assert material.delete_material(1) is False

    _install(monkeypatch, material)
    assert material.add_material_synonym(None, "x") is False
    assert material.add_material_synonym(1, " ") is False
    conn = _install(monkeypatch, material, _Result(row=(3,)))
    assert material.add_material_synonym(1, "alias") is True
    assert len(conn.calls) == 1
    conn = _install(monkeypatch, material, _Result(row=None), _Result())
    assert material.add_material_synonym(1, "alias") is True
    assert len(conn.calls) == 2
    _install(monkeypatch, material, RuntimeError("synonym failed"))
    assert material.add_material_synonym(1, "alias") is False
    _install(monkeypatch, material, _Result())
    assert material.delete_material_synonym(3) is True
    _install(monkeypatch, material, RuntimeError("synonym delete failed"))
    assert material.delete_material_synonym(3) is False


def _provider_row(**overrides):
    row = {
        "Provider": "voyage",
        "Endpoint": "https://api.example",
        "DefaultModel": "rerank-2.5-lite",
        "SecretReference": "env:VOYAGE_API_KEY",
        "AllowedSurfacesJson": '["rag", "ui"]',
        "RetentionMode": "none",
        "PolicyVersion": "p1",
        "ApprovedBy": "admin",
        "RiskAcceptanceRef": "risk-1",
        "ReviewExpiresAt": datetime.now() + timedelta(days=1),
        "IsActive": 1,
        "UpdatedAt": datetime(2026, 1, 1),
        "UpdatedBy": "admin",
    }
    row.update(overrides)
    return row


def test_external_ai_profile_listing_get_and_validation(monkeypatch):
    monkeypatch.setattr(external_ai, "engine", None)
    monkeypatch.setattr(external_ai, "_ensure_engine", lambda: None)
    assert external_ai.list_external_ai_provider_profiles() == []
    assert external_ai.get_external_ai_provider_profile("") is None
    assert external_ai.get_external_ai_provider_profile("voyage") is None
    with pytest.raises(RuntimeError, match="Database"):
        external_ai.upsert_external_ai_provider_profile(
            "voyage", endpoint="e", default_model="m", secret_reference="env:X",
            allowed_surfaces=["rag"], retention_mode="none", policy_version="p",
            approved_by="a", risk_acceptance_ref="r", review_expires_at="2026-01-01",
        )

    conn = _install(monkeypatch, external_ai, _Result(rows=[_provider_row(AllowedSurfacesJson="bad-json")]))
    profiles = external_ai.list_external_ai_provider_profiles()
    assert profiles[0]["allowed_surfaces"] == ["bad-json"]
    assert profiles[0]["review_state"] == "current"
    conn = _install(monkeypatch, external_ai, _Result(row=_provider_row(ReviewExpiresAt="bad-date")))
    assert external_ai.get_external_ai_provider_profile(" VOYAGE ")["review_state"] == "unknown"
    with pytest.raises(ValueError):
        external_ai.upsert_external_ai_provider_profile(
            "BAD PROVIDER", endpoint="e", default_model="m", secret_reference="env:X",
            allowed_surfaces=["rag"], retention_mode="none", policy_version="p",
            approved_by="a", risk_acceptance_ref="r", review_expires_at="2026-01-01",
        )
    with pytest.raises(ValueError, match="Thieu truong"):
        external_ai.upsert_external_ai_provider_profile(
            "voyage", endpoint="", default_model="m", secret_reference="env:X",
            allowed_surfaces=["rag"], retention_mode="none", policy_version="p",
            approved_by="a", risk_acceptance_ref="r", review_expires_at="2026-01-01",
        )
    with pytest.raises(ValueError, match="it nhat"):
        external_ai.upsert_external_ai_provider_profile(
            "voyage", endpoint="e", default_model="m", secret_reference="env:X",
            allowed_surfaces=[], retention_mode="none", policy_version="p",
            approved_by="a", risk_acceptance_ref="r", review_expires_at="2026-01-01",
        )
    with pytest.raises(ValueError, match="SecretReference"):
        external_ai.upsert_external_ai_provider_profile(
            "voyage", endpoint="e", default_model="m", secret_reference="raw-key",
            allowed_surfaces=["rag"], retention_mode="none", policy_version="p",
            approved_by="a", risk_acceptance_ref="r", review_expires_at="2026-01-01",
        )


def test_external_ai_upsert_never_persists_raw_secret_and_returns_profile(monkeypatch):
    audit_calls = []
    monkeypatch.setattr(external_ai._r_audit, "write_audit_log", lambda *args: audit_calls.append(args))
    conn = _install(monkeypatch, external_ai, _Result(), _Result(row=_provider_row()))
    profile = external_ai.upsert_external_ai_provider_profile(
        " Voyage ", endpoint=" https://api.example ", default_model="model",
        secret_reference="env:VOYAGE_API_KEY", allowed_surfaces=["ui", "rag", "ui"],
        retention_mode="none", policy_version="p1", approved_by="admin",
        risk_acceptance_ref="risk", review_expires_at="2026-01-01", updated_by="operator",
    )
    assert profile["provider"] == "voyage"
    assert profile["allowed_surfaces"] == ["rag", "ui"]
    assert conn.calls[0][1]["secret_reference"] == "env:VOYAGE_API_KEY"
    assert audit_calls and "raw-key" not in repr(audit_calls)


def test_lifecycle_classification_and_overview_are_fail_closed(monkeypatch):
    today = date(2026, 7, 20)
    assert lifecycle.classify_lifecycle("2026-07-19", None, today) == "expired"
    assert lifecycle.classify_lifecycle(today, None, today, effective_status="superseded") == "expired"
    assert lifecycle.classify_lifecycle(today + timedelta(days=3), None, today, soon_days=5) == "expiring_soon"
    assert lifecycle.classify_lifecycle(None, today, today) == "needs_review"
    rows = [
        (1, "expired.pdf", "Tech", 1, "effective", None, date.today() - timedelta(days=1), None, None),
        (2, "soon.pdf", "Tech", 1, "effective", None, date.today() + timedelta(days=1), None, None),
        (3, "review.pdf", "Tech", 1, "effective", None, None, date.today(), None),
        (4, "ok.pdf", "Tech", 1, "effective", None, date.today() + timedelta(days=90), date.today() + timedelta(days=90), None),
    ]
    _install(monkeypatch, lifecycle, _Result(rows=rows))
    overview = lifecycle.get_lifecycle_overview()
    assert overview["counts"] == {"expired": 1, "expiring_soon": 1, "needs_review": 1}
    _install(monkeypatch, lifecycle, RuntimeError("read failed"))
    assert lifecycle.get_lifecycle_overview() == {"expired": [], "expiring_soon": [], "needs_review": [], "counts": {}}


def test_lifecycle_mutations_sync_side_effects_and_handle_failures(monkeypatch):
    qdrant_calls = []
    cache_calls = []
    audit_calls = []
    monkeypatch.setattr(lifecycle._r_qdrant, "update_qdrant_metadata", lambda *args: qdrant_calls.append(args))
    monkeypatch.setattr(lifecycle._r_semantic_cache, "_invalidate_semantic_cache", lambda *args: cache_calls.append(args))
    monkeypatch.setattr(lifecycle._r_audit, "write_audit_log", lambda *args, **kwargs: audit_calls.append((args, kwargs)))

    _install(monkeypatch, lifecycle, _Result(rowcount=0))
    assert lifecycle.set_document_lifecycle(1) is False
    conn = _install(monkeypatch, lifecycle, _Result(rowcount=1))
    assert lifecycle.set_document_lifecycle(1, "2026-01-01", "2026-12-31", reviewer="r") is True
    assert qdrant_calls[-1][0] == 1 and cache_calls[-1][0] == "lifecycle.update"
    assert conn.calls[0][1]["ed"] == "2026-01-01"

    _install(monkeypatch, lifecycle, RuntimeError("update failed"))
    assert lifecycle.set_document_lifecycle(1) is False

    _install(monkeypatch, lifecycle, _Result(rowcount=1), _Result(row=(date(2026, 8, 1),)))
    assert lifecycle.mark_document_reviewed(1, "reviewer", 90) is True
    assert cache_calls[-1][0] == "lifecycle.reviewed"
    _install(monkeypatch, lifecycle, _Result(rowcount=0), _Result(row=None))
    assert lifecycle.mark_document_reviewed(1, "reviewer") is False
    _install(monkeypatch, lifecycle, RuntimeError("review failed"))
    assert lifecycle.mark_document_reviewed(1, "reviewer") is False

    _install(monkeypatch, lifecycle, _Result(rows=[]))
    assert lifecycle.refresh_expired_status() == 0
    conn = _install(monkeypatch, lifecycle, _Result(rows=[(1,), (2,)]), _Result())
    monkeypatch.setattr(
        lifecycle._r_qdrant,
        "update_qdrant_metadata",
        lambda doc_id, payload: (_ for _ in ()).throw(RuntimeError("qdrant"))
        if doc_id == 2 else qdrant_calls.append((doc_id, payload)),
    )
    assert lifecycle.refresh_expired_status() == 2
    assert cache_calls[-1][0] == "lifecycle.expired"
    assert len(conn.calls) == 2
    _install(monkeypatch, lifecycle, RuntimeError("refresh failed"))
    assert lifecycle.refresh_expired_status() == 0


def _rollout_readiness_row(**overrides):
    row = {
        "DeptCode": "TECH",
        "WaveNumber": 1,
        "RolloutStatus": "planned",
        "EvaluationQuestionTarget": 75,
        "KnowledgeOwnerUserID": 1,
        "KnowledgeApproverUserID": 2,
        "TaxonomyVersion": "tax-v1",
        "GovernanceActive": 1,
        "DomainProfileActive": 1,
        "DomainProfileValid": 1,
        "MissingSiteCount": 0,
        "ServableDocumentCount": 10,
        "EvaluationQuestionCount": 75,
        "BatchID": "batch-1",
        "GateQuestionCount": 75,
        "SourceTop5Rate": 0.95,
        "CitationOrRefusalRate": 1.0,
        "EvidenceSupportRate": 0.9,
        "RbacSitePublicationLeaks": 0,
        "GatePassed": 1,
        "EvaluatedAt": datetime(2026, 7, 20),
    }
    row.update(overrides)
    return row


def test_rollout_readiness_listing_and_gate_result_contract(monkeypatch):
    plan_row = {
        "DeptCode": "TECH", "WaveNumber": 1, "RolloutStatus": "planned",
        "EvaluationQuestionTarget": 75, "DarkLaunchStartedAt": None,
        "ActivatedAt": None, "UpdatedAt": datetime(2026, 7, 20), "UpdatedBy": "admin",
    }
    _install(monkeypatch, rollout, _Result(rows=[plan_row]))
    assert rollout.list_department_rollout_plans()[0]["department_code"] == "TECH"
    _install(monkeypatch, rollout, _Result(rows=[_rollout_readiness_row()]))
    ready = rollout.get_department_rollout_readiness(" TECH ")[0]
    assert ready["ready_for_next_wave"] is True
    assert ready["missing_prerequisites"] == []
    _install(monkeypatch, rollout, _Result(rows=[_rollout_readiness_row(DomainProfileValid=0, MissingSiteCount=2)]))
    not_ready = rollout.get_department_rollout_readiness()[0]
    assert "domain_profile_valid" in not_ready["missing_prerequisites"]
    assert "site_backfill" in not_ready["missing_prerequisites"]

    monkeypatch.setattr(rollout, "get_department_rollout_readiness", lambda code: [{"evaluation_question_target": 75}])
    monkeypatch.setattr(rollout._r_audit, "write_audit_log", lambda *args, **kwargs: None)
    _install(monkeypatch, rollout, _Result(row=(12, datetime(2026, 7, 20))))
    gate = rollout.record_department_evaluation_gate(
        "TECH", batch_id="batch-1", question_count=75, source_top5_rate=0.9,
        citation_or_refusal_rate=1, evidence_support_rate=0.85,
    )
    assert gate["passed"] is True and gate["gate_id"] == 12
    _install(monkeypatch, rollout, _Result(row=None))
    failed = rollout.record_department_evaluation_gate(
        "TECH", batch_id="batch-2", question_count=1, source_top5_rate=0,
        citation_or_refusal_rate=0, evidence_support_rate=0, rbac_site_publication_leaks=1,
    )
    assert failed["passed"] is False and failed["gate_id"] is None
    with pytest.raises(ValueError):
        rollout.record_department_evaluation_gate(
            "", batch_id="", question_count=1, source_top5_rate=0,
            citation_or_refusal_rate=0, evidence_support_rate=0,
        )


def test_rollout_upsert_validates_transitions_capacity_and_success(monkeypatch):
    monkeypatch.setattr(rollout._r_audit, "write_audit_log", lambda *args, **kwargs: None)
    with pytest.raises(ValueError):
        rollout.upsert_department_rollout_plan("TECH", wave_number=0)
    with pytest.raises(ValueError):
        rollout.upsert_department_rollout_plan("TECH", wave_number=5)
    with pytest.raises(ValueError):
        rollout.upsert_department_rollout_plan("TECH", wave_number=1, rollout_status="pilot", evaluation_question_target=74)

    # Pending prior wave blocks an active Wave 2 request.
    _install(monkeypatch, rollout, _Result(rows=[_rollout_readiness_row(WaveNumber=2)]), _Result(scalar_value=0), _Result(row={"WaveNumber": 2, "RolloutStatus": "pilot"}), _Result(scalar_value=0), _Result(scalar_value=1))
    with pytest.raises(ValueError, match="wave truoc"):
        rollout.upsert_department_rollout_plan("TECH", wave_number=2, rollout_status="active")

    # A fully ready Wave 1 plan can be persisted and read back.
    plan_row = {
        "DeptCode": "TECH", "WaveNumber": 1, "RolloutStatus": "planned",
        "EvaluationQuestionTarget": 75, "DarkLaunchStartedAt": None,
        "ActivatedAt": None, "UpdatedAt": datetime(2026, 7, 20), "UpdatedBy": "operator",
    }
    conn = _install(
        monkeypatch,
        rollout,
        _Result(rows=[_rollout_readiness_row()]),
        _Result(scalar_value=0),
        _Result(row=None),
        _Result(scalar_value=0),
        _Result(),
        _Result(rows=[plan_row]),
    )
    result = rollout.upsert_department_rollout_plan("TECH", wave_number=1, updated_by="operator")
    assert result["department_code"] == "TECH"
    assert len(conn.calls) == 6
