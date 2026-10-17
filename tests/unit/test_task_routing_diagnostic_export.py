"""Private candidate decoder receipts through completed executor/runner seams."""
from dataclasses import replace
import json
import threading

import pytest
from sqlalchemy import event

from mech_chatbot.config.repository_runtime import bind_repository_runtime
from mech_chatbot.rag.execution import (
    DefaultRagExecutor, RequestBudgetLimits, RagInvocation, RagCompleted, collect_rag_events,
)
from mech_chatbot.rag.query_decomposition import GRAPH_TASK_ROUTING
from scripts.eval.run_fixed_math_v2 import run_fixed_case
from .test_fixed_math_runner_v2 import current_profile, BINDING
from .test_rag_scope_comparison import store, approved_pair
from .test_rag_bom_chat import bom_store, connection
from .test_semantic_graph_task_routing import execute, graph_request, payload, Planner
from .test_semantic_task_runner_v3 import candidate_case
from .test_rag_server_endpoints import rag_client, SERVICE_HEADERS


def test_decoder_receipt_is_private_and_old_observation_is_unchanged(bom_store, caplog):
    from mech_chatbot.evaluation.grounded_math_v2 import (
        export_math_observation, export_task_routing_diagnostics, export_task_routing_observation,
    )
    _, engine = bom_store
    result = execute(engine, graph_request(), Planner('private invalid payload'))
    old = export_math_observation(result)
    routing = export_task_routing_observation(result)
    receipt = export_task_routing_diagnostics(result)
    assert receipt == {'schema':'task-routing-diagnostics-v1', 'trace_id':result.trace_id,
        'task_routing_contract':GRAPH_TASK_ROUTING, 'origin':'planner', 'disposition':'invalid',
        'planner_count':1, 'failure_code':'json_syntax', 'accepted':False, 'offline_only':True}
    assert result.observed_task_routing_failure_code == 'json_syntax'
    assert 'failure_code' not in routing and 'failure_code' not in json.dumps(old)
    assert 'observed_task_routing_failure_code' not in repr(result)
    assert 'private invalid payload' not in json.dumps(receipt)
    assert 'failure_code' not in json.dumps(dict(result.diagnostics))
    assert 'json_syntax' not in caplog.text
    receipt['failure_code'] = 'caller mutation'
    assert export_task_routing_diagnostics(result)['failure_code'] == 'json_syntax'
    assert export_math_observation(result) == old


@pytest.mark.parametrize('fault', ['terminal', 'trace_bool', 'trace_empty', 'trace_long', 'trace_control',
    'routing_contract', 'not_completed'])
def test_diagnostics_reuse_strict_completion_and_contract_validation(bom_store, fault):
    from mech_chatbot.evaluation.grounded_math_v2 import export_task_routing_diagnostics
    _, engine = bom_store
    result = execute(engine, graph_request(), Planner('invalid JSON'))
    if fault == 'terminal':
        result = replace(result, outcome='answered')
    elif fault.startswith('trace'):
        result = replace(result, trace_id={'trace_bool':True, 'trace_empty':'', 'trace_long':'a'*101,
                                        'trace_control':'trace\nprivate'}[fault])
    elif fault == 'routing_contract':
        result = replace(result, observed_task_routing={**result.observed_task_routing, 'contract':'legacy'})
    else:
        result = None
    with pytest.raises(ValueError):
        export_task_routing_diagnostics(result)


@pytest.mark.parametrize('trace', ['a'*100, 'trace:Đối chiếu'])
def test_export_preserves_bounded_printable_trace_without_inventing_format(bom_store, trace):
    from mech_chatbot.evaluation.grounded_math_v2 import export_task_routing_diagnostics
    _, engine = bom_store
    result = execute(engine, graph_request(), Planner('invalid JSON'))
    assert export_task_routing_diagnostics(replace(result, trace_id=trace))['trace_id'] == trace


def test_prevalidation_without_decoder_has_no_failure_code(bom_store):
    from mech_chatbot.evaluation.grounded_math_v2 import export_task_routing_diagnostics
    _, engine = bom_store
    result = execute(engine, replace(graph_request(), graph_query=None), Planner('unused'))
    receipt = export_task_routing_diagnostics(result)
    assert receipt['origin'] == 'prevalidation' and receipt['planner_count'] == 0
    assert receipt['failure_code'] is None


@pytest.mark.parametrize('path', ['/chat', '/chat/stream'])
def test_real_completed_private_receipt_does_not_reach_rest_or_sse(bom_store, rag_client, monkeypatch, path):
    from mech_chatbot.api import rag_server
    _, engine = bom_store
    with bind_repository_runtime(db_engine=engine):
        events = list(DefaultRagExecutor(provider_adapter=Planner('invalid private response')).run(graph_request(),
            RagInvocation('private-receipt', mode='evaluation', task_routing_contract=GRAPH_TASK_ROUTING)))
    completed = next(value for value in events if isinstance(value, RagCompleted))
    assert completed.observed_task_routing_failure_code == 'json_syntax'
    assert all('json_syntax' not in repr(value) for value in events)
    monkeypatch.setattr(rag_server, '_open_rag_events', lambda *_a, **_k:iter(events))
    response = rag_client.post(path, headers=SERVICE_HEADERS, json={'user_question':'Explain selected contents.'})
    assert response.status_code == 200
    assert 'failure_code' not in response.text and 'json_syntax' not in response.text
    assert 'invalid private response' not in response.text
    assert 'observed_task_routing' not in response.text


