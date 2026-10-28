"""Graph v2 through the same event lifecycle and governed SQL reader as chat."""
from contextlib import nullcontext
from types import SimpleNamespace
import pytest
from mech_chatbot.db.repositories import graph_v2
from mech_chatbot.rag.execution import AccessScope,DefaultRagExecutor,RagRequest,RagInvocation,collect_rag_events
from mech_chatbot.domain.graph_query import GraphQuery
from .test_graph_v2_repository import connection
from dataclasses import replace
from .test_rag_bom_chat import bom_store,store,expand_bom,run


def request():
    return RagRequest('Cụm này chứa những chi tiết nào?',AccessScope(roles=frozenset({'reviewer'}),
        allowed_departments=frozenset({'tech'}),allowed_sites=frozenset({'factory'}),max_security_level='internal'),
        graph_query=GraphQuery('document:1','product','factory'))


def test_graph_query_uses_reviewed_sql_relations_and_emits_source_citations(connection,monkeypatch):
    monkeypatch.setattr(graph_v2,'engine',SimpleNamespace(connect=lambda:nullcontext(connection)))
    monkeypatch.setattr(graph_v2,'_ensure_engine',lambda:None)
    result=collect_rag_events(DefaultRagExecutor().run(request(),RagInvocation('graph-query',mode='evaluation')))
    assert result.outcome=='answered'
    assert result.diagnostics['graph_v2']['status']=='complete'
    assert len(result.diagnostics['graph_v2']['edges'])==1
    assert 'part:p' in result.answer
    assert result.citations and result.citations[0]['doc_id']==1
    assert result.diagnostics.budget.graph_edges==1
    assert result.diagnostics.budget.final_generations==0


@pytest.mark.parametrize('language',['vi','en'])
def test_complete_graph_always_states_source_coverage_without_requested_outputs(bom_store,language):
    _,engine=bom_store
    result=run(engine,replace(request(),response_language=language))
    notice='Phạm vi nguồn đã xác nhận đầy đủ.' if language=='vi' else 'The source coverage is confirmed complete.'
    assert result.outcome=='answered'
    assert result.answer.count(notice)==1
    assert result.citations and all('[SRC:'+c['source_id']+']' in result.answer for c in result.citations)
    assert result.diagnostics['graph_v2']['status']=='complete'


@pytest.mark.parametrize('language',['vi','en'])
def test_graph_answer_shows_reviewed_quantity_at_its_occurrence(connection,monkeypatch,language):
    monkeypatch.setattr(graph_v2,'engine',SimpleNamespace(connect=lambda:nullcontext(connection)))
    monkeypatch.setattr(graph_v2,'_ensure_engine',lambda:None)
    result=collect_rag_events(DefaultRagExecutor().run(replace(request(),response_language=language),RagInvocation('quantity-answer',mode='evaluation')))
    assert '2 piece' in result.answer
    assert '1:row-1:1' in result.answer
    assert result.citations and all('[SRC:'+c['source_id']+']' in result.answer for c in result.citations)
    assert result.diagnostics.budget.calculations==0


@pytest.mark.parametrize('language',['vi','en'])
def test_partial_contains_answer_keeps_quantities_without_claiming_whole_tree_total(connection,monkeypatch,language):
    from sqlalchemy import text
    connection.conn.execute(text('UPDATE ReviewedGraphOccurrences SET IsLeaf=0'))
    monkeypatch.setattr(graph_v2,'engine',SimpleNamespace(connect=lambda:nullcontext(connection)))
    monkeypatch.setattr(graph_v2,'_ensure_engine',lambda:None)
    result=collect_rag_events(DefaultRagExecutor().run(replace(request(),response_language=language),
        RagInvocation('partial-quantities',mode='test')))
    notice=('Số lượng đang hiển thị là số lượng đã xác nhận cho mỗi cụm cha, chưa phải tổng toàn bộ cây BOM. '
            'Nhánh chưa xác minh không được tính là 0.' if language=='vi' else
            'The displayed quantities are reviewed quantities per parent assembly, not a whole-tree BOM total. '
            'Unverified branches are not treated as zero.')
    assert result.outcome=='answered' and result.diagnostics['answer_outcome']=='partial_answer'
    assert result.diagnostics['graph_v2']['source_status']=='partial'
    assert '2 piece' in result.answer and result.citations
    assert notice in result.answer
    assert result.diagnostics.budget.calculations==0


