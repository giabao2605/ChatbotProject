from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from mech_chatbot.api.dependencies import csrf_profile, current_profile
from mech_chatbot.api.routers import rag_evidence
from .test_structured_evidence_repository import store
from .test_text_evidence_repository import PROFILE, caption_steps, text_store

pytestmark = pytest.mark.unit


def client(monkeypatch, mode='evaluation', roles=('reviewer',)):
    app = FastAPI()
    app.state.settings_snapshot = SimpleNamespace(RAG_EXECUTION_CONTEXT=mode)
    app.include_router(rag_evidence.router)
    profile = {'user_id': 7, 'roles': roles, 'username': 'reviewer'}
    app.dependency_overrides[current_profile] = lambda: profile
    app.dependency_overrides[csrf_profile] = lambda: profile
    return TestClient(app)


def test_v2_math_production_is_blocked_before_loading_scope(monkeypatch):
    monkeypatch.setattr(rag_evidence.service, 'compare_scopes', lambda *a: pytest.fail('must not load'))
    response = client(monkeypatch, 'production').post('/api/rag-evidence/evaluate/compare', json={'scope_a':'a', 'scope_b':'b'})
    assert response.status_code == 409


@pytest.mark.parametrize('excluded', [{'subtotal':'Already represented by detail rows'},
    {'subtotal':''}, {'subtotal':' '}, {'subtotal':False}, {'':'reason'}, {'subtotal':'x'*2001}])
def test_scope_review_exclusion_contract(monkeypatch, excluded):
    calls = []
    monkeypatch.setattr(rag_evidence.service, 'review_scope', lambda *a, **kw: calls.append(kw) or 'scope')
    body = {'expected_row_ids':['detail'], 'excluded_row_reasons':excluded,
            'rounding_scale':2, 'rounding_mode':'ROUND_HALF_UP',
            'definition':'Detail amounts', 'note':'Checked full export'}
    response = client(monkeypatch).post('/api/rag-evidence/scopes/scope/review', json=body)
    if excluded == {'subtotal':'Already represented by detail rows'}:
        assert response.status_code == 200
        assert calls[0]['excluded_row_reasons'] == excluded
    else:
        assert response.status_code == 422 and calls == []


def test_empty_scope_review_accepts_explicit_unit_and_currency_without_client_approval(monkeypatch):
    calls = []
    monkeypatch.setattr(rag_evidence.service, 'review_scope', lambda *a, **kw: calls.append((a, kw)) or 'empty')
    body = {'expected_row_ids': [], 'rounding_scale': 2, 'rounding_mode': 'ROUND_HALF_UP',
            'definition': 'Net value in checked export', 'note': 'Explicitly checked zero rows',
            'unit': 'VND', 'currency': 'VND'}
    api = client(monkeypatch)
    response = api.post('/api/rag-evidence/scopes/empty/review', json=body)
    assert response.status_code == 200 and response.json()['ok'] is True
    assert calls[0][1]['expected_row_ids'] == []
    assert calls[0][1]['unit'] == 'VND' and calls[0][1]['currency'] == 'VND'
    assert api.post('/api/rag-evidence/scopes/empty/review', json={**body, 'approved': True}).status_code == 422
    assert len(calls) == 1


@pytest.mark.parametrize('extra', [{'unit':' '}, {'unit':12}, {'unit':'a'*65}, {'currency':False}, {'currency':'a'*17}])
def test_scope_review_rejects_invalid_unit_or_currency_before_storage(monkeypatch, extra):
    monkeypatch.setattr(rag_evidence.service, 'review_scope', lambda *a, **kw: pytest.fail('invalid input reached review'))
    body = {'expected_row_ids': [], 'rounding_scale': 2, 'rounding_mode':'ROUND_HALF_UP',
            'definition':'Net value', 'note':'Checked empty export', 'unit':'VND', 'currency':'VND'}
    assert client(monkeypatch).post('/api/rag-evidence/scopes/empty/review', json={**body, **extra}).status_code==422


