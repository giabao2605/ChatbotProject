"""Compatibility exports for the domain-neutral graph policy."""

from mech_chatbot.domain.graph_policy import (
    AMBIGUOUS_OR_REDUNDANT_RELATIONS,
    GraphProposalDecision,
    RELATION_ONTOLOGY,
    validate_graph_proposal,
)


__all__ = [
    "AMBIGUOUS_OR_REDUNDANT_RELATIONS",
    "GraphProposalDecision",
    "RELATION_ONTOLOGY",
    "validate_graph_proposal",
]
