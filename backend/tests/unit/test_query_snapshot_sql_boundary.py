"""Exercise the SQL Server session boundary through the public executor."""
from contextlib import contextmanager
from dataclasses import replace
from types import SimpleNamespace
import threading
import pytest
from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError
from mech_chatbot.config.repository_runtime import bind_repository_runtime
from mech_chatbot.rag.execution import DefaultRagExecutor,RequestBudgetLimits,RagInvocation,RagFailed,RagToken,RagCitation,RagCancelled,collect_rag_events
from .test_rag_query_plan import combined_request
from .test_rag_bom_chat import bom_store,store,connection,run,request,FixtureEngine
from .test_graph_v2_repository import SqliteConnection


class SqlServerSession:
    def __init__(self,store,timeout,*,fail_restore=False,cancel=None,fault_state=None,fault_scope='policy'):
        self.store=store
        self.timeout=timeout
        self.original_timeout=timeout
        self.fail_restore=fail_restore
        self.cancel=cancel
        self.read_timeouts=[]
        self.invalidated=False
        self.fault_state=fault_state
        self.fault_scope=fault_scope
        self.faulted=False
        self.reads_after_fault=0

    @contextmanager
    def connect(self):
        with self.store.connect() as conn:
            yield SqlServerConnection(conn,self)


class SqlServerConnection(SqliteConnection):
    def __init__(self,conn,session):
        super().__init__(conn)
        self.session=session
        self.dialect=SimpleNamespace(name='mssql')

    @contextmanager
    def begin(self):
        with self.conn.begin():
            self.conn.execute(text('BEGIN'))
            yield

    def execute(self,statement,params=None):
        sql=str(statement)
        if sql=='SELECT @@LOCK_TIMEOUT':
            return self.conn.execute(text('SELECT :timeout'),{'timeout':self.session.timeout})
        if sql=='SELECT XACT_STATE()':
            if self.session.faulted and self.session.fault_state=='unavailable':raise SQLAlchemyError('Private transaction state detail')
            state=self.session.fault_state if self.session.faulted else 1
            return self.conn.execute(text('SELECT :state'),{'state':state})
        if sql.startswith('SET LOCK_TIMEOUT '):
            timeout=int(sql.split()[-1])
            if self.session.fail_restore and timeout==self.session.original_timeout:
                raise SQLAlchemyError('Fixture session reset failed')
            self.session.timeout=timeout
            return None
        if self.session.faulted:self.session.reads_after_fault+=1
        selected=(params or {}).get('scope')==self.session.fault_scope or (self.session.fault_scope=='graph' and 'SafeOccurrences' in sql)
        if not self.session.faulted and self.session.fault_state is not None and selected:
            self.session.faulted=True
            if self.session.fault_state==0:self.conn.connection.driver_connection.rollback()
            elif self.session.fault_state=='invalidated':self.invalidate()
            raise SQLAlchemyError('Private SQL statement failure')
        self.session.read_timeouts.append(self.session.timeout)
        result=super().execute(statement,params or {})
        if self.session.cancel is not None:self.session.cancel.set()
        return result

    def invalidate(self):
        self.session.invalidated=True
        self.conn.invalidate()


@pytest.mark.parametrize('original_timeout',[-1,0,600])
def test_sql_session_waits_stay_within_deadline_and_restore_the_original_setting(bom_store,original_timeout):
    store,engine=bom_store
    # This case measures SQL session waits after application imports, not cold startup.
    assert run(FixtureEngine(store),request()).outcome=='answered'
    session=SqlServerSession(store,original_timeout)
    with bind_repository_runtime(db_engine=session):
        result=collect_rag_events(DefaultRagExecutor(budget_limits=RequestBudgetLimits(deadline_seconds=2)).run(
            combined_request(),RagInvocation('session',mode='test')))
    assert result.outcome=='answered' and '6.00' in result.answer
    assert result.diagnostics['query_v2']['read_consistency']=='serializable'
    assert session.read_timeouts and all(0<=timeout<=2000 for timeout in session.read_timeouts)
    if original_timeout>=0:assert all(timeout<=original_timeout for timeout in session.read_timeouts)
    assert session.timeout==original_timeout and not session.invalidated
    assert run(FixtureEngine(store),request()).outcome=='answered'


def test_cancellation_during_sql_releases_the_snapshot_and_restores_the_session(bom_store):
    store,engine=bom_store
    cancel=threading.Event()
    session=SqlServerSession(store,-1,cancel=cancel)
    with bind_repository_runtime(db_engine=session):
        events=list(DefaultRagExecutor().run(combined_request(),RagInvocation('cancel-sql',mode='test'),cancel))
    assert any(isinstance(event,RagCancelled) for event in events)
    assert not any(isinstance(event,(RagToken,RagCitation)) for event in events)
    assert session.timeout==-1 and not session.invalidated
    assert run(FixtureEngine(store),request()).outcome=='answered'


