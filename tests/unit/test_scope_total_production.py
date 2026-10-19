"""Synthetic authority at the real SQL/event boundary; never live approval."""
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from mech_chatbot.config.repository_runtime import bind_repository_runtime
from mech_chatbot.db.repositories.structured_evidence import load_approved_scope
from mech_chatbot.domain.structured_evidence import reviewed_scope_fingerprint
from mech_chatbot.rag.execution import DefaultRagExecutor, RagInvocation, collect_rag_events
from .test_rag_scope_comparison import approved_pair
from .test_rag_scope_total import selected
from .test_structured_evidence_repository import PROFILE, store


def capability(store):
    from mech_chatbot.governance.scope_total_activation import CONTRACT, ScopeTotalCapability
    scope = load_approved_scope('a', PROFILE, db_engine=store)
    return ScopeTotalCapability(CONTRACT, 'sum_reviewed_scope',
        ((scope.scope_id, scope.snapshot_id, reviewed_scope_fingerprint(scope)),),
        'candidate', 'b'*64, 'c'*64, (datetime.now(timezone.utc)+timedelta(hours=1)).isoformat())


def test_authorized_total_keeps_one_step_history_and_its_own_completion_event(store, monkeypatch):
    from mech_chatbot.application.chat_result_history import serialize_history_result, restore_history_result
    from mech_chatbot.config import logging
    approved_pair(store)
    cap = capability(store)
    events = []
    monkeypatch.setattr(logging, 'log_trace', lambda event, trace, **fields: events.append((event, fields)))
    query = selected()
    query = replace(query, access=replace(query.access, roles=frozenset({'viewer'})))
    with bind_repository_runtime(db_engine=store):
        result = collect_rag_events(DefaultRagExecutor(math_v2_authorizer=lambda: cap).run(query, RagInvocation('live-total')))
    assert result.outcome == 'answered' and '4.00 VND' in result.answer
    assert result.diagnostics.budget.calculations == 1
    assert result.diagnostics.budget.planners == result.diagnostics.budget.final_generations == 0
    math = result.diagnostics['math_v2']
    assert math['contract'] == cap.contract and math['offline_only'] is False
    assert math['accepted'] is False and math['activation_scope'] == 'controlled_demo'
    payload = serialize_history_result({'math': math}, result.citations)
    assert restore_history_result(payload, result.citations)['math']['steps'][0]['value'] == '4'
    completions = [fields for event, fields in events if event == 'math_scope_total_completed']
    assert len(completions) == 1 and completions[0]['calculation_result_status'] == 'valid'
    assert not any(event in {'math_v2_comparison_completed', 'pilot_request_evidence'} for event, _ in events)
    assert not any(key in completions[0] for key in ('question', 'answer', 'steps', 'scope_pins', 'scopes', 'value'))


@pytest.mark.parametrize('fault', ['revoked', 'scope', 'rights', 'revision', 'expired'])
def test_total_rechecks_authority_and_source_inside_snapshot(store, fault):
    from sqlalchemy import text
    approved_pair(store)
    cap = capability(store)
    query = selected('b' if fault == 'scope' else 'a')
    if fault == 'rights':
        query = replace(query, access=replace(query.access, allowed_sites=frozenset({'other'})))
    if fault == 'revision':
        with store.begin() as conn:
            conn.execute(text('UPDATE TaiLieu SET VersionNo=3'))
    if fault == 'expired':
        cap = replace(cap, expires_at='2020-01-01T00:00:00Z')
    calls = iter((cap, None)) if fault == 'revoked' else None
    authorizer = (lambda: next(calls)) if calls else lambda: cap
    with bind_repository_runtime(db_engine=store), pytest.raises(PermissionError, match='Scope total source authority unavailable'):
        collect_rag_events(DefaultRagExecutor(math_v2_authorizer=authorizer).run(query, RagInvocation('denied-total')))


def test_scope_total_http_guard_is_distinct_from_comparison(store):
    from mech_chatbot.api.evidence_contracts import require_scope_total_context, require_comparison_context
    approved_pair(store)
    cap = capability(store)
    assert require_scope_total_context('production', {}, authorizer=lambda: cap) == 'production'
    with pytest.raises(HTTPException) as exc:
        require_comparison_context('production', {}, comparison_only=True, authorizer=lambda: cap)
    assert exc.value.status_code == 409
    with pytest.raises(HTTPException) as exc:
        require_scope_total_context('pilot_replay', {}, authorizer=lambda: cap)
    assert exc.value.status_code == 409


def test_scope_total_http_modes_bind_grant_header_and_do_not_open_query(store):
    from mech_chatbot.api.rag_server import ChatRequest, _scope_comparison_mode
    approved_pair(store)
    cap = capability(store)
    runtime = SimpleNamespace(runtime_contract=SimpleNamespace(execution_context='production'), math_v2_authorizer=lambda: cap)
    req = ChatRequest(user_question='Tổng phạm vi đã chọn', scope_total={'scope_id':'a'})
    assert _scope_comparison_mode(req, {}, runtime, headers={'X-RAG-Activation-Bundle-SHA256':cap.bundle_sha256}) == 'production'
    with pytest.raises(HTTPException) as exc:
        _scope_comparison_mode(req, {}, runtime, headers={})
    assert exc.value.status_code == 409
    req = ChatRequest(user_question='Tổng', query_plan={'branches':[{'branch_id':'sum','question':'Tổng','scope_total':{'scope_id':'a'}}]})
    with pytest.raises(HTTPException) as exc:
        _scope_comparison_mode(req, {}, runtime)
    assert exc.value.status_code == 409


