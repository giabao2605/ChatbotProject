"""Frozen-case seam; legacy fixed templates are provider-free.

Explicit candidate task routing can invoke a provider; observations remain unaccepted.

This internal runner does not authenticate operators, publish raw operands,
authorize a live window or turn declared run bindings into verified evidence.
"""
from dataclasses import replace
import json
import math
from pathlib import Path
import re
from time import monotonic

from mech_chatbot.api.evidence_contracts import (
    ScopeComparisonRequest, GraphQueryRequest, BomRollupRequest, QueryPlanRequest, CandidateSelectionContextRequest,
)
from mech_chatbot.db.evidence_snapshot import evidence_read_snapshot, evidence_snapshot_active
from mech_chatbot.evaluation.grounded_math_v2 import (
    export_math_observation, export_task_routing_observation, export_task_routing_diagnostics,
    export_candidate_task_routing_diagnostics,
    export_candidate_task_routing_observation,
)
from mech_chatbot.domain.task_routing import CANDIDATE_TASK_ROUTING
from mech_chatbot.rag.candidate_task_routing import candidate_context_sha256
from mech_chatbot.rag.query_decomposition import GRAPH_TASK_ROUTING
from mech_chatbot.evaluation.schema import validate_frozen_actor
from mech_chatbot.governance.artifact_references import load_json_reference
from mech_chatbot.rag.execution import (
    AccessScope, DefaultRagExecutor, NEVER_CANCELLED, RagInvocation, RagRequest,
    RequestBudgetLimits, collect_rag_events,
)

_BINDINGS = ('source_commit','snapshot_fingerprint','config_hash','gate_hash','eval_hash')


def _frozen_case(reference, root, binding, task_routing_contract=None):
    case = load_json_reference(reference, root=root)
    fields = {'schema', *_BINDINGS, 'run_id','case_id','department','dataset_sha256','actor','question','selection'}
    schema = 'fixed-template-case-v2'
    if task_routing_contract is not None:
        fields = fields | {'task_routing_contract', 'response_language'}
        schema = ('fixed-task-routing-case-v4-draft' if task_routing_contract == CANDIDATE_TASK_ROUTING
                  else 'fixed-task-routing-case-v3-draft')
    if (not isinstance(case, dict) or set(case) != fields or case['schema'] != schema
            or not isinstance(binding, dict) or any(not isinstance(binding.get(f), str)
                or not re.fullmatch(r'[0-9a-f]{40}' if f == 'source_commit' else r'[0-9a-f]{64}', binding[f])
                or case.get(f) != binding[f] for f in _BINDINGS)
            or any(not isinstance(case[f], str) or not re.fullmatch(r'[A-Za-z0-9_-]{1,64}', case[f])
                   for f in ('run_id','case_id','department'))
            or not isinstance(case['dataset_sha256'], str) or not re.fullmatch(r'[0-9a-f]{64}',case['dataset_sha256'])
            or not isinstance(case['question'], str) or not case['question'].strip() or len(case['question']) > 20000):
        raise ValueError('A hash-pinned fixed-template case with matching declared bindings is required')
    if task_routing_contract is not None and (
            type(case['task_routing_contract']) is not str or case['task_routing_contract'] != task_routing_contract
            or type(case['response_language']) is not str or case['response_language'] not in ('vi', 'en')):
        raise ValueError('Exact candidate routing contract and language required')
    validate_frozen_actor(case['actor'])
    return case


def _case_request(case):
    selection = case['selection']
    codecs = {'scope_comparison':ScopeComparisonRequest, 'graph_query':GraphQueryRequest,
              'bom_rollup':BomRollupRequest, 'query_plan':QueryPlanRequest}
    if case.get('task_routing_contract') == CANDIDATE_TASK_ROUTING:
        codecs = {'candidate_selection_context': CandidateSelectionContextRequest}
    if not isinstance(selection, dict) or len(selection) != 1 or not set(selection) <= set(codecs):
        raise ValueError('One explicit fixed-template selection is required')
    kind = next(iter(selection))
    parsed = codecs[kind].model_validate(selection[kind])
    value = ((parsed.scope_a,parsed.scope_b) if kind == 'scope_comparison' else
             parsed.to_query() if kind == 'graph_query' else
             parsed.to_rollup() if kind == 'bom_rollup' else
             parsed.to_context() if kind == 'candidate_selection_context' else parsed.to_plan())
    actor = case['actor']
    access = AccessScope(actor['user_department'],frozenset(actor['user_roles']),
                         frozenset(actor['allowed_departments']),actor['max_security_level'],frozenset(actor['allowed_sites']))
    return RagRequest(case['question'],access,response_language=case.get('response_language','vi'),**{kind:value})


