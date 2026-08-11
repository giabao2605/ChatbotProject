import json
import inspect

import pytest

from scripts.graph.report import build_graph_report, validate_review_samples
from scripts.graph_eval.preflight import (
    REQUIRED_GRAPH_MIGRATIONS,
    check_graph_fixture,
    run_live_preflight,
)
from scripts.graph_eval.cleanup_fixture import (
    build_cleanup_plan,
    fixture_only_community_versions,
)
from mech_chatbot.evaluation.graph import evaluate_graph_case, summarize_graph_evaluation
from mech_chatbot.evaluation.schema import validate_manifest_ground_truth


pytestmark = pytest.mark.unit


class _ReviewRows:
    def __init__(self, rows):
        self._rows = rows

    def mappings(self):
        return self

    def all(self):
        return self._rows


class _ReviewConnection:
    def __init__(self, rows):
        self._rows = rows

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def execute(self, *_args, **_kwargs):
        selected_source_quote = "e.SourceQuote source_quote" in str(_args[0])
        rows = [
            dict(row) if selected_source_quote else {
                key: value for key, value in row.items() if key != "source_quote"
            }
            for row in self._rows
        ]
        return _ReviewRows(rows)


class _ReviewEngine:
    def __init__(self, rows):
        self._rows = rows

    def connect(self):
        return _ReviewConnection(self._rows)


class _ProposalResult:
    def __init__(self, row=None):
        self._row = row

    def mappings(self):
        return self

    def first(self):
        return self._row


class _ProposalConnection:
    def __init__(self, proposal):
        self.proposal = proposal
        self.statements = []

    def execute(self, statement, *_args, **_kwargs):
        sql = str(statement)
        self.statements.append(sql)
        if "SELECT p.ProposalID" in sql:
            return _ProposalResult(self.proposal)
        return _ProposalResult()


class _ProposalEngine:
    def __init__(self, proposal):
        self.connection = _ProposalConnection(proposal)

    def begin(self):
        connection = self.connection

        class _Transaction:
            def __enter__(self):
                return connection

            def __exit__(self, *_args):
                return False

        return _Transaction()


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
        "is_current": True, "servable": True, "source_quote": "verified source text",
        "source_evidence_matches": True,
    }
    value.update(overrides)
    return value


def _single_owner_governance():
    return {
        "schema": "rag-review-governance-v1",
        "mode": "single_owner",
        "owner": "bao.nguyen",
        "scope": "controlled_demo",
        "source_commit": "a" * 40,
        "risk_accepted": True,
        "accepted_at": "2026-07-20T10:00:00Z",
        "role_signoffs": {
            role: {
                "owner": "bao.nguyen",
                "signed": True,
                "note": f"{role} checklist reviewed",
            }
            for role in ("rag", "security_qa", "operations")
        },
    }


@pytest.mark.parametrize(
    ("source_matches", "endpoints_match"),
    [(False, True), (True, False)],
)
def test_graph_review_rejects_approval_without_verified_applies_to_provenance(
    monkeypatch,
    source_matches,
    endpoints_match,
):
    from mech_chatbot.db.repositories import graph as graph_repository

    proposal = {
        "ProposalID": 7,
        "SourceNodeID": 1,
        "TargetNodeID": 2,
        "RelationType": "APPLIES_TO",
        "SourceDocID": 10,
        "SourcePage": 1,
        "SourceVersion": 2,
        "Confidence": 1.0,
        "Status": "pending",
        "ThuMuc": "Technical",
        "Site": "GRAPH-EVAL-HQ",
        "SecurityLevel": "internal",
        "SourceQuote": "Cụm GRAPH-EVAL-ASM-001 áp dụng cho GRAPH-EVAL-PART-A.",
        "SourceGovernanceMatches": True,
        "SourceEvidenceMatches": source_matches,
        "RelationEndpointsMatch": endpoints_match,
    }
    fake_engine = _ProposalEngine(proposal)
    monkeypatch.setattr(graph_repository, "_ensure_engine", lambda: None)
    monkeypatch.setattr(graph_repository, "engine", fake_engine)

    result = graph_repository.review_graph_proposal(7, "approve", "reviewer")

    assert result == {"ok": False, "reason": "invalid_provenance"}
    assert not any("MERGE dbo.KnowledgeGraphEdge" in sql for sql in fake_engine.connection.statements)


