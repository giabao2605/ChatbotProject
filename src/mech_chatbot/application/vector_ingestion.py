"""Application contract for vector persistence during document ingestion."""

from __future__ import annotations

from dataclasses import dataclass
from collections.abc import Callable
from typing import Any


@dataclass(frozen=True, slots=True)
class IngestionPipelineDependencies:
    """Infrastructure required by the ingestion application workflow."""

    vector_store: Any
    qdrant_client: Any
    collection_name: str

    def __post_init__(self) -> None:
        if self.vector_store is None or self.qdrant_client is None:
            raise ValueError("Ingestion vector dependencies must be configured.")
        if not str(self.collection_name).strip():
            raise ValueError("Ingestion vector collection must be configured.")


@dataclass(frozen=True, slots=True)
class IngestionPersistence:
    """SQL operations required by the ingestion workflow.

    The bundle keeps the PDF pipeline independent from repository globals. A
    composition root binds every callable to one process-owned database
    engine.
    """

    reset_document_metadata: Callable[..., Any]
    get_document_info: Callable[..., Any]
    update_document_classification: Callable[..., Any]
    clear_reingest_snapshot: Callable[..., Any]
    mark_document_ingest_failed: Callable[..., Any]
    restore_document_children: Callable[..., Any]
    save_bom_records: Callable[..., Any]
    save_technical_attributes: Callable[..., Any]
    save_document_attributes: Callable[..., Any]
    save_document_page: Callable[..., Any]
    save_page_metadata: Callable[..., Any]


def require_pipeline_dependencies(
    dependencies: IngestionPipelineDependencies | None,
) -> IngestionPipelineDependencies:
    """Fail before ingestion side effects when composition is incomplete."""

    if dependencies is None:
        raise RuntimeError("Ingestion vector dependencies are not configured.")
    return dependencies


__all__ = [
    "IngestionPersistence",
    "IngestionPipelineDependencies",
    "require_pipeline_dependencies",
]