@pytest.mark.parametrize('language',['vi','en'])
def test_graph_answer_distinguishes_complete_source_from_50_displayed_relations(bom_store,language):
    fixture_store,engine=bom_store
    expand_bom(fixture_store,count=60)
    result=run(engine,replace(request(),response_language=language))
    graph=result.diagnostics['graph_v2']
    assert graph['source_status']=='complete' and len(graph['manifest']['occurrences'])==60
    assert len(graph['edges'])==50 and result.diagnostics.budget.graph_examined_relations==60
    assert graph['status']=='partial' and result.diagnostics['answer_outcome']=='partial_answer'
    assert 'Chưa xác nhận' not in result.answer
    assert 'Coverage is not confirmed' not in result.answer
    assert '50/60' in result.answer
    assert ('nguồn' if language=='vi' else 'source') in result.answer.lower()
    assert ('đầy đủ' if language=='vi' else 'complete') in result.answer.lower()
    assert ('chưa phải tổng toàn bộ cây BOM' if language=='vi' else 'not a whole-tree BOM total') not in result.answer
    assert result.diagnostics.budget.calculations==0


@pytest.mark.parametrize('language',['vi','en'])
def test_graph_answer_explains_missing_preview_without_inventing_an_image(bom_store,language):
    _,engine=bom_store
    result=run(engine,replace(request(),response_language=language))
    assert result.diagnostics['graph_v2']['source_status']=='complete'
    assert result.diagnostics['answer_outcome']=='full_answer'
    assert ('ảnh xem trước' if language=='vi' else 'preview') in result.answer.lower()
    assert result.citations and all(c['page_url'] is None for c in result.citations)
    assert all(not c['has_vision'] for c in result.citations)
    assert all('[SRC:'+c['source_id']+']' in result.answer for c in result.citations)


def shared_child_fixture(connection):
    """Synthetic approved Graph rows; never business acceptance."""
    import json
    from sqlalchemy import text
    for table in ('KnowledgeGraphNode','KnowledgeGraphEdge','ReviewedGraphOccurrences','BangKeVatTu','StructuredNumericFact'):
        connection.conn.execute(text('DELETE FROM '+table))
    connection.conn.execute(text("INSERT INTO KnowledgeGraphNode VALUES(1,'document:1'),(2,'document:2'),(3,'part:sub'),(4,'part:p')"))
    connection.conn.execute(text("INSERT INTO ReviewedAssemblyBindings VALUES(1,2,1,1,1,1,'binding','product','factory','part:sub','approved','fixture','now',:hash,'snapshot')"), {'hash':'a'*64})
    connection.conn.execute(text('UPDATE ReviewedGraphDeclarations SET ExpectedRows=2 WHERE DocID=1'))
    rows=((1,'root-a','sub','2',False),(1,'root-b','sub','3',False),(2,'child-p','p','4',True))
    for edge_id,(doc,row_key,part,quantity,leaf) in enumerate(rows,1):
        quote=json.dumps({'source_row_id':row_key,'part':part,'quantity':quantity})
        connection.conn.execute(text("INSERT INTO KnowledgeGraphEdge VALUES(:id,:doc,:target,'approved','CONTAINS_PART',:doc,1,1,:quote)"),
            {'id':edge_id,'doc':doc,'target':4 if leaf else 3,'quote':quote})
        connection.conn.execute(text('INSERT INTO BangKeVatTu VALUES(:doc,1,:part,:quote)'),{'doc':doc,'part':part,'quote':quote})
        connection.conn.execute(text("INSERT INTO ReviewedGraphOccurrences VALUES(:oid,:id,:doc,1,'product','factory',:part,:binding,'approved','fixture','now',1,'quantity',:row,:quantity,'piece','per_parent',:leaf)"),
            {'oid':f'{doc}:{row_key}:1','id':edge_id,'doc':doc,'part':'part:'+part,'binding':None if leaf else 1,'row':row_key,'quantity':quantity,'leaf':int(leaf)})
        payload=json.dumps({'value':quantity,'unit':'piece','locator':{'page':1,'row_key':'page-1:'+row_key}})
        connection.conn.execute(text("INSERT INTO StructuredNumericFact VALUES(:doc,1,:hash,'approved',7,:payload,'quantity-scope')"),
            {'doc':doc,'hash':'a'*64,'payload':payload})


