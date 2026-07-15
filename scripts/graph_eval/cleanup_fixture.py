"""Delete only graph-eval-v1 graph rows, documents, collection and assets."""

from __future__ import annotations

import argparse
import os
import shutil
import sys
from pathlib import Path

from scripts.graph_eval.constants import DEFAULT_OUTPUT, FIXTURE_BATCH, FIXTURE_COLLECTION, LIVE_OPT_IN, ROOT

SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))


def build_cleanup_plan(asset_root: Path, workspace_root: Path = ROOT) -> dict:
    asset = Path(asset_root).resolve()
    expected = (Path(workspace_root).resolve() / "data" / "graph_eval_v1").resolve()
    if asset != expected:
        raise ValueError(f"fixture asset root must equal {expected}")
    return {"source_system": FIXTURE_BATCH, "collection": FIXTURE_COLLECTION, "asset_root": str(asset)}


def cleanup_fixture(asset_root: Path = DEFAULT_OUTPUT) -> dict:
    if os.getenv(LIVE_OPT_IN) != "1":
        raise RuntimeError(f"set {LIVE_OPT_IN}=1 before destructive staging cleanup")
    plan = build_cleanup_plan(asset_root)
    from sqlalchemy import text
    from mech_chatbot.config.settings import QDRANT_COLLECTION
    from mech_chatbot.db.engine import _ensure_engine, engine
    from mech_chatbot.db.repositories.document import delete_document_completely
    from mech_chatbot.db.repositories.qdrant import _get_qdrant_client
    if QDRANT_COLLECTION != FIXTURE_COLLECTION:
        raise RuntimeError(f"QDRANT_COLLECTION must equal {FIXTURE_COLLECTION}")
    _ensure_engine()
    with engine.begin() as connection:
        doc_ids = [int(row[0]) for row in connection.execute(text(
            "SELECT DocID FROM dbo.TaiLieu WHERE SourceSystem=:batch"
        ), {"batch": FIXTURE_BATCH}).all()]
        if doc_ids:
            connection.execute(text("""
                DELETE p FROM dbo.GraphExtractionProposal p
                WHERE p.SourceDocID IN (SELECT DocID FROM dbo.TaiLieu WHERE SourceSystem=:batch)
            """), {"batch": FIXTURE_BATCH})
            connection.execute(text("""
                DELETE e FROM dbo.KnowledgeGraphEdge e
                WHERE e.SourceDocID IN (SELECT DocID FROM dbo.TaiLieu WHERE SourceSystem=:batch)
                   OR e.SourceNodeID IN (SELECT NodeID FROM dbo.KnowledgeGraphNode WHERE SourceDocID IN (SELECT DocID FROM dbo.TaiLieu WHERE SourceSystem=:batch))
                   OR e.TargetNodeID IN (SELECT NodeID FROM dbo.KnowledgeGraphNode WHERE SourceDocID IN (SELECT DocID FROM dbo.TaiLieu WHERE SourceSystem=:batch))
            """), {"batch": FIXTURE_BATCH})
            connection.execute(text("""
                DELETE n FROM dbo.KnowledgeGraphNode n
                WHERE n.SourceDocID IN (SELECT DocID FROM dbo.TaiLieu WHERE SourceSystem=:batch)
            """), {"batch": FIXTURE_BATCH})
    deleted = sum(
        bool(delete_document_completely(doc_id, reviewer="graph-eval-cleanup"))
        for doc_id in sorted(doc_ids, reverse=True)
    )
    if deleted != len(doc_ids):
        raise RuntimeError(f"deleted {deleted}/{len(doc_ids)} fixture documents")
    client = _get_qdrant_client()
    existed = client.collection_exists(FIXTURE_COLLECTION)
    if existed:
        client.delete_collection(FIXTURE_COLLECTION)
    if Path(asset_root).exists():
        shutil.rmtree(asset_root)
    return {**plan, "documents": deleted, "collection_deleted": existed, "assets_deleted": not Path(asset_root).exists()}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--asset-root", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--execute", action="store_true")
    args = parser.parse_args()
    result = cleanup_fixture(args.asset_root) if args.execute else build_cleanup_plan(args.asset_root)
    print(result)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
