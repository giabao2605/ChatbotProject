"""Candidate alternatives through the pinned runner and actual typed actions."""
import pytest
import threading
from dataclasses import replace

from mech_chatbot.config.repository_runtime import bind_repository_runtime
from mech_chatbot.rag.execution import DefaultRagExecutor
from .test_fixed_math_runner_v2 import BINDING, ACTOR, frozen_case, current_profile
from .test_rag_scope_comparison import approved_pair, store
from .test_semantic_fixed_task_routing import Planner
from .test_rag_bom_chat import bom_store, connection


VERSION = 'semantic-candidate-task-routing-v4-draft'


def candidate_case(root, **changes):
    return frozen_case(root, **{'schema': 'fixed-task-routing-case-v4-draft',
        'task_routing_contract': VERSION, 'response_language': 'en',
        'question': 'Compare the two approved scopes and show their difference.',
        'selection': {'candidate_selection_context': {'candidates': [
            {'selection_id': 'comparison', 'scope_comparison': {'scope_a': 'a', 'scope_b': 'b'}}]}},
        **changes})


def response(question, disposition='ready', slots=(('comparison', 'compare_two_scopes'),)):
    return {'schema': 'candidate-task-routing-quotes-v1', 'disposition': disposition, 'tasks': [
        {'task_id': key, 'selection_id': key, 'template': template,
         'spans': [{'text': question}],
         'requested_outputs': ['sum_a', 'sum_b', 'delta', 'percent_change']
            if template == 'compare_two_scopes' else ['relations']}
        for key, template in slots], 'unresolved': []}


def test_v4_runner_resolves_and_executes_real_math_with_one_planner(store, tmp_path):
    from scripts.eval.run_fixed_math_v2 import run_fixed_case
    approved_pair(store)
    ref = candidate_case(tmp_path)
    question = 'Compare the two approved scopes and show their difference.'
    planner = Planner(response(question))
    with bind_repository_runtime(db_engine=store):
        observed = run_fixed_case(ref, root=tmp_path, run_binding=BINDING,
            profile_loader=current_profile, executor=DefaultRagExecutor(provider_adapter=planner),
            task_routing_contract=VERSION)
    assert observed['schema'] == 'fixed-task-routing-case-observation-v4-draft'
    assert observed['task_routing']['contract'] == VERSION
    assert observed['task_routing']['planner_count'] == len(planner.calls) == 1
    assert observed['task_routing']['tasks'][0]['selection_id'] == 'comparison'
    assert observed['calculation_count'] == 4 and len(observed['citations']) == 2
    assert [step['value'] for step in observed['math_results'][0]['steps']] == ['4', '8', '4', '100']
    assert observed['binding_verification'] == 'pending' and observed['accepted'] is False
    assert observed['adjudication_reference'] is None


@pytest.mark.parametrize('disposition', ['clarify', 'overflow', 'invalid'])
def test_v4_nonready_runner_never_executes_typed_actions(store, tmp_path, disposition):
    from scripts.eval.run_fixed_math_v2 import run_fixed_case
    ref = candidate_case(tmp_path)
    question = 'Compare the two approved scopes and show their difference.'
    value = response(question, disposition='overflow' if disposition == 'overflow' else 'clarify',
        slots=tuple((f'slot{i}', 'compare_two_scopes') for i in range(4)) if disposition == 'overflow' else ())
    planner = Planner('private malformed response' if disposition == 'invalid' else value)
    with bind_repository_runtime(db_engine=store):
        observed = run_fixed_case(ref, root=tmp_path, run_binding=BINDING,
            profile_loader=current_profile, executor=DefaultRagExecutor(provider_adapter=planner),
            task_routing_contract=VERSION)
    routing = observed['task_routing']
    assert routing['disposition'] == disposition and routing['planner_count'] == len(planner.calls) == 1
    assert routing['resolved_request_sha256'] is None
    assert observed['calculation_count'] == 0
    assert observed['math_results'] == observed['citations'] == observed['query_branches'] == []
    assert all(task['selection_id'] is None for task in routing['tasks'])
    assert 'private malformed' not in str(observed)


def test_v4_four_alternatives_are_not_four_tasks(store, tmp_path):
    from scripts.eval.run_fixed_math_v2 import run_fixed_case
    approved_pair(store)
    candidates = [{'selection_id': f'comparison{i}', 'scope_comparison': {'scope_a': 'a', 'scope_b': 'b'}}
                  for i in range(4)]
    ref = candidate_case(tmp_path, selection={'candidate_selection_context': {'candidates': candidates}})
    planner = Planner(response('Compare the two approved scopes and show their difference.',
                               slots=(('comparison2', 'compare_two_scopes'),)))
    with bind_repository_runtime(db_engine=store):
        observed = run_fixed_case(ref, root=tmp_path, run_binding=BINDING, profile_loader=current_profile,
            executor=DefaultRagExecutor(provider_adapter=planner), task_routing_contract=VERSION)
    assert observed['task_routing']['disposition'] == 'ready'
    assert observed['task_routing']['tasks'][0]['selection_id'] == 'comparison2'
    assert observed['calculation_count'] == 4 and len(planner.calls) == 1


