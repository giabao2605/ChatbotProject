"""Graph/BOM image provenance through public chat, history and file seams."""
from dataclasses import replace
from hashlib import sha256
from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine,event,text
from sqlalchemy.pool import StaticPool

from mech_chatbot.api.dependencies import current_profile
from mech_chatbot.api.routers import documents
from mech_chatbot.application.chat_citations import build_citation_list
from mech_chatbot.application.protected_files import AuthorizedFile
from mech_chatbot.config.repository_runtime import bind_repository_runtime
from mech_chatbot.db.repositories import chat
from mech_chatbot.db.repositories.source_page_images import save_source_page_images
from .test_rag_bom_chat import bom_store,store,connection,history_store,request,run,expand_bom,FixtureEngine,PROFILE,rag_client
from .test_rag_query_plan import combined_request


@pytest.fixture
def drawing_images(bom_store,tmp_path):
    store,engine=bom_store
    image=tmp_path/'drawing.svg'
    image.write_bytes(b'<svg xmlns="http://www.w3.org/2000/svg" width="40" height="20"><rect width="40" height="20" fill="blue"/></svg>')
    digest=sha256(image.read_bytes()).hexdigest()
    with store.begin() as conn:conn.execute(text('ALTER TABLE StructuredSourceSnapshot ADD PageImagesJson TEXT'))
    save_source_page_images(1,1,'snapshot','a'*64,{1:digest},db_engine=store)
    return store,engine,image,digest


def image_request(kind):
    query=request()
    if kind=='graph':return replace(query,bom_rollup=None,graph_query=query.bom_rollup.graph)
    return combined_request() if kind=='query' else query


@pytest.fixture
def shared_drawing_images(drawing_images):
    import json
    store,engine,image,digest=drawing_images
    shared=create_engine('sqlite://',connect_args={'check_same_thread':False},poolclass=StaticPool)
    @event.listens_for(shared,'connect')
    def functions(dbapi,record):
        dbapi.create_function('CHARINDEX',2,lambda needle,value:value.find(needle)+1)
        def json_value(value,path):
            result=json.loads(value or '{}')
            for key in path[2:].split('.'):result=result.get(key) if isinstance(result,dict) else None
            return result
        dbapi.create_function('JSON_VALUE',2,json_value)
    with store.connect() as source,shared.connect() as target:
        source.connection.driver_connection.backup(target.connection.driver_connection)
    try:yield shared,image,digest
    finally:shared.dispose()


@pytest.mark.parametrize('kind',['graph','bom','query'])
def test_graph_and_bom_citations_keep_one_verified_page_hash_per_source(drawing_images,kind):
    store,engine,image,digest=drawing_images
    expand_bom(store)
    reads=[]
    @event.listens_for(store,'before_cursor_execute')
    def record_page_read(conn,cursor,statement,params,context,many):
        if statement.startswith('SELECT PageImagesJson') and params[0] in (1,'1'):reads.append(params)
    try:result=run(engine,image_request(kind))
    finally:event.remove(store,'before_cursor_execute',record_page_read)
    records=result.diagnostics['citation_docs']
    graph_records=[record for record in records if record.get('graph_context')]
    assert graph_records and all(record.get('image_hash')==digest for record in graph_records)
    emitted=[card for card in result.citations if card.get('graph_context')]
    assert all(card['has_vision'] and card['page_url'] for card in emitted)
    assert len(reads)==(2 if kind=='query' else 1)
    cards=build_citation_list(records,page_has_vision=lambda *_:True)
    graph_cards=[card for card in cards if card.get('graph_context')]
    assert all(card['has_vision'] and 'image_hash='+digest in card['page_url'] for card in graph_cards)
    assert {card['source_id'] for card in cards}=={card['source_id'] for card in result.citations}


@pytest.mark.parametrize('kind',['graph','bom','query'])
def test_saved_graph_image_provenance_is_preserved_after_history_revalidation(drawing_images,history_store,kind):
    store,engine,image,digest=drawing_images
    result=run(engine,image_request(kind))
    with store.begin() as conn:
        conn.execute(text("INSERT INTO LichSuChat VALUES(11,'images','Question',:answer,NULL,NULL,NULL,'approver','2026-10-06')"),{'answer':result.answer})
    assert chat.save_answer_evidence(11,list(result.diagnostics['retrieved_docs']),db_engine=engine)
    assert chat.save_answer_sources(11,list(result.citations),db_engine=engine)
    restored=chat.get_chat_history('images',username='approver',allowed_departments=['tech','ENG'],
                                  allowed_sites=['factory'],user_clearance='internal',db_engine=engine)[-1]
    assert restored['content']==result.answer
    cards=build_citation_list(restored['structured_citations'],page_has_vision=lambda *_:True)
    graph_cards=[card for card in cards if card.get('graph_context')]
    assert graph_cards and all(card['image_hash']==digest and card['page_url'] for card in graph_cards)