def test_ui_capabilities_are_evaluation_only_and_require_reviewer(monkeypatch):
    path = '/api/rag-evidence/evaluation-capabilities'
    assert client(monkeypatch, 'production').get(path).status_code == 409
    assert client(monkeypatch, roles=('viewer',)).get(path).status_code == 403
    value = client(monkeypatch).get(path).json()
    assert value == {'schema':'advanced-rag-v2-evaluation', 'accepted':False, 'offline_only':True,
                     'max_branches':3, 'actions':['contains','where_used','bom_rollup','scope_comparison','scope_total'],'text_query':True,'scope_total_query':True}


def test_graph_scope_picker_guard_pagination_and_safe_storage_error(monkeypatch):
    calls = []
    monkeypatch.setattr(rag_evidence.service, 'list_approved_graph_scopes',
                        lambda *a, **kw: calls.append((a, kw)) or {'items':[], 'has_more':False}, raising=False)
    path = '/api/rag-evidence/documents/17/graph-scopes'
    assert client(monkeypatch, 'production').get(path + '?site=factory').status_code == 409
    assert client(monkeypatch, roles=('viewer',)).get(path + '?site=factory').status_code == 403
    assert calls == []
    api = client(monkeypatch)
    response = api.get(path + '?site=factory&offset=1&limit=1')
    assert response.status_code == 200
    assert calls == [((17, {'user_id':7, 'roles':('reviewer',), 'username':'reviewer'}),
                     {'site':'factory', 'offset':1, 'limit':1, 'deadline_seconds':120})]
    for bad in ('', '?site=', '?site=+++', '?site=factory&limit=51', '?site=factory&offset=100001'):
        assert api.get(path + bad).status_code == 422
    assert api.get('/api/rag-evidence/documents/0/graph-scopes?site=factory').status_code == 422
    def unavailable(*a, **kw):
        from sqlalchemy.exc import OperationalError
        raise OperationalError('restricted table', {}, Exception('secret'))
    monkeypatch.setattr(rag_evidence.service, 'list_approved_graph_scopes', unavailable)
    response = api.get(path + '?site=factory')
    assert response.status_code == 503 and 'secret' not in response.text and 'restricted table' not in response.text


def test_scope_picker_guard_pagination_and_safe_storage_error(monkeypatch):
    calls = []
    monkeypatch.setattr(rag_evidence.service, 'list_approved_document_scopes',
                        lambda *a, **kw: calls.append((a, kw)) or {'items':[], 'has_more':False})
    path = '/api/rag-evidence/documents/17/approved-scopes'
    assert client(monkeypatch, 'production').get(path).status_code == 409
    assert client(monkeypatch, roles=('viewer',)).get(path).status_code == 403
    assert calls == []
    api = client(monkeypatch)
    assert api.get(path + '?offset=1&limit=1').status_code == 200
    assert calls[0][0][0] == 17 and calls[0][0][1]['user_id'] == 7
    assert calls[0][1] == {'offset':1, 'limit':1, 'deadline_seconds':120}
    for bad in ('?limit=51', '?offset=100001', '?offset=-1'):
        assert api.get(path + bad).status_code == 422
    assert api.get('/api/rag-evidence/documents/0/approved-scopes').status_code == 422
    def unavailable(*a, **kw):
        from sqlalchemy.exc import OperationalError
        raise OperationalError('restricted table', {}, Exception('secret'))
    monkeypatch.setattr(rag_evidence.service, 'list_approved_document_scopes', unavailable)
    response = api.get(path)
    assert response.status_code == 503 and 'secret' not in response.text and 'restricted' not in response.text


@pytest.mark.parametrize('capability,extra', [('graph', {}), ('rollup', {'order_quantity':'3','rounding_scope':'one'})])
def test_graph_and_rollup_are_blocked_in_production_before_source_access(monkeypatch, capability, extra):
    monkeypatch.setattr(rag_evidence.service, capability, lambda *a, **kw: pytest.fail('must not load'))
    response = client(monkeypatch, 'production').post('/api/rag-evidence/evaluate/'+capability,
        json={'root_key':'document:1','product_scope':'product','site':'factory', **extra})
    assert response.status_code == 409


