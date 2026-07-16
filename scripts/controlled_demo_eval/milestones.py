"""Single source of truth for controlled-demo milestone evaluation scopes."""

from __future__ import annotations

from dataclasses import dataclass


FEATURE_FLAGS = (
    "RAG_CRAG_ENABLED",
    "RAG_CLAIM_REPAIR_ENABLED",
    "RAG_GROUNDED_MATH_ENABLED",
    "RAG_LATE_INTERACTION_ENABLED",
    "RAG_QUERY_DECOMPOSITION_ENABLED",
    "RAG_GRAPH_RETRIEVAL_ENABLED",
    "RAG_GRAPH_COMMUNITY_SUMMARIES_ENABLED",
)

EXPECTED_GROUP_COUNTS = {
    "factual": 12,
    "insufficient_evidence": 3,
    "access_denied": 3,
    "grounded_math": 10,
    "complex": 9,
    "graphrag": 6,
    "global": 1,
}


@dataclass(frozen=True)
class MilestoneConfig:
    groups: tuple[str, ...]
    minimum_cases: int
    baseline_enabled: frozenset[str]
    candidate_additions: frozenset[str]
    pair_enabled: bool


MILESTONES = {
    "crag": MilestoneConfig(
        groups=("factual", "insufficient_evidence", "access_denied"),
        minimum_cases=20,
        baseline_enabled=frozenset(),
        candidate_additions=frozenset({
            "RAG_CRAG_ENABLED", "RAG_CLAIM_REPAIR_ENABLED",
        }),
        pair_enabled=True,
    ),
    "grounded_math": MilestoneConfig(
        groups=("grounded_math",),
        minimum_cases=10,
        baseline_enabled=frozenset({
            "RAG_CRAG_ENABLED", "RAG_CLAIM_REPAIR_ENABLED",
        }),
        candidate_additions=frozenset({"RAG_GROUNDED_MATH_ENABLED"}),
        pair_enabled=True,
    ),
    "query_decomposition": MilestoneConfig(
        groups=("complex",),
        minimum_cases=10,
        baseline_enabled=frozenset({
            "RAG_CRAG_ENABLED", "RAG_CLAIM_REPAIR_ENABLED",
            "RAG_GROUNDED_MATH_ENABLED",
        }),
        candidate_additions=frozenset({"RAG_QUERY_DECOMPOSITION_ENABLED"}),
        pair_enabled=True,
    ),
    "graph_retrieval": MilestoneConfig(
        groups=("graphrag",),
        minimum_cases=10,
        baseline_enabled=frozenset(),
        candidate_additions=frozenset({"RAG_GRAPH_RETRIEVAL_ENABLED"}),
        pair_enabled=False,
    ),
    "community_summaries": MilestoneConfig(
        groups=("global",),
        minimum_cases=10,
        baseline_enabled=frozenset(),
        candidate_additions=frozenset({
            "RAG_GRAPH_RETRIEVAL_ENABLED",
            "RAG_GRAPH_COMMUNITY_SUMMARIES_ENABLED",
        }),
        pair_enabled=False,
    ),
}

PAIR_STAGES = tuple(
    name for name, config in MILESTONES.items() if config.pair_enabled
)