@pytest.mark.parametrize('changes', [
    {'schema': 'fixed-task-routing-case-v3-draft'},
    {'task_routing_contract': 'semantic-graph-fixed-task-routing-v3-draft'},
    {'selection': {'scope_comparison': {'scope_a': 'a', 'scope_b': 'b'}}},
    {'selection': {'candidate_selection_context': {'candidates': [], 'private': True}}},
    {'selection': {'candidate_selection_context': {'candidates': [
        {'selection_id': 'x', 'scope_comparison': {'scope_a': 'a', 'scope_b': 'b'}, 'private': True}]}}},
])
def test_v4_runner_rejects_malformed_cases_before_actor_lookup(store, tmp_path, changes):
    from scripts.eval.run_fixed_math_v2 import run_fixed_case
    ref = candidate_case(tmp_path, **changes)
    def forbidden(*args, **kwargs):
        pytest.fail('Malformed candidate case must fail before actor lookup')
    with bind_repository_runtime(db_engine=store), pytest.raises(ValueError):
        run_fixed_case(ref, root=tmp_path, run_binding=BINDING,
                       profile_loader=forbidden, task_routing_contract=VERSION)


@pytest.mark.parametrize('failure', ['cancelled', 'deadline', 'commit'])
def test_v4_runner_never_returns_after_cancel_timeout_or_failed_snapshot(store, tmp_path, failure):
    from scripts.eval.run_fixed_math_v2 import run_fixed_case
    from mech_chatbot.rag.execution import RequestBudgetLimits
    from mech_chatbot.llm.external_ai import ExternalAICallCancelled
    from sqlalchemy import event
    approved_pair(store)
    ref = candidate_case(tmp_path)
    stop = threading.Event()
    if failure == 'cancelled':
        stop.set()
    def failed_commit(_conn):
        raise RuntimeError('Synthetic snapshot failure')
    if failure == 'commit':
        event.listen(store, 'commit', failed_commit)
    planner = Planner(response('Compare the two approved scopes and show their difference.'))
    expected = {'cancelled': ExternalAICallCancelled, 'deadline': TimeoutError, 'commit': RuntimeError}[failure]
    try:
        with bind_repository_runtime(db_engine=store), pytest.raises(expected):
            run_fixed_case(ref, root=tmp_path, run_binding=BINDING, profile_loader=current_profile,
                executor=DefaultRagExecutor(provider_adapter=planner), cancellation=stop,
                budget_limits=RequestBudgetLimits(deadline_seconds=0 if failure == 'deadline' else 120),
                task_routing_contract=VERSION)
    finally:
        if failure == 'commit':
            event.remove(store, 'commit', failed_commit)


def test_v4_runner_rejects_stale_candidate_receipt(store, tmp_path):
    from scripts.eval.run_fixed_math_v2 import run_fixed_case
    from mech_chatbot.rag.execution import RagCompleted
    approved_pair(store)
    ref = candidate_case(tmp_path)
    real = DefaultRagExecutor(provider_adapter=Planner(response('Compare the two approved scopes and show their difference.')))
    class StaleReceipt:
        def run(self, *args, **kwargs):
            for item in real.run(*args, **kwargs):
                if isinstance(item, RagCompleted):
                    item = replace(item, observed_task_routing={**item.observed_task_routing,
                                                               'candidate_context_sha256': '0' * 64})
                yield item
    with bind_repository_runtime(db_engine=store), pytest.raises(ValueError, match='frozen contract and candidate context'):
        run_fixed_case(ref, root=tmp_path, run_binding=BINDING, profile_loader=current_profile,
                       executor=StaleReceipt(), task_routing_contract=VERSION)


def test_v4_diagnostics_callback_gets_its_own_receipt_after_snapshot_commit(store, tmp_path):
    from scripts.eval.run_fixed_math_v2 import run_fixed_case
    from mech_chatbot.db.evidence_snapshot import evidence_snapshot_active

    receipts = []

    def receive(value):
        assert not evidence_snapshot_active()
        receipts.append(value)

    with bind_repository_runtime(db_engine=store):
        observed = run_fixed_case(candidate_case(tmp_path), root=tmp_path, run_binding=BINDING,
            profile_loader=current_profile, executor=DefaultRagExecutor(provider_adapter=Planner('{')),
            task_routing_contract=VERSION, task_routing_diagnostics_observer=receive)
    assert observed['task_routing']['disposition'] == 'invalid'
    assert receipts == [{'schema': 'candidate-task-routing-diagnostics-v4-draft',
        'trace_id': 'fixed:fixture-run:ENG-1', 'task_routing_contract': VERSION,
        'origin': 'planner', 'disposition': 'invalid', 'planner_count': 1,
        'failure_code': 'json_syntax', 'accepted': False, 'offline_only': True}]