def test_missing_image_hash_keeps_valid_graph_answer_without_inventing_a_preview(drawing_images):
    store,engine,image,digest=drawing_images
    with store.begin() as conn:conn.execute(text('UPDATE StructuredSourceSnapshot SET PageImagesJson=NULL'))
    result=run(engine,image_request('graph'))
    cards=build_citation_list(result.diagnostics['citation_docs'],page_has_vision=lambda *_:True)
    assert result.outcome=='answered' and cards
    assert all(not card['has_vision'] and card['page_url'] is None for card in cards)
    assert all(card['original_url'] and card['graph_hash'] for card in cards)


@pytest.mark.parametrize('change',[None,'bytes','review','revision','rights','manifest'])
def test_graph_page_link_serves_bound_bytes_or_denies_before_file_resolution(drawing_images,shared_drawing_images,change):
    store,engine,image,digest=drawing_images
    result=run(engine,image_request('graph'))
    card=build_citation_list(result.diagnostics['citation_docs'],page_has_vision=lambda *_:True)[0]
    assert card['page_url'] is not None
    shared,image,digest=shared_drawing_images
    app=FastAPI();app.include_router(documents.files_router)
    app.dependency_overrides[current_profile]=lambda:PROFILE
    opened=[]
    app.state.runtime=SimpleNamespace(protected_file_resolver=SimpleNamespace(
        resolve=lambda ref,actor:opened.append(ref) or AuthorizedFile(image)))
    changes={'review':"UPDATE ReviewedGraphOccurrences SET ReviewStatus='rejected'",
             'revision':'UPDATE TaiLieu SET VersionNo=2 WHERE DocID=1',
             'rights':'DELETE FROM PhongBanChiaSe',
             'manifest':'UPDATE StructuredSourceSnapshot SET PageImagesJson=NULL'}
    if change in changes:
        with shared.begin() as conn:conn.execute(text(changes[change]))
    if change=='bytes':image.write_bytes(b'wrong private drawing image')
    with bind_repository_runtime(db_engine=FixtureEngine(shared)):
        response=TestClient(app).get(card['page_url'])
    if change is None:
        assert response.status_code==200 and response.content==image.read_bytes()
        assert response.headers['cache-control']=='private, no-store'
        assert response.headers['content-disposition'].startswith('inline')
    else:
        assert response.status_code==404 and b'wrong private drawing image' not in response.content
        if change!='bytes':assert opened==[]
        assert 'snapshot' not in response.text and 'document:1' not in response.text


@pytest.mark.parametrize('path',['/chat','/chat/stream'])
def test_rest_and_sse_emit_the_registered_graph_image_pin(rag_client,shared_drawing_images,monkeypatch,path):
    import json
    from mech_chatbot.auth import core
    from mech_chatbot.rag.execution import DefaultRagExecutor
    from mech_chatbot.rag.execution_contracts import RagRuntimeContract
    from .test_rag_bom_chat import VIEWER_PROFILE,SERVICE_HEADERS,_replace_runtime,_replace_server_state
    shared,image,digest=shared_drawing_images
    monkeypatch.setattr(core,'load_user_profile',lambda **kwargs:{**VIEWER_PROFILE,**PROFILE})
    _replace_runtime(rag_client,executor=DefaultRagExecutor(),runtime_contract=RagRuntimeContract('evaluation',False,120))
    _replace_server_state(rag_client,database_runtime=SimpleNamespace(engine=FixtureEngine(shared)))
    response=rag_client.post(path,headers=SERVICE_HEADERS,json={'user_id':7,'user_question':'Cụm chứa gì?',
        'graph_query':{'root_key':'document:1','product_scope':'product','site':'factory'}})
    assert response.status_code==200
    if path=='/chat':cards=response.json()['citations']
    else:cards=[json.loads(block.split('data: ',1)[1]) for block in response.text.split('\n\n')
                if block.startswith('event: citation\n')]
    assert cards and all(card['image_hash']==digest and card['has_vision'] for card in cards)
    assert all('image_hash='+digest in card['page_url'] and card['graph_context'] for card in cards)
