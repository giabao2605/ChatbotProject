"""Export approved fixture edges as an unlabelled independent-review queue."""

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


def export_review_queue(output: Path, *, limit=50):
    if os.getenv(LIVE_OPT_IN) != "1":
        raise RuntimeError(f"set {LIVE_OPT_IN}=1 before exporting graph review data")
    from mech_chatbot.db.engine import _ensure_engine, engine
    _ensure_engine()
    with engine.connect() as connection:
        rows = [dict(row) for row in connection.execute(text("""
            SELECT TOP (:limit) e.EdgeID edge_id, e.RelationType relation_type,
                   sn.CanonicalKey source_key, sn.DisplayName source_name,
                   tn.CanonicalKey target_key, tn.DisplayName target_name,
                   e.Origin origin, e.SourceDocID doc_id, e.SourcePage page,
                   e.SourceVersion version, e.Department department, e.Site site,
                   e.SecurityLevel security_level, t.TenFile document
            FROM dbo.KnowledgeGraphEdge e
            JOIN dbo.KnowledgeGraphNode sn ON sn.NodeID=e.SourceNodeID
            JOIN dbo.KnowledgeGraphNode tn ON tn.NodeID=e.TargetNodeID
            JOIN dbo.TaiLieu t ON t.DocID=e.SourceDocID
            WHERE t.SourceSystem=:batch AND e.ServingStatus='approved'
            ORDER BY e.EdgeID
        """), {"limit": max(1, min(int(limit), 500)), "batch": FIXTURE_BATCH}).mappings().all()]
    queue = [{
        **row, "reviewer": "", "review_source": "independent",
        "expected_correct": None, "decision": "approved", "review_note": "",
    } for row in rows]
    output = Path(output)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(
        "".join(json.dumps(row, ensure_ascii=False, default=str) + "\n" for row in queue),
        encoding="utf-8",
    )
    return {"schema": "graph-review-queue-v1", "edges": len(queue), "output": str(output)}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--limit", type=int, default=50)
    args = parser.parse_args()
    report = export_review_queue(args.output, limit=args.limit)
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if report["edges"] >= 20 else 2


if __name__ == "__main__":
    raise SystemExit(main())