@pytest.mark.parametrize('capability,extra', [('graph', {}), ('rollup', {'order_quantity':'3','rounding_scope':'one'})])
def test_v2_graph_endpoints_keep_offline_result_and_server_deadline(monkeypatch, capability, extra):
    calls = []
    monkeypatch.setattr(rag_evidence.service, capability, lambda **kw: calls.append(kw) or {'status':'partial'})
    response = client(monkeypatch).post('/api/rag-evidence/evaluate/'+capability,
        json={'root_key':'document:1','product_scope':'product','site':'factory', **extra})
    assert response.status_code == 200 and response.json()['accepted'] is False
    assert response.json()['offline_only'] is True
    assert calls[0]['deadline_seconds'] == 120
    assert calls[0]['profile']['user_id'] == 7


def test_rollup_rejects_reverse_direction_before_running_calculator(monkeypatch):
    monkeypatch.setattr(rag_evidence.service, 'rollup', lambda **kw: pytest.fail('must not calculate'))
    response = client(monkeypatch).post('/api/rag-evidence/evaluate/rollup', json={
        'root_key':'part:p','product_scope':'product','site':'factory','direction':'where_used',
        'order_quantity':'3','rounding_scope':'one'})
    assert response.status_code == 400


def test_math_uses_scope_identifiers_not_client_approval(monkeypatch):
    calls = []
    monkeypatch.setattr(rag_evidence.service, 'compare_scopes', lambda a,b,p: calls.append((a,b,p)) or {'status':'blocked'})
    api = client(monkeypatch)
    assert api.post('/api/rag-evidence/evaluate/compare', json={'scope_a':'a', 'scope_b':'b','approved':True}).status_code == 422
    response = api.post('/api/rag-evidence/evaluate/compare', json={'scope_a':'a', 'scope_b':'b'})
    assert response.status_code == 200 and calls[0][:2] == ('a','b')
    assert response.json()['accepted'] is False


def test_evaluation_timeout_returns_a_bounded_error_without_internal_details(monkeypatch):
    def timed_out(*args):raise TimeoutError('Private SQL operation detail')
    monkeypatch.setattr(rag_evidence.service,'compare_scopes',timed_out)
    api=TestClient(client(monkeypatch).app,raise_server_exceptions=False)
    response=api.post('/api/rag-evidence/evaluate/compare',json={'scope_a':'a','scope_b':'b'})
    assert response.status_code==504 and 'Private SQL' not in response.text


def test_review_requires_role_and_note(monkeypatch):
    api = client(monkeypatch, roles=('user',))
    response = api.post('/api/rag-evidence/facts/one/review', json={'action':'approve','note':'Checked source'})
    assert response.status_code == 403
    assert client(monkeypatch).post('/api/rag-evidence/facts/one/review', json={'action':'approve','note':''}).status_code == 422


def test_technical_text_review_uses_session_identity_and_strict_body(monkeypatch):
    calls = []
    monkeypatch.setattr(rag_evidence.service, 'review_text_fact',
                        lambda *args, **values: calls.append((args, values)) or {'review_status': 'approved'}, raising=False)
    api = client(monkeypatch)
    response = api.post('/api/rag-evidence/text-facts/material-1/review',
                        json={'action': 'approve', 'value_text': 'Inox 201', 'note': 'Checked original'})
    assert response.status_code == 200
    assert calls[0][0][0] == 'material-1' and calls[0][0][1]['user_id'] == 7
    assert api.post('/api/rag-evidence/text-facts/material-1/review',
                    json={'action': 'approve', 'note': 'Checked', 'reviewer_id': 99}).status_code == 422
    assert client(monkeypatch, roles=('user',)).post('/api/rag-evidence/text-facts/a/review',
                    json={'action': 'reject', 'note': 'Checked'}).status_code == 403


