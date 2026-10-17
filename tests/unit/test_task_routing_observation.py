"""Candidate-only routing observations at the completed executor boundary."""
from dataclasses import replace
import json
import threading

import pytest

from mech_chatbot.evaluation.grounded_math_v2 import export_math_observation, export_task_routing_observation
from mech_chatbot.rag.execution import (
    DefaultRagExecutor, RagInvocation, RagCompleted, RagCancelled, collect_rag_events, consume_rag_events,
)
from mech_chatbot.config.repository_runtime import bind_repository_runtime
from mech_chatbot.domain.query_plan import QueryPlan, QueryBranch
from .test_semantic_graph_task_routing import bom_store, connection, store, graph_request, payload, execute, Planner, VERSION
from .test_rag_bom_chat import request as bom_request
from .test_rag_scope_comparison import request as comparison_request
from .test_rag_query_plan import combined_request


def test_uncertain_overflow_exports_one_planner_without_evidence(bom_store):
    _, engine = bom_store
    selected = graph_request('Review the selected assemblies and their use, with BOM totals and a scope comparison.')
    response = payload(selected.question, (('a', 'graph_contains'), ('b', 'graph_where_used'),
                                          ('c', 'bom_rollup'), ('d', 'compare_two_scopes')))
    response['disposition'] = 'overflow'
    result = execute(engine, selected, Planner(response))
    assert result.refusal_reason == 'intent_overflow'
    assert result.diagnostics.budget.planners == 1
    assert result.diagnostics.budget.graph_examined_relations == 0
    assert result.citations == () and result.observed_math_results == ()
    assert export_math_observation(result)['actual_outcome'] == 'clarification_required'
    observed = export_task_routing_observation(result)
    assert observed['origin'] == 'planner' and observed['planner_count'] == 1
    assert observed['disposition'] == 'overflow' and len(observed['tasks']) == 4
    assert all(t['selection_id'] is None and t['depends_on'] == [] for t in observed['tasks'])


@pytest.fixture
def overflow_result(bom_store):
    _, engine = bom_store
    selected = graph_request('Review the selected assemblies and their use, with BOM totals and a scope comparison.')
    response = payload(selected.question, (('a', 'graph_contains'), ('b', 'graph_where_used'),
                                          ('c', 'bom_rollup'), ('d', 'compare_two_scopes')))
    response['disposition'] = 'overflow'
    return execute(engine, selected, Planner(response))


def test_simple_ready_observation_has_no_planner_or_provider_text(bom_store, caplog):
    _, engine = bom_store
    selected = graph_request('Show selected assembly relations.')
    result = execute(engine, selected, Planner(lambda *_a, **_k: pytest.fail('Unexpected planner')))
    observed = export_task_routing_observation(result)
    assert observed == {'schema': 'task-routing-observation-v3', 'contract': VERSION,
        'origin': 'fast_path', 'disposition': 'ready', 'planner_count': 0,
        'tasks': [{'selection_id': 'graph', 'template': 'graph_contains',
                   'requested_outputs': ['relations', 'citations', 'coverage'], 'depends_on': []}],
        'accepted': False, 'offline_only': True}
    assert 'observed_task_routing' not in dict(result.diagnostics)
    assert 'task-routing-observation' not in repr(result) + caplog.text
    assert selected.question not in json.dumps(observed)
    assert 'document:1' not in json.dumps(observed)


@pytest.mark.parametrize('template', ['bom_rollup', 'compare_two_scopes'])
def test_math_routing_preserves_requested_output_sets(bom_store, template):
    _, engine = bom_store
    selected = bom_request() if template == 'bom_rollup' else replace(
        comparison_request(), scope_comparison=('quantity-scope', 'policy'))
    selected = replace(selected, question='Explain the selected calculation using its formula and citations.')
    key = 'bom' if template == 'bom_rollup' else 'comparison'
    response = payload(selected.question, ((key, template),))
    response['tasks'][0]['requested_outputs'] = ['citations', 'formula']
    result = execute(engine, selected, Planner(response))
    task = export_task_routing_observation(result)['tasks'][0]
    assert task == {'selection_id': key, 'template': template,
                    'requested_outputs': ['citations', 'formula'], 'depends_on': []}
    assert result.diagnostics.budget.planners == 1
    assert result.observed_math_results


