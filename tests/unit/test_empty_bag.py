from types import SimpleNamespace

import pytest

from scripts.danger_ops import empty_bag


pytestmark = pytest.mark.unit


def _args(**overrides):
    values = {
        "with_chat": False,
        "with_eval": False,
        "with_audit": False,
        "with_files": False,
        "with_raw_files": False,
        "drop_collection": False,
    }
    values.update(overrides)
    return SimpleNamespace(**values)


def test_core_cleanup_orders_document_dependencies_before_documents():
    tables = empty_bag.collect_tables(_args())

    required_children = {
        "AnswerEvidence",
        "ChatEvidenceManifest",
        "GraphCommunitySummary",
        "GraphCommunityMembership",
        "GraphCommunityVersion",
        "GraphExtractionProposal",
        "KnowledgeGraphEdge",
        "KnowledgeGraphNode",
    }
    assert required_children <= set(tables)
    assert tables.index("GraphCommunitySummary") < tables.index("GraphCommunityVersion")
    assert tables.index("GraphCommunityMembership") < tables.index("KnowledgeGraphNode")
    assert tables.index("GraphExtractionProposal") < tables.index("KnowledgeGraphNode")
    assert tables.index("KnowledgeGraphEdge") < tables.index("KnowledgeGraphNode")
    assert tables.index("KnowledgeGraphNode") < tables.index("TaiLieu")
    assert tables.index("AnswerEvidence") < tables.index("TaiLieu")
    assert tables.index("TaiLieu") < tables.index("DocumentFamily")


def test_wipe_stops_before_qdrant_and_files_when_sql_cleanup_fails(monkeypatch):
    calls = []

    def fail_sql(_args):
        calls.append("sql")
        raise RuntimeError("required table could not be deleted")

    monkeypatch.setattr(empty_bag, "wipe_sql", fail_sql)
    monkeypatch.setattr(empty_bag, "wipe_qdrant", lambda _args: calls.append("qdrant"))
    monkeypatch.setattr(empty_bag, "wipe_files", lambda: calls.append("files"))

    with pytest.raises(RuntimeError, match="required table"):
        empty_bag.do_wipe(_args(with_files=True))

    assert calls == ["sql"]
