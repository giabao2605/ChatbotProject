"""Compatibility import for the application-owned ingestion contract."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from mech_chatbot.application.vector_ingestion import (
    IngestionPipelineDependencies,
    require_pipeline_dependencies,
)


@dataclass(frozen=True, slots=True)
class CompatibilityIngestionResources:
    """Per-call resources for callers outside the explicit composition roots."""

    dependencies: IngestionPipelineDependencies | None
    vision_model: Any


def build_compatibility_ingestion_resources(
    *,
    include_dependencies: bool,
    include_vision: bool,
    settings_factory: Callable[[], Any] | None = None,
    qdrant_runtime_factory: Callable[[Any], IngestionPipelineDependencies]
    | None = None,
    vision_model_factory: Callable[[str | None], Any] | None = None,
) -> CompatibilityIngestionResources:
    """Build missing legacy resources once for this call, without caching them."""

    from mech_chatbot.config.settings import (
        QdrantSettings,
        Settings,
        VisionSettings,
    )

    load_settings = settings_factory or Settings.from_env
    settings = load_settings()

    dependencies = None
    if include_dependencies:
        if qdrant_runtime_factory is None:
            from mech_chatbot.adapters.qdrant_runtime import build_qdrant_runtime

            qdrant_runtime_factory = build_qdrant_runtime
        dependencies = qdrant_runtime_factory(
            QdrantSettings.from_settings(settings)
        )

    vision_model = None
    if include_vision:
        if vision_model_factory is None:
            from mech_chatbot.llm.vision_client import build_vision_model

            vision_model_factory = build_vision_model
        vision_settings = VisionSettings.from_settings(settings)
        vision_model = vision_model_factory(vision_settings.model_name)

    return CompatibilityIngestionResources(dependencies, vision_model)


__all__ = [
    "CompatibilityIngestionResources",
    "IngestionPipelineDependencies",
    "build_compatibility_ingestion_resources",
    "require_pipeline_dependencies",
]
