import pytest

from scripts.graph.report import build_graph_report, validate_review_samples
from scripts.graph_eval.preflight import check_graph_fixture
from scripts.graph_eval.cleanup_fixture import build_cleanup_plan
from mech_chatbot.evaluation.graph import evaluate_graph_case, summarize_graph_evaluation


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


def test_graph_cleanup_is_scoped_to_fixture_assets_and_collection(tmp_path):
    workspace = tmp_path / "repo"
    expected = workspace / "data" / "graph_eval_v1"
    plan = build_cleanup_plan(expected, workspace)

    assert plan["source_system"] == "graph-eval-v1"
    assert plan["collection"] == "MechChatbot_Graph_Eval_v1"
    with pytest.raises(ValueError):
        build_cleanup_plan(workspace / "data", workspace)


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
