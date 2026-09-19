"""Inspect the configured Qdrant collection schema without mutating it."""

from __future__ import annotations

import json
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]
SRC = PROJECT_ROOT / "src"
for path in (PROJECT_ROOT, SRC):
    if str(path) not in sys.path:
        sys.path.insert(0, str(path))

from qdrant_client import models
from mech_chatbot.adapters.qdrant_runtime import build_qdrant_admin_runtime
from mech_chatbot.config.settings import QdrantSettings, load_settings
from scripts.create_qdrant_indexes import REQUIRED_INDEXES


REQUIRED_BACKFILL_FIELDS = (
    "metadata.servable",
    "metadata.serving_epoch",
    "metadata.taxonomy_version",
    "metadata.parent_context_enabled",
    "metadata.external_processing_policy",
)


def readiness_failures(payload_schema, missing_payload_counts) -> dict:
    def schema_type(value):
        if isinstance(value, dict):
            value = value.get("data_type")
        else:
            value = getattr(value, "data_type", value)
        return getattr(value, "value", value)

    return {
        "missing_indexes": sorted(set(REQUIRED_INDEXES) - set(payload_schema or {})),
        "invalid_indexes": sorted(
            field
            for field, expected in REQUIRED_INDEXES.items()
            if field in (payload_schema or {})
            and schema_type(payload_schema[field]) != schema_type(expected)
        ),
        "missing_payload_fields": sorted(
            field
            for field, count in missing_payload_counts.items()
            if int(count or 0) > 0
        ),
    }


def _count_missing_payload(client, collection: str, field: str) -> int:
    result = client.count(
        collection_name=collection,
        count_filter=models.Filter(
            must=[
                models.IsEmptyCondition(
                    is_empty=models.PayloadField(key=field),
                )
            ]
        ),
        exact=True,
    )
    return int(result.count or 0)


def main() -> int:
    settings = QdrantSettings.from_settings(load_settings())
    runtime = build_qdrant_admin_runtime(settings)
    try:
        info = runtime.client.get_collection(runtime.collection_name)
        missing_payload_counts = {
            field: _count_missing_payload(
                runtime.client,
                runtime.collection_name,
                field,
            )
            for field in REQUIRED_BACKFILL_FIELDS
        }
        failures = readiness_failures(
            info.payload_schema,
            missing_payload_counts,
        )
        print(json.dumps(failures, sort_keys=True))
        return 0 if not any(failures.values()) else 1
    except Exception as exc:
        print(f"Loi: {type(exc).__name__}")
        return 1
    finally:
        runtime.close()


if __name__ == "__main__":
    raise SystemExit(main())