def test_graph_review_can_reject_proposal_with_invalid_provenance(monkeypatch):
    from mech_chatbot.db.repositories import graph as graph_repository

    proposal = {
        "ProposalID": 8,
        "Status": "pending",
        "SourceGovernanceMatches": False,
        "SourceEvidenceMatches": False,
        "RelationEndpointsMatch": False,
    }
    fake_engine = _ProposalEngine(proposal)
    monkeypatch.setattr(graph_repository, "_ensure_engine", lambda: None)
    monkeypatch.setattr(graph_repository, "engine", fake_engine)

    result = graph_repository.review_graph_proposal(8, "reject", "reviewer")

    assert result == {"ok": True, "proposal_id": 8, "status": "rejected"}
    assert any("UPDATE dbo.GraphExtractionProposal" in sql for sql in fake_engine.connection.statements)
    assert not any("MERGE dbo.KnowledgeGraphEdge" in sql for sql in fake_engine.connection.statements)


def test_graph_review_rejects_approval_when_source_governance_is_invalid(
    monkeypatch,
):
    from mech_chatbot.db.repositories import graph as graph_repository

    proposal = {
        "ProposalID": 9,
        "Status": "pending",
        "SourceQuote": "verified quote",
        "SourceGovernanceMatches": False,
        "SourceEvidenceMatches": True,
        "RelationEndpointsMatch": True,
    }
    fake_engine = _ProposalEngine(proposal)
    monkeypatch.setattr(graph_repository, "_ensure_engine", lambda: None)
    monkeypatch.setattr(graph_repository, "engine", fake_engine)

    result = graph_repository.review_graph_proposal(9, "approve", "reviewer")

    assert result == {"ok": False, "reason": "invalid_provenance"}
    assert not any("MERGE dbo.KnowledgeGraphEdge" in sql for sql in fake_engine.connection.statements)


def test_graph_review_query_validates_each_supported_relation_endpoint():
    from mech_chatbot.db.repositories.graph import review_graph_proposal

    source = inspect.getsource(review_graph_proposal)
    endpoint_contract = source[
        source.index("AS SourceEvidenceMatches"):
        source.index("AS RelationEndpointsMatch")
    ]
    compact = "".join(endpoint_contract.split())

    for relation in (
        "HAS_VERSION",
        "SUPERSEDES",
        "HAS_PAGE",
        "CONTAINS_PART",
        "USES_MATERIAL",
        "APPLIES_TO",
    ):
        assert f"p.RelationType='{relation}'" in compact
    assert "p.RelationType <> 'APPLIES_TO'" not in endpoint_contract
    assert "p.RelationType = 'REQUIRES_TOOL'" not in endpoint_contract
    assert "CHARINDEX(LTRIM(RTRIM(endpoint_bom.MaHang))" in compact


def test_graph_review_query_keeps_invalid_pending_proposals_rejectable():
    from mech_chatbot.db.repositories.graph import review_graph_proposal

    source = inspect.getsource(review_graph_proposal)
    query = source[source.index("SELECT p.ProposalID"):source.index('"""), {')]

    assert "SourceGovernanceMatches" in query
    assert "LEFT JOIN dbo.TaiLieu t" in query
    assert "LEFT JOIN dbo.KnowledgeGraphNode sn" in query
    assert "LEFT JOIN dbo.KnowledgeGraphNode tn" in query
    where_clause = query[query.index("WHERE p.ProposalID = :proposal_id"):]
    assert "t.Servable" not in where_clause
    assert "t.IsCurrent" not in where_clause