def test_parent_three_tasks_survive_child_guards_and_keep_dependency_order(bom_store):
    _, engine = bom_store
    selected = combined_request()
    graph, bom = selected.query_plan.branches
    selected = replace(selected, query_plan=QueryPlan((graph, replace(bom, depends_on=('relations',)),
        QueryBranch('compare', 'Compare selected scopes.', scope_comparison=('quantity-scope', 'policy'),
                    depends_on=('total',)))))
    slots = (('relations', 'graph_contains'), ('total', 'bom_rollup'), ('compare', 'compare_two_scopes'))
    result = execute(engine, selected, Planner(payload(selected.question, slots)))
    observed = export_task_routing_observation(result)
    assert observed['planner_count'] == 1
    assert [(t['selection_id'], t['template'], t['depends_on']) for t in observed['tasks']] == [
        ('relations', 'graph_contains', []), ('total', 'bom_rollup', ['relations']),
        ('compare', 'compare_two_scopes', ['total'])]
    assert result.diagnostics.budget.subqueries == 3


@pytest.mark.parametrize('disposition', ['clarify', 'unsupported'])
def test_nonready_dispositions_keep_distinct_bounded_decisions(bom_store, disposition):
    _, engine = bom_store
    selected = graph_request()
    response = payload(selected.question, (('private_unresolved_identifier', 'graph_contains'),))
    response['tasks'][0]['task_id'] = 'private_provider_label'
    response['disposition'] = disposition
    result = execute(engine, selected, Planner(response))
    observed = export_task_routing_observation(result)
    assert observed['disposition'] == disposition and observed['planner_count'] == 1
    assert observed['tasks'][0]['selection_id'] is None
    assert 'private_' not in json.dumps(observed)
    assert 'spans' not in json.dumps(observed)


def test_fast_overflow_records_zero_planners_and_exports_math(bom_store):
    _, engine = bom_store
    selected = graph_request('1. Show selected assembly relations [a]\n2. Show selected part parents [b]\n'
                             '3. Calculate selected BOM [c]\n4. Compare selected scopes [d]')
    result = execute(engine, selected, Planner(lambda *_a, **_k: pytest.fail('Unexpected planner')))
    observed = export_task_routing_observation(result)
    assert observed['origin'] == 'fast_path' and observed['planner_count'] == 0
    assert observed['tasks'] == [] and observed['disposition'] == 'overflow'
    assert export_math_observation(result)['actual_outcome'] == 'clarification_required'


@pytest.mark.parametrize('response', ['not JSON private text', '{"extra": "private text"}'])
def test_malformed_plans_export_only_invalid_category(bom_store, response):
    _, engine = bom_store
    result = execute(engine, graph_request(), Planner(response))
    observed = export_task_routing_observation(result)
    assert observed['disposition'] == 'invalid' and observed['origin'] == 'planner'
    assert observed['tasks'] == [] and 'private' not in json.dumps(observed)


def test_prevalidation_unsupported_selection_records_no_planner(bom_store):
    _, engine = bom_store
    selected = replace(graph_request(), graph_query=None)
    result = execute(engine, selected, Planner(lambda *_a, **_k: pytest.fail('Unexpected planner')))
    observed = export_task_routing_observation(result)
    assert observed['origin'] == 'prevalidation' and observed['disposition'] == 'unsupported'
    assert observed['tasks'] == [] and observed['planner_count'] == 0


@pytest.mark.parametrize('version', [None, 'semantic-fixed-task-routing-v2-draft'])
def test_default_and_older_contracts_have_no_task_observation(bom_store, version):
    _, engine = bom_store
    result = execute(engine, graph_request('1. A\n2. B\n3. C\n4. D'), Planner('unused'), version)
    assert result.observed_task_routing is None
    with pytest.raises(ValueError, match='completed candidate'):
        export_task_routing_observation(result)


def test_observation_needs_no_math_capture_and_copies_at_each_public_boundary(bom_store):
    _, engine = bom_store
    selected = graph_request('Show selected assembly relations.')
    with bind_repository_runtime(db_engine=engine):
        events = list(DefaultRagExecutor().run(selected,
            RagInvocation('routing-copy', mode='test', task_routing_contract=VERSION)))
    completed = next(event for event in events if isinstance(event, RagCompleted))
    assert completed.observed_math_results is None
    result = collect_rag_events(iter(events))
    completed.observed_task_routing['tasks'][0]['requested_outputs'].append('private_mutation')
    assert 'private_mutation' not in json.dumps(export_task_routing_observation(result))
    exported = export_task_routing_observation(result)
    exported['tasks'].clear()
    assert len(export_task_routing_observation(result)['tasks']) == 1
    assert all('task-routing-observation' not in repr(event) for event in events)


