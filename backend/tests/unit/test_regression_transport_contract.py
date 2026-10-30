"""Exercise regression through the HTTP/SSE adapter with isolated SQL evidence."""
from types import SimpleNamespace

import pytest
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient
from sqlalchemy import text

from mech_chatbot.adapters.chat_runtime import HttpRagStreamAdapter
from mech_chatbot.api import rag_server
from mech_chatbot.api import dependencies
from mech_chatbot.api.routers import operations
from mech_chatbot.application.chat_turn import RagDelta
from mech_chatbot.db.repositories import feedback
from mech_chatbot.rag import regression
from .test_chat_runtime_adapters import Response
from .test_feedback_scoped_flow import scoped_db, evidence_docs

pytestmark = pytest.mark.unit


@pytest.fixture
def review_client(scoped_db, monkeypatch):
    _, actor = scoped_db
    app = FastAPI()
    app.include_router(operations.router)
    app.dependency_overrides[dependencies.current_profile] = lambda: actor
    app.dependency_overrides[dependencies.csrf_profile] = lambda: actor
    monkeypatch.setattr(operations.audit_service, 'write_audit_log', lambda **_: None)
    with TestClient(app) as client:
        yield client


@pytest.mark.parametrize('terminal,status,passed', [
    (('cancelled', {}), 'cancelled', 0),
    (('error', {'message': 'private upstream detail'}), 'failed', 0),
    (None, 'incomplete', 0),
    (('done', {'outcome': 'unknown'}), 'incomplete', 0),
    (('done', {'outcome': 'refused'}), 'completed', 0),
    (('done', {'outcome': 'answered'}), 'completed', 1),
])
def test_sse_terminal_controls_persistence_and_scoring(scoped_db, terminal, status, passed):
    engine, actor = scoped_db
    feedback.add_regression_question('Q', expected_keywords='approved', department='ENG', site='HN', actor=actor)
    diagnostics = {'retrieved_docs': evidence_docs(engine, actor), 'evidence_complete': True}
    events = [('metadata', {'debug_info': diagnostics}), ('token', {'text': 'approved'})]
    if terminal:
        events.append(terminal)
    response = Response(events=events)
    requests = []
    adapter = HttpRagStreamAdapter(post=lambda _, **kw: requests.append(kw['json']) or response,
                                   base_url='http://offline.invalid', headers={})
    summary = regression.run_regression_batch(actor=actor, rag_stream=adapter)
    row, = feedback.get_regression_runs(batch_id=summary['batch_id'], actor=actor)
    assert row['status'] == status
    assert summary['passed'] == passed
    assert row['answer'] == ('approved' if status == 'completed' else '')
    assert 'private upstream detail' not in str(row)
    assert response.closed
    assert requests[0]['user_id'] == actor['user_id']
    assert requests[0]['regression_scope']['department'] == 'ENG'
    assert 'roles' not in requests[0]


def test_interrupted_iterator_discards_partial_answer(scoped_db):
    _, actor = scoped_db
    feedback.add_regression_question('Q', expected_keywords='approved', department='ENG', site='HN', actor=actor)
    def interrupted(*_):
        yield RagDelta('approved but unfinished')
        raise RuntimeError('upstream connection interrupted')
    summary = regression.run_regression_batch(actor=actor, rag_stream=SimpleNamespace(stream=interrupted))
    row, = feedback.get_regression_runs(batch_id=summary['batch_id'], actor=actor)
    assert row['status'] == 'failed'
    assert row['answer'] == '' and not row['passed']


@pytest.mark.parametrize('changed_event', ['duplicate_metadata', 'changed_final_evidence'])
def test_sse_evidence_cannot_change_after_preparation(scoped_db, changed_event):
    engine, actor = scoped_db
    feedback.add_regression_question('Q', expected_keywords='approved', department='ENG', site='HN', actor=actor)
    initial = {'retrieved_docs': evidence_docs(engine, actor), 'evidence_complete': True}
    final = {'retrieved_docs': evidence_docs(engine, actor, (2,)), 'evidence_complete': True}
    events = [('metadata', {'debug_info': initial}), ('token', {'text': 'approved'})]
    events.append(('metadata', {'debug_info': initial}) if changed_event == 'duplicate_metadata'
                  else ('done', {'outcome': 'answered', 'debug_info': final}))
    response = Response(events=events)
    adapter = HttpRagStreamAdapter(post=lambda *_args, **_kwargs: response,
                                   base_url='http://offline.invalid', headers={})
    with pytest.raises(PermissionError):
        regression.run_regression_batch(actor=actor, rag_stream=adapter)
    with engine.connect() as conn:
        assert conn.execute(text('SELECT COUNT(*) FROM RegressionRun')).scalar() == 0
    assert response.closed


@pytest.mark.parametrize('roles,scope,status', [
    (['viewer'], {'department': 'ENG', 'site': 'HN'}, 403),
    (['reviewer'], {'department': 'HR', 'site': 'HN'}, 422),
    (['reviewer'], {'department': 'ENG', 'site': 'HN', 'max_security_level': 'confidential'}, 422),
])
def test_rag_rejects_scope_before_starting_executor(scoped_db, roles, scope, status):
    _, actor = scoped_db
    calls = []
    runtime = SimpleNamespace(executor=SimpleNamespace(run=lambda *args, **kwargs: calls.append(args)))
    request = rag_server.ChatRequest(user_id=9, username='reviewer', user_question='Q', regression_scope=scope)
    with pytest.raises(HTTPException) as error:
        list(rag_server._open_rag_events(request, {**actor, 'roles': roles}, 'trace', None, runtime=runtime))
    assert error.value.status_code == status
    assert calls == []


def test_http_question_mutation_rechecks_grants_after_authenticated_profile(scoped_db, review_client):
    engine, actor = scoped_db
    question_id = feedback.add_regression_question('Q', expected_doc_id=1, department='ENG', site='HN', actor=actor)
    assert len(review_client.get('/api/regression/questions').json()['questions']) == 1
    assert review_client.get('/api/regression/runs').json()['runs'] == []
    path = f'/api/regression/questions/{question_id}/active'
    assert review_client.patch(path, json={'is_active': False}).json()['ok'] is True
    with engine.begin() as conn:
        conn.execute(text('DELETE FROM UserSites WHERE UserID=9'))
    for identifier in (question_id, 999):
        response = review_client.patch(f'/api/regression/questions/{identifier}/active', json={'is_active': True})
        assert response.status_code == 404
        assert response.json() == {'detail': 'Record unavailable'}
    with engine.connect() as conn:
        assert conn.execute(text('SELECT IsActive FROM RegressionQuestion')).scalar() == 0


def test_http_unapplied_schema_rejects_without_creating_question(scoped_db, review_client):
    engine, _ = scoped_db
    with engine.begin() as conn:
        conn.execute(text('ALTER TABLE GoldenAnswer DROP COLUMN AccessBasisJson'))
    for path in ('/api/regression/questions', '/api/regression/runs'):
        response = review_client.get(path)
        assert response.status_code == 503
        assert 'V0048' in response.json()['detail']
    response = review_client.post('/api/regression/questions', json={'question': 'Q', 'department': 'ENG', 'site': 'HN'})
    assert response.status_code == 503
    with engine.connect() as conn:
        assert conn.execute(text('SELECT COUNT(*) FROM RegressionQuestion')).scalar() == 0


@pytest.mark.parametrize('limit', ['3', 0, 1001, True])
def test_http_invalid_batch_limit_never_reaches_runtime(review_client, limit):
    assert review_client.post('/api/regression/run', json={'limit': limit}).status_code == 422
