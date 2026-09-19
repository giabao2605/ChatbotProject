"""Typed diagnostic value objects used by the public RAG executor.

This module is internal.  The public compatibility surface remains
``mech_chatbot.rag.execution`` which re-exports every type defined here.
"""

from __future__ import annotations

from collections.abc import Mapping as MappingABC
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Mapping

if TYPE_CHECKING:
    from mech_chatbot.rag.execution import RequestBudgetLedger


@dataclass(frozen=True, slots=True)
class EvidenceDiagnostics:
    outcome: str | None = None
    state: str | None = None
    stage: str | None = None
    quotes: tuple[str, ...] = ()
    correction_allowed: bool = False


@dataclass(frozen=True, slots=True)
class BudgetDiagnostics:
    planners: int = 0
    subqueries: int = 0
    corrections: int = 0
    repairs: int = 0
    calculations: int = 0
    graph_edges: int = 0
    provider_retries: int = 0
    final_generations: int = 0
    deadline_exceeded: bool = False


@dataclass(frozen=True, slots=True)
class GenerationDiagnostics:
    input_tokens: int = 0
    output_tokens: int = 0
    estimated_cost: float = 0.0
    repair_count: int = 0
    provider_retries: int = 0


def _as_int(item: Any) -> int:
    try:
        return int(item or 0)
    except (TypeError, ValueError):
        return 0


def _as_float(item: Any) -> float:
    try:
        return float(item or 0.0)
    except (TypeError, ValueError):
        return 0.0


def _budget_diagnostics(
    raw: Mapping[str, Any],
    generation_raw: Mapping[str, Any],
    ledger: RequestBudgetLedger | None,
) -> BudgetDiagnostics:
    return BudgetDiagnostics(
        planners=_as_int(raw.get("planner_count") or getattr(ledger, "planners", 0)),
        subqueries=_as_int(
            raw.get("subquery_count") or getattr(ledger, "subqueries", 0)
        ),
        corrections=_as_int(
            raw.get("correction_count") or getattr(ledger, "corrections", 0)
        ),
        repairs=_as_int(
            generation_raw.get("repair_count") or getattr(ledger, "repairs", 0)
        ),
        calculations=_as_int(
            generation_raw.get("calculation_count")
            or getattr(ledger, "calculations", 0)
        ),
        graph_edges=_as_int(
            raw.get("graph_edge_count") or getattr(ledger, "graph_edges", 0)
        ),
        provider_retries=_as_int(
            generation_raw.get("provider_retries")
            or getattr(ledger, "provider_retries", 0)
        ),
        final_generations=_as_int(
            raw.get("final_generation_count")
            or getattr(ledger, "final_generations", 0)
        ),
        deadline_exceeded=bool(
            raw.get("deadline_exceeded")
            or getattr(ledger, "deadline_exceeded", False)
        ),
    )


_MODELED_DIAGNOSTIC_KEYS = frozenset(
    {
        "retrieved_docs",
        "citation_docs",
        "answer_outcome",
        "evidence_state",
        "evidence_stage",
        "evidence_quotes",
        "correction_allowed",
        "generation_metrics",
        "conversation_context",
        "planner_count",
        "subquery_count",
        "correction_count",
        "final_generation_count",
        "deadline_exceeded",
        "graph_edge_count",
    }
)


@dataclass(frozen=True, slots=True, eq=False)
class RagDiagnostics(MappingABC[str, Any]):
    """Typed view over the legacy diagnostics payload."""

    retrieved_documents: tuple[Mapping[str, Any], ...] = ()
    citations: tuple[Mapping[str, Any], ...] = ()
    evidence: EvidenceDiagnostics = EvidenceDiagnostics()
    budget: BudgetDiagnostics = BudgetDiagnostics()
    generation: GenerationDiagnostics = GenerationDiagnostics()
    conversation_delta: Mapping[str, Any] | None = None
    extra: Mapping[str, Any] = field(default_factory=dict)
    _raw: Mapping[str, Any] = field(default_factory=dict, repr=False)

    @classmethod
    def from_mapping(
        cls,
        value: Mapping[str, Any] | RagDiagnostics | None,
        *,
        ledger: RequestBudgetLedger | None = None,
    ) -> RagDiagnostics:
        if isinstance(value, cls) and ledger is None:
            return value
        raw = dict(value or {})
        generation_raw = dict(raw.get("generation_metrics") or {})
        return cls(
            retrieved_documents=tuple(raw.get("retrieved_docs") or ()),
            citations=tuple(raw.get("citation_docs") or ()),
            evidence=EvidenceDiagnostics(
                outcome=raw.get("answer_outcome"),
                state=raw.get("evidence_state"),
                stage=raw.get("evidence_stage"),
                quotes=tuple(raw.get("evidence_quotes") or ()),
                correction_allowed=bool(raw.get("correction_allowed", False)),
            ),
            budget=_budget_diagnostics(raw, generation_raw, ledger),
            generation=GenerationDiagnostics(
                input_tokens=_as_int(generation_raw.get("input_tokens")),
                output_tokens=_as_int(generation_raw.get("output_tokens")),
                estimated_cost=_as_float(generation_raw.get("estimated_cost")),
                repair_count=_as_int(generation_raw.get("repair_count")),
                provider_retries=_as_int(generation_raw.get("provider_retries")),
            ),
            conversation_delta=raw.get("conversation_context"),
            extra={
                key: item
                for key, item in raw.items()
                if key not in _MODELED_DIAGNOSTIC_KEYS
            },
            _raw=raw,
        )

    def __getitem__(self, key: str) -> Any:
        return self._raw[key]

    def __iter__(self):
        return iter(self._raw)

    def __len__(self) -> int:
        return len(self._raw)

    def __eq__(self, other: object) -> bool:
        if isinstance(other, MappingABC):
            return dict(self) == dict(other)
        return NotImplemented


__all__ = [
    "BudgetDiagnostics",
    "EvidenceDiagnostics",
    "GenerationDiagnostics",
    "RagDiagnostics",
]