def test_graph_traversal_revalidates_source_quote_before_serving():
    from mech_chatbot.db.repositories.graph import traverse_knowledge_graph

    source = inspect.getsource(traverse_knowledge_graph)
    eligible = source[source.index("EligibleEdges AS"):source.index("Walk AS")]

    assert "NULLIF(LTRIM(RTRIM(e.SourceQuote)), N'') IS NOT NULL" in eligible
    assert "dbo.DocumentPages eligible_page" in eligible
    assert "CHARINDEX(" in eligible
    assert "e.SourceQuote" in eligible
    assert "e.RelationType = 'APPLIES_TO'" in eligible
    assert "eligible_target.CanonicalKey" in eligible
    assert "eligible_applies_bom.MaHang" in eligible
    assert "'REQUIRES_TOOL'" not in eligible
    compact = "".join(eligible.split())
    for relation in (
        "HAS_VERSION",
        "SUPERSEDES",
        "HAS_PAGE",
        "CONTAINS_PART",
        "USES_MATERIAL",
        "APPLIES_TO",
    ):
        assert f"e.RelationType='{relation}'" in compact
    assert "e.RelationTypeIN(" not in compact
    assert "eligible_page.PageNo=e.SourcePage" in compact
    assert "eligible_version_bom.RawRowJson" in eligible
    assert "eligible_contains_bom.RawRowJson" in eligible
    assert "eligible_material_bom.RawRowJson" in eligible
    assert "e.SourceQuote=LEFT(" in compact
    assert "'family:'+CAST(" in compact
    assert "'page:'+CAST(" in compact


def test_applies_to_quote_contract_requires_source_target_and_explanatory_text():
    from mech_chatbot.db.repositories.graph import (
        review_graph_proposal,
        traverse_knowledge_graph,
    )

    review = "".join(inspect.getsource(review_graph_proposal).split())
    traversal = "".join(inspect.getsource(traverse_knowledge_graph).split())
    preflight = "".join(inspect.getsource(run_live_preflight).split())

    assert "COALESCE(NULLIF(LTRIM(RTRIM(t.BaseCode)),''),NULLIF(LTRIM(RTRIM(sn.DisplayName)),''))" in review
    assert "LEN(LTRIM(RTRIM(JSON_VALUE(p.EvidenceJson,'$.source_quote'))))>=" in review
    assert "+8" in review
    assert "COALESCE(NULLIF(LTRIM(RTRIM(t.BaseCode)),''),NULLIF(LTRIM(RTRIM(sn.DisplayName)),''))" in preflight
    assert "LEN(LTRIM(RTRIM(e.SourceQuote)))>=" in preflight
    assert "+8" in preflight
    assert "COALESCE(NULLIF(LTRIM(RTRIM(governed.BaseCode)),''),NULLIF(LTRIM(RTRIM(eligible_source.DisplayName)),''))" in traversal
    assert "LEN(LTRIM(RTRIM(e.SourceQuote)))>=" in traversal
    assert "+8" in traversal


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
    assert report["reviewer_count"] == 2
    assert report["review_mode"] == "multi_reviewer"
    assert report["coverage_denominator"] == 2
    assert report["domain_coverage"] == {"Technical": True, "Production": False, "Maintenance": False}


def test_graph_report_counts_source_quote_as_required_provenance():
    report = build_graph_report(
        nodes=[],
        edges=[
            _edge(source_quote="verified source text"),
            _edge(edge_id=2, source_quote=""),
        ],
        proposals=[],
        expected_relations=[],
        review_samples=[],
        expected_domains=[],
    )

    assert report["provenance_complete_count"] == 1
    assert report["provenance_completeness"] == 0.5


def test_graph_report_rejects_source_evidence_that_no_longer_matches():
    report = build_graph_report(
        nodes=[],
        edges=[_edge(source_evidence_matches=False)],
        proposals=[],
        expected_relations=[],
        review_samples=[],
        expected_domains=[],
    )

    assert report["provenance_complete_count"] == 0
    assert report["provenance_completeness"] == 0.0


def test_live_graph_source_match_binds_endpoints_and_governance():
    source = inspect.getsource(run_live_preflight)
    contains_branch = source[
        source.index("(e.RelationType = 'CONTAINS_PART'"):
        source.index("(e.RelationType = 'USES_MATERIAL'")
    ]

    assert "sn.CanonicalKey" in contains_branch
    assert "'document:' + CAST(" in contains_branch
    assert "e.SourceDocID AS NVARCHAR(30)" in contains_branch
    assert "e.Department = t.ThuMuc" in source
    assert "e.Site = t.Site" in source
    assert "ISNULL(t.SecurityLevel, 'confidential')" in source