from .test_math_v2_live_comparison import live_store
from .test_scope_total_activation import grant, reference
from .test_rag_server_endpoints import rag_client, SERVICE_HEADERS, _replace_runtime, _replace_server_state


@pytest.mark.parametrize('path', ['/chat', '/chat/stream'])
def test_granted_scope_total_rest_and_sse_use_the_real_executor(path, rag_client, live_store, monkeypatch):
    from mech_chatbot.auth import core
    from mech_chatbot.rag.execution_contracts import RagRuntimeContract
    cap = capability(live_store)
    monkeypatch.setattr(core, 'load_user_profile', lambda **kw: PROFILE)
    _replace_server_state(rag_client, database_runtime=SimpleNamespace(engine=live_store))
    _replace_runtime(rag_client, executor=DefaultRagExecutor(math_v2_authorizer=lambda: cap),
        math_v2_authorizer=lambda: cap, runtime_contract=RagRuntimeContract('production', False, 120))
    body = {'user_id':7, 'user_question':'Tổng phạm vi đã chọn', 'scope_total':{'scope_id':'a'}}
    assert rag_client.post(path, headers=SERVICE_HEADERS, json=body).status_code == 409
    headers = {**SERVICE_HEADERS, 'X-RAG-Activation-Bundle-SHA256':cap.bundle_sha256}
    response = rag_client.post(path, headers=headers, json=body)
    assert response.status_code == 200 and '4.00 VND' in response.text
    assert cap.contract in response.text and 'grounded-calculation-scope-total-v1' in response.text
    assert rag_client.post(path, headers=headers, json={**body,'text_query':True}).status_code == 422


def test_signed_total_bundle_executes_only_its_scope_and_revocation_blocks_next_request(grant, live_store):
    from mech_chatbot.governance.math_v2_activation import load_math_v2_capability
    root, _, gate, decision, issue, environment = grant
    pins = [dict(zip(('scope_id','snapshot_id','scope_hash'), pin)) for pin in capability(live_store).scope_pins]
    gate = {**gate, 'scope_pins':pins}
    decision = {**decision, 'evidence':[reference(root, 'gate.json', gate)]}
    bundle, digest = issue(scope_pins=pins, decision=reference(root, 'decision.json', decision))
    env = environment(bundle, digest)
    executor = DefaultRagExecutor(math_v2_authorizer=lambda: load_math_v2_capability(env, root=root, current_commit='candidate'))
    with bind_repository_runtime(db_engine=live_store):
        result = collect_rag_events(executor.run(selected(), RagInvocation('signed-total')))
        assert result.outcome == 'answered' and result.diagnostics.budget.calculations == 1
        env['RAG_ACTIVATION_BUNDLE_PATH'] = ''
        with pytest.raises(ValueError, match='Scope total'):
            collect_rag_events(executor.run(selected(), RagInvocation('revoked-total')))


def test_total_capabilities_and_picker_show_only_the_current_granted_scope(live_store, monkeypatch):
    from .test_rag_evidence_api import client
    from mech_chatbot.api.dependencies import current_profile
    cap = capability(live_store)
    with client(monkeypatch, mode='production') as api:
        api.app.state.runtime = SimpleNamespace(math_v2_authorizer=lambda: cap)
        api.app.dependency_overrides[current_profile] = lambda: PROFILE
        with bind_repository_runtime(db_engine=live_store):
            metadata = api.get('/api/rag-evidence/evaluation-capabilities')
            page = api.get('/api/rag-evidence/documents/1/approved-scopes')
        assert metadata.status_code == 200 and metadata.json()['actions'] == ['scope_total']
        assert metadata.json()['contract'] == cap.contract and metadata.json()['offline_only'] is False
        assert page.status_code == 200 and [item['scope_id'] for item in page.json()['items']] == ['a']


@pytest.mark.parametrize('failure', ['cancelled', 'deadline'])
def test_authorized_total_keeps_cancellation_and_deadline_without_a_completion_receipt(store, monkeypatch, failure):
    import threading
    from mech_chatbot.config import logging
    from mech_chatbot.rag.execution import RequestBudgetLimits, RagCancelled, RagFailed, RagToken, RagCitation, RagCompleted
    approved_pair(store)
    cap = capability(store)
    calls = []
    monkeypatch.setattr(logging, 'log_trace', lambda event, *args, **fields: calls.append(event))
    cancel = threading.Event()
    if failure == 'cancelled':
        cancel.set()
    executor = DefaultRagExecutor(math_v2_authorizer=lambda: cap,
        budget_limits=RequestBudgetLimits(deadline_seconds=0 if failure == 'deadline' else 120))
    with bind_repository_runtime(db_engine=store):
        events = list(executor.run(selected(), RagInvocation('stopped-total'), cancellation=cancel))
    result = events[-1]
    if failure == 'cancelled':
        assert isinstance(result, RagCancelled)
    else:
        assert isinstance(result, RagFailed) and result.code == 'TimeoutError'
        assert result.diagnostics.budget.calculations == 0
    assert not any(isinstance(event, (RagToken, RagCitation, RagCompleted)) for event in events)
    assert 'math_scope_total_completed' not in calls
