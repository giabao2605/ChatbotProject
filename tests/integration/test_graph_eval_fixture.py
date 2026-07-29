import os
from pathlib import Path

import pytest


pytestmark = pytest.mark.integration


@pytest.fixture(scope="module", autouse=True)
def _require_opt_in():
    if os.getenv("RUN_GRAPH_EVAL_FIXTURE") != "1" or os.getenv("RUN_DB_TESTS") != "1" or os.getenv("RUN_QDRANT_TESTS") != "1":
        pytest.skip("explicit graph fixture, SQL and Qdrant opt-ins are required")
    if os.getenv("QDRANT_COLLECTION") != "MechChatbot_Graph_Eval_v1":
        pytest.fail("QDRANT_COLLECTION must be the isolated graph fixture collection")


def test_graph_fixture_preflight_and_governed_traversal():
    from scripts.eval.run_eval import load_manifest_files
    from scripts.graph_eval.preflight import run_live_preflight
    from mech_chatbot.db.repositories.graph import traverse_knowledge_graph

    cases = load_manifest_files([Path("data/graph_eval_v1/eval_manifest.jsonl")])
    report = run_live_preflight(cases)

    assert report["passed"] is True
    assert report["graph_report"]["structured_coverage"] == 1.0
    assert report["graph_report"]["workflow_fixture_passed"] is True
    assert report["graph_report"]["review_sample_count"] == 0
    assert report["graph_report"]["pending_serving_edges"] == 0

    allowed = traverse_knowledge_graph(["GRAPH-EVAL-ASM-001"], {
        "roles": ["viewer"], "allowed_departments": ["Technical"],
        "allowed_sites": ["GRAPH-EVAL-HQ"], "max_security_level": "internal",
    }, max_hops=2, limit=50)
    site_denied = traverse_knowledge_graph(["GRAPH-EVAL-SITE-001"], {
        "roles": ["viewer"], "allowed_departments": ["Technical"],
        "allowed_sites": ["GRAPH-EVAL-HQ"], "max_security_level": "internal",
    }, max_hops=2, limit=50)
    security_denied = traverse_knowledge_graph(["GRAPH-EVAL-SEC-001"], {
        "roles": ["viewer"], "allowed_departments": ["Technical"],
        "allowed_sites": ["GRAPH-EVAL-HQ"], "max_security_level": "internal",
    }, max_hops=2, limit=50)
    department_denied = traverse_knowledge_graph(["GRAPH-EVAL-DEPT-001"], {
        "roles": ["viewer"], "allowed_departments": ["Technical"],
        "allowed_sites": ["GRAPH-EVAL-HQ"], "max_security_level": "internal",
    }, max_hops=2, limit=50)

    assert allowed
    assert len(allowed) <= 50
    assert all(edge["serving_status"] == "approved" for edge in allowed)
    assert site_denied == []
    assert security_denied == []
    assert department_denied == []
