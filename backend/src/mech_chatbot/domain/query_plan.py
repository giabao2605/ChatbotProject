"""Explicit bounded branches; dependencies refer only to earlier branches."""
from dataclasses import asdict, dataclass
import re
from mech_chatbot.domain.graph_query import GraphQuery,BomRollupQuery,bom_rollup_payload

SCOPE_TOTAL_QUERY_SCHEMA = 'query-plan-scope-total-v1'

@dataclass(frozen=True,slots=True)
class QueryBranch:
    branch_id:str
    question:str
    scope_comparison:tuple[str,str]|None=None
    graph_query:GraphQuery|None=None
    bom_rollup:BomRollupQuery|None=None
    depends_on:tuple[str,...]=()
    text_query:bool=False
    scope_total:str|None=None

    def __post_init__(self):
        if (not isinstance(self.branch_id,str) or not re.fullmatch(r'[A-Za-z0-9_-]{1,64}',self.branch_id)
                or not isinstance(self.question,str) or not self.question.strip() or len(self.question)>20000
                or type(self.text_query) is not bool
                or sum(v is not None for v in (self.scope_total,self.scope_comparison,self.graph_query,self.bom_rollup))+int(self.text_query)!=1
                or not isinstance(self.depends_on,tuple) or len(self.depends_on)>2
                or any(not isinstance(v,str) for v in self.depends_on) or len(set(self.depends_on))!=len(self.depends_on)):
            raise ValueError('One bounded action and unique dependencies per branch required')
        if self.scope_total is not None and (not isinstance(self.scope_total,str)
                or not self.scope_total.strip() or len(self.scope_total)>255):
            raise ValueError('One bounded scope identifier required')
        if self.graph_query is not None and not isinstance(self.graph_query,GraphQuery):
            raise ValueError('Validated graph context required')
        if self.bom_rollup is not None and not isinstance(self.bom_rollup,BomRollupQuery):
            raise ValueError('Validated BOM context required')
        if self.scope_comparison is not None and (not isinstance(self.scope_comparison,tuple) or len(self.scope_comparison)!=2
                or any(not isinstance(v,str) or not v.strip() or len(v)>255 for v in self.scope_comparison)):
            raise ValueError('Two bounded scope identifiers required')


@dataclass(frozen=True,slots=True)
class QueryPlan:
    branches:tuple[QueryBranch,...]

    def __post_init__(self):
        if not isinstance(self.branches,tuple) or not 1<=len(self.branches)<=3:
            raise ValueError('One to three branches required; narrow the request')
        seen=set()
        text_ids=set()
        for branch in self.branches:
            if not isinstance(branch,QueryBranch) or branch.branch_id in seen or not set(branch.depends_on)<=seen:
                raise ValueError('Unique branches and earlier dependencies required')
            if set(branch.depends_on) & text_ids:
                raise ValueError("Mixed draft does not support dependencies on text branches")
            seen.add(branch.branch_id)
            if branch.text_query:text_ids.add(branch.branch_id)


def query_plan_snapshot(plan):
    return {'branches': tuple({**{key:value for key,value in asdict(branch).items()
                                  if key!='scope_total' or value is not None},
                              **({'bom_rollup':bom_rollup_payload(branch.bom_rollup)} if branch.bom_rollup else {})}
                             for branch in plan.branches)}
