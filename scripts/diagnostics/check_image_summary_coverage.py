"""Report SQL/Qdrant image-summary coverage without mutating stored data."""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

from sqlalchemy import text

PROJECT_ROOT = Path(__file__).resolve().parents[2]
SRC = PROJECT_ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from mech_chatbot.adapters.qdrant_runtime import build_qdrant_admin_runtime
from mech_chatbot.config.settings import (
    QdrantSettings,
    SqlSettings,
    load_settings,
)
from mech_chatbot.db.engine import build_database_runtime


def _print_coverage(
    *,
    database_engine: Any,
    qdrant_client: Any,
    collection_name: str,
) -> None:
    print("Checking SQL Database...")
    with database_engine.connect() as connection:
        total_docs = connection.execute(
            text("SELECT COUNT(*) FROM TaiLieu")
        ).scalar()
        failed_pages = connection.execute(
            text(
                "SELECT COUNT(*) FROM IngestionJobs "
                "WHERE Status = 'failed'"
            )
        ).scalar()

    print("Checking Qdrant...")
    all_points = []
    next_offset = None
    while True:
        points, next_offset = qdrant_client.scroll(
            collection_name=collection_name,
            scroll_filter=None,
            limit=1000,
            offset=next_offset,
            with_payload=True,
            with_vectors=False,
        )
        all_points.extend(points)
        if next_offset is None:
            break

    docs_with_image_summary = set()
    all_docs = set()
    image_summary_chunks = 0
    uncertain_fields = 0

    for point in all_points:
        payload = point.payload or {}
        metadata = payload.get("metadata", {})
        document_id = metadata.get("doc_id")
        data_type = metadata.get("loai_du_lieu")

        if document_id:
            all_docs.add(document_id)
            if data_type == "image_summary":
                docs_with_image_summary.add(document_id)
                image_summary_chunks += 1

        content = payload.get("page_content", "")
        if "Khong ro" in content or "uncertain" in content.lower():
            uncertain_fields += 1

    docs_without = len(all_docs) - len(docs_with_image_summary)

    print("\n--- Coverage Report ---")
    print(f"Total docs (SQL): {total_docs}")
    print(f"Total docs (Qdrant): {len(all_docs)}")
    print(f"Docs with image_summary: {len(docs_with_image_summary)}")
    print(f"Docs without image_summary: {docs_without}")
    print(f"Image summary chunk count: {image_summary_chunks}")
    print(f"OCR failed pages (Ingestion jobs): {failed_pages}")
    print(f"Uncertain fields count (heuristic): {uncertain_fields}")


def check_coverage() -> None:
    settings = load_settings(PROJECT_ROOT / ".env")
    database_runtime = build_database_runtime(
        SqlSettings.from_settings(settings)
    )
    qdrant_runtime = None
    try:
        qdrant_runtime = build_qdrant_admin_runtime(
            QdrantSettings.from_settings(settings)
        )
        try:
            _print_coverage(
                database_engine=database_runtime.engine,
                qdrant_client=qdrant_runtime.client,
                collection_name=qdrant_runtime.collection_name,
            )
        finally:
            qdrant_runtime.close()
    finally:
        database_runtime.close()


if __name__ == "__main__":
    check_coverage()
