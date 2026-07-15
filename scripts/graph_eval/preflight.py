"""Fail-closed SQL/Qdrant provenance checks for graph-eval-v1."""

from __future__ import annotations

import hashlib
import json
import argparse
import os
import sys
from pathlib import Path

from scripts.graph_eval.constants import FIXTURE_BATCH, FIXTURE_COLLECTION

ROOT = Path(__file__).resolve().parents[2]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))


def _relation_identity(value):
    return (
        str(value.get("source_key") or "").strip().casefold(),
        str(value.get("relation_type") or "").strip().upper(),
        str(value.get("target_key") or "").strip().casefold(),
    )


def _resolve_key(value, documents_by_key):
    raw = str(value or "")
    if raw.startswith("$DOC:"):
        document = documents_by_key.get(raw[5:])
        return f"document:{int(document['DocID'])}" if document else raw
    if raw.startswith("$PAGEKEY:"):
        document = documents_by_key.get(raw[9:])
        return f"page:{int(document['DocID'])}:1" if document else raw
    if raw.startswith("$FAMILY:"):
        document = documents_by_key.get("assembly_v2" if raw[8:] == "assembly" else raw[8:])
        return f"family:{int(document['FamilyID'])}" if document and document.get("FamilyID") else raw
    return raw


def check_graph_fixture(
    cases, sql_documents, graph_edges, qdrant_points, *, applied_versions,
    pending_serving_edge_count, collection, graph_nodes=None, proposals=None,
    review_samples=None, review_sample_source="independent",
    workflow_fixture_passed=False,
):
    if collection != FIXTURE_COLLECTION:
        raise ValueError(f"collection must equal {FIXTURE_COLLECTION}")
    failures = []
    required_versions = {"V0033", "V0034"}
    missing_versions = sorted(required_versions - {str(value).upper() for value in applied_versions})
    if missing_versions:
        failures.append({"reason": "migration_missing", "versions": missing_versions})
    if int(pending_serving_edge_count or 0):
        failures.append({"reason": "pending_edge_in_serving_table", "count": int(pending_serving_edge_count)})
    documents = {str(row.get("TenFile") or "").casefold(): row for row in sql_documents}
    documents_by_key = {str(row.get("FixtureKey") or ""): row for row in sql_documents}
    edge_identities = {
        _relation_identity(edge)
        for edge in graph_edges or ()
        if str(edge.get("serving_status") or "").casefold() == "approved"
    }
    resolutions = {}
    resolved_relations = []
    for case in cases or ():
        case_id = str(case.get("id") or "")
        filename = str(case.get("expected_document") or "")
        document = documents.get(filename.casefold())
        if not document:
            failures.append({"case_id": case_id, "reason": "sql_document_missing", "document": filename})
            continue
        retrieval_expected = bool(case.get("expected_retrieval", True))
        sql_valid = all((
            document.get("SourceSystem") == FIXTURE_BATCH,
            int(document.get("VersionNo") or 0) == int(case.get("expected_version") or 0),
        ))
        if retrieval_expected:
            sql_valid = sql_valid and all((
                str(document.get("LifecycleStatus") or "").casefold() == "published",
                str(document.get("ReviewStatus") or "").casefold() == "approved",
                str(document.get("PublicationState") or "").casefold() == "published",
                bool(document.get("IsCurrent")), bool(document.get("Servable")),
                str(document.get("EffectiveStatus") or "effective").casefold()
                not in {"draft", "expired", "superseded"},
            ))
        else:
            sql_valid = sql_valid and not all((
                str(document.get("LifecycleStatus") or "").casefold() == "published",
                str(document.get("ReviewStatus") or "").casefold() == "approved",
                str(document.get("PublicationState") or "").casefold() == "published",
                bool(document.get("IsCurrent")), bool(document.get("Servable")),
                str(document.get("EffectiveStatus") or "effective").casefold()
                not in {"draft", "expired", "superseded"},
            ))
        if not sql_valid:
            failures.append({"case_id": case_id, "reason": "sql_provenance_invalid", "document": filename})
        page = int(case.get("expected_page") or 1)
        point_valid = any(
            int(point.get("doc_id") or 0) == int(document["DocID"])
            and int(point.get("trang_so") or point.get("page") or 0) == page
            and int(point.get("version_no") or 0) == int(document.get("VersionNo") or 0)
            and point.get("source_system") == FIXTURE_BATCH
            and bool(point.get("servable")) and bool(point.get("is_current"))
            and str(point.get("publication_state") or "").casefold() == "published"
            and str(point.get("lifecycle_status") or "").casefold() == "published"
            and str(point.get("review_status") or "").casefold() == "approved"
            for point in qdrant_points or ()
        )
        if retrieval_expected and not point_valid:
            failures.append({"case_id": case_id, "reason": "qdrant_page_missing", "document": filename})
        relation = dict(case.get("expected_relation") or {})
        if relation:
            relation["source_key"] = _resolve_key(relation.get("source_key"), documents_by_key)
            relation["target_key"] = _resolve_key(relation.get("target_key"), documents_by_key)
            if _relation_identity(relation) not in edge_identities:
                failures.append({"case_id": case_id, "reason": "expected_relation_missing", "relation": relation})
            resolved_relations.append(relation)
        citation = {
            "document": filename, "doc_id": int(document["DocID"]), "page": page,
            "version": int(document["VersionNo"]),
            "source_id": f"D{int(document['DocID'])}P{page}",
        }
        resolutions[case_id] = {
            "expected_citations": [citation] if (
                case.get("expected_citations") or relation or case.get("expected_claims")
            ) else [],
            "expected_claims": [
                {
                    **claim,
                    "allowed_source_ids": [citation["source_id"]],
                }
                for claim in case.get("expected_claims") or []
            ],
            "expected_relation": relation,
        }
    fingerprint = hashlib.sha256(json.dumps({
        "documents": sql_documents, "edges": graph_edges, "points": qdrant_points,
        "versions": sorted(applied_versions),
    }, sort_keys=True, default=str).encode()).hexdigest()
    from scripts.graph.report import build_graph_report
    graph_report = build_graph_report(
        nodes=graph_nodes or [], edges=graph_edges or [], proposals=proposals or [],
        expected_relations=resolved_relations, review_samples=review_samples or [],
        expected_domains=["Technical", "Production", "Maintenance"],
        review_sample_source=review_sample_source,
    )
    graph_report["pending_serving_edges"] = int(pending_serving_edge_count or 0)
    graph_report["workflow_fixture_passed"] = bool(workflow_fixture_passed)
    return {
        "schema": "graph-fixture-preflight-v1", "passed": not failures,
        "batch": FIXTURE_BATCH, "collection": collection,
        "checked_cases": len(cases or ()), "failures": failures,
        "case_resolutions": resolutions, "fixture_fingerprint": fingerprint,
        "graph_report": graph_report,
    }


