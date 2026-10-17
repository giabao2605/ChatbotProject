"""Comparison-only authority never promotes the other draft or legacy paths."""
from dataclasses import replace
from datetime import datetime, timedelta, timezone
import json
from types import SimpleNamespace

import pytest
from sqlalchemy import create_engine

from mech_chatbot.config.repository_runtime import bind_repository_runtime
from mech_chatbot.db.repositories.structured_evidence import load_approved_scope, review_numeric_fact
from mech_chatbot.domain.structured_evidence import reviewed_scope_fingerprint
from mech_chatbot.governance.math_v2_activation import CONTRACT, MathV2Capability
from mech_chatbot.rag.execution import DefaultRagExecutor, RagInvocation, RagFailed, RagToken, RagCitation, RagCompleted, collect_rag_events
from .test_rag_scope_comparison import approved_pair, request
from .test_structured_evidence_repository import PROFILE, store


def capability(store):
    scopes = tuple(load_approved_scope(name, PROFILE, db_engine=store) for name in ('a', 'b'))
    return MathV2Capability(CONTRACT, 'compare_two_scopes',
        tuple((scope.scope_id, scope.snapshot_id, reviewed_scope_fingerprint(scope)) for scope in scopes),
        'a' * 40, 'b' * 64, 'c' * 64, (datetime.now(timezone.utc) + timedelta(minutes=10)).isoformat())


@pytest.fixture
def live_store(store, tmp_path):
    approved_pair(store)
    engine = create_engine('sqlite:///' + (tmp_path / 'live-comparison.sqlite').as_posix())
    with store.connect() as source, engine.connect() as target:
        source.connection.driver_connection.backup(target.connection.driver_connection)
    try:
        yield engine
    finally:
        engine.dispose()


def test_math_v2_trusted_live_comparison_records_four_steps_for_an_authorized_viewer(live_store):
    cap = capability(live_store)
    query = replace(request(), access=replace(request().access, roles=frozenset({'viewer'})))
    with bind_repository_runtime(db_engine=live_store):
        result = collect_rag_events(DefaultRagExecutor(math_v2_authorizer=lambda: cap).run(
            query, RagInvocation('controlled-comparison')))
    math = result.diagnostics['math_v2']
    assert result.outcome == 'answered' and math['status'] == 'complete'
    assert [step['display_value'] for step in math['steps']] == ['4.00', '8.00', '4.00', '100.00']
    assert result.diagnostics.budget.calculations == 4
    assert not math['offline_only'] and not math['accepted']
    assert math['activation_scope'] == 'controlled_demo' and math['contract'] == CONTRACT
    assert {card['version_no'] for card in result.citations} == {2}


def test_math_v2_production_without_server_authorizer_remains_blocked(live_store):
    with bind_repository_runtime(db_engine=live_store):
        events = list(DefaultRagExecutor().run(request(), RagInvocation('no-grant')))
    result = events[-1]
    assert isinstance(result, RagFailed) and isinstance(result.cause, ValueError)
    assert not any(isinstance(e, (RagToken, RagCitation, RagCompleted)) for e in events)
    assert result.diagnostics.budget.calculations == 0


@pytest.mark.parametrize('change', ['revoked', 'replaced_bundle', 'scope_pin', 'expired', 'actor_acl'])
def test_math_v2_live_pin_or_authority_change_never_emits_calculation(live_store, change):
    cap = capability(live_store)
    fresh = None if change == 'revoked' else replace(cap, bundle_sha256='d' * 64) if change == 'replaced_bundle' else cap
    calls = []
    def authorize():
        calls.append(True)
        return cap if len(calls) == 1 else fresh
    query = request()
    if change == 'scope_pin':
        review_numeric_fact('fact-a', PROFILE, action='approve', value_text='5', note='Changed reviewed fixture', db_engine=live_store)
    elif change == 'expired':
        cap = replace(cap, expires_at='2000-01-01T00:00:00+00:00')
        fresh = cap
    elif change == 'actor_acl':
        query = replace(query, access=replace(query.access, allowed_departments=frozenset({'other'})))
    with bind_repository_runtime(db_engine=live_store):
        events = list(DefaultRagExecutor(math_v2_authorizer=authorize).run(query, RagInvocation('changed')))
    assert isinstance(events[-1], RagFailed) and isinstance(events[-1].cause, PermissionError)
    assert not any(isinstance(e, (RagToken, RagCitation, RagCompleted)) for e in events)
    assert events[-1].diagnostics.budget.calculations == 0


