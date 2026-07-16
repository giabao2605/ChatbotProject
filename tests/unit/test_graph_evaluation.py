import pytest

from scripts.graph.report import build_graph_report, validate_review_samples
from scripts.graph_eval.preflight import check_graph_fixture
from scripts.graph_eval.cleanup_fixture import (
    build_cleanup_plan,
    fixture_only_community_versions,
)
from mech_chatbot.evaluation.graph import evaluate_graph_case, summarize_graph_evaluation
from mech_chatbot.evaluation.schema import validate_manifest_ground_truth


pytestmark = pytest.mark.unit


def _document(doc_id=10, **overrides):
    value = {
        "DocID": doc_id, "TenFile": "assembly.md", "VersionNo": 2,
        "SourceSystem": "graph-eval-v1", "LifecycleStatus": "published",
        "ReviewStatus": "approved", "PublicationState": "published",
        "IsCurrent": True, "Servable": True, "OwnerDepartment": "Technical",
        "Site": "GRAPH-EVAL-HQ", "SecurityLevel": "internal",
    }
    value.update(overrides)
    return value


def _edge(edge_id=1, **overrides):
    value = {
        "edge_id": edge_id, "relation_type": "CONTAINS_PART",
        "source_key": "document:10", "target_key": "part:graph-eval-part-a",
        "origin": "deterministic", "serving_status": "approved",
        "doc_id": 10, "page": 1, "version": 2,
        "department": "Technical", "site": "GRAPH-EVAL-HQ",
        "security_level": "internal", "publication_state": "published",
        "lifecycle_status": "published", "review_status": "approved",
        "is_current": True, "servable": True,
    }
    value.update(overrides)
    return value


def test_graph_report_uses_explicit_relation_denominator_and_review_labels():
    report = build_graph_report(
        nodes=[{"node_type": "document", "department": "Technical"}],
        edges=[_edge(), _edge(edge_id=2, relation_type="HAS_PAGE", target_key="page:10:1")],
        proposals=[{"status": "approved"}, {"status": "rejected"}],
        expected_relations=[
            {"source_key": "document:10", "relation_type": "CONTAINS_PART", "target_key": "part:graph-eval-part-a"},
            {"source_key": "part:graph-eval-part-a", "relation_type": "USES_MATERIAL", "target_key": "material:steel"},
        ],
        review_samples=[
            {"edge_id": 1, "reviewer": "alice", "review_source": "independent", "expected_correct": True, "decision": "approved"},
                {"edge_id": 2, "reviewer": "bob", "review_source": "independent", "expected_correct": False, "decision": "approved"},
        ],
        expected_domains=["Technical", "Production", "Maintenance"],
    )

    assert report["structured_coverage"] == 0.5
    assert report["reviewed_edge_precision"] == 0.5
    assert report["coverage_denominator"] == 2
    assert report["domain_coverage"] == {"Technical": True, "Production": False, "Maintenance": False}


def test_independent_review_samples_require_unique_identity_and_reviewer():
    duplicated = [
        {"edge_id": 1, "reviewer": "alice", "review_source": "independent", "expected_correct": True, "decision": "approved"},
        {"edge_id": 1, "reviewer": "bob", "review_source": "independent", "expected_correct": True, "decision": "approved"},
    ]

    with pytest.raises(ValueError, match="duplicate review sample"):
        validate_review_samples(duplicated, require_independent=True)
    with pytest.raises(ValueError, match="requires reviewer"):
        validate_review_samples([{
            "proposal_id": 2, "review_source": "independent",
            "expected_correct": False, "decision": "rejected",
        }], require_independent=True)

    with pytest.raises(ValueError, match="unknown edge_id"):
        validate_review_samples([{
            "edge_id": 999, "reviewer": "alice", "review_source": "independent",
            "expected_correct": True, "decision": "approved",
        }], require_independent=True, allowed_edge_ids={1, 2})

    with pytest.raises(ValueError, match="approved edge_id"):
        validate_review_samples([{
            "proposal_id": 2, "reviewer": "alice", "review_source": "independent",
            "expected_correct": True, "decision": "approved",
        }], require_independent=True, allowed_proposal_ids={2})
    with pytest.raises(ValueError, match="approved serving state"):
        validate_review_samples([{
            "edge_id": 1, "reviewer": "alice", "review_source": "independent",
            "expected_correct": False, "decision": "rejected",
        }], require_independent=True, allowed_edge_ids={1})