def test_failed_session_restore_discards_the_connection_and_never_publishes_the_answer(bom_store):
    store,engine=bom_store
    session=SqlServerSession(store,-1,fail_restore=True)
    with bind_repository_runtime(db_engine=session):
        events=list(DefaultRagExecutor().run(combined_request(),RagInvocation('reset-fail',mode='test')))
    assert session.invalidated and any(isinstance(event,RagFailed) for event in events)
    assert not any(isinstance(event,(RagToken,RagCitation)) for event in events)


@pytest.mark.parametrize('kind',['graph','bom','comparison'])
def test_standalone_templates_use_the_same_snapshot_and_session_cleanup(bom_store,kind):
    store,engine=bom_store
    query=request()
    if kind=='graph':query=replace(query,bom_rollup=None,graph_query=query.bom_rollup.graph)
    elif kind=='comparison':query=replace(query,bom_rollup=None,scope_comparison=('quantity-scope','policy'))
    session=SqlServerSession(store,-1)
    with bind_repository_runtime(db_engine=session):
        result=collect_rag_events(DefaultRagExecutor().run(query,RagInvocation('standalone',mode='test')))
    assert result.outcome=='answered'
    metadata=result.diagnostics['graph_v2' if kind=='graph' else 'math_v2']
    assert metadata['read_consistency']=='serializable'
    assert session.read_timeouts and all(0<=timeout<=120000 for timeout in session.read_timeouts)
    assert session.timeout==-1 and not session.invalidated


@pytest.mark.parametrize('kind',['graph','bom','comparison'])
def test_evidence_service_templates_share_the_snapshot_contract(bom_store,kind):
    from mech_chatbot.services import rag_evidence_service as service
    from .test_rag_bom_chat import PROFILE
    store,engine=bom_store
    session=SqlServerSession(store,-1)
    with bind_repository_runtime(db_engine=session):
        if kind=='graph':result=service.graph('document:1',PROFILE,product_scope='product',site='factory')
        elif kind=='bom':result=service.rollup('document:1',PROFILE,product_scope='product',site='factory',order_quantity='3',rounding_scope='policy')
        else:result=service.compare_scopes('quantity-scope','policy',PROFILE)
    assert result['status']=='complete'
    assert session.read_timeouts and all(0<=timeout<=120000 for timeout in session.read_timeouts)
    assert session.timeout==-1 and not session.invalidated


@pytest.mark.parametrize('fault_state',[0,-1,'unavailable','invalidated'])
def test_lost_sql_transaction_discards_all_prepared_branches(bom_store,fault_state):
    from mech_chatbot.domain.query_plan import QueryPlan,QueryBranch
    store,engine=bom_store
    session=SqlServerSession(store,-1,fault_state=fault_state)
    query=combined_request()
    third=QueryBranch('after','Tìm lại quan hệ',graph_query=query.query_plan.branches[0].graph_query)
    query=replace(query,question='Cụm chứa gì; tính BOM; tìm lại quan hệ',
                  query_plan=QueryPlan((*query.query_plan.branches,third)))
    with bind_repository_runtime(db_engine=session):
        events=list(DefaultRagExecutor().run(query,RagInvocation('lost-snapshot',mode='test')))
    assert session.faulted and any(isinstance(event,RagFailed) for event in events)
    assert not any(isinstance(event,(RagToken,RagCitation)) for event in events)
    assert session.reads_after_fault==0
    if fault_state in {'unavailable','invalidated'}:assert session.invalidated
    else:assert session.timeout==-1 and not session.invalidated
    # Discarding an in-memory SQLite connection also discards its fixture database.
    if not session.invalidated:assert run(FixtureEngine(store),request()).outcome=='answered'


def test_statement_error_keeps_independent_branch_when_sql_transaction_is_still_valid(bom_store):
    store,engine=bom_store
    session=SqlServerSession(store,-1,fault_state=1)
    with bind_repository_runtime(db_engine=session):
        result=collect_rag_events(DefaultRagExecutor().run(combined_request(),RagInvocation('valid-snapshot',mode='test')))
    assert session.faulted and result.outcome=='answered'
    assert [branch['status'] for branch in result.diagnostics['query_v2']['branches']]==['complete','unknown']
    assert 'part:p' in result.answer and '6.00' not in result.answer
    assert session.timeout==-1 and not session.invalidated


def test_graph_evaluation_api_rejects_lost_snapshot_without_private_details(bom_store,monkeypatch):
    from mech_chatbot.api.dependencies import csrf_profile,current_profile
    from .test_rag_evidence_api import client
    from .test_rag_bom_chat import PROFILE
    store,engine=bom_store
    session=SqlServerSession(store,-1,fault_state=0,fault_scope='graph')
    api=client(monkeypatch)
    api.app.dependency_overrides[current_profile]=lambda:PROFILE
    api.app.dependency_overrides[csrf_profile]=lambda:PROFILE
    with bind_repository_runtime(db_engine=session):
        response=api.post('/api/rag-evidence/evaluate/graph',json={
            'root_key':'document:1','product_scope':'product','site':'factory'})
    assert session.faulted and response.status_code==503
    assert 'Private' not in response.text and 'document:1' not in response.text
    assert session.reads_after_fault==0 and session.timeout==-1
