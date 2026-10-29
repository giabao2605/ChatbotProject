"""The shared runtime selects one independently granted Math template per request."""
from dataclasses import replace
from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from mech_chatbot.config.repository_runtime import bind_repository_runtime
from mech_chatbot.rag.execution import DefaultRagExecutor, RagInvocation, collect_rag_events
from .test_math_v2_live_comparison import capability as comparison_capability, live_store
from .test_scope_total_production import capability as total_capability
from .test_rag_scope_comparison import request as comparison_request
from .test_rag_scope_total import selected as total_request
from .test_structured_evidence_repository import PROFILE, store
from .test_math_templates_activation import grant


def capability(store):
    from mech_chatbot.governance.math_templates_activation import MathTemplatesCapability
    comparison, total = replace(comparison_capability(store), source_commit='candidate'), total_capability(store)
    return MathTemplatesCapability(comparison=comparison, total=total, source_commit='candidate',
        bundle_sha256='b'*64, snapshot_fingerprint='c'*64, expires_at=total.expires_at)


def test_one_runtime_keeps_both_templates_and_refreshes_the_selected_child(live_store):
    cap = capability(live_store)
    with bind_repository_runtime(db_engine=live_store):
        executor = DefaultRagExecutor(math_v2_authorizer=lambda: cap)
        total = collect_rag_events(executor.run(total_request(), RagInvocation('total')))
        comparison = collect_rag_events(executor.run(comparison_request(), RagInvocation('comparison')))
    assert total.diagnostics.budget.calculations == 1 and '4.00 VND' in total.answer
    assert comparison.diagnostics.budget.calculations == 4 and '100.00' in comparison.answer
    assert total.diagnostics['math_v2']['contract'] == cap.total.contract
    assert comparison.diagnostics['math_v2']['contract'] == cap.comparison.contract
    assert not total.diagnostics['math_v2']['offline_only'] and not comparison.diagnostics['math_v2']['offline_only']


@pytest.mark.parametrize('action', ['total', 'comparison'])
def test_removing_the_parent_grant_during_source_read_blocks_either_template(live_store, action):
    cap = capability(live_store)
    grants = iter((cap, None))
    executor = DefaultRagExecutor(math_v2_authorizer=lambda: next(grants))
    with bind_repository_runtime(db_engine=live_store), pytest.raises(PermissionError, match='source authority unavailable'):
        collect_rag_events(executor.run(total_request() if action == 'total' else comparison_request(), RagInvocation('revoked')))


def test_total_cannot_borrow_a_comparison_scope_pin(live_store):
    cap = capability(live_store)
    with bind_repository_runtime(db_engine=live_store), pytest.raises(PermissionError, match='Scope total source authority unavailable'):
        collect_rag_events(DefaultRagExecutor(math_v2_authorizer=lambda: cap).run(total_request('b'), RagInvocation('wrong-total-scope')))


def test_parent_grant_opens_two_standalone_http_guards_but_not_query(live_store):
    from mech_chatbot.api.evidence_contracts import require_comparison_context, require_scope_total_context
    cap = capability(live_store)
    assert require_scope_total_context('production', PROFILE, authorizer=lambda: cap) == 'production'
    assert require_comparison_context('production', PROFILE, comparison_only=True, authorizer=lambda: cap) == 'production'
    with pytest.raises(HTTPException) as exc:
        require_comparison_context('production', PROFILE, comparison_only=False, authorizer=lambda: cap)
    assert exc.value.status_code == 409


def test_browser_lists_two_standalone_templates_and_only_the_union_of_granted_scopes(live_store, monkeypatch):
    from mech_chatbot.api.dependencies import current_profile
    from .test_rag_evidence_api import client
    cap = capability(live_store)
    with client(monkeypatch, mode='production') as api:
        api.app.state.runtime = SimpleNamespace(math_v2_authorizer=lambda: cap)
        api.app.dependency_overrides[current_profile] = lambda: PROFILE
        with bind_repository_runtime(db_engine=live_store):
            response = api.get('/api/rag-evidence/evaluation-capabilities')
            scopes = api.get('/api/rag-evidence/documents/1/approved-scopes')
        assert response.status_code == 200
        assert response.json() == {'schema':'advanced-rag-v2-controlled-demo', 'contract':cap.contract,
            'accepted':False, 'offline_only':False, 'max_branches':1,
            'actions':['scope_comparison','scope_total'], 'text_query':False}
        assert scopes.status_code == 200 and {item['scope_id'] for item in scopes.json()['items']} == {'a','b'}
        with bind_repository_runtime(db_engine=live_store):
            totals = api.get('/api/rag-evidence/documents/1/approved-scopes?template=scope_total')
            comparisons = api.get('/api/rag-evidence/documents/1/approved-scopes?template=scope_comparison')
        assert [item['scope_id'] for item in totals.json()['items']] == ['a']
        assert {item['scope_id'] for item in comparisons.json()['items']} == {'a','b'}
        assert api.get('/api/rag-evidence/documents/1/approved-scopes?template=bom_rollup').status_code == 422


@pytest.mark.parametrize('action', ['total', 'comparison'])
def test_composite_header_matches_the_parent_identity(live_store, action):
    from mech_chatbot.api.rag_server import ChatRequest, _scope_comparison_mode
    cap = capability(live_store)
    runtime = SimpleNamespace(runtime_contract=SimpleNamespace(execution_context='production'), math_v2_authorizer=lambda: cap)
    selection = {'scope_total':{'scope_id':'a'}} if action == 'total' else {'scope_comparison':{'scope_a':'a','scope_b':'b'}}
    req = ChatRequest(user_question='Nguồn đã chọn', **selection)
    assert _scope_comparison_mode(req, PROFILE, runtime,
        headers={'X-RAG-Activation-Bundle-SHA256':cap.bundle_sha256}) == 'production'
    with pytest.raises(HTTPException) as exc:
        _scope_comparison_mode(req, PROFILE, runtime, headers={'X-RAG-Activation-Bundle-SHA256':'wrong-child'})
    assert exc.value.status_code == 409


def test_signed_parent_runs_both_real_sql_templates_and_revocation_stops_both(grant, live_store):
    import json
    from mech_chatbot.governance.math_v2_activation import load_math_v2_capability
    from .test_math_v2_activation import reference
    root, issue, change_child, _ = grant
    for name, cap in (('comparison', comparison_capability(live_store)), ('total', total_capability(live_store))):
        pins = [dict(zip(('scope_id','snapshot_id','scope_hash'), pin)) for pin in cap.scope_pins]
        gate = {**json.loads((root/f'{name}-gate.json').read_text()), 'scope_pins':pins}
        decision = {**json.loads((root/f'{name}-decision.json').read_text()),
                    'evidence':[reference(root, f'{name}-gate.json', gate)]}
        change_child(name, ledger_changes={'scope_pins':pins,
            'decision':reference(root, f'{name}-decision.json', decision)})
    _, _, env = issue()
    executor = DefaultRagExecutor(math_v2_authorizer=lambda: load_math_v2_capability(env, root=root, current_commit='candidate'))
    with bind_repository_runtime(db_engine=live_store):
        for query, count in ((total_request(), 1), (comparison_request(), 4)):
            result = collect_rag_events(executor.run(query, RagInvocation('signed-both')))
            assert result.outcome == 'answered' and result.diagnostics.budget.calculations == count
        env['RAG_ACTIVATION_BUNDLE_SHA256'] = '0'*64
        for query in (total_request(), comparison_request()):
            with pytest.raises(ValueError, match='authority'):
                collect_rag_events(executor.run(query, RagInvocation('revoked-parent')))
