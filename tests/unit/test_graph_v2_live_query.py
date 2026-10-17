"""Graph-only release authority uses the native ACL reader before emitting data."""
from dataclasses import replace
from datetime import datetime, timedelta, timezone
import json

import pytest
from sqlalchemy import text

from mech_chatbot.config.repository_runtime import bind_repository_runtime
from mech_chatbot.domain.graph_query import GraphQuery, graph_fingerprint, graph_query_context
from mech_chatbot.governance.graph_v2_activation import CONTRACT, GraphV2Capability
from mech_chatbot.rag.execution import DefaultRagExecutor, RagInvocation, RagFailed, RagToken, RagCitation, RagCompleted, collect_rag_events
from .test_rag_graph_query_v2 import request, bom_store, store, connection


def capability(engine, query):
    with bind_repository_runtime(db_engine=engine):
        result=collect_rag_events(DefaultRagExecutor().run(query,RagInvocation('freeze',mode='evaluation')))
    graph=result.diagnostics['graph_v2']
    assert result.citations,graph
    return GraphV2Capability(contract=CONTRACT,graph_pins=((json.dumps(graph_query_context(query.graph_query),sort_keys=True,separators=(',',':')),graph['graph_hash']),),
        source_commit='a'*40,bundle_sha256='b'*64,snapshot_fingerprint='c'*64,
        expires_at=(datetime.now(timezone.utc)+timedelta(minutes=10)).isoformat())


def test_graph_v2_authorized_viewer_gets_native_relations_without_model(bom_store):
    _,engine=bom_store
    selected=request(); cap=capability(engine,selected)
    selected=replace(selected,access=replace(selected.access,roles=frozenset({'viewer'})))
    with bind_repository_runtime(db_engine=engine):
        result=collect_rag_events(DefaultRagExecutor(graph_v2_authorizer=lambda:cap).run(selected,RagInvocation('live-graph')))
    assert result.outcome=='answered' and 'part:p' in result.answer and result.citations
    assert result.diagnostics['graph_v2']['offline_only'] is False
    assert result.diagnostics['graph_v2']['contract']==CONTRACT
    assert result.diagnostics.budget.planners==result.diagnostics.budget.final_generations==result.diagnostics.budget.calculations==0


@pytest.mark.parametrize('change',['withdrawn','quantity','acl','unlisted_context','expired','revoked'])
def test_graph_v2_changed_authority_or_native_data_never_emits_relations(bom_store,change):
    db,engine=bom_store
    selected=request(); cap=capability(engine,selected)
    if change=='withdrawn':
        with db.begin() as conn:conn.execute(text("UPDATE TaiLieu SET Servable=0 WHERE DocID=1"))
    elif change=='quantity':
        with db.begin() as conn:conn.execute(text("UPDATE ReviewedGraphOccurrences SET Quantity='77' WHERE ParentDocID=1"))
    elif change=='acl':selected=replace(selected,access=replace(selected.access,allowed_departments=frozenset({'other'})))
    elif change=='unlisted_context':selected=replace(selected,graph_query=GraphQuery('document:2','product','factory'))
    elif change=='expired':cap=replace(cap,expires_at='2000-01-01T00:00:00+00:00')
    calls=[]
    def authorize():
        calls.append(True)
        return None if change=='revoked' and len(calls)>1 else cap
    with bind_repository_runtime(db_engine=engine):
        events=list(DefaultRagExecutor(graph_v2_authorizer=authorize).run(selected,RagInvocation('changed')))
    assert isinstance(events[-1],RagFailed)
    assert not any(isinstance(event,(RagToken,RagCitation,RagCompleted)) for event in events)
    assert events[-1].diagnostics.budget.final_generations==0


def test_graph_v2_trusted_partial_where_used_is_not_promoted_complete(bom_store):
    _,engine=bom_store
    selected=replace(request(),graph_query=GraphQuery('part:p','product','factory','where_used'))
    cap=capability(engine,selected)
    with bind_repository_runtime(db_engine=engine):
        result=collect_rag_events(DefaultRagExecutor(graph_v2_authorizer=lambda:cap).run(selected,RagInvocation('reverse')))
    assert result.citations and result.diagnostics['graph_v2']['source_status']=='partial'
    assert 'Chưa xác nhận được đầy đủ' in result.answer


from types import SimpleNamespace
from .test_rag_server_endpoints import rag_client,SERVICE_HEADERS,_replace_runtime,_replace_server_state


@pytest.fixture
def threaded_graph_store(bom_store,tmp_path):
    from sqlalchemy import create_engine,event
    from .test_rag_bom_chat import FixtureEngine
    source,_=bom_store
    database=create_engine('sqlite:///'+(tmp_path/'graph-http.sqlite').as_posix())
    @event.listens_for(database,'connect')
    def scalar_functions(dbapi,_):
        dbapi.create_function('CHARINDEX',2,lambda needle,value:value.find(needle)+1)
        def json_value(value,path):
            result=json.loads(value) if value else {}
            for key in path[2:].split('.'):result=result.get(key) if isinstance(result,dict) else None
            return result
        dbapi.create_function('JSON_VALUE',2,json_value)
    with source.connect() as source_conn,database.connect() as target:
        source_conn.connection.driver_connection.backup(target.connection.driver_connection)
    try:yield FixtureEngine(database)
    finally:database.dispose()


