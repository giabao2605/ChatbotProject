"""Domain-neutral progress contract shared by ingestion modules and adapters."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal


IngestionPhase = Literal[
    "classifying",
    "extracting",
    "embedding",
    "quality_check",
    "completed",
]


@dataclass(frozen=True, slots=True)
class IngestionProgressEvent:
    phase: IngestionPhase
    message: str


__all__ = ["IngestionPhase", "IngestionProgressEvent"]