def test_math_v2_next_request_refreshes_revoked_authority(live_store):
    grants = [capability(live_store)]
    executor = DefaultRagExecutor(math_v2_authorizer=lambda: grants[0])
    with bind_repository_runtime(db_engine=live_store):
        assert collect_rag_events(executor.run(request(), RagInvocation('first'))).outcome == 'answered'
        grants[0] = None
        events = list(executor.run(request(), RagInvocation('revoked')))
    assert isinstance(events[-1], RagFailed)
    assert not any(isinstance(e, (RagToken, RagCitation, RagCompleted)) for e in events)
    assert events[-1].diagnostics.budget.calculations == 0


@pytest.mark.parametrize('path', ['/chat', '/chat/stream'])
def test_math_v2_live_chat_uses_independent_server_grant_and_bundle_header(path, rag_client, live_store, monkeypatch):
    from mech_chatbot.auth import core
    from mech_chatbot.rag.execution_contracts import RagRuntimeContract
    cap = capability(live_store)
    monkeypatch.setattr(core, 'load_user_profile', lambda **kw: PROFILE)
    _replace_server_state(rag_client, database_runtime=SimpleNamespace(engine=live_store))
    _replace_runtime(rag_client, executor=DefaultRagExecutor(math_v2_authorizer=lambda: cap),
        math_v2_authorizer=lambda: cap, runtime_contract=RagRuntimeContract('production', False, 120))
    body = {'user_id': 7, 'user_question': 'So sánh hai kỳ', 'scope_comparison': {'scope_a': 'a', 'scope_b': 'b'}}
    assert rag_client.post(path, headers=SERVICE_HEADERS, json=body).status_code == 409
    headers = {**SERVICE_HEADERS, 'X-RAG-Activation-Bundle-SHA256': cap.bundle_sha256}
    result = rag_client.post(path, headers=headers, json=body)
    assert result.status_code == 200 and '100.00' in result.text
    if path == '/chat':
        math = result.json()['debug_info']['math_v2']
    else:
        events = [json.loads(block.splitlines()[1][6:]) for block in result.text.strip().split('\n\n')
                  if block.startswith('event: metadata')]
        math = events[0]['debug_info']['math_v2']
    assert math['domain_step_count'] == 4 and math['offline_only'] is False
    for field in ('graph_query', 'bom_rollup', 'query_plan', 'text_query'):
        mixed = {**body, field: True if field == 'text_query' else {}}
        assert rag_client.post(path, headers=headers, json=mixed).status_code in {409, 422}


from .test_rag_server_endpoints import rag_client, SERVICE_HEADERS, _replace_runtime, _replace_server_state


def test_signed_scope_grant_runs_actual_math_and_bundle_removal_rolls_back(grant, live_store):
    from mech_chatbot.governance.math_v2_activation import load_math_v2_capability
    from .test_math_v2_activation import reference
    root, _, gate, decision, issue, environment = grant
    pins = [{'scope_id':sid,'snapshot_id':snapshot,'scope_hash':digest}
            for sid,snapshot,digest in capability(live_store).scope_pins]
    gate = {**gate,'scope_pins':pins}
    decision = {**decision,'evidence':[reference(root,'gate.json',gate)]}
    bundle,digest = issue(scope_pins=pins,decision=reference(root,'decision.json',decision))
    env = environment(bundle,digest)
    executor = DefaultRagExecutor(math_v2_authorizer=lambda:load_math_v2_capability(env,root=root,current_commit='candidate'))
    with bind_repository_runtime(db_engine=live_store):
        result = collect_rag_events(executor.run(request(),RagInvocation('signed')))
        assert result.diagnostics.budget.calculations == 4 and '100.00' in result.answer
        (root/'bundle.json').unlink()
        events = list(executor.run(request(),RagInvocation('rollback')))
    assert isinstance(events[-1],RagFailed)
    assert not any(isinstance(e,(RagToken,RagCitation,RagCompleted)) for e in events)


