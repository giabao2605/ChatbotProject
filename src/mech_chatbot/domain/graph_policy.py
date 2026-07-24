"""Domain-neutral ontology and normalization policy for the knowledge graph."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Mapping


# Adding a relation changes the serving contract and requires a reviewed
# migration/evaluation.
RELATION_ONTOLOGY = frozenset({
    "HAS_VERSION",
    "SUPERSEDES",
    "HAS_PAGE",
    "CONTAINS_PART",
    "USES_MATERIAL",
    "REQUIRES_TOOL",
    "APPLIES_TO",
})

AMBIGUOUS_OR_REDUNDANT_RELATIONS = frozenset({
    "RELATED_COMPONENT",
    "RELATED_TO",
    "CONNECTED_TO",
    "PART_OF",
})


@dataclass(frozen=True)
class GraphProposalDecision:
    accepted: bool
    reason: str
    relation_type: str
    source_quote: str | None = None


def validate_graph_proposal(
    relation_type: str,
    *,
    evidence: Mapping | None,
) -> GraphProposalDecision:
    """Validate an extracted edge before it may enter the review queue."""
    relation = str(relation_type or "").strip().upper()
    if relation in AMBIGUOUS_OR_REDUNDANT_RELATIONS:
        return GraphProposalDecision(
            False, "ambiguous_or_redundant_relation", relation,
        )
    if relation not in RELATION_ONTOLOGY:
        return GraphProposalDecision(False, "relation_not_in_ontology", relation)

    values = dict(evidence or {})
    quote = str(
        values.get("source_quote")
        or values.get("evidence_quote")
        or ""
    ).strip()
    if not quote:
        return GraphProposalDecision(False, "source_quote_required", relation)
    if len(quote) > 2000:
        return GraphProposalDecision(False, "source_quote_too_long", relation)
    return GraphProposalDecision(True, "accepted_for_review", relation, quote)


def expand_seed_keys(seed_keys) -> list[str]:
    """Expand user-visible identifiers to deterministic canonical graph keys."""
    expanded = set()
    for item in seed_keys or ():
        value = str(item or "").strip().lower()
        if not value:
            continue
        expanded.update((value, f"part:{value}", f"material:{value}"))
    return sorted(expanded)


__all__ = [
    "AMBIGUOUS_OR_REDUNDANT_RELATIONS",
    "GraphProposalDecision",
    "RELATION_ONTOLOGY",
    "expand_seed_keys",
    "validate_graph_proposal",
]