def test_live_graph_source_match_validates_applies_to_endpoint_and_quote():
    source = inspect.getsource(run_live_preflight)
    applies_to = source[source.index("(e.RelationType = 'APPLIES_TO'"):]
    compact = "".join(applies_to.split())

    assert "sn.NodeType = 'document'" in applies_to
    assert "tn.NodeType = 'part'" in applies_to
    assert "'document:' + CAST(e.SourceDocID AS NVARCHAR(30))" in applies_to
    assert "'part:' + LOWER(LTRIM(RTRIM(applies_bom.MaHang)))" in applies_to
    assert "CHARINDEX(LTRIM(RTRIM(applies_bom.MaHang)),e.SourceQuote)" in compact
    assert "CHARINDEX(e.SourceQuote" in applies_to
    assert "source_page.PageNo = e.SourcePage" in applies_to


def test_graph_review_fixture_uses_a_quote_from_the_current_source_page():
    from scripts.graph_eval.generate_fixture import DOCUMENTS

    quote = "Cụm GRAPH-EVAL-ASM-001 áp dụng cho GRAPH-EVAL-PART-A."
    assembly = next(item for item in DOCUMENTS if item["key"] == "assembly_v2")
    path = __import__("pathlib").Path
    exercise = path(
        "scripts/graph_eval/exercise_review.py"
    ).read_text(encoding="utf-8")
    checked_in_page = path(
        "data/graph_eval_v1/corpus/graph_eval_assembly_v2.md"
    ).read_text(encoding="utf-8")
    checked_in_manifest = [
        json.loads(line)
        for line in path("data/graph_eval_v1/corpus_manifest.jsonl")
        .read_text(encoding="utf-8")
        .splitlines()
        if line.strip()
    ]
    checked_in_assembly = next(
        item for item in checked_in_manifest if item["key"] == "assembly_v2"
    )

    assert quote in assembly["body"]
    assert f'"source_quote": "{quote}"' in exercise
    assert quote in checked_in_page
    assert quote in checked_in_assembly["body"]


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
    with pytest.raises(ValueError, match="requires reviewer"):
        validate_review_samples([{
            "edge_id": 2, "reviewer": 7, "review_source": "independent",
            "expected_correct": True, "decision": "approved",
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


def test_independent_review_samples_require_two_distinct_reviewers():
    samples = [
        {
            "edge_id": edge_id,
            "reviewer": "Alice Smith" if edge_id == 1 else " ALICE  SMITH ",
            "review_source": "independent",
            "expected_correct": True,
            "decision": "approved",
        }
        for edge_id in (1, 2)
    ]

    with pytest.raises(ValueError, match="distinct reviewers"):
        validate_review_samples(
            samples,
            require_independent=True,
            allowed_edge_ids={1, 2},
        )


def test_graph_review_exercise_counts_only_audits_from_the_current_run():
    source = __import__("pathlib").Path(
        "scripts/graph_eval/exercise_review.py"
    ).read_text(encoding="utf-8")

    assert "SELECT SYSUTCDATETIME()" in source
    assert "CreatedAt >= :started_at" in source
    assert "EntityID=:approved_id AND Action='graph_proposal_approve'" in source
    assert "EntityID=:rejected_id AND Action='graph_proposal_reject'" in source


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
        applied_versions=REQUIRED_GRAPH_MIGRATIONS, pending_serving_edge_count=0,
        collection="MechChatbot_Graph_Eval_v1",
    )
    blocked = check_graph_fixture(
        [case], [_document()], [_edge(serving_status="pending")], [point],
        applied_versions=REQUIRED_GRAPH_MIGRATIONS, pending_serving_edge_count=1,
        collection="MechChatbot_Graph_Eval_v1",
    )

    assert passed["passed"] is True
    assert passed["case_resolutions"]["assembly-part"]["expected_citations"][0]["source_id"] == "D10P1"
    assert blocked["passed"] is False
    assert {failure["reason"] for failure in blocked["failures"]} >= {
        "expected_relation_missing", "pending_edge_in_serving_table",
    }


def test_graph_preflight_accepts_commit_bound_single_owner_governance():
    edges = [_edge(edge_id=index) for index in range(1, 21)]
    reviews = [
        {
            "edge_id": index,
            "reviewer": "bao.nguyen",
            "review_source": "owner_review",
            "expected_correct": True,
            "decision": "approved",
        }
        for index in range(1, 21)
    ]
    governance = _single_owner_governance()

    report = check_graph_fixture(
        [], [], edges, [],
        applied_versions=REQUIRED_GRAPH_MIGRATIONS,
        pending_serving_edge_count=0,
        collection="MechChatbot_Graph_Eval_v1",
        review_samples=reviews,
        review_sample_source="owner_review",
        review_governance=governance,
        review_governance_source_commit="a" * 40,
        review_governance_scope="controlled_demo",
        review_governance_reference={
            "path": "governance.json",
            "sha256": "b" * 64,
            "schema": "rag-review-governance-v1",
        },
        review_sample_reference={
            "path": "reviews.jsonl",
            "sha256": "c" * 64,
            "format": "jsonl",
        },
    )

    graph = report["graph_report"]
    assert report["passed"] is True
    assert graph["review_mode"] == "single_owner"
    assert graph["review_sample_source"] == "owner_review"
    assert graph["review_governance_valid"] is True
    assert graph["reviewer_count"] == 1
    assert graph["approved_edge_ids"] == list(range(1, 21))
    assert graph["review_governance"] == {
        "path": "governance.json",
        "sha256": "b" * 64,
        "schema": "rag-review-governance-v1",
    }
    assert graph["review_samples"] == {
        "path": "reviews.jsonl",
        "sha256": "c" * 64,
        "format": "jsonl",
    }

    with pytest.raises(ValueError, match="review governance"):
        check_graph_fixture(
            [], [], edges, [],
            applied_versions=REQUIRED_GRAPH_MIGRATIONS,
            pending_serving_edge_count=0,
            collection="MechChatbot_Graph_Eval_v1",
            review_samples=reviews,
            review_sample_source="owner_review",
            review_governance=governance,
            review_governance_source_commit="f" * 40,
            review_governance_scope="controlled_demo",
        )


def test_graph_preflight_requires_source_evidence_migrations():
    report = check_graph_fixture(
        [], [], [], [],
        applied_versions={"V0033", "V0034"},
        pending_serving_edge_count=0,
        collection="MechChatbot_Graph_Eval_v1",
    )

    assert report["passed"] is False
    assert report["failures"] == [{
        "reason": "migration_missing",
        "versions": ["V0037", "V0038"],
    }]


def test_graph_preflight_rejects_approved_edge_without_source_quote():
    report = check_graph_fixture(
        [], [], [_edge(source_quote="   ")], [],
        applied_versions=REQUIRED_GRAPH_MIGRATIONS,
        pending_serving_edge_count=0,
        collection="MechChatbot_Graph_Eval_v1",
    )

    assert report["passed"] is False
    assert report["failures"] == [{
        "reason": "approved_edge_provenance_incomplete",
        "complete_count": 0,
        "approved_edge_count": 1,
    }]


def test_graph_live_preflight_stops_before_source_quote_query_when_migration_is_missing(
    monkeypatch,
):
    from mech_chatbot.config.repository_runtime import bind_repository_runtime
    from mech_chatbot.db import engine as engine_module

    class Connection:
        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        def execute(self, statement, *_args, **_kwargs):
            assert "_SchemaVersions" in str(statement)
            return type("Rows", (), {"all": lambda self: [("V0033",), ("V0034",)]})()

    fake_engine = type("Engine", (), {"connect": lambda self: Connection()})()
    monkeypatch.setenv("RUN_GRAPH_EVAL_FIXTURE", "1")
    monkeypatch.setattr(engine_module, "_ensure_engine", lambda: None)
    monkeypatch.setattr(engine_module, "engine", fake_engine)

    with bind_repository_runtime(
        db_engine=fake_engine,
        qdrant_client=object(),
        qdrant_collection="MechChatbot_Graph_Eval_v1",
    ):
        report = run_live_preflight([{"id": "blocked-by-migration"}])

    assert report["passed"] is False
    assert report["checked_cases"] == 1
    assert report["failures"] == [{
        "reason": "migration_missing",
        "versions": ["V0037", "V0038"],
    }]


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
        applied_versions=REQUIRED_GRAPH_MIGRATIONS,
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
        applied_versions=REQUIRED_GRAPH_MIGRATIONS, pending_serving_edge_count=0,
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
        applied_versions=REQUIRED_GRAPH_MIGRATIONS, pending_serving_edge_count=0,
        collection="MechChatbot_Controlled_Demo_v2",
        expected_batch="controlled-demo-v2",
        expected_collection="MechChatbot_Controlled_Demo_v2",
    )

    assert report["passed"] is True
    assert report["batch"] == "controlled-demo-v2"

    changed_case = {**case, "question": "Changed controlled-demo question"}
    changed_report = check_graph_fixture(
        [changed_case], [document], [edge], [point],
        applied_versions=REQUIRED_GRAPH_MIGRATIONS, pending_serving_edge_count=0,
        collection="MechChatbot_Controlled_Demo_v2",
        expected_batch="controlled-demo-v2",
        expected_collection="MechChatbot_Controlled_Demo_v2",
    )
    assert changed_report["fixture_fingerprint"] != report["fixture_fingerprint"]

    changed_id_case = {
        **changed_case,
        "id": "controlled-demo-page-changed",
    }
    changed_id_report = check_graph_fixture(
        [changed_id_case],
        [document],
        [edge],
        [point],
        applied_versions=REQUIRED_GRAPH_MIGRATIONS,
        pending_serving_edge_count=0,
        collection="MechChatbot_Controlled_Demo_v2",
        expected_batch="controlled-demo-v2",
        expected_collection="MechChatbot_Controlled_Demo_v2",
    )
    combined_report = check_graph_fixture(
        [case, changed_id_case],
        [document],
        [edge],
        [point],
        applied_versions=REQUIRED_GRAPH_MIGRATIONS,
        pending_serving_edge_count=0,
        collection="MechChatbot_Controlled_Demo_v2",
        expected_batch="controlled-demo-v2",
        expected_collection="MechChatbot_Controlled_Demo_v2",
    )
    assert combined_report["case_fixture_fingerprints"] == {
        case["id"]: report["fixture_fingerprint"],
        "controlled-demo-page-changed": changed_id_report[
            "fixture_fingerprint"
        ],
    }


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
        applied_versions=REQUIRED_GRAPH_MIGRATIONS, pending_serving_edge_count=0,
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
        [{
            "source_key": "document:10",
            "relation_type": "RELATED_COMPONENT",
            "target_key": "part:a",
        }],
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


def test_independent_review_queue_contains_source_evidence(tmp_path, monkeypatch):
    from mech_chatbot.db import engine as engine_module
    from scripts.graph_eval.export_review_queue import export_review_queue

    row = {
        "edge_id": 7,
        "relation_type": "CONTAINS_PART",
        "source_key": "document:31",
        "source_name": "Assembly",
        "target_key": "part:p-100",
        "target_name": "P-100",
        "origin": "deterministic",
        "doc_id": 31,
        "page": 1,
        "version": 2,
        "department": "Technical",
        "site": "GRAPH-EVAL-HQ",
        "security_level": "internal",
        "document": "assembly.md",
        "source_quote": '{"part":"P-100","quantity":"2"}',
    }
    monkeypatch.setenv("RUN_GRAPH_EVAL_FIXTURE", "1")
    monkeypatch.setattr(engine_module, "_ensure_engine", lambda: None)
    monkeypatch.setattr(engine_module, "engine", _ReviewEngine([row]))
    output = tmp_path / "review.jsonl"

    report = export_review_queue(output, limit=20)
    exported = json.loads(output.read_text(encoding="utf-8").strip())

    assert report["edges"] == 1
    assert exported["source_quote"] == row["source_quote"]