def test_browser_capability_and_scope_picker_only_expose_current_granted_scopes(monkeypatch, live_store):
    from .test_rag_evidence_api import client
    from mech_chatbot.api.dependencies import current_profile
    cap = capability(live_store)
    with client(monkeypatch,mode='production') as app_client:
        app_client.app.state.runtime = SimpleNamespace(math_v2_authorizer=lambda:cap)
        app_client.app.dependency_overrides[current_profile] = lambda:PROFILE
        with bind_repository_runtime(db_engine=live_store):
            metadata = app_client.get('/api/rag-evidence/evaluation-capabilities')
            page = app_client.get('/api/rag-evidence/documents/1/approved-scopes')
        assert metadata.status_code == 200 and metadata.json()['actions'] == ['scope_comparison']
        assert metadata.json()['offline_only'] is False
        assert page.status_code == 200 and {row['scope_id'] for row in page.json()['items']} == {'a','b'}
        limited = replace(cap,scope_pins=(cap.scope_pins[0],('not-this-document','other','d'*64)))
        app_client.app.state.runtime = SimpleNamespace(math_v2_authorizer=lambda:limited)
        with bind_repository_runtime(db_engine=live_store):
            page = app_client.get('/api/rag-evidence/documents/1/approved-scopes')
        assert [row['scope_id'] for row in page.json()['items']] == ['a']
        app_client.app.state.runtime = SimpleNamespace(math_v2_authorizer=lambda:None)
        assert app_client.get('/api/rag-evidence/evaluation-capabilities').status_code == 409


from .test_math_v2_activation import grant


def test_v2_bundle_does_not_enable_legacy_math_and_authorizer_captures_settings(tmp_path, monkeypatch):
    from mech_chatbot.config.settings import Settings,RagProcessSettings
    from mech_chatbot.composition.rag_runtime import _retrieval_feature_settings
    from mech_chatbot.governance import math_v2_activation as authority
    path = tmp_path/'bundle.json'
    path.write_text('{"schema":"rag-activation-bundle-v2"}')
    settings = Settings(RAG_GROUNDED_MATH_ENABLED=True,RAG_ACTIVATION_BUNDLE_PATH=str(path))
    assert not _retrieval_feature_settings(settings,RagProcessSettings.from_settings(settings))['grounded_math_enabled']
    assert _retrieval_feature_settings(Settings(RAG_GROUNDED_MATH_ENABLED=True),
        RagProcessSettings.from_settings(settings))['grounded_math_enabled']
    calls=[]
    monkeypatch.setattr(authority,'load_math_v2_capability',lambda env,**kw:calls.append((env,kw)) or None)
    authorizer=authority.math_v2_authorizer_for_settings(settings)
    authorizer();authorizer()
    assert len(calls)==2 and all(env['RAG_ACTIVATION_BUNDLE_PATH']==str(path) for env,_ in calls)
    assert all(not any('KEY' in key or 'PASSWORD' in key for key in env) for env,_ in calls)


def test_live_comparison_capability_cannot_authorize_other_draft_paths(live_store):
    from mech_chatbot.api.evidence_contracts import require_comparison_context
    from fastapi import HTTPException
    cap = capability(live_store)
    with pytest.raises(HTTPException) as failure:
        require_comparison_context('production',PROFILE,comparison_only=False,authorizer=lambda:cap)
    assert failure.value.status_code==409
    with bind_repository_runtime(db_engine=live_store):
        query=replace(request(),scope_comparison=None,text_query=True)
        events=list(DefaultRagExecutor(math_v2_authorizer=lambda:cap).run(query,RagInvocation('text-denied')))
    assert isinstance(events[-1],RagFailed) and events[-1].diagnostics.budget.calculations==0
    assert not any(isinstance(e,(RagToken,RagCitation,RagCompleted)) for e in events)


def test_app_runtime_forwards_its_own_fresh_bundle_identity(monkeypatch, live_store):
    from mech_chatbot.api import app_server
    from mech_chatbot.config.settings import Settings,AppProcessSettings
    from mech_chatbot.governance import math_v2_activation as authority
    grants=[capability(live_store)]
    monkeypatch.setattr(authority,'math_v2_authorizer_for_settings',lambda _:lambda:grants[0])
    captured={}
    monkeypatch.setattr(app_server,'build_default_app_runtime',lambda *a,**kw:captured.update(kw))
    settings=Settings()
    app_server._build_default_app_runtime(AppProcessSettings.from_settings(settings),SimpleNamespace(),
        post=lambda *a,**kw:None,settings_snapshot=settings)
    assert captured['headers']()['X-RAG-Activation-Bundle-SHA256']==grants[0].bundle_sha256
    assert captured['math_v2_authorizer']() is grants[0]
    grants[0]=None
    assert 'X-RAG-Activation-Bundle-SHA256' not in captured['headers']()