def test_drawing_note_assignment_preserves_omitted_versus_explicit_null(monkeypatch):
    calls = []
    monkeypatch.setattr(rag_evidence.service, 'review_text_fact',
                        lambda *args, **values: calls.append(values) or {'review_status': 'approved'})
    api = client(monkeypatch)
    for extra in ({}, {'linked_step_fact_id': None}, {'linked_step_fact_id': 'step-1'}):
        response = api.post('/api/rag-evidence/text-facts/note-1/review',
                            json={'action': 'approve', 'note': 'Checked leader', **extra})
        assert response.status_code == 200
    assert 'linked_step_fact_id' not in calls[0]
    assert calls[1]['linked_step_fact_id'] is None and calls[2]['linked_step_fact_id'] == 'step-1'
    for invalid in ('', ' ', 'x' * 451, 3, False):
        assert api.post('/api/rag-evidence/text-facts/note-1/review', json={
            'action': 'approve', 'note': 'Checked', 'linked_step_fact_id': invalid}).status_code == 422


def test_technical_fields_are_paginated_and_do_not_echo_restricted_metadata(monkeypatch):
    calls = []
    monkeypatch.setattr(rag_evidence.service, 'list_document_text_facts',
                        lambda *args, **values: calls.append((args, values)) or {'items': [], 'has_more': False}, raising=False)
    api = client(monkeypatch)
    assert api.get('/api/rag-evidence/documents/17/text-facts?offset=50&limit=50').status_code == 200
    assert calls[0][0][0] == 17 and calls[0][1] == {'offset': 50, 'limit': 50}
    def denied(*args, **kwargs):
        raise PermissionError('Restricted drawing secret-code')
    monkeypatch.setattr(rag_evidence.service, 'list_document_text_facts', denied)
    response = api.get('/api/rag-evidence/documents/18/text-facts')
    assert response.status_code == 404 and 'secret-code' not in response.text


def test_paginated_evidence_preserves_access_profile(monkeypatch):
    monkeypatch.setattr(rag_evidence.service, 'list_scope_facts', lambda scope,p,**kw: {'items': [], 'offset':kw['offset'],'limit':kw['limit'],'has_more':False})
    api = client(monkeypatch)
    assert api.get('/api/rag-evidence/scopes/a/facts?limit=501').status_code == 422
    assert api.get('/api/rag-evidence/scopes/a/facts?offset=50&limit=50').json()['offset'] == 50


def test_review_facts_receive_composed_protected_storage(monkeypatch):
    from mech_chatbot.application.protected_files import ProtectedFileResolver
    storage = object()
    resolver = ProtectedFileResolver(store=None, storage=storage, audit=None, strict_site_filter=True)
    api = client(monkeypatch)
    api.app.state.runtime = SimpleNamespace(protected_file_resolver=resolver)
    def load(scope, profile, **kwargs):
        assert kwargs['source_storage'] is storage
        assert profile['user_id'] == 7
        return {'items': [], 'offset': 0, 'has_more': False}
    monkeypatch.setattr(rag_evidence.service, 'list_scope_facts', load)
    assert api.get('/api/rag-evidence/scopes/a/facts').status_code == 200


def test_serving_citation_fact_pages_accept_readers_but_hide_inaccessible_sources(monkeypatch):
    calls = []
    monkeypatch.setattr(rag_evidence.service, 'list_approved_scope_facts',
                        lambda scope, profile, **kw: calls.append((scope,profile,kw)) or {'items':[], 'has_more':False})
    api = client(monkeypatch, 'production', roles=('viewer',))
    response = api.get('/api/rag-evidence/approved-scopes/a/facts?offset=50&limit=50')
    assert response.status_code == 200 and calls[0][2] == {'offset':50,'limit':50}
    assert calls[0][1]['roles'] == ('viewer',)
    assert api.get('/api/rag-evidence/approved-scopes/a/facts?limit=101').status_code == 422
    def denied(*args, **kwargs):
        raise PermissionError('private file secret-code')
    monkeypatch.setattr(rag_evidence.service, 'list_approved_scope_facts', denied)
    response = api.get('/api/rag-evidence/approved-scopes/a/facts')
    assert response.status_code == 404 and 'secret-code' not in response.text


