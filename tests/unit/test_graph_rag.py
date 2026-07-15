from pathlib import Path

import pytest

from mech_chatbot.rag.graph_retrieval import (
    expand_seed_keys, filter_servable_edges, hydrate_graph_edges,
    select_graph_seeds, should_attempt_graph,
)


pytestmark = pytest.mark.unit


@pytest.mark.parametrize("question", [
    "Tài liệu A supersedes tài liệu nào?",
    "Assembly X contains part nào?",
    "Vật tư P dùng vật liệu gì?",
    "Mối quan hệ giữa DOC-A và DOC-B là gì?",
])
def test_graph_router_accepts_relational_queries(question):
    assert should_attempt_graph(question) is True


@pytest.mark.parametrize("question", [
    "Chu kỳ bảo trì là bao nhiêu?",
    "Xin chào",
    "Tóm tắt tài liệu DOC-A",
])
def test_graph_router_keeps_simple_queries_on_regular_retrieval(question):
    assert should_attempt_graph(question) is False


def test_graph_seeds_keep_explicit_query_code_when_retrieval_has_other_entities():
    seeds = select_graph_seeds(
        "GRAPH-EVAL-ASM-001 chứa bộ phận nào?", ["GRAPH-EVAL-PART-B"]
    )

    assert seeds == ["GRAPH-EVAL-ASM-001", "GRAPH-EVAL-PART-B"]


def edge(**overrides):
    value = {
        "edge_id": 1,
        "origin": "deterministic",
        "serving_status": "approved",
        "publication_state": "published",
        "lifecycle_status": "published",
        "review_status": "approved",
        "is_current": True,
        "servable": True,
        "department": "Technical",
        "site": "HQ",
        "security_level": "internal",
    }
    value.update(overrides)
    return value


def test_graph_evidence_is_fail_closed_for_review_lifecycle_and_rbac():
    access = {
        "roles": ["viewer"],
        "allowed_departments": ["Technical"],
        "allowed_sites": ["HQ"],
        "max_security_level": "internal",
    }
    candidates = [
        edge(edge_id=1),
        edge(edge_id=2, serving_status="pending", origin="llm"),
        edge(edge_id=3, publication_state="draft"),
        edge(edge_id=4, department="Finance"),
        edge(edge_id=5, security_level="confidential"),
    ]

    assert [item["edge_id"] for item in filter_servable_edges(candidates, access)] == [1]


def test_graph_edge_requires_exact_qdrant_page_hydration():
    from types import SimpleNamespace

    candidate = edge(
        edge_id=1, doc_id=7, page=3, version=2, file_goc="approved.pdf",
        source_name="A", target_name="B", relation_type="USES_MATERIAL",
    )

    class Client:
        def scroll(self, **kwargs):
            return ([SimpleNamespace(payload={
                "page_content": "Approved source text",
                "metadata": {"doc_id": 7, "trang_so": 3, "version_no": 2},
            })], None)

    docs = hydrate_graph_edges([candidate], Client(), "shadow")

    assert len(docs) == 1
    assert "Approved source text" in docs[0].page_content
    assert docs[0].metadata["graph_edge_id"] == 1


def test_graph_migration_defines_proposals_separately_from_serving_edges():
    migration = Path("database/migrations/V0033__governed_knowledge_graph.sql").read_text(encoding="utf-8")

    assert "KnowledgeGraphNode" in migration
    assert "KnowledgeGraphEdge" in migration
    assert "GraphExtractionProposal" in migration
    assert "ServingStatus" in migration
    assert "pending" in migration


def test_graph_seed_keys_cover_canonical_part_and_material_forms():
    assert expand_seed_keys(["CRAG-EVAL-PART-A"]) == [
        "crag-eval-part-a",
        "material:crag-eval-part-a",
        "part:crag-eval-part-a",
    ]


def test_deterministic_seed_is_deduplicated_and_includes_version_relations():
    seed_source = Path("scripts/graph/seed_deterministic.py").read_text(encoding="utf-8")

    assert "ROW_NUMBER() OVER" in seed_source
    assert "document_family" in seed_source
    assert "HAS_VERSION" in seed_source
    assert "SUPERSEDES" in seed_source


def test_graph_repository_filters_every_traversed_edge_and_only_returns_two_hops():
    source = Path("src/mech_chatbot/db/repositories/graph.py").read_text(encoding="utf-8")

    assert "EligibleEdges AS" in source
    assert "JOIN EligibleEdges e ON e.SourceNodeID = w.NodeID" in source
    assert "WHERE w.Depth < :max_hops" in source


def test_llm_edge_producer_only_inserts_pending_proposals():
    source = Path("src/mech_chatbot/db/repositories/graph.py").read_text(encoding="utf-8")

    producer = source[source.index("def propose_graph_edge"):source.index("def list_graph_proposals")]
    assert "GraphExtractionProposal" in producer
    assert "'pending'" in producer
    assert "KnowledgeGraphEdge" not in producer
    assert "t.VersionNo=:version" in producer
    assert ":page > 0" in producer


@pytest.mark.parametrize("role", ["knowledge_approver", "reviewer", "admin"])
def test_graph_review_endpoint_allows_governed_review_roles_and_audits_without_prompt(monkeypatch, role):
    from mech_chatbot.api import app_server

    with pytest.raises(app_server.HTTPException) as denied:
        app_server.graph_proposal_approve(7, {}, {"roles": ["viewer"], "username": "alice"})
    assert denied.value.status_code == 403

    audits = []
    monkeypatch.setattr(
        app_server,
        "review_graph_proposal",
        lambda proposal_id, action, reviewer, note=None: {
            "ok": True, "proposal_id": proposal_id, "status": "approved"
        },
    )
    monkeypatch.setattr(app_server, "write_audit_log", lambda **kwargs: audits.append(kwargs))

    result = app_server.graph_proposal_approve(
        7, {"note": "verified"}, {"roles": [role], "username": "bob", "user_id": 9}
    )

    assert result["status"] == "approved"
    assert audits[0]["entity_id"] == 7
    assert "prompt" not in str(audits[0]).lower()


def test_graph_proposal_listing_rejects_viewer_even_when_called_directly(monkeypatch):
    from mech_chatbot.api import app_server

    monkeypatch.setattr(app_server, "list_graph_proposals", lambda **_kwargs: [])

    with pytest.raises(app_server.HTTPException) as denied:
        app_server.graph_proposals(profile={"roles": ["viewer"]})

    assert denied.value.status_code == 403