def test_unauthorized_and_cancelled_invocations_do_not_observe(bom_store):
    _, engine = bom_store
    selected = replace(graph_request(), access=replace(graph_request().access, roles=frozenset({'employee'})))
    planner = Planner(lambda *_a, **_k: pytest.fail('Unexpected planner'))
    with pytest.raises(PermissionError):
        execute(engine, selected, planner)
    cancel = threading.Event()
    cancel.set()
    with bind_repository_runtime(db_engine=engine):
        terminal = consume_rag_events(DefaultRagExecutor(provider_adapter=planner).run(graph_request(),
            RagInvocation('cancel-observation', mode='evaluation', task_routing_contract=VERSION), cancel))
    assert isinstance(terminal, RagCancelled) and not planner.calls
    with pytest.raises(ValueError):
        export_task_routing_observation(terminal)


@pytest.mark.parametrize('mode', ['production', 'pilot_replay'])
def test_routing_sidecar_cannot_be_requested_in_live_modes(mode):
    with pytest.raises(ValueError):
        RagInvocation('live-forbidden', mode=mode, task_routing_contract=VERSION)


@pytest.mark.parametrize('fault', ['missing', 'contract', 'extra', 'schema', 'origin', 'disposition',
    'planner_bool', 'planner_float', 'planner_zero', 'ready', 'unsupported', 'too_many', 'task_extra',
    'template', 'outputs', 'output_duplicate', 'output_bool', 'selection', 'deps', 'deps_duplicate',
    'unknown_deps', 'tasks_type', 'fast_tasks', 'prevalidation', 'invalid_tasks'])
def test_forged_one_planner_overflow_observations_are_rejected(overflow_result, fault):
    result = overflow_result
    observed = json.loads(json.dumps(result.observed_task_routing))
    if fault == 'missing':
        observed = None
    elif fault.startswith('planner_'):
        count = {'planner_bool': True, 'planner_float': 1.0, 'planner_zero': 0}[fault]
        result = replace(result, diagnostics=replace(result.diagnostics,
            budget=replace(result.diagnostics.budget, planners=count)))
    elif fault in ('ready', 'unsupported'):
        observed['disposition'] = fault
        observed['tasks'] = []
    elif fault == 'too_many':
        observed['tasks'] *= 3
    elif fault == 'tasks_type':
        observed['tasks'] = {}
    elif fault == 'fast_tasks':
        observed['origin'] = 'fast_path'
    elif fault == 'prevalidation':
        observed.update(origin='prevalidation', disposition='invalid')
    elif fault == 'invalid_tasks':
        observed['disposition'] = 'invalid'
    elif fault in ('contract', 'extra', 'schema', 'origin', 'disposition'):
        observed[fault] = 'invalid'
    else:
        task = observed['tasks'][0]
        key, value = {'task_extra': ('extra', 'private'), 'template': ('template', 'invented'),
            'outputs': ('requested_outputs', ['invented']), 'output_duplicate': ('requested_outputs', ['relations'] * 2),
            'output_bool': ('requested_outputs', [True]), 'selection': ('selection_id', 'invalid key'),
            'deps': ('depends_on', [False]), 'deps_duplicate': ('depends_on', ['a', 'a']),
            'unknown_deps': ('depends_on', ['a'])}[fault]
        task[key] = value
    result = replace(result, observed_task_routing=observed)
    with pytest.raises(ValueError):
        export_math_observation(result)


@pytest.mark.parametrize('field', ['subqueries', 'corrections', 'repairs', 'calculations', 'graph_edges',
    'graph_examined_relations', 'provider_retries', 'final_generations'])
def test_candidate_overflow_rejects_any_other_action(overflow_result, field):
    result = replace(overflow_result, diagnostics=replace(overflow_result.diagnostics,
        budget=replace(overflow_result.diagnostics.budget, **{field: 1})))
    with pytest.raises(ValueError):
        export_math_observation(result)