def test_acl_denial_does_not_echo_scope_or_restricted_source(monkeypatch):
    def denied(*args, **kwargs):
        raise PermissionError('restricted file payroll.xlsx')
    monkeypatch.setattr(rag_evidence.service, 'list_scope_facts', denied)
    response = client(monkeypatch).get('/api/rag-evidence/scopes/private/facts')
    assert response.status_code == 404
    assert 'payroll' not in response.text


def test_storage_failure_has_safe_retryable_response(monkeypatch):
    from sqlalchemy.exc import OperationalError

    def unavailable(*args, **kwargs):
        raise OperationalError('SELECT private source', {}, Exception('private database'))

    monkeypatch.setattr(rag_evidence.service, 'list_scope_facts', unavailable)
    response = client(monkeypatch).get('/api/rag-evidence/scopes/one/facts')
    assert response.status_code == 503
    assert 'private' not in response.text


def test_graph_review_uses_verified_session_identity(monkeypatch):
    from mech_chatbot.services import graph_service
    calls = []
    monkeypatch.setattr(graph_service, 'review_graph_v2_record', lambda *a, **kw: calls.append((a,kw)) or {'ok':True})
    response = client(monkeypatch).post('/api/rag-evidence/graph/binding/12/review', json={
        'action':'approve','product_scope':'product-a','site':'HQ','note':'checked source'})
    assert response.status_code == 200
    assert calls[0][0] == ('binding',12,'approve')
    assert calls[0][1]['reviewer_id'] == 7
    assert client(monkeypatch).post('/api/rag-evidence/graph/binding/12/review', json={
        'action':'approve','product_scope':'product-a','site':'HQ','note':'checked','reviewer_id':99}).status_code == 422


def test_sequence_review_checks_role_and_identity_and_never_accepts_client_completeness(monkeypatch):
    calls = []
    monkeypatch.setattr(rag_evidence.service, 'review_assembly_sequence', lambda *args, **kw: calls.append((args,kw)) or {'status':'complete'})
    monkeypatch.setattr(rag_evidence.service, 'list_assembly_sequence', lambda *args: {'status':'unknown','snapshot_id':'s1'})
    api = client(monkeypatch)
    body = {'snapshot_id':'s1','expected_step_numbers':[1,2,3], 'definition':'Whole assembly', 'note':'Checked original'}
    assert api.get('/api/rag-evidence/documents/1/assembly-sequence').json()['snapshot_id'] == 's1'
    assert api.post('/api/rag-evidence/documents/1/assembly-sequence/review', json=body).status_code == 200
    assert calls[0][0][1]['user_id'] == 7 and calls[0][1]['expected_step_numbers'] == [1,2,3]
    assert api.post('/api/rag-evidence/documents/1/assembly-sequence/review', json={**body,'approved':True}).status_code == 422
    assert api.post('/api/rag-evidence/documents/1/assembly-sequence/review', json={**body,'expected_step_numbers':[True,2]}).status_code == 422
    assert client(monkeypatch,roles=('user',)).post('/api/rag-evidence/documents/1/assembly-sequence/review',json=body).status_code == 403


def test_sequence_evaluation_keeps_v2_contract_offline_and_blocks_production(monkeypatch):
    calls = []
    monkeypatch.setattr(rag_evidence.service, 'assembly_sequence', lambda *args,**kw: calls.append((args,kw)) or {'status':'complete'})
    assert client(monkeypatch,mode='production').post('/api/rag-evidence/evaluate/assembly-sequence/1').status_code == 409
    assert calls == []
    response = client(monkeypatch).post('/api/rag-evidence/evaluate/assembly-sequence/1')
    assert response.status_code == 200 and response.json()['offline_only'] is True and response.json()['accepted'] is False
    assert calls[0][0][0] == 1 and calls[0][1]['deadline_seconds'] == 120