def test_shared_child_answer_preserves_each_quantity_path_and_both_source_pins(connection,monkeypatch):
    from mech_chatbot.api.source_file_pins import SourceFilePin,verify_file_pin
    shared_child_fixture(connection)
    monkeypatch.setattr(graph_v2,'engine',SimpleNamespace(connect=lambda:nullcontext(connection)))
    monkeypatch.setattr(graph_v2,'_ensure_engine',lambda:None)
    query=request()
    result=collect_rag_events(DefaultRagExecutor().run(query,RagInvocation('shared-child-answer',mode='test')))
    leaf_lines=[line for line in result.answer.splitlines() if '→ part:p ' in line]
    assert len(leaf_lines)==2
    assert any('2 → 4' in line and '1:root-a:1/2:child-p:1' in line for line in leaf_lines)
    assert any('3 → 4' in line and '1:root-b:1/2:child-p:1' in line for line in leaf_lines)
    profile={'roles':['reviewer'],'allowed_departments':['tech'],'allowed_sites':['factory'],'max_security_level':'internal'}
    for path in ('1:root-a:1/2:child-p:1','1:root-b:1/2:child-p:1'):
        cards=[c for c in result.citations if c['locator']['row_key']==path]
        assert {c['doc_id'] for c in cards}=={1,2}
        for card in cards:
            pin=SourceFilePin(version_no=card['version_no'],snapshot_id=card['snapshot_id'],source_hash=card['source_hash'],
                graph_context=query.graph_query,graph_hash=card['graph_hash'])
            assert verify_file_pin(card['doc_id'],profile,pin) is None
    assert result.diagnostics.budget.calculations==0


def test_graph_answer_does_not_publish_quantities_from_conflicting_source_pins(connection,monkeypatch):
    from .test_graph_v2 import Reader,batch,row,source
    from mech_chatbot.rag.graph_retrieval_v2 import traverse_graph_v2
    from mech_chatbot.rag import graph_query_v2
    conflicting={**row(1,1),'sources':({**source(1),'snapshot_id':'other-pin'},)}
    value=traverse_graph_v2(1,Reader({1:batch(1,[conflicting])}),product_scope='product')
    assert 'snapshot_mismatch' in value['reasons']
    monkeypatch.setattr(graph_v2,'engine',SimpleNamespace(connect=lambda:nullcontext(connection)))
    monkeypatch.setattr(graph_v2,'_ensure_engine',lambda:None)
    # Fault injection at the public repository boundary; SQL-path cases above use real reads.
    monkeypatch.setattr(graph_query_v2,'retrieve_graph_v2',lambda *args,**kwargs:value)
    result=collect_rag_events(DefaultRagExecutor().run(request(),RagInvocation('conflicting-pins',mode='test')))
    assert result.outcome=='refused' and result.citations==()
    assert '2 piece' not in result.answer and 'part:p' not in result.answer
    assert result.diagnostics['graph_v2']['status']=='unknown'
    assert result.diagnostics['graph_v2']['manifest'] is None


