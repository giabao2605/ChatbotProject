"""History HTTP reads against two real SQLite connections, without source mocks."""
from contextlib import contextmanager
from functools import partial
from types import SimpleNamespace
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, event, text
from mech_chatbot.api.dependencies import csrf_profile
from mech_chatbot.api.routers import chat as routes
from mech_chatbot.adapters.document_runtime import SqlAppSupportQueries
from mech_chatbot.db.repositories import chat
from .test_structured_chat_history import history_store, store
from .test_structured_evidence_repository import PROFILE
from .test_graph_v2_repository import SqliteConnection


class HistoryConnection(SqliteConnection):
    def execute(self, statement, params=None):
        sql = str(statement).replace('TRY_CONVERT(INT, a.ChunkRef)', 'CAST(a.ChunkRef AS INT)')
        sql = sql.replace('SELECT TOP 1', 'SELECT')
        return super().execute(text(sql), params or {})


class HistoryEngine:
    def __init__(self, database):
        self.database = database
        self.opens = 0

    @contextmanager
    def connect(self):
        self.opens += 1
        with self.database.connect() as connection:
            wrapped = HistoryConnection(connection)
            wrapped.dialect = connection.dialect
            yield wrapped


def seed_history(database):
    with database.begin() as conn:
        conn.execute(text('CREATE TABLE DocumentPages(DocID INT,PageNo INT,VisionSummary TEXT,ImagePath TEXT)'))
        conn.execute(text('INSERT INTO DocumentPages VALUES(1,1,NULL,NULL)'))
        conn.execute(text("INSERT INTO LichSuChat VALUES(11,'s1','Question','Answer [SRC:D1P1]',NULL,NULL,NULL,'approver','2026-10-07')"))
    card = {'doc_id':1,'version_no':2,'source_id':'D1P1','page_no':1,'file_name':'before.pdf'}
    metadata = {'text_query':{'schema':'query-text-v2','accepted':False,'offline_only':True,'branches':[
        {'branch_id':'b1','subquery':'Question','outcome':'full_answer','cited_sources':[card]}]}}
    assert chat.save_answer_evidence(11, [{**card,'trang':1}], db_engine=database,
                                     advanced_rag=metadata, citation_cards=[card])
    assert chat.save_answer_sources(11, [{**card,'trang':1,'file_goc':'before.pdf'}], db_engine=database)


def test_http_history_keeps_source_version_and_title_in_the_authorization_snapshot(history_store, tmp_path, monkeypatch):
    seed_history(history_store)
    path = tmp_path/'history.sqlite'
    reader = create_engine('sqlite:///'+path.as_posix())
    writer = create_engine('sqlite:///'+path.as_posix())
    with history_store.connect() as source, reader.connect() as target:
        source.connection.driver_connection.backup(target.connection.driver_connection)
        target.exec_driver_sql('PRAGMA journal_mode=WAL')
    changed = []
    @event.listens_for(reader, 'before_cursor_execute')
    def change_source(conn, cursor, statement, params, context, many):
        if 'SELECT a.ChatID' in statement and not changed:
            with writer.begin() as other:
                other.execute(text('UPDATE TaiLieu SET VersionNo=3'))
                other.execute(text("UPDATE AnswerSource SET VersionNo=3,FileName='after.pdf'"))
            changed.append(True)
    engine = HistoryEngine(reader)
    app = FastAPI()
    app.include_router(routes.router)
    app.dependency_overrides[csrf_profile] = lambda:PROFILE
    app.state.database_runtime = SimpleNamespace(engine=engine)
    app.state.settings_snapshot = SimpleNamespace(RAG_REQUEST_DEADLINE_SECONDS=120)
    app.state.runtime = SimpleNamespace(app_support_queries=SqlAppSupportQueries(engine=engine))
    monkeypatch.setattr(routes, 'get_chat_history', partial(chat.get_chat_history, db_engine=engine))
    try:
        with TestClient(app) as client:
            response = client.post('/api/chat/history', json={'session_id':'s1'})
            assert response.status_code == 200
            answer = response.json()['messages'][-1]
            assert changed
            assert [(card['file_name'],card['version_no']) for card in answer['citations']] == [('before.pdf',2)]
            assert answer['citations'][0]['original_url']=='/api/files/documents/1/original?citation_version=2'
            assert answer['advanced_rag']['text_query']['branches'][0]['cited_sources'][0]['file_name']=='before.pdf'
            assert engine.opens==1
            current = client.post('/api/chat/history', json={'session_id':'s1'}).json()['messages'][-1]
            assert current['sources_redacted'] and current['citations']==[] and 'advanced_rag' not in current
    finally:
        reader.dispose()
        writer.dispose()