def test_graph_preflight_resolves_relations_and_fails_closed_on_pending_edge():
    case = {
        "id": "assembly-part", "expected_document": "assembly.md",
        "expected_page": 1, "expected_version": 2,
        "expected_relation": {
            "source_key": "document:10", "relation_type": "CONTAINS_PART",
            "target_key": "part:graph-eval-part-a",
        },
    }
    point = {
        "doc_id": 10, "trang_so": 1, "version_no": 2,
        "source_system": "graph-eval-v1", "servable": True, "is_current": True,
        "publication_state": "published", "lifecycle_status": "published",
        "review_status": "approved", "owner_department": "Technical",
        "site": "GRAPH-EVAL-HQ", "security_level": "internal",
    }

    passed = check_graph_fixture(
        [case], [_document()], [_edge()], [point],
        applied_versions={"V0033", "V0034"}, pending_serving_edge_count=0,
        collection="MechChatbot_Graph_Eval_v1",
    )
    blocked = check_graph_fixture(
        [case], [_document()], [_edge(serving_status="pending")], [point],
        applied_versions={"V0033", "V0034"}, pending_serving_edge_count=1,
        collection="MechChatbot_Graph_Eval_v1",
    )

    assert passed["passed"] is True
    assert passed["case_resolutions"]["assembly-part"]["expected_citations"][0]["source_id"] == "D10P1"
    assert blocked["passed"] is False
    assert {failure["reason"] for failure in blocked["failures"]} >= {
        "expected_relation_missing", "pending_edge_in_serving_table",
    }


def test_graph_preflight_resolves_and_verifies_every_multi_relation():
    case = {
        "id": "assembly-material",
        "expected_document": "assembly.md",
        "expected_page": 1,
        "expected_version": 2,
        "expected_relations": [
            {
                "source_key": "$DOC:assembly_v2",
                "relation_type": "CONTAINS_PART",
                "target_key": "part:graph-eval-part-a",
            },
            {
                "source_key": "part:graph-eval-part-a",
                "relation_type": "USES_MATERIAL",
                "target_key": "material:steel",
            },
        ],
    }
    point = {
        "doc_id": 10,
        "trang_so": 1,
        "version_no": 2,
        "source_system": "graph-eval-v1",
        "servable": True,
        "is_current": True,
        "publication_state": "published",
        "lifecycle_status": "published",
        "review_status": "approved",
    }
    edges = [
        _edge(),
        _edge(
            edge_id=2,
            relation_type="USES_MATERIAL",
            source_key="part:graph-eval-part-a",
            target_key="material:steel",
        ),
    ]

    report = check_graph_fixture(
        [case],
        [_document(FixtureKey="assembly_v2")],
        edges,
        [point],
        applied_versions={"V0033", "V0034"},
        pending_serving_edge_count=0,
        collection="MechChatbot_Graph_Eval_v1",
    )

    assert report["passed"] is True
    resolved = report["case_resolutions"]["assembly-material"]["expected_relations"]
    assert len(resolved) == 2
    assert resolved[0]["source_key"] == "document:10"


def test_graph_preflight_resolves_canonical_part_and_material_symbols():
    case = {
        "id": "symbolic-bom-relation",
        "expected_document": "assembly.md",
        "expected_page": 1,
        "expected_version": 2,
        "expected_relations": [
            {
                "source_key": "$PART:DEMO-PART-C",
                "relation_type": "USES_MATERIAL",
                "target_key": "$MATERIAL:DEMO-MAT-RUBBER",
            },
        ],
    }
    point = {
        "doc_id": 10,
        "trang_so": 1,
        "version_no": 2,
        "source_system": "graph-eval-v1",
        "servable": True,
        "is_current": True,
        "publication_state": "published",
        "lifecycle_status": "published",
        "review_status": "approved",
    }
    edge = _edge(
        relation_type="USES_MATERIAL",
        source_key="part:demo-part-c",
        target_key="material:demo-mat-rubber",
    )

    report = check_graph_fixture(
        [case], [_document(FixtureKey="assembly_v2")], [edge], [point],
        applied_versions={"V0033", "V0034"}, pending_serving_edge_count=0,
        collection="MechChatbot_Graph_Eval_v1",
    )

    assert report["passed"] is True
    relation = report["case_resolutions"]["symbolic-bom-relation"]["expected_relations"][0]
    assert relation["source_key"] == "part:demo-part-c"
    assert relation["target_key"] == "material:demo-mat-rubber"


