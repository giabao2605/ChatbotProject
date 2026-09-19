"""Canonicalize part-identifier casing in existing Qdrant payloads."""

from __future__ import annotations

import json
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
SRC_ROOT = ROOT / "src"
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from mech_chatbot.adapters.qdrant_runtime import build_qdrant_admin_runtime  # noqa: E402
from mech_chatbot.config.settings import QdrantSettings, load_settings  # noqa: E402
from mech_chatbot.domain.part_ids import canonical_part_id_updates  # noqa: E402


def backfill_part_id_case(client, collection_name: str) -> dict:
    scanned = 0
    updated = 0
    failed_point_ids = []
    offset = None

    while True:
        points, offset = client.scroll(
            collection_name=collection_name,
            limit=100,
            offset=offset,
            with_payload=True,
            with_vectors=False,
        )
        scanned += len(points)
        for point in points:
            try:
                metadata = (
                    (getattr(point, "payload", {}) or {}).get("metadata") or {}
                )
                updates = canonical_part_id_updates(metadata)
                if not updates:
                    continue
                client.set_payload(
                    collection_name=collection_name,
                    payload=updates,
                    key="metadata",
                    points=[point.id],
                    wait=True,
                )
                updated += 1
            except Exception:
                failed_point_ids.append(str(point.id))
        if offset is None:
            break

    return {
        "scanned": scanned,
        "updated": updated,
        "failed_point_ids": failed_point_ids,
    }


def main() -> int:
    settings = QdrantSettings.from_settings(load_settings())
    runtime = build_qdrant_admin_runtime(settings)
    try:
        result = backfill_part_id_case(runtime.client, runtime.collection_name)
    finally:
        runtime.close()
    print(json.dumps(result, ensure_ascii=False))
    return 0 if not result["failed_point_ids"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