def test_v4_receipt_not_emitted_when_snapshot_commit_fails(store, tmp_path):
    from sqlalchemy import event
    from scripts.eval.run_fixed_math_v2 import run_fixed_case

    receipts = []

    def fail_commit(_connection):
        raise RuntimeError('Synthetic commit failure')

    event.listen(store, 'commit', fail_commit)
    try:
        with bind_repository_runtime(db_engine=store), pytest.raises(RuntimeError):
            run_fixed_case(candidate_case(tmp_path), root=tmp_path, run_binding=BINDING,
                profile_loader=current_profile, executor=DefaultRagExecutor(provider_adapter=Planner('{')),
                task_routing_contract=VERSION, task_routing_diagnostics_observer=receipts.append)
    finally:
        event.remove(store, 'commit', fail_commit)
    assert receipts == []


def test_v4_runner_executes_three_mixed_tasks_and_preserves_declared_dependency(bom_store, tmp_path):
    from scripts.eval.run_fixed_math_v2 import run_fixed_case
    _, engine = bom_store
    graph = {'root_key': 'document:1', 'product_scope': 'product', 'site': 'factory'}
    candidates = [
        {'selection_id': 'relations', 'question': 'Show parts.', 'graph_query': graph},
        {'selection_id': 'total', 'question': 'Calculate the BOM.', 'depends_on': ['relations'],
         'bom_rollup': {**graph, 'order_quantity': '3', 'rounding_scope': 'policy'}},
        {'selection_id': 'comparison', 'question': 'Compare approved scopes.',
         'scope_comparison': {'scope_a': 'quantity-scope', 'scope_b': 'policy'}},
    ]
    question = 'Show parts, calculate the BOM, and compare approved scopes.'
    ref = candidate_case(tmp_path, question=question, actor={**ACTOR, 'allowed_departments': ['tech', 'ENG']},
                         selection={'candidate_selection_context': {'candidates': candidates}})
    payload = response(question, slots=(('relations', 'graph_contains'), ('total', 'bom_rollup'),
                                        ('comparison', 'compare_two_scopes')))
    payload['tasks'][1]['requested_outputs'] = ['totals_by_part']
    planner = Planner(payload)
    def profile(*args, **kwargs):
        return {**current_profile(*args, **kwargs), 'allowed_departments': ['tech', 'ENG']}
    with bind_repository_runtime(db_engine=engine):
        observed = run_fixed_case(ref, root=tmp_path, run_binding=BINDING, profile_loader=profile,
            executor=DefaultRagExecutor(provider_adapter=planner), task_routing_contract=VERSION)
    assert len(planner.calls) == 1 and observed['calculation_count'] == 5
    assert [branch['executed'] for branch in observed['query_branches']] == [True, True, True]
    assert [task['selection_id'] for task in observed['task_routing']['tasks']] == ['relations', 'total', 'comparison']
    assert observed['task_routing']['tasks'][1]['depends_on'] == ['relations']
    assert [record['steps'][0]['value'] for record in observed['math_results']] == ['6', '2']


def test_v4_runner_respects_frozen_acl_and_reveals_no_denied_sources(bom_store, tmp_path):
    from scripts.eval.run_fixed_math_v2 import run_fixed_case
    _, engine = bom_store
    question = 'Show assembly parts.'
    ref = candidate_case(tmp_path, question=question, actor={**ACTOR, 'allowed_sites': ['other']},
        selection={'candidate_selection_context': {'candidates': [
        {'selection_id': 'graph', 'graph_query': {'root_key': 'document:1', 'product_scope': 'product', 'site': 'factory'}}]}})
    planner = Planner(response(question, slots=(('graph', 'graph_contains'),)))
    def profile(*args, **kwargs):
        return {**current_profile(*args, **kwargs), 'allowed_sites': ['other']}
    with bind_repository_runtime(db_engine=engine):
        observed = run_fixed_case(ref, root=tmp_path, run_binding=BINDING, profile_loader=profile,
            executor=DefaultRagExecutor(provider_adapter=planner), task_routing_contract=VERSION)
    assert observed['task_routing']['disposition'] == 'ready'
    assert observed['answer_outcome'] == 'refused' and observed['citations'] == []
    assert 'document:1' not in str(observed) and 'part:p' not in str(observed)
