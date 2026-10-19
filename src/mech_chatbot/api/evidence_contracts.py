"""Shared input and fail-closed transport guards for evaluation-only v2 work."""
from fastapi import HTTPException
from pydantic import BaseModel, ConfigDict, Field, model_validator
from typing import Literal
from mech_chatbot.domain.graph_query import GraphQuery,BomRollupQuery
from mech_chatbot.domain.query_plan import QueryBranch,QueryPlan
from mech_chatbot.domain.task_routing import CandidateSelection, CandidateSelectionContext

from mech_chatbot.api.transport_utils import assert_any_role


class ScopeTotalRequest(BaseModel):
    model_config = ConfigDict(extra='forbid', frozen=True)
    scope_id: str = Field(strict=True, min_length=1, max_length=255, pattern=r'\S')


class ScopeComparisonRequest(BaseModel):
    model_config = ConfigDict(extra='forbid')
    scope_a: str = Field(strict=True, min_length=1, max_length=255, pattern=r'\S')
    scope_b: str = Field(strict=True, min_length=1, max_length=255, pattern=r'\S')


class GraphQueryRequest(BaseModel):
    model_config=ConfigDict(extra='forbid',frozen=True)
    root_key:str=Field(min_length=1,max_length=255)
    product_scope:str=Field(min_length=1,max_length=255)
    site:str=Field(min_length=1,max_length=100)
    direction:Literal['contains','where_used']='contains'
    max_relations:int=Field(default=500,strict=True,ge=0,le=500)
    max_generation_edges:int=Field(default=50,strict=True,ge=0,le=50)

    @model_validator(mode='after')
    def bounded_context(self):
        self.to_query()
        return self

    def to_query(self):
        return GraphQuery(self.root_key,self.product_scope,self.site,self.direction,
                          self.max_relations,self.max_generation_edges)


class BomRollupRequest(GraphQueryRequest):
    direction:Literal['contains']='contains'
    order_quantity:str|None=Field(default=None,strict=True,min_length=1,max_length=128,pattern=r'^[0-9]+(?:\.[0-9]+)?$')
    quantity_scope:str|None=Field(default=None,strict=True,min_length=1,max_length=255,pattern=r'\S')
    rounding_scope:str=Field(strict=True,min_length=1,max_length=255,pattern=r'\S')

    @model_validator(mode='after')
    def one_quantity_basis(self):
        self.to_rollup()
        return self

    def to_rollup(self):
        return BomRollupQuery(self.to_query(),self.order_quantity,self.rounding_scope,self.quantity_scope)


class QueryBranchRequest(BaseModel):
    model_config=ConfigDict(extra='forbid',frozen=True)
    branch_id:str=Field(strict=True,min_length=1,max_length=64,pattern=r'^[A-Za-z0-9_-]+$')
    question:str=Field(strict=True,min_length=1,max_length=20000,pattern=r'\S')
    scope_comparison:ScopeComparisonRequest|None=None
    graph_query:GraphQueryRequest|None=None
    bom_rollup:BomRollupRequest|None=None
    depends_on:tuple[str,...]=Field(default=(),max_length=2)
    text_query:bool=Field(default=False,strict=True)
    scope_total:ScopeTotalRequest|None=None

    @model_validator(mode='after')
    def one_action(self):
        self.to_branch()
        return self

    def to_branch(self):
        return QueryBranch(self.branch_id,self.question,
            scope_comparison=(self.scope_comparison.scope_a,self.scope_comparison.scope_b) if self.scope_comparison else None,
            graph_query=self.graph_query.to_query() if self.graph_query else None,
            bom_rollup=self.bom_rollup.to_rollup() if self.bom_rollup else None,depends_on=self.depends_on,text_query=self.text_query,
            scope_total=self.scope_total.scope_id if self.scope_total else None)