def run_live_preflight(cases):
    if os.getenv("RUN_GRAPH_EVAL_FIXTURE") != "1":
        raise RuntimeError("set RUN_GRAPH_EVAL_FIXTURE=1 to access graph-eval-v1")
    from sqlalchemy import text
    from qdrant_client import models
    from mech_chatbot.config.settings import QDRANT_COLLECTION
    from mech_chatbot.db.engine import _ensure_engine, engine
    from mech_chatbot.db.repositories.qdrant import _get_qdrant_client
    if QDRANT_COLLECTION != FIXTURE_COLLECTION:
        raise RuntimeError(f"QDRANT_COLLECTION must equal {FIXTURE_COLLECTION}")
    _ensure_engine()
    with engine.connect() as connection:
        versions = {str(row[0]).upper() for row in connection.execute(text(
            "SELECT Version FROM dbo._SchemaVersions WHERE Version IN ('V0033','V0034')"
        )).all()}
        documents = [dict(row) for row in connection.execute(text("""
            SELECT DocID, FamilyID, TenFile, VersionNo, LifecycleStatus, ReviewStatus,
                   PublicationState, IsCurrent, Servable, EffectiveStatus, SourceSystem,
                   ThuMuc AS OwnerDepartment, Site, SecurityLevel,
                   JSON_VALUE(ClassificationJson, '$.fixture_key') AS FixtureKey
            FROM dbo.TaiLieu WHERE SourceSystem=:batch
        """), {"batch": FIXTURE_BATCH}).mappings().all()]
        edges = [dict(row) for row in connection.execute(text("""
            SELECT e.EdgeID edge_id, e.RelationType relation_type,
                   sn.CanonicalKey source_key, tn.CanonicalKey target_key,
                   e.Origin origin, e.ServingStatus serving_status,
                   e.SourceDocID doc_id, e.SourcePage page, e.SourceVersion version,
                   e.Department department, e.Site site, e.SecurityLevel security_level
            FROM dbo.KnowledgeGraphEdge e
            JOIN dbo.KnowledgeGraphNode sn ON sn.NodeID=e.SourceNodeID
            JOIN dbo.KnowledgeGraphNode tn ON tn.NodeID=e.TargetNodeID
            JOIN dbo.TaiLieu t ON t.DocID=e.SourceDocID
            WHERE t.SourceSystem=:batch
        """), {"batch": FIXTURE_BATCH}).mappings().all()]
        pending = int(connection.execute(text("""
            SELECT COUNT(1) FROM dbo.KnowledgeGraphEdge e
            JOIN dbo.TaiLieu t ON t.DocID=e.SourceDocID
            WHERE t.SourceSystem=:batch AND e.ServingStatus = 'pending'
        """), {"batch": FIXTURE_BATCH}).scalar_one())
        nodes = [dict(row) for row in connection.execute(text("""
            SELECT n.NodeID node_id, n.NodeType node_type, n.CanonicalKey canonical_key,
                   n.SourceDocID doc_id, n.SourcePage page, n.SourceVersion version,
                   n.Department department, n.Site site, n.SecurityLevel security_level
            FROM dbo.KnowledgeGraphNode n JOIN dbo.TaiLieu t ON t.DocID=n.SourceDocID
            WHERE t.SourceSystem=:batch
        """), {"batch": FIXTURE_BATCH}).mappings().all()]
        proposals = [dict(row) for row in connection.execute(text("""
            SELECT p.ProposalID proposal_id, p.Status status, p.RelationType relation_type,
                   p.SourceDocID doc_id, p.SourcePage page, p.SourceVersion version,
                   p.EvidenceJson evidence_json
            FROM dbo.GraphExtractionProposal p JOIN dbo.TaiLieu t ON t.DocID=p.SourceDocID
            WHERE t.SourceSystem=:batch
        """), {"batch": FIXTURE_BATCH}).mappings().all()]
        workflow_samples = []
        for proposal in proposals:
            try:
                evidence = json.loads(proposal.get("evidence_json") or "{}")
            except (TypeError, json.JSONDecodeError):
                evidence = {}
            if evidence.get("fixture_batch") == FIXTURE_BATCH and "expected_correct" in evidence:
                workflow_samples.append({
                    "expected_correct": bool(evidence["expected_correct"]),
                    "decision": proposal.get("status"),
                })
    client = _get_qdrant_client()
    points = []
    for document in documents:
        found, _ = client.scroll(
            collection_name=FIXTURE_COLLECTION,
            scroll_filter=models.Filter(must=[models.FieldCondition(
                key="metadata.doc_id", match=models.MatchValue(value=int(document["DocID"]))
            )]), limit=100, with_payload=True, with_vectors=False,
        )
        points.extend(dict((point.payload or {}).get("metadata") or {}) for point in found)
    review_samples = []
    review_sample_source = "none"
    review_path = os.getenv("RAG_GRAPH_REVIEW_SAMPLE_FILE")
    if review_path:
        review_samples = [
            json.loads(line) for line in Path(review_path).read_text(encoding="utf-8").splitlines()
            if line.strip()
        ]
        review_sample_source = "independent"
    workflow_fixture_passed = (
        len(workflow_samples) >= 2
        and all(
            (sample["expected_correct"] and sample["decision"] == "approved")
            or (not sample["expected_correct"] and sample["decision"] == "rejected")
            for sample in workflow_samples
        )
    )
    return check_graph_fixture(
        cases, documents, edges, points, applied_versions=versions,
        pending_serving_edge_count=pending, collection=QDRANT_COLLECTION,
        graph_nodes=nodes, proposals=proposals, review_samples=review_samples,
        review_sample_source=review_sample_source,
        workflow_fixture_passed=workflow_fixture_passed,
    )


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--manifest", type=Path, action="append", required=True)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    from scripts.eval.run_eval import load_manifest_files
    report = run_live_preflight(load_manifest_files(args.manifest))
    payload = json.dumps(report, ensure_ascii=False, indent=2) + "\n"
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(payload, encoding="utf-8")
    else:
        print(payload, end="")
    return 0 if report["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
