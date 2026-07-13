"""Delete only the controlled CRAG staging fixture."""

from __future__ import annotations

import argparse
import os
import shutil
from pathlib import Path

from scripts.crag_eval.constants import DEFAULT_OUTPUT, FIXTURE_BATCH, FIXTURE_COLLECTION, LIVE_OPT_IN

ROOT = Path(__file__).resolve().parents[2]


def build_cleanup_plan(asset_root: Path, workspace_root: Path = ROOT) -> dict:
    asset = Path(asset_root).resolve()
    expected = (Path(workspace_root).resolve() / "data" / "crag_eval_v1").resolve()
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
    with engine.connect() as conn:
        doc_ids = [int(row[0]) for row in conn.execute(
            text("SELECT DocID FROM dbo.TaiLieu WHERE SourceSystem=:batch ORDER BY DocID DESC"),
            {"batch": FIXTURE_BATCH},
        ).all()]
    deleted = sum(bool(delete_document_completely(doc_id, reviewer="crag-eval-cleanup")) for doc_id in doc_ids)
    if deleted != len(doc_ids):
        raise RuntimeError(f"deleted {deleted}/{len(doc_ids)} fixture documents; collection and assets retained")
    client = _get_qdrant_client()
    collection_existed = client.collection_exists(FIXTURE_COLLECTION)
    if collection_existed:
        client.delete_collection(FIXTURE_COLLECTION)
    if asset_root.exists():
        shutil.rmtree(asset_root)
    return {
        **plan, "documents": deleted, "collection_deleted": collection_existed,
        "assets_deleted": not asset_root.exists(),
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--asset-root", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--execute", action="store_true")
    args = parser.parse_args()
    plan = build_cleanup_plan(args.asset_root)
    if not args.execute:
        print(plan)
        return 0
    print(cleanup_fixture(args.asset_root))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