@pytest.mark.parametrize('fault', ['citation', 'retrieval', 'graph_metadata', 'math', 'outcome', 'reason'])
def test_candidate_overflow_rejects_evidence_or_inconsistent_terminal(overflow_result, fault):
    result = overflow_result
    if fault == 'citation':
        result = replace(result, citations=({'source_id': 'private'},))
    elif fault == 'math':
        result = replace(result, observed_math_results=({'private': True},))
    elif fault in ('retrieval', 'graph_metadata'):
        from mech_chatbot.rag.execution import RagDiagnostics
        field = 'retrieved_docs' if fault == 'retrieval' else 'graph_v2'
        result = replace(result, diagnostics=RagDiagnostics.from_mapping({**dict(result.diagnostics), field: [{'private': True}]}))
    else:
        result = replace(result, **({'outcome': 'answered'} if fault == 'outcome' else {'refusal_reason': 'other'}))
    with pytest.raises(ValueError):
        export_math_observation(result)


def test_legacy_overflow_still_requires_zero_planners(bom_store):
    _, engine = bom_store
    result = execute(engine, graph_request('1. A\n2. B\n3. C\n4. D'), Planner('unused'), None)
    assert export_math_observation(result)['actual_outcome'] == 'clarification_required'
    result = replace(result, diagnostics=replace(result.diagnostics,
        budget=replace(result.diagnostics.budget, planners=1)))
    with pytest.raises(ValueError):
        export_math_observation(result)


def test_ready_decision_cannot_authorize_one_planner_overflow(overflow_result):
    observed = json.loads(json.dumps(overflow_result.observed_task_routing))
    observed['disposition'] = 'ready'
    observed['tasks'] = [{'selection_id': 'graph', 'template': 'graph_contains',
                           'requested_outputs': ['coverage'], 'depends_on': []}]
    with pytest.raises(ValueError, match='overflow routing decision'):
        export_math_observation(replace(overflow_result, observed_task_routing=observed))


@pytest.mark.parametrize('fault', ['null_id', 'duplicate_id', 'forward_dependency', 'task_type', 'empty_outputs',
                                 'wrong_tasks_type', 'duplicate_dependency', 'unbounded_dependency'])
def test_ready_observation_rejects_malformed_or_unordered_tasks(bom_store, fault):
    _, engine = bom_store
    result = execute(engine, graph_request('Show selected assembly relations.'), Planner('unused'))
    observed = json.loads(json.dumps(result.observed_task_routing))
    task = observed['tasks'][0]
    if fault == 'null_id':
        task['selection_id'] = None
    elif fault == 'duplicate_id':
        observed['origin'] = 'planner'
        observed['tasks'].append(dict(task))
        result = replace(result, diagnostics=replace(result.diagnostics,
            budget=replace(result.diagnostics.budget, planners=1)))
    elif fault == 'forward_dependency':
        task['depends_on'] = ['later']
    elif fault == 'task_type':
        observed['tasks'] = [False]
    elif fault == 'empty_outputs':
        task['requested_outputs'] = []
    elif fault == 'wrong_tasks_type':
        observed['tasks'] = False
    elif fault == 'duplicate_dependency':
        task['depends_on'] = ['prior', 'prior']
    else:
        task['depends_on'] = ['prior', 'prior2', 'prior3']
    with pytest.raises(ValueError):
        export_task_routing_observation(replace(result, observed_task_routing=observed))


@pytest.mark.parametrize('fault', ['outcome', 'outcome_mismatch', 'diagnostic_outcome', 'reason_type'])
def test_standalone_ready_export_checks_completed_outcome(bom_store, fault):
    _, engine = bom_store
    result = execute(engine, graph_request('Show selected assembly relations.'), Planner('unused'))
    if fault == 'diagnostic_outcome':
        from mech_chatbot.rag.execution import RagDiagnostics
        result = replace(result, diagnostics=RagDiagnostics.from_mapping(
            {**dict(result.diagnostics), 'answer_outcome': True}))
    else:
        field, value = {'outcome': ('outcome', 'bogus'), 'outcome_mismatch': ('outcome', 'refused'),
                        'reason_type': ('refusal_reason', ['private'])}[fault]
        result = replace(result, **{field: value})
    with pytest.raises(ValueError, match='Invalid observed outcome'):
        export_task_routing_observation(result)


def test_ready_routing_remains_observable_when_source_access_refuses_answer(bom_store):
    _, engine = bom_store
    selected = graph_request('Show selected assembly relations.')
    selected = replace(selected, access=replace(selected.access, allowed_departments=frozenset({'other'})))
    result = execute(engine, selected, Planner('unused'))
    assert result.outcome == 'refused'
    assert export_task_routing_observation(result)['disposition'] == 'ready'
    assert 'document:1' not in json.dumps(export_task_routing_observation(result))