def test_graph_history_revalidates_the_actual_relations_before_replay(connection,monkeypatch):
    from sqlalchemy import text
    from mech_chatbot.db.repositories import chat
    connection.dialect=SimpleNamespace(name='sqlite')
    connection.conn.execute(text("INSERT INTO PhongBanChiaSe VALUES(1,'tech')"))
    for sql in (
        'CREATE TABLE LichSuChat(ChatID INTEGER PRIMARY KEY,SessionID TEXT,CauHoi_User TEXT,TraLoi_Bot TEXT,HinhAnhUpload TEXT,DanhGia INTEGER,RefImages TEXT,Username TEXT,ThoiGian TEXT)',
        'CREATE TABLE AnswerSource(ChatID INTEGER,DocID INTEGER,FileName TEXT,VersionNo INTEGER,VariantCode TEXT,ChunkRef TEXT,Score REAL,RankNo INTEGER,IsCurrent INTEGER,SourceLocatorJson TEXT)',
        'CREATE TABLE AnswerEvidence(EvidenceID INTEGER PRIMARY KEY,ChatID INTEGER,DocID INTEGER,PageNo INTEGER,SourceRef TEXT,SecurityLevel TEXT,RankNo INTEGER,SourceLocatorJson TEXT)',
        'CREATE TABLE ChatEvidenceManifest(ChatID INTEGER PRIMARY KEY,RequiresAuthorization INTEGER,IsComplete INTEGER,EvidenceCount INTEGER,SchemaVersion TEXT,UpdatedAt TEXT,IsStructuredComplete INTEGER DEFAULT 0)',
    ):connection.conn.execute(text(sql))
    engine=SimpleNamespace(connect=lambda:nullcontext(connection),begin=lambda:nullcontext(connection))
    monkeypatch.setattr(graph_v2,'engine',engine);monkeypatch.setattr(graph_v2,'_ensure_engine',lambda:None)
    result=collect_rag_events(DefaultRagExecutor().run(request(),RagInvocation('history',mode='test')))
    connection.conn.execute(text("INSERT INTO LichSuChat VALUES(11,'s','Question',:answer,NULL,NULL,NULL,'reader','2026-10-06')"),{'answer':result.answer})
    assert chat.save_answer_evidence(11,list(result.diagnostics['retrieved_docs']),db_engine=engine)
    assert chat.save_answer_sources(11,list(result.citations),db_engine=engine)
    def read():return chat.get_chat_history('s',username='reader',allowed_departments=['tech'],
                                         allowed_sites=['factory'],user_clearance='internal',db_engine=engine)[-1]
    restored=read()
    assert restored['content']==result.answer and len(restored['structured_citations'])==len(result.citations)
    connection.conn.execute(text("UPDATE ReviewedGraphOccurrences SET ReviewStatus='rejected'"))
    revoked=read()
    assert 'part:p' not in revoked['content'] and revoked.get('structured_citations',[])==[]


@pytest.mark.parametrize('change',['site','clearance','revision','pending','binding'])
def test_graph_query_never_leaks_unavailable_part_or_source_metadata(connection,monkeypatch,change):
    from sqlalchemy import text
    monkeypatch.setattr(graph_v2,'engine',SimpleNamespace(connect=lambda:nullcontext(connection)))
    monkeypatch.setattr(graph_v2,'_ensure_engine',lambda:None)
    query=request()
    if change=='site':query=replace(query,access=replace(query.access,allowed_sites=frozenset({'other'})))
    elif change=='clearance':query=replace(query,access=replace(query.access,max_security_level='public'))
    else:
        sql={'revision':'UPDATE TaiLieu SET VersionNo=2 WHERE DocID=1',
             'pending':"UPDATE ReviewedGraphOccurrences SET ReviewStatus='pending'",
             'binding':"UPDATE ReviewedGraphOccurrences SET IsLeaf=0"}[change]
        connection.conn.execute(text(sql))
    result=collect_rag_events(DefaultRagExecutor().run(query,RagInvocation('unavailable',mode='evaluation')))
    if change=='binding':
        assert result.diagnostics['graph_v2']['status']=='partial'
        assert 'Chưa xác nhận' in result.answer
    else:
        assert result.outcome=='refused' and result.citations==()
        assert 'part:p' not in result.answer


def test_where_used_marks_unproven_corpus_coverage_as_partial(connection,monkeypatch):
    monkeypatch.setattr(graph_v2,'engine',SimpleNamespace(connect=lambda:nullcontext(connection)))
    monkeypatch.setattr(graph_v2,'_ensure_engine',lambda:None)
    query=replace(request(),graph_query=GraphQuery('part:p','product','factory','where_used'))
    result=collect_rag_events(DefaultRagExecutor().run(query,RagInvocation('where',mode='test')))
    assert result.outcome=='answered' and result.diagnostics['graph_v2']['status']=='partial'
    assert result.diagnostics['answer_outcome']=='partial_answer'
    assert 'chưa phải tổng toàn bộ cây BOM' not in result.answer


