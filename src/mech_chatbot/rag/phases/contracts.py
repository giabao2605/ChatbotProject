"""Shared typed terminal handoff for private RAG phases."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True, slots=True)
class PhaseTerminal:
    prepared: Any
    reason_code: str
