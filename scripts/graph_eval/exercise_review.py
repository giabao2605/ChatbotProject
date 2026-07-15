"""Exercise pending, approve, reject and audit behavior on graph-eval-v1."""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

from sqlalchemy import text

from scripts.graph_eval.constants import FIXTURE_BATCH, LIVE_OPT_IN, ROOT

SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))


def exercise_review(output: Path):
    if os.getenv(LIVE_OPT_IN) != "1":
        raise RuntimeError(f"set {LIVE_OPT_IN}=1 before graph reviewer exercise")
    from fastapi import HTTPException
    from mech_chatbot.api import app_server
    from mech_chatbot.db.engine import _ensure_engine, engine
    from mech_chatbot.db.repositories.graph import propose_graph_edge, traverse_knowledge_graph
    _ensure_engine()
    with engine.connect() as connection:
        rows = {row["CanonicalKey"]: dict(row) for row in connection.execute(text("""
            SELECT n.NodeID, n.CanonicalKey, n.SourceDocID, n.SourcePage, n.SourceVersion
            FROM dbo.KnowledgeGraphNode n JOIN dbo.TaiLieu t ON t.DocID=n.SourceDocID
            WHERE t.SourceSystem=:batch
        """), {"batch": FIXTURE_BATCH}).mappings().all()}
        document = next(row for key, row in rows.items() if key.startswith("document:") and row["SourceVersion"] == 2)
        part_a = rows["part:graph-eval-part-a"]
        material_aluminum = rows["material:aluminum"]
    try:
        app_server.graph_proposals(profile={"roles": ["viewer"], "username": "graph-eval-viewer"})
        viewer_blocked = False
    except HTTPException as exc:
        viewer_blocked = exc.status_code == 403
    correct = propose_graph_edge(
        document["NodeID"], part_a["NodeID"], "RELATED_COMPONENT",
        doc_id=document["SourceDocID"], page=1, version=document["SourceVersion"],
        confidence=1.0,
        evidence={"fixture_batch": FIXTURE_BATCH, "expected_correct": True},
        proposed_by="graph-eval-extractor",
    )
    wrong = propose_graph_edge(
        part_a["NodeID"], material_aluminum["NodeID"], "USES_MATERIAL",
        doc_id=document["SourceDocID"], page=1, version=document["SourceVersion"],
        confidence=1.0,
        evidence={"fixture_batch": FIXTURE_BATCH, "expected_correct": False},
        proposed_by="graph-eval-extractor",
    )
    if not correct.get("ok") or not wrong.get("ok"):
        raise RuntimeError("could not create graph review proposals")
    access = {
        "roles": ["viewer"], "allowed_departments": ["Technical"],
        "allowed_sites": ["GRAPH-EVAL-HQ"], "max_security_level": "internal",
    }
    before = traverse_knowledge_graph(["GRAPH-EVAL-ASM-001"], access, max_hops=2, limit=50)
    pending_not_served = all(edge.get("relation_type") != "RELATED_COMPONENT" for edge in before)
    approved = app_server.graph_proposal_approve(
        int(correct["proposal_id"]), {"note": "fixture expected correct"},
        {"roles": ["knowledge_approver"], "username": "graph-eval-approver"},
    )
    rejected = app_server.graph_proposal_reject(
        int(wrong["proposal_id"]), {"note": "fixture expected incorrect"},
        {"roles": ["reviewer"], "username": "graph-eval-reviewer"},
    )
    with engine.connect() as connection:
        audit_count = int(connection.execute(text("""
            SELECT COUNT(1) FROM dbo.AuditLog
            WHERE EntityType='graph_proposal' AND EntityID IN (:approved_id,:rejected_id)
              AND Action IN ('graph_proposal_approve','graph_proposal_reject')
        """), {
            "approved_id": int(correct["proposal_id"]), "rejected_id": int(wrong["proposal_id"]),
        }).scalar_one())
        statuses = [dict(row) for row in connection.execute(text("""
            SELECT ProposalID proposal_id, Status status, ReviewedBy reviewed_by,
                   ReviewNote review_note, ReviewedAt reviewed_at
            FROM dbo.GraphExtractionProposal WHERE ProposalID IN (:approved_id,:rejected_id)
        """), {
            "approved_id": int(correct["proposal_id"]), "rejected_id": int(wrong["proposal_id"]),
        }).mappings().all()]
    passed = all((
        viewer_blocked, pending_not_served, approved.get("status") == "approved",
        rejected.get("status") == "rejected", audit_count == 2,
        all(row.get("reviewed_by") and row.get("reviewed_at") for row in statuses),
    ))
    artifact = {
        "schema": "graph-review-workflow-v1", "passed": passed,
        "viewer_blocked": viewer_blocked, "pending_not_served": pending_not_served,
        "approved": approved, "rejected": rejected, "audit_count": audit_count,
        "history": statuses,
    }
    output = Path(output)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(artifact, ensure_ascii=False, indent=2, default=str) + "\n", encoding="utf-8")
    return artifact


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    artifact = exercise_review(args.output)
    print(json.dumps(artifact, ensure_ascii=False, indent=2, default=str))
    return 0 if artifact["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