@pytest.mark.parametrize('kind',['ordinary','text_query','query'])
def test_http_history_revision_links_apply_only_to_new_branch_records(history_store,monkeypatch,tmp_path,kind):
    from sqlalchemy.pool import StaticPool
    from mech_chatbot.api import file_access
    from mech_chatbot.adapters.protected_files import SqlProtectedFileStore, FilesystemProtectedFileStorage
    from mech_chatbot.application.chat_result_history import serialize_history_result
    seed_history(history_store)
    image=tmp_path/'page.png';image.write_bytes(b'verified preview')
    with history_store.begin() as conn:
        conn.execute(text("UPDATE DocumentPages SET VisionSummary='verified preview',ImagePath=:path"),{'path':str(image)})
        if kind=='ordinary':
            conn.execute(text('UPDATE ChatEvidenceManifest SET AdvancedRagJson=NULL'))
        elif kind=='query':
            card={'source_id':'D1P1'}
            payload=serialize_history_result({'query':{'schema':'query-plan-v2','status':'partial','accepted':False,
                'offline_only':True,'branches':[{'branch_id':'b1','question':'Question','status':'partial',
                                               'data_complete':True,'sources':[card]}]}},[card])
            conn.execute(text('UPDATE ChatEvidenceManifest SET AdvancedRagJson=:payload'),{'payload':payload})
    shared=create_engine('sqlite://',connect_args={'check_same_thread':False},poolclass=StaticPool)
    with history_store.connect() as source,shared.connect() as target:
        source.connection.driver_connection.backup(target.connection.driver_connection)
    engine=HistoryEngine(shared)
    app=FastAPI();app.include_router(routes.router)
    app.dependency_overrides[csrf_profile]=lambda:PROFILE
    app.state.database_runtime=SimpleNamespace(engine=engine)
    app.state.runtime=SimpleNamespace(app_support_queries=SqlAppSupportQueries(engine=engine))
    monkeypatch.setattr(routes,'get_chat_history',partial(chat.get_chat_history,db_engine=engine))
    monkeypatch.setattr(file_access,'_store',lambda:SqlProtectedFileStore(engine_provider=lambda:engine))
    monkeypatch.setattr(file_access,'_storage',lambda:FilesystemProtectedFileStorage(
        project_root=tmp_path,raw_root=tmp_path,processed_root=tmp_path))
    try:
        response=TestClient(app).post('/api/chat/history',json={'session_id':'s1'})
        assert response.status_code==200
        answer=response.json()['messages'][-1]
        card=answer['citations'][0]
        suffix='' if kind=='ordinary' else '?citation_version=2'
        assert card['original_url']=='/api/files/documents/1/original'+suffix
        assert card['page_url']=='/api/files/documents/1/pages/1'+suffix
        assert ('advanced_rag' in answer)==(kind!='ordinary')
    finally:shared.dispose()


@pytest.mark.parametrize('failure', ['deadline','late_deadline','different_engine','lost_vision_snapshot',
                                   'initial_sql','authorization_sql','column_sql','result_sql','vision_sql'])
def test_http_history_never_releases_data_after_snapshot_or_deadline_failure(history_store, monkeypatch, failure):
    seed_history(history_store)
    from sqlalchemy.pool import StaticPool
    from mech_chatbot.api import file_access
    from mech_chatbot.db.evidence_snapshot import EvidenceSnapshotLost
    shared = create_engine('sqlite://',connect_args={'check_same_thread':False},poolclass=StaticPool)
    other = create_engine('sqlite://',connect_args={'check_same_thread':False},poolclass=StaticPool)
    with history_store.connect() as source, shared.connect() as target:
        source.connection.driver_connection.backup(target.connection.driver_connection)
    engine, unrelated = HistoryEngine(shared), HistoryEngine(other)
    app = FastAPI();app.include_router(routes.router)
    app.dependency_overrides[csrf_profile] = lambda:PROFILE
    app.state.database_runtime = SimpleNamespace(engine=engine)
    app.state.settings_snapshot = SimpleNamespace(RAG_REQUEST_DEADLINE_SECONDS=0 if failure=='deadline' else 5)
    app.state.runtime = SimpleNamespace(app_support_queries=SqlAppSupportQueries(
        engine=unrelated if failure=='different_engine' else engine))
    monkeypatch.setattr(routes,'get_chat_history',partial(chat.get_chat_history,db_engine=engine))
    if failure=='late_deadline':
        clock = [0]
        monkeypatch.setattr(routes,'monotonic',lambda:clock[0])
        @event.listens_for(shared,'before_cursor_execute')
        def expire(conn,cursor,statement,params,context,many):
            if 'SELECT a.ChatID' in statement:clock[0]=6
    if failure in ('lost_vision_snapshot','vision_sql'):
        with shared.begin() as conn:
            conn.execute(text("UPDATE DocumentPages SET VisionSummary='private summary',ImagePath='/private.png'"))
        class LostStore:
            def load_page_vision(self,*args):
                if failure=='vision_sql':
                    from sqlalchemy.exc import SQLAlchemyError
                    raise SQLAlchemyError('Private database detail')
                raise EvidenceSnapshotLost('Private database detail')
        monkeypatch.setattr(file_access,'_store',lambda:LostStore())
    target = {'initial_sql':'FROM LichSuChat','authorization_sql':'SELECT e.ChatID',
              'column_sql':'PRAGMA table_info','result_sql':'SELECT AdvancedRagJson'}.get(failure)
    if target:
        @event.listens_for(shared,'before_cursor_execute')
        def storage_error(conn,cursor,statement,params,context,many):
            if target in statement:
                from sqlalchemy.exc import SQLAlchemyError
                raise SQLAlchemyError('Private database detail')
    try:
        with TestClient(app) as client:
            response=client.post('/api/chat/history',json={'session_id':'s1'})
            assert response.status_code == (504 if 'deadline' in failure else 503)
            assert 'messages' not in response.json()
            assert 'before.pdf' not in response.text and 'Private' not in response.text
            assert unrelated.opens==0
    finally:
        shared.dispose();other.dispose()