def test_reviewed_empty_assembly_is_answered_with_its_declaration_source(connection,monkeypatch):
    from sqlalchemy import text
    connection.conn.execute(text('DELETE FROM ReviewedGraphOccurrences WHERE ParentDocID=1'))
    connection.conn.execute(text('UPDATE ReviewedGraphDeclarations SET ExpectedRows=0 WHERE DocID=1'))
    monkeypatch.setattr(graph_v2,'engine',SimpleNamespace(connect=lambda:nullcontext(connection)))
    monkeypatch.setattr(graph_v2,'_ensure_engine',lambda:None)
    result=collect_rag_events(DefaultRagExecutor().run(request(),RagInvocation('empty',mode='evaluation')))
    assert result.outcome=='answered' and result.diagnostics['graph_v2']['status']=='complete'
    assert 'không có quan hệ' in result.answer and len(result.citations)==1
    assert result.diagnostics.budget.graph_edges==0


@pytest.mark.parametrize('mode,roles,code',[('production',{'reviewer'},'ValueError'),
                                         ('pilot_replay',{'reviewer'},'ValueError'),('evaluation',{'viewer'},'PermissionError')])
def test_graph_executor_enforces_its_own_context_and_role_boundary(connection,monkeypatch,mode,roles,code):
    from mech_chatbot.rag.execution import RagFailed
    query=replace(request(),access=replace(request().access,roles=frozenset(roles)))
    events=list(DefaultRagExecutor().run(query,RagInvocation('guard',mode=mode)))
    assert any(isinstance(event,RagFailed) and event.code==code for event in events)


@pytest.mark.parametrize('root,scope,site,direction',[('document:0','p','s','contains'),('part:','p','s','where_used'),
    ('document:x','p','s','contains'),('document:1','','s','contains'),('document:1','p','s','invalid')])
def test_graph_context_validation_is_bounded_and_explicit(root,scope,site,direction):
    with pytest.raises(ValueError):GraphQuery(root,scope,site,direction)


def test_graph_request_refuses_silent_mixing_with_an_unrelated_template():
    with pytest.raises(ValueError):replace(request(),scope_comparison=('a','b'))


def test_graph_request_honors_cancellation_and_intent_overflow(connection,monkeypatch):
    import threading
    from mech_chatbot.rag.execution import RagCancelled,RagToken
    from mech_chatbot.rag.query_decomposition import QUERY_INTENT_OVERFLOW_NOTICE
    cancel=threading.Event();cancel.set()
    events=list(DefaultRagExecutor().run(request(),RagInvocation('cancel',mode='test'),cancellation=cancel))
    assert any(isinstance(e,RagCancelled) for e in events) and not any(isinstance(e,RagToken) for e in events)
    query=replace(request(),question='Cụm chứa gì; dùng ở đâu; vật liệu; thời gian giao')
    result=collect_rag_events(DefaultRagExecutor().run(query,RagInvocation('overflow',mode='evaluation')))
    assert result.outcome=='refused' and result.answer==QUERY_INTENT_OVERFLOW_NOTICE
    assert result.diagnostics.budget.graph_edges==0

@pytest.mark.parametrize('change',[None,'relation','revision','rights','snapshot'])
def test_graph_source_download_pin_rechecks_real_sql_evidence(connection,monkeypatch,change):
    from sqlalchemy import text
    from fastapi import HTTPException
    from mech_chatbot.api.source_file_pins import SourceFilePin,verify_file_pin
    monkeypatch.setattr(graph_v2,'engine',SimpleNamespace(connect=lambda:nullcontext(connection)))
    monkeypatch.setattr(graph_v2,'_ensure_engine',lambda:None)
    query=request()
    result=collect_rag_events(DefaultRagExecutor().run(query,RagInvocation('pin',mode='test')))
    card=result.citations[0]
    pin=SourceFilePin(version_no=card['version_no'],snapshot_id=card['snapshot_id'],source_hash=card['source_hash'],
                      graph_context=query.graph_query,graph_hash=card['graph_hash'])
    statements={'relation':"UPDATE ReviewedGraphOccurrences SET ReviewStatus='rejected'",
                'revision':'UPDATE TaiLieu SET VersionNo=2 WHERE DocID=1',
                'rights':'DELETE FROM PhongBanChiaSe',
                'snapshot':'UPDATE StructuredSourceSnapshot SET CurrentSnapshot=0'}
    profile={'roles':['reviewer'],'allowed_departments':['tech'],'allowed_sites':['factory'],'max_security_level':'internal'}
    if change is None:
        assert verify_file_pin(card['doc_id'],profile,pin) is None
        from urllib.parse import urlsplit,parse_qs
        import json
        parameters=parse_qs(urlsplit(card['original_url']).query)
        assert parameters['graph_hash']==[pin.graph_hash]
        assert json.loads(parameters['graph_context'][0])==card['graph_context']
    else:
        connection.conn.execute(text(statements[change]))
        with pytest.raises(HTTPException) as failure:verify_file_pin(card['doc_id'],profile,pin)
        assert failure.value.status_code==404 and failure.value.detail=='Source unavailable'