def _verify_actor(case, profile):
    actor = case['actor']
    names = {'user_id':'user_id','username':'username','user_department':'department','max_security_level':'max_security_level'}
    lists = {'user_roles':'roles','allowed_departments':'allowed_departments','allowed_sites':'allowed_sites'}
    if (not isinstance(profile, dict) or any(actor[f] != profile.get(key) for f,key in names.items())
            or any(not isinstance(profile.get(key), (list,tuple))
                   or not all(isinstance(v,str) for v in profile[key])
                   or set(actor[f]) != set(profile[key]) for f,key in lists.items())
            or not {r.lower() for r in actor['user_roles']}.intersection({'reviewer','knowledge_approver','admin'})):
        raise PermissionError('The current actor must match frozen reviewer permissions')


def _validate_diagnostics_observer(observer, contract):
    if observer is not None and (
            not callable(observer) or contract not in (GRAPH_TASK_ROUTING, CANDIDATE_TASK_ROUTING) or evidence_snapshot_active()):
        raise ValueError('Candidate routing diagnostics require a callable observer, explicit V3 or V4 and an owned snapshot')


def _routing_case_observation(collected, case, request, contract):
    if contract is None:
        return {}
    v4 = contract == CANDIDATE_TASK_ROUTING
    routing = (export_candidate_task_routing_observation if v4 else export_task_routing_observation)(collected)
    if routing['contract'] != contract or v4 and routing['candidate_context_sha256'] != candidate_context_sha256(request):
        raise ValueError('Observed routing must match the frozen contract and candidate context')
    return {'schema': 'fixed-task-routing-case-observation-v4-draft' if v4 else 'fixed-task-routing-case-observation-v3-draft',
            'task_routing': routing, 'task_routing_contract': contract, 'response_language': case['response_language']}


def run_fixed_case(case_reference, *, root: str | Path, run_binding, profile_loader=None,
                   executor=None, cancellation=NEVER_CANCELLED, budget_limits=None, task_routing_contract=None,
                   task_routing_diagnostics_observer=None):
    """Collect real fixed actions after strict actor reads in the same snapshot."""
    if task_routing_contract is not None and (type(task_routing_contract) is not str
            or task_routing_contract not in (GRAPH_TASK_ROUTING, CANDIDATE_TASK_ROUTING)):
        raise ValueError('Only explicit V3 or V4 candidate task routing contracts are supported')
    _validate_diagnostics_observer(task_routing_diagnostics_observer, task_routing_contract)
    case = _frozen_case(case_reference, root, run_binding, task_routing_contract)
    request = _case_request(case)
    limits = budget_limits or RequestBudgetLimits()
    if type(limits.deadline_seconds) not in (int,float) or not math.isfinite(limits.deadline_seconds) or limits.deadline_seconds < 0:
        raise ValueError('A finite nonnegative deadline is required')
    started = monotonic()
    deadline = started + limits.deadline_seconds
    def checkpoint(_phase):
        if cancellation.is_set():
            from mech_chatbot.llm.external_ai import ExternalAICallCancelled
            raise ExternalAICallCancelled('Fixed-case execution cancelled')
        if monotonic() >= deadline:
            raise TimeoutError('Fixed-case deadline exceeded')
    if profile_loader is None:
        from mech_chatbot.auth.core import load_user_profile
        profile_loader = load_user_profile
    with evidence_read_snapshot(checkpoint, deadline):
        profile = profile_loader(case['actor']['user_id'],case['actor']['username'],strict=True)
        _verify_actor(case, profile)
        checkpoint('actor')
        selected = executor or DefaultRagExecutor(budget_limits=replace(limits,deadline_seconds=max(0,deadline-monotonic())))
        collected = collect_rag_events(selected.run(request, RagInvocation(
            f"fixed:{case['run_id']}:{case['case_id']}", mode='evaluation', capture_math_observations=True,
            task_routing_contract=task_routing_contract), cancellation=cancellation))
        checkpoint('collection')
        observed = export_math_observation(collected)
        candidate = _routing_case_observation(collected, case, request, task_routing_contract)
        receipt = None
        if task_routing_diagnostics_observer is not None:
            exporter = export_candidate_task_routing_diagnostics if task_routing_contract == CANDIDATE_TASK_ROUTING else export_task_routing_diagnostics
            receipt = exporter(collected)
    checkpoint('completion')
    if task_routing_diagnostics_observer is not None:
        task_routing_diagnostics_observer(json.loads(json.dumps(receipt, allow_nan=False)))
        checkpoint('diagnostics')
    # Routing V3/V4 retain their closed historical shape; the budget is a Math v2 field.
    if candidate:
        observed = {key:value for key,value in observed.items() if key != 'execution_budget'}
    return {**observed, 'schema':'fixed-template-case-observation-v2',
            **candidate,
            **{f:case[f] for f in (*_BINDINGS,'run_id','case_id','department','dataset_sha256','actor')},
            'case_sha256':case_reference['sha256'],'latency_ms':round((monotonic()-started)*1000,3),
            'binding_verification':'pending','adjudication_reference':None}