@pytest.mark.parametrize('path',['/chat','/chat/stream'])
def test_graph_v2_http_requires_matching_server_bundle_for_native_sql(path,rag_client,threaded_graph_store,monkeypatch):
    from mech_chatbot.auth import core
    from mech_chatbot.rag.execution_contracts import RagRuntimeContract
    engine=threaded_graph_store
    selected=request(); cap=capability(engine,selected)
    profile={'user_id':7,'username':'viewer','roles':['viewer'],'allowed_departments':['tech'],
             'allowed_sites':['factory'],'max_security_level':'internal'}
    monkeypatch.setattr(core,'load_user_profile',lambda **_:profile)
    _replace_server_state(rag_client,database_runtime=SimpleNamespace(engine=engine))
    _replace_runtime(rag_client,executor=DefaultRagExecutor(graph_v2_authorizer=lambda:cap),
        graph_v2_authorizer=lambda:cap,runtime_contract=RagRuntimeContract('production',False,120))
    body={'user_id':7,'user_question':selected.question,'graph_query':graph_query_context(selected.graph_query)}
    assert rag_client.post(path,headers=SERVICE_HEADERS,json=body).status_code==409
    response=rag_client.post(path,headers={**SERVICE_HEADERS,'X-RAG-Activation-Bundle-SHA256':cap.bundle_sha256},json=body)
    assert response.status_code==200 and 'part:p' in response.text
    if path=='/chat':assert response.json()['debug_info']['graph_v2']['offline_only'] is False


def test_graph_v2_browser_capability_and_picker_require_current_grant(threaded_graph_store,monkeypatch):
    from .test_rag_evidence_api import client
    from mech_chatbot.api.dependencies import current_profile
    engine=threaded_graph_store; cap=capability(engine,request())
    profile={'user_id':7,'username':'viewer','roles':['viewer'],'allowed_departments':['tech'],
             'allowed_sites':['factory'],'max_security_level':'internal'}
    with client(monkeypatch,mode='production',roles=('viewer',)) as api:
        api.app.state.runtime=SimpleNamespace(graph_v2_authorizer=lambda:cap)
        api.app.dependency_overrides[current_profile]=lambda:profile
        with bind_repository_runtime(db_engine=engine):
            meta=api.get('/api/rag-evidence/evaluation-capabilities')
            scopes=api.get('/api/rag-evidence/documents/1/graph-scopes',params={'site':'factory'})
        assert meta.status_code==200 and meta.json()['actions']==['contains','where_used']
        assert meta.json()['max_branches']==1 and meta.json()['text_query'] is False
        assert scopes.status_code==200 and scopes.json()['items']==[{'product_scope':'product','site':'factory'}]
        api.app.state.runtime=SimpleNamespace(graph_v2_authorizer=lambda:None)
        assert api.get('/api/rag-evidence/evaluation-capabilities').status_code==409
        assert api.get('/api/rag-evidence/documents/1/graph-scopes',params={'site':'factory'}).status_code==409


def test_graph_app_bridge_forwards_fresh_bundle_identity(monkeypatch,bom_store):
    from mech_chatbot.api import app_server
    from mech_chatbot.config.settings import Settings,AppProcessSettings
    from mech_chatbot.governance import graph_v2_activation as authority
    _,engine=bom_store; grants=[capability(engine,request())]
    monkeypatch.setattr(authority,'graph_v2_authorizer_for_settings',lambda _:lambda:grants[0])
    captured={}
    monkeypatch.setattr(app_server,'build_default_app_runtime',lambda *a,**kw:captured.update(kw))
    settings=Settings()
    app_server._build_default_app_runtime(AppProcessSettings.from_settings(settings),SimpleNamespace(),
        post=lambda *a,**kw:None,settings_snapshot=settings)
    assert captured['headers']()['X-RAG-Activation-Bundle-SHA256']==grants[0].bundle_sha256
    assert captured['graph_v2_authorizer']() is grants[0]
    grants[0]=None
    assert 'X-RAG-Activation-Bundle-SHA256' not in captured['headers']()


def test_graph_v2_bundle_disables_legacy_enrichment_only_for_selected_contract(tmp_path):
    from mech_chatbot.config.settings import Settings,RagProcessSettings
    from mech_chatbot.composition.rag_runtime import _retrieval_feature_settings
    path=tmp_path/'bundle.json'; path.write_text('{"schema":"rag-activation-bundle-v3"}')
    settings=Settings(RAG_GRAPH_RETRIEVAL_ENABLED=True,RAG_ACTIVATION_BUNDLE_PATH=str(path))
    assert not _retrieval_feature_settings(settings,RagProcessSettings.from_settings(settings))['graph_retrieval_enabled']
    assert _retrieval_feature_settings(Settings(RAG_GRAPH_RETRIEVAL_ENABLED=True),RagProcessSettings.from_settings(settings))['graph_retrieval_enabled']