@pytest.fixture
def assembly_api(monkeypatch, request):
    from sqlalchemy import create_engine
    from sqlalchemy.pool import StaticPool
    from mech_chatbot.config.repository_runtime import bind_repository_runtime
    from . import test_structured_evidence_repository as fixtures

    monkeypatch.setattr(fixtures, 'create_engine', lambda url: create_engine(
        url, poolclass=StaticPool, connect_args={'check_same_thread': False}))
    engine = request.getfixturevalue('text_store')
    app = client(monkeypatch).app
    app.dependency_overrides[current_profile] = lambda: PROFILE
    app.dependency_overrides[csrf_profile] = lambda: PROFILE
    with bind_repository_runtime(db_engine=engine), TestClient(app) as api:
        yield api, engine
    engine.dispose()


def approve_caption_fixture(api, engine):
    from mech_chatbot.db.repositories.text_evidence import save_extracted_text_facts

    facts = caption_steps()
    save_extracted_text_facts(facts, snapshot_id='s1', db_engine=engine)
    for fact in facts:
        response = api.post(f'/api/rag-evidence/text-facts/{fact.fact_id}/review', json={
            'action': 'approve', 'note': 'Synthetic caption fixture; not business acceptance'})
        assert response.status_code == 200
    response = api.post('/api/rag-evidence/documents/1/assembly-sequence/review', json={
        'snapshot_id': 's1', 'expected_step_numbers': [1, 2, 3, 4, 5, 6],
        'definition': 'Six fixture captions', 'note': 'Explicit fixture completeness; not human acceptance'})
    assert response.status_code == 200


def test_sequence_api_preserves_both_caption_sources_after_save_and_review(assembly_api):
    api, engine = assembly_api
    approve_caption_fixture(api, engine)
    response = api.post('/api/rag-evidence/evaluate/assembly-sequence/1')
    payload = response.json()
    assert response.status_code == 200 and payload['accepted'] is False and payload['offline_only'] is True
    result = payload['result']
    assert result['status'] == 'complete'
    assert [(edge['source_step'], edge['target_step']) for edge in result['edges']] == [
        (1, 2), (2, 3), (3, 4), (4, 5), (5, 6)]
    assert [source['row_key'] for source in result['edges'][-1]['sources']] == [
        'page-1:step-5', 'page-1:step-6']
    for edge in result['edges']:
        for source in edge['sources']:
            assert source['bbox'] == [17.123456789, 25.987654321, 103.123456789, 78.89056720000008]
            assert source['doc_id'] == 1 and source['version'] == 2 and source['page'] == 1
            assert source['source_hash'] == 'a' * 64 and source['snapshot_id'] == 's1'


def test_drawing_note_api_requires_choice_and_hides_unavailable_targets(assembly_api):
    from mech_chatbot.db.repositories.text_evidence import save_extracted_text_facts
    from .test_drawing_note_review import drawing_note

    api, engine = assembly_api
    approve_caption_fixture(api, engine)
    save_extracted_text_facts((drawing_note(),), snapshot_id='s1', db_engine=engine)
    path = '/api/rag-evidence/text-facts/note-1/review'
    body = {'action': 'approve', 'note': 'Checked source region'}
    assert api.post(path, json=body).status_code == 400
    response = api.post(path, json={**body, 'linked_step_fact_id': 'restricted-secret-step'})
    assert response.status_code == 404 and 'secret' not in response.text
    assert api.post('/api/rag-evidence/evaluate/assembly-sequence/1').json()['result']['manifest'] is None
    response = api.post(path, json={**body, 'linked_step_fact_id': None})
    assert response.status_code == 200 and response.json()['result']['linked_step_fact_id'] is None
    assert api.post('/api/rag-evidence/documents/1/assembly-sequence/review', json={
        'snapshot_id': 's1', 'expected_step_numbers': [1, 2, 3, 4, 5, 6],
        'definition': 'Whole drawing and note', 'note': 'Checked fixture'}).status_code == 200
    result = api.post('/api/rag-evidence/evaluate/assembly-sequence/1').json()['result']
    assert result['manifest']['notes'][0]['locator']['bbox'] == [1, 2, 3, 4]
    assert result['manifest']['notes'][0]['raw_value'] == 'Hàn đầy'


