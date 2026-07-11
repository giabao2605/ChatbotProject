"""Backfill SQL publication state into Qdrant payloads after V0019."""

from __future__ import annotations

from pathlib import Path
import argparse
import sys


PROJECT_ROOT = Path(__file__).resolve().parents[2]
SRC_ROOT = PROJECT_ROOT / "src"
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from qdrant_client import models  # noqa: E402
from mech_chatbot.config.settings import QDRANT_COLLECTION  # noqa: E402
from mech_chatbot.db.repositories.publication import backfill_qdrant_servable  # noqa: E402
from mech_chatbot.db.repositories.qdrant import _get_qdrant_client  # noqa: E402


def count_missing_servable() -> int:
    client = _get_qdrant_client()
    result = client.count(
        collection_name=QDRANT_COLLECTION,
        count_filter=models.Filter(
            must=[
                models.IsEmptyCondition(
                    is_empty=models.PayloadField(key="metadata.servable")
                )
            ]
        ),
        exact=True,
    )
    return int(result.count or 0)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--force", action="store_true")
    args = parser.parse_args()

    missing = count_missing_servable()
    if missing == 0 and not args.force:
        print("Qdrant serving metadata da day du.")
        return 0

    print(f"Dang backfill Qdrant serving metadata; missing_points={missing}")
    result = backfill_qdrant_servable()
    print(result)
    return 0 if not result["failed_doc_ids"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
