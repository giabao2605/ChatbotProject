"""Application contract for vector persistence during document ingestion."""

from __future__ import annotations

from dataclasses import dataclass
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


def require_pipeline_dependencies(
    dependencies: IngestionPipelineDependencies | None,
) -> IngestionPipelineDependencies:
    """Fail before ingestion side effects when composition is incomplete."""

    if dependencies is None:
        raise RuntimeError("Ingestion vector dependencies are not configured.")
    return dependencies


__all__ = ["IngestionPipelineDependencies", "require_pipeline_dependencies"]