def test_graph_preflight_can_validate_an_explicit_non_default_staging_scope():
    case = {
        "id": "controlled-demo-page",
        "expected_document": "maintenance.md",
        "expected_page": 1,
        "expected_version": 1,
        "expected_relation": {
            "source_key": "$DOC:maintenance-v1",
            "relation_type": "HAS_PAGE",
            "target_key": "$PAGEKEY:maintenance-v1",
        },
    }
    document = _document(
        DocID=21,
        TenFile="maintenance.md",
        VersionNo=1,
        FixtureKey="maintenance-v1",
        SourceSystem="controlled-demo-v2",
    )
    point = {
        "doc_id": 21,
        "trang_so": 1,
        "version_no": 1,
        "source_system": "controlled-demo-v2",
        "servable": True,
        "is_current": True,
        "publication_state": "published",
        "lifecycle_status": "published",
        "review_status": "approved",
    }
    edge = _edge(
        source_key="document:21",
        relation_type="HAS_PAGE",
        target_key="page:21:1",
    )

    report = check_graph_fixture(
        [case], [document], [edge], [point],
        applied_versions={"V0033", "V0034"}, pending_serving_edge_count=0,
        collection="MechChatbot_Controlled_Demo_v2",
        expected_batch="controlled-demo-v2",
        expected_collection="MechChatbot_Controlled_Demo_v2",
    )

    assert report["passed"] is True
    assert report["batch"] == "controlled-demo-v2"

    changed_case = {**case, "question": "Changed controlled-demo question"}
    changed_report = check_graph_fixture(
        [changed_case], [document], [edge], [point],
        applied_versions={"V0033", "V0034"}, pending_serving_edge_count=0,
        collection="MechChatbot_Controlled_Demo_v2",
        expected_batch="controlled-demo-v2",
        expected_collection="MechChatbot_Controlled_Demo_v2",
    )
    assert changed_report["fixture_fingerprint"] != report["fixture_fingerprint"]


def test_graph_preflight_reports_unresolved_relation_symbol_explicitly():
    case = {
        "id": "unsupported-symbol",
        "expected_document": "assembly.md",
        "expected_page": 1,
        "expected_version": 2,
        "expected_relation": {
            "source_key": "$VERSION:assembly:2",
            "relation_type": "HAS_VERSION",
            "target_key": "$DOC:assembly_v2",
        },
    }

    report = check_graph_fixture(
        [case], [_document(FixtureKey="assembly_v2")], [], [],
        applied_versions={"V0033", "V0034"}, pending_serving_edge_count=0,
        collection="MechChatbot_Graph_Eval_v1",
    )

    assert report["passed"] is False
    assert any(
        failure["reason"] == "relation_symbol_unresolved"
        and failure["symbol"] == "$VERSION:assembly:2"
        for failure in report["failures"]
    )


def test_graph_cleanup_is_scoped_to_fixture_assets_and_collection(tmp_path):
    workspace = tmp_path / "repo"
    expected = workspace / "data" / "graph_eval_v1"
    plan = build_cleanup_plan(expected, workspace)

    assert plan["source_system"] == "graph-eval-v1"
    assert plan["collection"] == "MechChatbot_Graph_Eval_v1"
    with pytest.raises(ValueError):
        build_cleanup_plan(workspace / "data", workspace)


def test_graph_cleanup_rejects_mixed_scope_community_versions():
    assert fixture_only_community_versions(
        [(10, 101), (10, 102), (11, 101)], {101, 102}
    ) == [10, 11]
    with pytest.raises(RuntimeError, match="mix fixture and non-fixture"):
        fixture_only_community_versions(
            [(10, 101), (10, 999)], {101, 102}
        )


def test_graph_evaluator_uses_explicit_router_and_traversal_budget_fields():
    case = {
        "evaluation_group": "relational",
        "expected_relation": {
            "source_key": "document:10", "relation_type": "CONTAINS_PART",
            "target_key": "part:graph-eval-part-a",
        },
    }
    relation_doc = {
        "graph_edge_id": 1, "graph_source_key": "document:10",
        "graph_relation_type": "CONTAINS_PART",
        "graph_target_key": "part:graph-eval-part-a",
    }

    missing_budget = evaluate_graph_case(case, {
        "retrieved_docs": [relation_doc], "graph_routed": True,
    })
    valid = evaluate_graph_case(case, {
        "retrieved_docs": [relation_doc], "graph_routed": True,
        "graph_max_hops": 2, "graph_edge_count": 1,
    })

    assert missing_budget["budget_ok"] is False
    assert valid["passed"] is True
    assert valid["budget_ok"] is True