def test_private_code_propagates_without_math_capture(bom_store):
    from mech_chatbot.evaluation.grounded_math_v2 import export_task_routing_diagnostics
    _, engine = bom_store
    with bind_repository_runtime(db_engine=engine):
        result = collect_rag_events(DefaultRagExecutor(provider_adapter=Planner('invalid')).run(graph_request(),
            RagInvocation('uncaptured-routing', mode='test', task_routing_contract=GRAPH_TASK_ROUTING)))
    assert result.observed_math_results is None
    assert export_task_routing_diagnostics(result)['failure_code'] == 'json_syntax'


@pytest.mark.parametrize('code', [None, True, 1, [], {}, '', 'provider text', ' json_syntax'])
def test_export_rejects_forged_or_missing_planner_failure_code(bom_store, code):
    from mech_chatbot.evaluation.grounded_math_v2 import export_task_routing_diagnostics
    _, engine = bom_store
    result = execute(engine, graph_request(), Planner('invalid JSON'))
    with pytest.raises(ValueError):
        export_task_routing_diagnostics(replace(result, observed_task_routing_failure_code=code))


@pytest.mark.parametrize('mode', ['ready', 'fast_path', 'clarify', 'unsupported', 'overflow'])
def test_noninvalid_candidate_receipt_has_no_failure_code(bom_store, mode):
    from mech_chatbot.evaluation.grounded_math_v2 import export_task_routing_diagnostics
    _, engine = bom_store
    selected = graph_request('Show selected assembly relations.' if mode == 'fast_path' else graph_request().question)
    response = payload(selected.question)
    if mode in ('clarify', 'unsupported'):
        response = {**response, 'disposition':mode, 'tasks':[]}
    elif mode == 'overflow':
        response = {**payload(selected.question, tuple((str(n), 'graph_contains') for n in range(4))),
                    'disposition':'overflow'}
    result = execute(engine, selected, Planner(response))
    receipt = export_task_routing_diagnostics(result)
    assert receipt['disposition'] == ('ready' if mode == 'fast_path' else mode)
    assert receipt['failure_code'] is None
    with pytest.raises(ValueError):
        export_task_routing_diagnostics(replace(result, observed_task_routing_failure_code='json_syntax'))


@pytest.mark.parametrize('version', [None, 'semantic-fixed-task-routing-v2-draft'])
def test_legacy_execution_does_not_opt_in_to_diagnostics(bom_store, version):
    from mech_chatbot.evaluation.grounded_math_v2 import export_task_routing_diagnostics
    _, engine = bom_store
    result = execute(engine, graph_request(), Planner('invalid'), version)
    assert result.observed_task_routing_failure_code is None
    with pytest.raises(ValueError):
        export_task_routing_diagnostics(result)


def test_observer_runs_after_snapshot_commit_and_old_return_shape_is_exact(store, tmp_path):
    approved_pair(store)
    ref = candidate_case(tmp_path)
    case = json.loads((tmp_path / ref['path']).read_text())
    commits, receipts = [], []
    def committed(_conn):
        commits.append(True)
    def observe(value):
        from mech_chatbot.db.evidence_snapshot import evidence_snapshot_active
        assert commits and not evidence_snapshot_active()
        receipts.append(dict(value))
        value['trace_id'] = 'caller mutation'
    event.listen(store, 'commit', committed)
    try:
        with bind_repository_runtime(db_engine=store):
            result = run_fixed_case(ref, root=tmp_path, run_binding=BINDING, profile_loader=current_profile,
                executor=DefaultRagExecutor(provider_adapter=Planner(payload(case['question'],
                    (('comparison','compare_two_scopes'),)))), task_routing_contract=GRAPH_TASK_ROUTING,
                task_routing_diagnostics_observer=observe)
    finally:
        event.remove(store, 'commit', committed)
    assert len(receipts) == 1 and receipts[0]['trace_id'] == result['trace_id']
    assert result['calculation_count'] == 4
    assert 'failure_code' not in json.dumps(result) and 'task_routing_diagnostics' not in result


@pytest.mark.parametrize('callback,contract', [(True, GRAPH_TASK_ROUTING), ({}, GRAPH_TASK_ROUTING),
    (lambda _:None, None), (lambda _:None, 'semantic-fixed-task-routing-v2-draft')])
def test_invalid_observer_fails_before_artifact_or_actor_reads(tmp_path, callback, contract):
    def forbidden(*_a, **_k):
        pytest.fail('Invalid observer cannot read actor')
    with pytest.raises(ValueError):
        run_fixed_case({'invalid':True}, root=tmp_path, run_binding=BINDING, profile_loader=forbidden,
            task_routing_contract=contract, task_routing_diagnostics_observer=callback)


