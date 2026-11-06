"""Synthetic SQL inputs exercise the public executor, collector and HTTP ACL."""
import sqlite3
import threading
from decimal import Decimal
from types import SimpleNamespace

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.pool import StaticPool

from mech_chatbot.auth.core import load_user_profile
from mech_chatbot.config.repository_runtime import bind_repository_runtime
from mech_chatbot.evaluation.grounded_math_v2 import export_math_observation
from mech_chatbot.rag.execution import (
    DefaultRagExecutor, RagCancelled, RagCitation, RagCollectedResult, RagCompleted,
    RagFailed, RagInvocation, RequestBudgetLimits, consume_rag_events,
    current_request_budget,
)
from mech_chatbot.rag.execution_contracts import RagRuntimeContract
from tests.unit.db.test_graph_v2_repository import connection
from tests.unit.rag.test_rag_scope_comparison import approved_pair, request
from tests.unit.api.test_rag_server_endpoints import (
    SERVICE_HEADERS, _replace_runtime, _replace_server_state, rag_client,
)
from tests.unit.db.test_structured_evidence_repository import store

pytestmark = pytest.mark.unit


@pytest.fixture
def comparison_store(store):
    approved_pair(store, values=('10', '15'))
    try:
        yield store
    finally:
        store.dispose()


def stopped_capture(engine, *, deadline, after_first_citation):
    from mech_chatbot.rag.pipeline import execute_pipeline

    budgets = []

    def execute(state):
        budgets.append(current_request_budget())
        return execute_pipeline(state)

    cancellation = threading.Event()
    if not deadline and not after_first_citation:
        cancellation.set()
    executor = DefaultRagExecutor(execute_pipeline=execute, budget_limits=RequestBudgetLimits(
        deadline_seconds=0 if deadline and not after_first_citation else 120))
    observed = []

    def observe():
        events = executor.run(request(), RagInvocation(
            'safety-stop', mode='evaluation', capture_math_observations=True), cancellation)
        try:
            for event in events:
                observed.append(event)
                if after_first_citation and isinstance(event, RagCitation):
                    if deadline:
                        budgets[0].started_monotonic -= 1000
                    else:
                        cancellation.set()
                yield event
        finally:
            events.close()

    with bind_repository_runtime(db_engine=engine):
        result = consume_rag_events(observe())
    return result, observed


@pytest.mark.parametrize('deadline', [False, True])
def test_stopped_requests_keep_real_terminal_and_cannot_export_success(comparison_store, deadline):
    for after_first_citation in (False, True):
        result, events = stopped_capture(comparison_store, deadline=deadline,
                                        after_first_citation=after_first_citation)
        assert result is events[-1]
        assert isinstance(result, RagFailed if deadline else RagCancelled)
        if deadline:
            assert result.diagnostics.budget.deadline_exceeded
        assert not any(isinstance(event, RagCompleted) for event in events)
        with pytest.raises(ValueError, match='completed opt-in'):
            export_math_observation(result)


def test_completed_capture_keeps_original_math_observation(comparison_store):
    with bind_repository_runtime(db_engine=comparison_store):
        result = consume_rag_events(DefaultRagExecutor().run(request(), RagInvocation(
            'safety-complete', mode='evaluation', capture_math_observations=True)))
    assert isinstance(result, RagCollectedResult)
    assert result.outcome == 'answered'
    observation = export_math_observation(result)
    assert observation['actual_outcome'] == 'full_answer'
    assert result.diagnostics.budget.calculations == observation['calculation_count'] == 4
    assert [Decimal(step['value']) for step in observation['math_results'][0]['steps']] == [
        Decimal('10'), Decimal('15'), Decimal('5'), Decimal('50')]
    assert len(observation['citations']) == 2


def test_sql_adapter_reads_nested_source_locators(connection):
    row = connection.execute(text(
        "SELECT JSON_VALUE(:value,'$.locator.page'),JSON_VALUE(:value,'$.locator.row_key'),"
        "JSON_VALUE(:value,'$.missing.page')"),
        {'value': '{"locator":{"page":1,"row_key":"page-1:row-1"}}'}).one()
    assert tuple(row) == (1, 'page-1:row-1', None)