def test_bom_link_api_uses_server_evidence_and_redacts_changed_target(assembly_api):
    from .test_bom_callout_review import prepare
    api, engine = assembly_api
    prepare(engine)
    path = '/api/rag-evidence/text-facts/note-1/review'
    body = {'action':'approve', 'note':'Explicit fixture check', 'linked_bom_fact_id':'symbol-19'}
    options = api.get('/api/rag-evidence/documents/1/bom-reference-options?limit=1').json()
    assert options['items'][0]['fact_id'] == 'symbol-19' and not options['has_more']
    assert api.get('/api/rag-evidence/documents/1/bom-reference-options?limit=101').status_code == 422
    assert api.post(path, json={**body, 'linked_bom_fingerprint':'a'*64}).status_code == 422
    unavailable = api.post(path, json={**body, 'linked_bom_fact_id':'restricted-target'})
    assert unavailable.status_code == 404 and 'restricted-target' not in unavailable.text
    response = api.post(path, json=body)
    assert response.status_code == 200 and response.json()['result']['linked_bom_status'] == 'valid'
    assert 'linked_bom_fingerprint' not in response.text
    assert api.post('/api/rag-evidence/text-facts/symbol-19/review', json={
        'action':'approve', 'value_text':'P20', 'note':'Fixture correction'}).status_code == 200
    items = api.get('/api/rag-evidence/documents/1/text-facts').json()['items']
    note = next(item for item in items if item['fact_id'] == 'note-1')
    assert note['linked_bom_status'] == 'unknown' and note['linked_bom_fact_id'] is None
    api.app.dependency_overrides[current_profile] = lambda: {**PROFILE, 'roles':['reader']}
    assert api.get('/api/rag-evidence/documents/1/bom-reference-options').status_code == 403


@pytest.mark.parametrize('invalidated', [
    'pending', 'rejected', 'withdrawn', 'noncurrent', 'version', 'snapshot', 'hash', 'department', 'site', 'clearance'])
def test_sequence_api_hides_regions_when_either_source_is_not_servable(assembly_api, invalidated):
    from dataclasses import replace
    from sqlalchemy import text
    from mech_chatbot.db.repositories.text_evidence import save_extracted_text_facts

    api, engine = assembly_api
    if invalidated == 'pending':
        save_extracted_text_facts(caption_steps(), snapshot_id='s1', db_engine=engine)
    else:
        approve_caption_fixture(api, engine)
    if invalidated == 'rejected':
        response = api.post('/api/rag-evidence/text-facts/caption-6/review', json={
            'action': 'reject', 'note': 'Fixture source requires further review'})
        assert response.status_code == 200
    elif invalidated == 'snapshot':
        facts = tuple(replace(fact, fact_id=fact.fact_id + '-s2') for fact in caption_steps())
        save_extracted_text_facts(facts, snapshot_id='s2', db_engine=engine)
    elif invalidated in ('department', 'site', 'clearance'):
        changes = {'department': {'allowed_departments': ['HR']}, 'site': {'allowed_sites': ['other']},
                   'clearance': {'max_security_level': 'public'}}
        denied = {**PROFILE, **changes[invalidated]}
        api.app.dependency_overrides[current_profile] = lambda: denied
        api.app.dependency_overrides[csrf_profile] = lambda: denied
    elif invalidated != 'pending':
        updates = {'withdrawn': "UPDATE TaiLieu SET LifecycleStatus='withdrawn' WHERE DocID=1",
                   'noncurrent': "UPDATE TaiLieu SET IsCurrent=0 WHERE DocID=1",
                   'version': "UPDATE TaiLieu SET VersionNo=3 WHERE DocID=1",
                   'hash': "UPDATE StructuredSourceSnapshot SET SourceHash=:hash WHERE DocID=1"}
        with engine.begin() as conn:
            conn.execute(text(updates[invalidated]), {'hash': 'b' * 64})
    response = api.post('/api/rag-evidence/evaluate/assembly-sequence/1')
    assert response.status_code == 200
    result = response.json()['result']
    assert result['status'] == 'unknown' and result['edges'] == [] and result['manifest'] is None
    assert 'page-1:step-' not in response.text and 'caption original' not in response.text
    assert '17.123456789' not in response.text and '78.89056720000008' not in response.text
