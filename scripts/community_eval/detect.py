"""Build a read-only, versioned community detection artifact from approved edges."""

from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from mech_chatbot.rag.community_summaries import detect_communities


def main(argv=None):
    parser = argparse.ArgumentParser()
    parser.add_argument("--edges", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--detection-version", default="connected-components-v1")
    parser.add_argument(
        "--input-contract",
        choices=("serving-edges-v1", "graph-review-queue-v1"),
        default="serving-edges-v1",
    )
    args = parser.parse_args(argv)
    started = time.perf_counter()
    payload = args.edges.read_bytes()
    edges = [
        json.loads(line) for line in payload.decode("utf-8").splitlines()
        if line.strip()
    ]
    if args.input_contract == "graph-review-queue-v1":
        for edge in edges:
            if "decision" not in edge:
                raise ValueError("graph review queue edge is missing decision")
            edge["serving_status"] = edge["decision"]
    required = {
        "edge_id", "source_key", "target_key", "serving_status", "doc_id",
        "page", "version", "department", "site", "security_level",
    }
    approved = [
        edge for edge in edges
        if str(edge.get("serving_status") or "").lower() == "approved"
    ]
    valid_approved = [
        edge for edge in approved
        if all(edge.get(field) not in (None, "") for field in required)
    ]
    provenance_completeness = (
        len(valid_approved) / len(approved) if approved else 0.0
    )
    graph_fingerprint = hashlib.sha256(payload).hexdigest()
    artifact = detect_communities(
        edges,
        detection_version=args.detection_version,
        graph_fingerprint=graph_fingerprint,
    )
    artifact.update({
        "generated_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        "git_sha": subprocess.check_output(
            ["git", "rev-parse", "HEAD"], text=True
        ).strip(),
        "source_path": str(args.edges.resolve()),
        "source_sha256": graph_fingerprint,
        "edge_count": len(edges),
        "approved_edge_count": len(approved),
        "input_contract": args.input_contract,
        "provenance_completeness": provenance_completeness,
        "serving_edge_validation_passed": (
            bool(approved) and len(valid_approved) == len(approved)
        ),
        "indexing_latency_ms": round((time.perf_counter() - started) * 1000, 3),
        "persisted": False,
        "summaries_generated": 0,
    })
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(artifact, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    print(json.dumps(artifact, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
