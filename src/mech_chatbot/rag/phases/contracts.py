"""Shared typed handoffs and trace vocabulary for private RAG phases."""

from __future__ import annotations

from collections.abc import Iterator, Mapping, Sequence
from dataclasses import dataclass
from typing import Any, Literal, Protocol, TypeAlias


PreparedValues: TypeAlias = tuple[
    Iterator[str],
    str,
    Sequence[str],
    Sequence[str],
    Mapping[str, Any],
]

PreparationReason: TypeAlias = Literal[
    "prepared",
    "safety_block",
    "exact_cache_hit",
]
RoutingReason: TypeAlias = Literal[
    "routed",
    "route_terminal",
    "semantic_cache_hit",
    "missing_compare_versions",
    "chitchat",
]
TerminalReason: TypeAlias = Literal[
    "access_denied",
    "no_docs_for_exact_code",
    "disambiguation_required",
    "no_retrieved_docs",
    "empty_context",
    "evidence_gate",
]
PhaseReason: TypeAlias = PreparationReason | RoutingReason | TerminalReason | Literal[
    "retrieved",
    "enriched",
    "reranked",
    "evidence_approved",
    "generated",
]
PrivatePhase: TypeAlias = Literal[
    "preparation",
    "routing",
    "retrieval",
    "evidence",
    "generation",
]


class PreparedExecution(Protocol):
    """Structural view of the executor-owned prepared stream handoff."""

    @property
    def stream(self) -> Iterator[str]: ...

    @property
    def ref_text(self) -> str: ...

    @property
    def ref_images(self) -> tuple[str, ...]: ...

    @property
    def new_part_ids(self) -> tuple[str, ...]: ...

    @property
    def diagnostics(self) -> Mapping[str, Any]: ...


@dataclass(frozen=True, slots=True)
class PhaseTerminal:
    prepared: PreparedExecution
    reason_code: TerminalReason


__all__ = [
    "PhaseReason",
    "PhaseTerminal",
    "PreparationReason",
    "PreparedExecution",
    "PreparedValues",
    "PrivatePhase",
    "RoutingReason",
    "TerminalReason",
]