@pytest.mark.parametrize('deadline', [False, True])
def test_cancel_or_deadline_after_first_citation_stops_before_the_next(comparison_store, deadline):
    result, events = stopped_capture(comparison_store, deadline=deadline, after_first_citation=True)
    assert isinstance(result, RagFailed if deadline else RagCancelled)
    assert len([event for event in events if isinstance(event, RagCitation)]) == 1
    assert not any(isinstance(event, RagCompleted) for event in events)


def test_http_replay_freezes_both_sql_actors_and_uses_real_acl(comparison_store, rag_client, tmp_path, monkeypatch):
    from mech_chatbot.auth import core
    monkeypatch.setattr(core, 'load_user_profile', load_user_profile)
    with comparison_store.begin() as conn:
        for sql in (
            'ALTER TABLE Users ADD DisplayName TEXT',
            'ALTER TABLE Users ADD Department TEXT',
            'ALTER TABLE Users ADD PasswordHash TEXT',
            'ALTER TABLE Users ADD PreferredLanguage TEXT',
            'CREATE TABLE UserDepartments(UserID INTEGER,Department TEXT)',
            'CREATE TABLE UserSites(UserID INTEGER,Site TEXT)',
            'CREATE TABLE UserSecurityClearance(UserID INTEGER,MaxLevel TEXT)',
            'CREATE TABLE Departments(DeptCode TEXT,DeptName TEXT,Domain TEXT,Site TEXT,Status TEXT,IsActive INTEGER)',
            "UPDATE Users SET DisplayName='approver',Department='ENG',PreferredLanguage='vi' WHERE UserID=7",
            "INSERT INTO Users(UserID,Username,IsActive,DisplayName,Department,PreferredLanguage) VALUES(9,'other-fixture',1,'Other','finance','vi')",
            'INSERT INTO UserRoles VALUES(9,1)',
            "INSERT INTO UserDepartments VALUES(7,'ENG'),(9,'finance')",
            "INSERT INTO UserSites VALUES(7,'factory'),(9,'factory')",
            "INSERT INTO UserSecurityClearance VALUES(7,'internal'),(9,'internal')",
            "INSERT INTO Departments VALUES('ENG','ENG','general','factory','active',1),('finance','finance','general','factory','active',1)",
        ):
            conn.execute(text(sql))
    shared = create_engine('sqlite://', connect_args={'check_same_thread': False}, poolclass=StaticPool)
    try:
        with comparison_store.connect() as original, shared.connect() as target:
            original.connection.driver_connection.backup(target.connection.driver_connection)
            dump = '\n'.join(target.connection.driver_connection.iterdump())
        snapshot = tmp_path / 'http-input.sql'
        snapshot.write_text(dump, encoding='utf-8')
        with sqlite3.connect(':memory:') as frozen:
            frozen.executescript(snapshot.read_text(encoding='utf-8'))
            assert frozen.execute('SELECT Username FROM Users WHERE UserID=7').fetchone() == ('approver',)
            assert frozen.execute('SELECT Username FROM Users WHERE UserID=9').fetchone() == ('other-fixture',)
            assert frozen.execute('SELECT Department FROM UserDepartments WHERE UserID=9').fetchone() == ('finance',)
            assert frozen.execute('SELECT DeptName,Domain,Site FROM Departments WHERE DeptCode=?',
                                  ('finance',)).fetchone() == ('finance', 'general', 'factory')
        _replace_server_state(rag_client, database_runtime=SimpleNamespace(engine=shared))
        _replace_runtime(rag_client, executor=DefaultRagExecutor(),
                         runtime_contract=RagRuntimeContract('evaluation', False, 120))
        body = {'user_question': request().question, 'scope_comparison': {'scope_a': 'a', 'scope_b': 'b'}}
        allowed = rag_client.post('/chat', headers=SERVICE_HEADERS,
                                  json={**body, 'user_id': 7, 'username': 'approver'})
        denied = rag_client.post('/chat', headers=SERVICE_HEADERS,
                                 json={**body, 'user_id': 9, 'username': 'other-fixture'})
        assert allowed.status_code == denied.status_code == 200
        assert len(allowed.json()['citations']) == 2 and '10.00 VND' in allowed.json()['response']
        assert denied.json()['citations'] == []
        assert denied.json()['debug_info']['answer_outcome'] == 'refused'
        assert '10.00 VND' not in denied.text and '15.00 VND' not in denied.text
    finally:
        shared.dispose()