def test_graph_evaluator_keeps_relation_evidence_after_document_deduplication():
    case = {
        "evaluation_group": "relational",
        "expected_relation": {
            "source_key": "document:10", "relation_type": "CONTAINS_PART",
            "target_key": "part:graph-eval-part-a",
        },
    }
    relation = {
        "graph_edge_id": 7, "graph_source_key": "document:10",
        "graph_relation_type": "CONTAINS_PART",
        "graph_target_key": "part:graph-eval-part-a",
    }

    result = evaluate_graph_case(case, {
        # The normal retrieval copy of the page wins document de-duplication.
        "retrieved_docs": [{"doc_id": 10, "graph_edge_id": None}],
        # Traversal evidence must remain independently auditable.
        "graph_evidence": [relation],
        "graph_routed": True,
        "graph_max_hops": 2,
        "graph_edge_count": 1,
    })

    assert result["passed"] is True
    assert result["relation_matched"] is True


def test_graph_evaluator_requires_every_expected_relation_in_multi_relation_case():
    case = {
        "evaluation_group": "graphrag",
        "expected_relations": [
            {
                "source_key": "document:10",
                "relation_type": "CONTAINS_PART",
                "target_key": "part:a",
            },
            {
                "source_key": "part:a",
                "relation_type": "USES_MATERIAL",
                "target_key": "material:steel",
            },
        ],
    }
    first_relation = {
        "graph_edge_id": 7,
        "graph_source_key": "document:10",
        "graph_relation_type": "CONTAINS_PART",
        "graph_target_key": "part:a",
    }
    second_relation = {
        "graph_edge_id": 8,
        "graph_source_key": "part:a",
        "graph_relation_type": "USES_MATERIAL",
        "graph_target_key": "material:steel",
    }

    incomplete = evaluate_graph_case(case, {
        "graph_evidence": [first_relation],
        "graph_routed": True,
        "graph_max_hops": 2,
        "graph_edge_count": 1,
    })
    complete = evaluate_graph_case(case, {
        "graph_evidence": [first_relation, second_relation],
        "graph_routed": True,
        "graph_max_hops": 2,
        "graph_edge_count": 2,
    })

    assert incomplete["applicable"] is True
    assert incomplete["passed"] is False
    assert incomplete["matched_relation_count"] == 1
    assert incomplete["expected_relation_count"] == 2
    assert complete["passed"] is True
    assert complete["relation_matched"] is True
    assert complete["matched_relation_count"] == 2


@pytest.mark.parametrize(
    "expected_relations",
    [
        [None],
        [
            {
                "source_key": "document:10",
                "relation_type": "CONTAINS_PART",
                "target_key": "part:a",
            },
            {"source_key": "part:a"},
        ],
    ],
)
def test_manifest_rejects_any_invalid_multi_relation_contract(expected_relations):
    case = {
        "manifest_schema": "rag-eval-manifest-v2",
        "evaluation_group": "graphrag",
        "expected_claims": [
            {
                "id": "part",
                "required_terms": ["part a"],
                "allowed_source_ids": ["D10P1"],
            }
        ],
        "expected_citations": [
            {
                "document": "assembly.md",
                "doc_id": 10,
                "page": 1,
                "version": 2,
                "source_id": "D10P1",
            }
        ],
        "expected_relations": expected_relations,
    }

    with pytest.raises(ValueError, match="expected_relations"):
        validate_manifest_ground_truth(case, expected_outcome="full_answer")


def test_graph_evaluator_fails_closed_when_all_relations_are_invalid():
    result = evaluate_graph_case(
        {"evaluation_group": "graphrag", "expected_relations": [None]},
        {"graph_evidence": []},
    )

    assert result["applicable"] is True
    assert result["passed"] is False
    assert result["invalid_relation_count"] == 1


@pytest.mark.parametrize(
    "relation",
    [
        {},
        {"source_key": "document:10", "relation_type": "CONTAINS_PART"},
    ],
)
def test_graph_evaluator_never_matches_incomplete_relation_to_sparse_evidence(relation):
    result = evaluate_graph_case(
        {"evaluation_group": "graphrag", "expected_relations": [relation]},
        {"graph_evidence": [{"graph_edge_id": 7}]},
    )

    assert result["applicable"] is True
    assert result["passed"] is False
    assert result["relation_matched"] is False
    assert result["expected_relation_count"] == 0
    assert result["invalid_relation_count"] == 1


def test_graph_summary_reports_relational_answer_accuracy_separately():
    rows = [
        {"graph_evaluation": {
            "applicable": True, "passed": True, "budget_ok": True,
            "relational_answer_passed": True,
        }},
        {"graph_evaluation": {
            "applicable": True, "passed": True, "budget_ok": True,
            "relational_answer_passed": False,
        }},
    ]

    summary = summarize_graph_evaluation(rows)

    assert summary["relation_accuracy"] == 1.0
    assert summary["relational_answer_accuracy"] == 0.5