class QueryPlanRequest(BaseModel):
    model_config=ConfigDict(extra='forbid',frozen=True)
    branches:tuple[QueryBranchRequest,...]=Field(min_length=1,max_length=3)

    @model_validator(mode='after')
    def bounded_dependencies(self):
        self.to_plan()
        return self

    def to_plan(self):
        return QueryPlan(tuple(branch.to_branch() for branch in self.branches))


class CandidateSelectionRequest(BaseModel):
    model_config = ConfigDict(extra='forbid', frozen=True)
    selection_id: str = Field(strict=True, min_length=1, max_length=64, pattern=r'^[A-Za-z0-9_-]+$')
    question: str | None = Field(default=None, strict=True, min_length=1, max_length=20000, pattern=r'\S')
    scope_comparison: ScopeComparisonRequest | None = None
    graph_query: GraphQueryRequest | None = None
    bom_rollup: BomRollupRequest | None = None
    depends_on: tuple[str, ...] = Field(default=(), max_length=2)
    text_query: bool = Field(default=False, strict=True)

    @model_validator(mode='after')
    def validated_action(self):
        self.to_candidate()
        return self

    def to_candidate(self):
        return CandidateSelection(self.selection_id,
            scope_comparison=(self.scope_comparison.scope_a, self.scope_comparison.scope_b) if self.scope_comparison else None,
            graph_query=self.graph_query.to_query() if self.graph_query else None,
            bom_rollup=self.bom_rollup.to_rollup() if self.bom_rollup else None,
            question=self.question, depends_on=self.depends_on, text_query=self.text_query)


class CandidateSelectionContextRequest(BaseModel):
    model_config = ConfigDict(extra='forbid', frozen=True)
    candidates: tuple[CandidateSelectionRequest, ...] = Field(min_length=1, max_length=8)

    @model_validator(mode='after')
    def validated_context(self):
        self.to_context()
        return self

    def to_context(self):
        return CandidateSelectionContext(tuple(candidate.to_candidate() for candidate in self.candidates))


def require_v2_evaluation_context(mode, profile):
    if mode not in {'evaluation', 'test'}:
        raise HTTPException(409, detail='V2 contract requires evaluation context')
    assert_any_role(profile, 'reviewer', 'knowledge_approver', 'admin')
    return mode


def require_comparison_context(mode, profile, *, comparison_only, authorizer=None):
    if mode in {'evaluation', 'test'}:
        return require_v2_evaluation_context(mode, profile)
    from mech_chatbot.governance.math_v2_activation import MathV2Capability, CONTRACT
    cap = authorizer() if callable(authorizer) else None
    if (mode != 'production' or not comparison_only or not isinstance(cap, MathV2Capability)
            or cap.contract != CONTRACT or cap.template != 'compare_two_scopes' or cap.context != 'controlled_demo'):
        raise HTTPException(409, detail='Math v2 comparison authority required')
    return mode


def require_graph_context(mode, profile, *, authorizer=None):
    if mode in {'evaluation','test'}:
        return require_v2_evaluation_context(mode,profile)
    from mech_chatbot.governance.graph_v2_activation import GraphV2Capability,CONTRACT
    cap=authorizer() if callable(authorizer) else None
    if (mode!='production' or not isinstance(cap,GraphV2Capability) or cap.contract!=CONTRACT
            or cap.context!='controlled_demo'):
        raise HTTPException(409,detail='Graph v2 source authority required')
    return mode


def require_scope_total_context(mode, profile, *, authorizer=None):
    if mode in {'evaluation', 'test'}:
        return require_v2_evaluation_context(mode, profile)
    from mech_chatbot.governance.scope_total_activation import ScopeTotalCapability, CONTRACT
    cap = authorizer() if callable(authorizer) else None
    if (mode != 'production' or not isinstance(cap, ScopeTotalCapability)
            or cap.contract != CONTRACT or cap.template != 'sum_reviewed_scope' or cap.context != 'controlled_demo'):
        raise HTTPException(409, detail='Scope total authority required')
    return mode