def test_one_invalid_source_blocks_the_graph_answer_instead_of_omitting_its_citation(connection,monkeypatch):
    import copy
    monkeypatch.setattr(graph_v2,'engine',SimpleNamespace(connect=lambda:nullcontext(connection)))
    monkeypatch.setattr(graph_v2,'_ensure_engine',lambda:None)
    profile={'roles':['reviewer'],'allowed_departments':['tech'],'allowed_sites':['factory'],'max_security_level':'internal'}
    value=copy.deepcopy(graph_v2.retrieve_graph_v2('document:1',profile,product_scope='product',site='factory'))
    edge=dict(value['edges'][0])
    edge['sources']=(*edge['sources'],{**edge['sources'][0],'source_hash':'invalid'})
    value={**value,'edges':(edge,)}
    from mech_chatbot.rag import graph_query_v2
    monkeypatch.setattr(graph_query_v2,'retrieve_graph_v2',lambda *args,**kwargs:value)
    result=collect_rag_events(DefaultRagExecutor().run(request(),RagInvocation('invalid-source',mode='test')))
    assert result.outcome=='refused' and 'part:p' not in result.answer and result.citations==()


@pytest.mark.parametrize('change',['wrong_document','wrong_snapshot','wrong_hash','missing_image'])
def test_graph_pin_fails_closed_for_wrong_source_or_unbound_preview(connection,monkeypatch,change):
    from fastapi import HTTPException
    from mech_chatbot.api.source_file_pins import SourceFilePin,verify_file_pin
    monkeypatch.setattr(graph_v2,'engine',SimpleNamespace(connect=lambda:nullcontext(connection)))
    monkeypatch.setattr(graph_v2,'_ensure_engine',lambda:None)
    result=collect_rag_events(DefaultRagExecutor().run(request(),RagInvocation('wrong-pin',mode='test')))
    card=result.citations[0]
    pin=SourceFilePin(version_no=card['version_no'],snapshot_id=card['snapshot_id'],source_hash=card['source_hash'],
                      graph_context=request().graph_query,graph_hash=card['graph_hash'])
    if change=='wrong_snapshot':pin=pin.model_copy(update={'snapshot_id':'wrong'})
    if change=='wrong_hash':pin=pin.model_copy(update={'source_hash':'b'*64})
    profile={'roles':['reviewer'],'allowed_departments':['tech'],'allowed_sites':['factory'],'max_security_level':'internal'}
    with pytest.raises(HTTPException) as failure:
        verify_file_pin(99 if change=='wrong_document' else card['doc_id'],profile,pin,
                        page_no=1 if change=='missing_image' else None)
    assert failure.value.status_code==404


@pytest.mark.parametrize('graph_context,scope_id',[('invalid',None),('{"root_key":"document:0","product_scope":"p","site":"s"}',None),
                                                  ('{}','a'),('[]',None)])
def test_graph_download_pin_rejects_malformed_or_mixed_context(graph_context,scope_id):
    from fastapi import HTTPException
    from mech_chatbot.api.source_file_pins import file_pin
    with pytest.raises(HTTPException) as failure:
        file_pin(version_no=1,snapshot_id='s',source_hash='a'*64,scope_id=scope_id,
                 scope_hash='b'*64 if scope_id else None,image_hash=None,graph_context=graph_context,graph_hash='c'*64)
    assert failure.value.status_code==422