def test_observer_rejects_outer_snapshot_before_actor_or_provider(store, tmp_path):
    from time import monotonic
    from mech_chatbot.db.evidence_snapshot import evidence_read_snapshot
    ref, calls = candidate_case(tmp_path), []
    def forbidden(*_a, **_k):
        calls.append(True)
        pytest.fail('Observer cannot precede an outer transaction commit')
    with bind_repository_runtime(db_engine=store), evidence_read_snapshot(lambda _:None, monotonic()+120):
        with pytest.raises(ValueError):
            run_fixed_case(ref, root=tmp_path, run_binding=BINDING, profile_loader=forbidden,
                task_routing_contract=GRAPH_TASK_ROUTING, task_routing_diagnostics_observer=calls.append)
    assert calls == []


def test_observer_failure_propagates_without_retry(store, tmp_path):
    approved_pair(store)
    ref = candidate_case(tmp_path)
    calls = []
    def observe(value):
        calls.append(value)
        raise RuntimeError('Observer unavailable')
    with bind_repository_runtime(db_engine=store), pytest.raises(RuntimeError, match='Observer unavailable'):
        run_fixed_case(ref, root=tmp_path, run_binding=BINDING, profile_loader=current_profile,
            executor=DefaultRagExecutor(provider_adapter=Planner('invalid JSON')),
            task_routing_contract=GRAPH_TASK_ROUTING, task_routing_diagnostics_observer=observe)
    assert len(calls) == 1


@pytest.mark.parametrize('failure', ['cancel','deadline'])
def test_observer_cannot_publish_after_cancellation_or_deadline(store, tmp_path, monkeypatch, failure):
    from scripts.eval import run_fixed_math_v2 as runner
    from mech_chatbot.llm.external_ai import ExternalAICallCancelled
    ref, calls, cancel = candidate_case(tmp_path), [], threading.Event()
    clock = [runner.monotonic()]
    monkeypatch.setattr(runner, 'monotonic', lambda:clock[0])
    def observe(receipt):
        calls.append(receipt)
        if failure == 'cancel':
            cancel.set()
        else:
            clock[0] += 1000
    expected = ExternalAICallCancelled if failure == 'cancel' else TimeoutError
    with bind_repository_runtime(db_engine=store), pytest.raises(expected):
        run_fixed_case(ref, root=tmp_path, run_binding=BINDING, profile_loader=current_profile,
            executor=DefaultRagExecutor(provider_adapter=Planner('invalid JSON')), cancellation=cancel,
            task_routing_contract=GRAPH_TASK_ROUTING, task_routing_diagnostics_observer=observe)
    assert len(calls) == 1


def test_invalid_completion_never_invokes_observer(store, tmp_path):
    approved_pair(store)
    ref, calls = candidate_case(tmp_path), []
    class AlteredExecutor:
        def run(self, *args, **kwargs):
            for value in DefaultRagExecutor(provider_adapter=Planner('invalid')).run(*args, **kwargs):
                yield replace(value, observed_task_routing_failure_code='provider text') if isinstance(value, RagCompleted) else value
    with bind_repository_runtime(db_engine=store), pytest.raises(ValueError):
        run_fixed_case(ref, root=tmp_path, run_binding=BINDING, profile_loader=current_profile,
            executor=AlteredExecutor(), task_routing_contract=GRAPH_TASK_ROUTING,
            task_routing_diagnostics_observer=calls.append)
    assert calls == []


@pytest.mark.parametrize('failure', ['cancelled','deadline','commit','cancel_commit','deadline_commit'])
def test_interrupted_runner_never_invokes_observer(store, tmp_path, monkeypatch, failure):
    from scripts.eval import run_fixed_math_v2 as runner
    from mech_chatbot.llm.external_ai import ExternalAICallCancelled
    approved_pair(store)
    ref = candidate_case(tmp_path)
    cancel, calls = threading.Event(), []
    if failure == 'cancelled':
        cancel.set()
    clock = [runner.monotonic()]
    monkeypatch.setattr(runner, 'monotonic', lambda:clock[0])
    def on_commit(_conn):
        if failure == 'commit':
            raise RuntimeError('Snapshot commit failed')
        if failure == 'cancel_commit':
            cancel.set()
        if failure == 'deadline_commit':
            clock[0] += 1000
    commit_failure = failure in ('commit','cancel_commit','deadline_commit')
    if commit_failure:
        event.listen(store, 'commit', on_commit)
    expected = ExternalAICallCancelled if failure.startswith('cancel') else TimeoutError if failure.startswith('deadline') else RuntimeError
    try:
        with bind_repository_runtime(db_engine=store), pytest.raises(expected):
            run_fixed_case(ref, root=tmp_path, run_binding=BINDING, profile_loader=current_profile,
                executor=DefaultRagExecutor(provider_adapter=Planner('invalid JSON')), cancellation=cancel,
                budget_limits=RequestBudgetLimits(deadline_seconds=0 if failure == 'deadline' else 120),
                task_routing_contract=GRAPH_TASK_ROUTING, task_routing_diagnostics_observer=calls.append)
    finally:
        if commit_failure:
            event.remove(store, 'commit', on_commit)
    assert calls == []
