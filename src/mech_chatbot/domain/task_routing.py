"""Immutable alternatives supplied by a trusted evaluation caller."""
from dataclasses import dataclass

from mech_chatbot.domain.graph_query import GraphQuery, BomRollupQuery
from mech_chatbot.domain.query_plan import QueryBranch

CANDIDATE_TASK_ROUTING = 'semantic-candidate-task-routing-v4-draft'
MIXED_CANDIDATE_TASK_ROUTING = 'semantic-mixed-candidate-task-routing-v5-draft'


@dataclass(frozen=True, slots=True)
class CandidateSelection:
    selection_id: str
    scope_comparison: tuple[str, str] | None = None
    graph_query: GraphQuery | None = None
    bom_rollup: BomRollupQuery | None = None
    question: str | None = None
    depends_on: tuple[str, ...] = ()
    text_query: bool = False

    def __post_init__(self):
        QueryBranch(self.selection_id, self.question if self.question is not None else 'Candidate',
                    self.scope_comparison, self.graph_query, self.bom_rollup, self.depends_on,
                    text_query=self.text_query)


@dataclass(frozen=True, slots=True)
class CandidateSelectionContext:
    candidates: tuple[CandidateSelection, ...]

    def __post_init__(self):
        if not isinstance(self.candidates, tuple) or not 1 <= len(self.candidates) <= 8:
            raise ValueError('One to eight candidate alternatives required')
        if any(not isinstance(candidate, CandidateSelection) for candidate in self.candidates):
            raise ValueError('Validated candidates required')
        by_id = {candidate.selection_id: candidate for candidate in self.candidates}
        if len(by_id) != len(self.candidates) or any(not set(candidate.depends_on) <= by_id.keys()
                or candidate.selection_id in candidate.depends_on for candidate in self.candidates):
            raise ValueError('Unique candidates and existing dependency references required')
        resolved = set()
        for _ in self.candidates:
            resolved = resolved | {key for key, candidate in by_id.items() if set(candidate.depends_on) <= resolved}
        if len(resolved) != len(by_id):
            raise ValueError('Candidate dependencies cannot contain cycles')
