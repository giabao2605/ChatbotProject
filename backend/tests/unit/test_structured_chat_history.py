"""Persist and re-authorize structured chat provenance through public SQL seams."""
from dataclasses import replace
import pytest
from sqlalchemy import text

from mech_chatbot.config.repository_runtime import bind_repository_runtime
from mech_chatbot.db.repositories import chat
from mech_chatbot.rag.execution import DefaultRagExecutor, RagInvocation, collect_rag_events
from .test_rag_scope_comparison import approved_pair, approved_empty_pair, request
from .test_structured_evidence_repository import PROFILE, store


@pytest.fixture
def history_store(store):
    with store.begin() as conn:
        for statement in (
            'CREATE TABLE LichSuChat(ChatID INTEGER PRIMARY KEY,SessionID TEXT,CauHoi_User TEXT,TraLoi_Bot TEXT,HinhAnhUpload TEXT,DanhGia INTEGER,RefImages TEXT,Username TEXT,ThoiGian TEXT)',
            'CREATE TABLE AnswerSource(ChatID INTEGER,DocID INTEGER,FileName TEXT,VersionNo INTEGER,VariantCode TEXT,ChunkRef TEXT,Score REAL,RankNo INTEGER,IsCurrent INTEGER,SourceLocatorJson TEXT)',
            'CREATE TABLE AnswerEvidence(EvidenceID INTEGER PRIMARY KEY,ChatID INTEGER,DocID INTEGER,PageNo INTEGER,SourceRef TEXT,SecurityLevel TEXT,RankNo INTEGER,SourceLocatorJson TEXT,SourceVersion INTEGER)',
            'CREATE TABLE ChatEvidenceManifest(ChatID INTEGER PRIMARY KEY,RequiresAuthorization INTEGER,IsComplete INTEGER,EvidenceCount INTEGER,SchemaVersion TEXT,UpdatedAt TEXT,IsStructuredComplete INTEGER DEFAULT 0,AdvancedRagJson TEXT)',
        ):
            conn.execute(text(statement))
    return store


def persist_comparison(store, *, query=False):
    approved_pair(store)
    selected = request()
    if query:
        from mech_chatbot.domain.query_plan import QueryBranch, QueryPlan
        selected = replace(selected, scope_comparison=None, query_plan=QueryPlan((
            QueryBranch('b1','So sánh hai kỳ',scope_comparison=('a','b')),
            QueryBranch('b2','Nguồn còn thiếu',scope_comparison=('missing','b')),
            QueryBranch('b3','Ý phụ thuộc',scope_comparison=('a','b'),depends_on=('b2',)))))
    with bind_repository_runtime(db_engine=store):
        result = collect_rag_events(DefaultRagExecutor().run(selected,RagInvocation('persist',mode='test')))
    with store.begin() as conn:
        conn.execute(text("INSERT INTO LichSuChat VALUES(11,'s1','Compare',:answer,NULL,NULL,NULL,'approver','2026-10-06')"),
                     {'answer':result.answer+result.ref_text})
    chat.save_answer_evidence(11, list(result.diagnostics['retrieved_docs']), db_engine=store)
    chat.save_answer_sources(11, [{**card, 'file_goc':card['file_name'],'trang':card['page_no']} for card in result.citations], db_engine=store)
    return result


def read_history(store, *, sites=('factory',)):
    return chat.get_chat_history('s1', username='approver', user_clearance='internal',
                                 allowed_departments=('ENG',),allowed_sites=sites,db_engine=store)


@pytest.mark.parametrize('change', ['site', 'revision', 'correction'])
@pytest.mark.parametrize('live', [False, True])
def test_scope_total_history_keeps_steps_and_redacts_unavailable_basis(history_store, change, live):
    from mech_chatbot.application.chat_result_history import restore_history_result
    from .test_scope_total_production import capability
    approved_pair(history_store)
    cap = capability(history_store) if live else None
    with bind_repository_runtime(db_engine=history_store):
        result = collect_rag_events(DefaultRagExecutor(math_v2_authorizer=lambda: cap).run(
            replace(request(),scope_comparison=None,scope_total='a'),RagInvocation('sum-history',mode='production' if live else 'test')))
    with history_store.begin() as conn:
        conn.execute(text("INSERT INTO LichSuChat VALUES(11,'s1','Total',:answer,NULL,NULL,NULL,'approver','2026-10-19')"),
                     {'answer':result.answer+result.ref_text})
    assert chat.save_answer_evidence(11,list(result.diagnostics['retrieved_docs']),db_engine=history_store,
        advanced_rag={'math':result.diagnostics['math_v2']},citation_cards=result.citations)
    assert chat.save_answer_sources(11,[{**c,'file_goc':c['file_name'],'trang':c['page_no']} for c in result.citations],db_engine=history_store)
    before=read_history(history_store)[-1]
    restored=restore_history_result(before['_advanced_rag_payload'],before['structured_citations'])
    assert restored['math']['steps'][0]['display_value']=='4.00'
    if change=='revision':
        with history_store.begin() as conn:conn.execute(text('UPDATE TaiLieu SET VersionNo=3'))
    elif change=='correction':
        from mech_chatbot.db.repositories.structured_evidence import review_numeric_fact
        review_numeric_fact('fact-a',PROFILE,action='reject',note='Rechecked source',db_engine=history_store)
    after=read_history(history_store,sites=('other',) if change=='site' else ('factory',))[-1]
    assert after['sources_redacted'] and '_advanced_rag_payload' not in after and '4.00' not in after['content']


def test_query_scope_totals_history_preserves_each_scope_and_redacts_changed_basis(history_store):
    from mech_chatbot.application.chat_result_history import restore_history_result
    from mech_chatbot.domain.query_plan import QueryBranch,QueryPlan
    approved_pair(history_store)
    plan=QueryPlan((QueryBranch('a','Tổng A',scope_total='a'),QueryBranch('b','Tổng B',scope_total='b')))
    with bind_repository_runtime(db_engine=history_store):
        result=collect_rag_events(DefaultRagExecutor().run(replace(request(),scope_comparison=None,query_plan=plan),RagInvocation('query-sum-history',mode='test')))
    with history_store.begin() as conn:
        conn.execute(text("INSERT INTO LichSuChat VALUES(11,'s1','Totals',:answer,NULL,NULL,NULL,'approver','2026-10-19')"),{'answer':result.answer+result.ref_text})
    assert chat.save_answer_evidence(11,list(result.diagnostics['retrieved_docs']),db_engine=history_store,
        advanced_rag={'query':result.diagnostics['query_v2']},citation_cards=result.citations)
    assert chat.save_answer_sources(11,[{**c,'file_goc':c['file_name'],'trang':c['page_no']} for c in result.citations],db_engine=history_store)
    before=read_history(history_store)[-1]
    restored=restore_history_result(before['_advanced_rag_payload'],before['structured_citations'])['query']
    assert [branch['sources'][0]['scope_id'] for branch in restored['branches']]==['a','b']
    with history_store.begin() as conn:conn.execute(text('UPDATE TaiLieu SET VersionNo=3'))
    after=read_history(history_store)[-1]
    assert after['sources_redacted'] and '_advanced_rag_payload' not in after


@pytest.mark.parametrize('change', ['excluded_review', 'excluded_revision'])
def test_detail_selection_history_invalidates_when_excluded_source_changes(history_store, change):
    from .test_structured_detail_selection import prepare, review
    from .test_rag_evidence_service import other_source, approve
    from mech_chatbot.db.repositories.structured_evidence import save_extracted_scope, review_numeric_fact
    from mech_chatbot.services import rag_evidence_service as service
    prepare(history_store, separate_subtotal_source=True)
    review(history_store)
    other = other_source(history_store)
    save_extracted_scope(other, db_engine=history_store)
    with bind_repository_runtime(db_engine=history_store):
        approve(other)
        service.review_scope(other.scope_id, PROFILE, expected_row_ids=('row1',),
            rounding_scale=2, rounding_mode='ROUND_HALF_UP', definition='Detail amounts', note='Checked source')
        result = collect_rag_events(DefaultRagExecutor().run(
            replace(request(), scope_comparison=('scope-s1', other.scope_id)), RagInvocation('detail-history', mode='test')))
    assert '300' in result.answer
    with history_store.begin() as conn:
        conn.execute(text("INSERT INTO LichSuChat VALUES(11,'s1','Compare',:answer,NULL,NULL,NULL,'approver','2026-10-14')"),
                     {'answer':result.answer+result.ref_text})
    assert chat.save_answer_evidence(11, list(result.diagnostics['retrieved_docs']), db_engine=history_store)
    assert chat.save_answer_sources(11, [{**c,'file_goc':c['file_name'],'trang':c['page_no']} for c in result.citations], db_engine=history_store)
    assert read_history(history_store)[-1]['content'] == result.answer+result.ref_text
    if change == 'excluded_review':
        review_numeric_fact('fact-2', PROFILE, action='reject', note='Rechecked subtotal', db_engine=history_store)
    else:
        with history_store.begin() as conn:
            conn.execute(text('UPDATE TaiLieu SET VersionNo=3 WHERE DocID=2'))
    answer = read_history(history_store)[-1]
    assert answer['sources_redacted'] and '300' not in answer['content']


@pytest.mark.parametrize('identity', ['strings', 'integers', 'mixed'])
def test_saved_empty_scope_keeps_both_sources_and_redacts_after_empty_source_revocation(history_store, tmp_path, identity):
    selected, _ = approved_empty_pair(history_store, tmp_path, identity=identity)
    with bind_repository_runtime(db_engine=history_store):
        result = collect_rag_events(DefaultRagExecutor().run(selected, RagInvocation('empty-history', mode='test')))
    with history_store.begin() as conn:
        conn.execute(text("INSERT INTO LichSuChat VALUES(11,'s1','Compare',:answer,NULL,NULL,NULL,'approver','2026-10-09')"),
                     {'answer':result.answer+result.ref_text})
    assert chat.save_answer_evidence(11, list(result.diagnostics['retrieved_docs']), db_engine=history_store)
    assert chat.save_answer_sources(11, [{**c, 'file_goc':c['file_name'], 'trang':c['page_no']} for c in result.citations], db_engine=history_store)
    answer = read_history(history_store)[-1]
    assert answer['content'] == result.answer+result.ref_text
    assert {card['doc_id'] for card in answer['structured_citations']} == {1, 2}
    assert next(c for c in answer['structured_citations'] if c['doc_id']==1)['fact_count'] == 0
    with history_store.begin() as conn:
        conn.execute(text('UPDATE StructuredSourceSnapshot SET CurrentSnapshot=0 WHERE DocID=1'))
    answer = read_history(history_store)[-1]
    assert answer['sources_redacted'] and 'structured_citations' not in answer
    assert '1.24' not in answer['content']


@pytest.mark.parametrize('change', ['VersionNo=3','IsCurrent=0',"EffectiveStatus='superseded'",
                                  "EffectiveDate='2099-01-01'","ExpiryDate='2000-01-01'",'display'])
def test_text_history_rechecks_revision_and_effective_dates_for_legacy_page_sources(history_store, change):
    with history_store.begin() as conn:
        conn.execute(text("INSERT INTO LichSuChat VALUES(11,'s1','Question','Answer [SRC:D1P1]',NULL,NULL,NULL,'approver','2026-10-07')"))
    card = {'doc_id':1,'version_no':2,'source_id':'D1P1','page_no':1,'file_name':'export.csv'}
    metadata = {'text_query':{'schema':'query-text-v2','accepted':False,'offline_only':True,'branches':[
        {'branch_id':'b1','subquery':'Question','outcome':'full_answer','cited_sources':[card]}]}}
    assert chat.save_answer_evidence(11, [{**card,'trang':1}], db_engine=history_store,
                                     advanced_rag=metadata, citation_cards=[card])
    assert chat.save_answer_sources(11, [{**card,'trang':1,'file_goc':'export.csv'}], db_engine=history_store)
    assert '_advanced_rag_payload' in read_history(history_store)[-1]
    with history_store.begin() as conn:
        conn.execute(text('UPDATE AnswerSource SET VersionNo=3' if change=='display' else 'UPDATE TaiLieu SET '+change))
    answer = read_history(history_store)[-1]
    assert answer['sources_redacted'] and '_advanced_rag_payload' not in answer


def test_branch_history_rejects_conflicting_revisions_in_deduplicated_retrieval_basis(history_store):
    cards = [{'doc_id':1,'version_no':2,'trang':1,'source_id':'D1P1'},
             {'doc_id':1,'version_no':3,'trang':1,'source_id':'D1P1'}]
    metadata = {'text_query':{'schema':'query-text-v2','accepted':False,'offline_only':True,'branches':[]}}
    assert chat.save_answer_evidence(11, cards, db_engine=history_store, advanced_rag=metadata, citation_cards=cards) is False


def test_previous_history_schema_reads_legacy_pins_without_requiring_branch_metadata(history_store):
    before = persist_comparison(history_store)
    with history_store.begin() as conn:
        conn.execute(text('ALTER TABLE ChatEvidenceManifest DROP COLUMN AdvancedRagJson'))
    answer = read_history(history_store)[-1]
    assert answer['content']==before.answer+before.ref_text
    assert '_advanced_rag_payload' not in answer and len(answer['structured_citations'])==2


def test_saved_comparison_keeps_both_scope_citations_and_sheet_cells(history_store):
    before = persist_comparison(history_store)
    messages = read_history(history_store)
    answer = messages[-1]
    assert answer['content'] == before.answer+before.ref_text
    assert {card['locator']['cell'] for card in answer['structured_citations']} == {'B2','B3'}
    assert {card['source_id'] for card in answer['structured_citations']} == {card['source_id'] for card in before.citations}
    assert all(card['page_no'] is None for card in answer['structured_citations'])


@pytest.mark.parametrize('change',['site','revision','snapshot','correction','missing_evidence','missing_source',
                                  'corrupt_json','wrong_pin','missing_schema','null_payload','wrong_source'])
def test_history_hides_entire_answer_when_any_calculation_basis_is_unavailable(history_store,change):
    persist_comparison(history_store)
    statements = {'revision':'UPDATE TaiLieu SET VersionNo=3',
                  'snapshot':'UPDATE StructuredSourceSnapshot SET CurrentSnapshot=0',
                  'missing_evidence':"DELETE FROM AnswerEvidence WHERE SourceRef LIKE '%L%' AND RankNo=2",
                  'missing_source':'DELETE FROM AnswerSource',
                  'corrupt_json':"UPDATE AnswerEvidence SET SourceLocatorJson='invalid'",
                  'wrong_pin':"UPDATE AnswerEvidence SET SourceRef='D1L0000000000000000'",
                  'missing_schema':'ALTER TABLE AnswerEvidence DROP COLUMN SourceLocatorJson',
                  'null_payload':'UPDATE AnswerEvidence SET SourceLocatorJson=NULL',
                  'wrong_source':"UPDATE AnswerSource SET SourceLocatorJson=REPLACE(SourceLocatorJson,'B2','B8')"}
    if change == 'correction':
        from mech_chatbot.db.repositories.structured_evidence import review_numeric_fact,review_scope_completeness
        from mech_chatbot.domain.structured_evidence import RoundingRule
        review_numeric_fact('fact-a',PROFILE,action='approve',value_text='6',note='Offline correction',db_engine=history_store)
        review_scope_completeness('a',PROFILE,expected_row_ids=('row-a',),rounding_rule=RoundingRule(2),
                                  definition='Reviewed amount',note='Offline complete recheck',db_engine=history_store)
    elif change in statements:
        with history_store.begin() as conn:
            conn.execute(text(statements[change]))
    messages = read_history(history_store,sites=('other',) if change=='site' else ('factory',))
    answer = messages[-1]
    assert '4.00' not in answer['content'] and '8.00' not in answer['content']
    assert 'row-a' not in answer['content'] and 'SourceID' not in answer['content']
    assert answer.get('structured_citations',[]) == [] and answer['ref_images']==[]


def test_history_never_replays_another_users_session_even_for_admin(history_store):
    persist_comparison(history_store)
    assert chat.get_chat_history('s1',username='other',is_admin=True,db_engine=history_store)==[]


def test_history_keeps_approved_shared_sources_readable_for_department_users(history_store):
    with history_store.begin() as conn:
        conn.execute(text("UPDATE PhongBanChiaSe SET DeptCode='CHUNG'"))
    before = persist_comparison(history_store)
    answer = read_history(history_store)[-1]
    assert answer['content']==before.answer+before.ref_text
    assert len(answer['structured_citations'])==2


def test_history_does_not_release_a_body_referencing_a_different_basis(history_store):
    before = persist_comparison(history_store)
    with history_store.begin() as conn:
        conn.execute(text('UPDATE LichSuChat SET TraLoi_Bot=REPLACE(TraLoi_Bot,:old,:new)'),
                     {'old':before.citations[0]['source_id'],'new':'D1L0000000000000000'})
    answer = read_history(history_store)[-1]
    assert '4.00' not in answer['content'] and 'D1L0000000000000000' not in answer['content']
    assert answer.get('structured_citations',[])==[]


def test_previous_bundle_reader_hides_v2_rows_after_rollback_without_deleting_them(history_store):
    from contextlib import contextmanager
    import subprocess
    from types import ModuleType
    persist_comparison(history_store)
    source = subprocess.run(['git','show','34cf24954faa78308aef985237ddb2228a225e85:src/mech_chatbot/db/repositories/chat.py'],
                            check=True,capture_output=True,encoding='utf-8').stdout
    legacy = ModuleType('mech_chatbot.db.repositories._rollback_fixture')
    legacy.__package__ = 'mech_chatbot.db.repositories'
    exec(compile(source,'<verified-baseline-chat-repository>','exec'),legacy.__dict__)
    class Connection:
        def __init__(self,connection): self.connection=connection
        def execute(self,statement,params=None):
            # Adapt schema qualification only; keep the actual baseline policy unchanged.
            return self.connection.execute(text(str(statement).replace('dbo.','')),params)
    class Engine:
        @contextmanager
        def connect(self):
            with history_store.connect() as connection:
                yield Connection(connection)
    with bind_repository_runtime(db_engine=Engine()):
        messages = legacy.get_chat_history('s1',username='approver',user_clearance='internal',
                                           allowed_departments=('ENG',),allowed_sites=('factory',))
    assert len(messages)==2 and '4.00' not in messages[-1]['content']
    # Forward again without clearing rows: current reader verifies and restores the answer.
    assert '4.00' in read_history(history_store)[-1]['content']


@pytest.mark.parametrize('kind', ['query','text_query','mixed_query'])
def test_history_http_restores_sheet_citations_and_hides_revoked_scope_metadata(history_store,monkeypatch,kind):
    from functools import partial
    from types import SimpleNamespace
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from sqlalchemy import create_engine
    from sqlalchemy.pool import StaticPool
    from mech_chatbot.api.dependencies import csrf_profile
    from mech_chatbot.api.routers import chat as routes
    before = persist_comparison(history_store, query=kind=='query')
    advanced = {'query':before.diagnostics['query_v2']} if kind=='query' else {}
    if kind=='text_query':
        advanced = {'text_query':{'schema':'query-text-v2','accepted':False,'offline_only':True,'branches':[
            {'branch_id':'b1','subquery':'Câu hỏi đầu','outcome':'full_answer','cited_sources':[
                *before.citations, {'source_id':'D99P1','original_url':'https://invalid.example/private'}]},
            {'branch_id':'b2','subquery':'Câu hỏi thiếu nguồn','outcome':'insufficient_evidence','cited_sources':[]}]}}
    if kind=='mixed_query':
        advanced = {'query':{'schema':'query-plan-mixed-v1-draft','status':'partial',
            'accepted':False,'offline_only':True,'text_answer':'Unchecked debug text', 'branches':[
                {'branch_id':'cost','question':'So sánh','status':'complete','data_complete':True,
                 'math':before.diagnostics['math_v2'],'sources':list(before.citations)},
                {'branch_id':'terms','question':'Điều kiện','status':'complete','data_complete':False,
                 'sources':list(before.citations)},
                {'branch_id':'guide','question':'Hướng dẫn','status':'unknown','data_complete':False,
                 'sources':[]}]}}
    assert chat.save_answer_evidence(11, list(before.diagnostics['retrieved_docs']), db_engine=history_store,
                                     advanced_rag=advanced, citation_cards=before.citations)
    shared = create_engine('sqlite://',connect_args={'check_same_thread':False},poolclass=StaticPool)
    with history_store.connect() as original, shared.connect() as target:
        original.connection.driver_connection.backup(target.connection.driver_connection)
    app = FastAPI()
    app.include_router(routes.router)
    app.dependency_overrides[csrf_profile] = lambda:PROFILE
    app.state.runtime = SimpleNamespace(app_support_queries=SimpleNamespace(answer_sources_for_chat_ids=lambda ids:[]))
    monkeypatch.setattr(routes,'get_chat_history',partial(chat.get_chat_history,db_engine=shared))
    try:
        with TestClient(app) as client:
            response = client.post('/api/chat/history',json={'session_id':'s1'})
            assert response.status_code==200
            answer = response.json()['messages'][-1]
            assert {c['locator']['cell'] for c in answer['citations']}=={'B2','B3'}
            assert 'Sheet CSV' in answer['ref_text'] and 'Trang None' not in answer['ref_text']
            assert 'structured_citations' not in answer
            assert '_advanced_rag_payload' not in answer
            branches = answer['advanced_rag']['query' if kind=='mixed_query' else kind]['branches']
            if kind=='query':
                assert [branch['status'] for branch in branches] == ['complete','unknown','blocked']
                assert [step['display_value'] for step in branches[0]['math']['steps']] == ['4.00','8.00','4.00','100.00']
                assert 'scopes' not in branches[0]['math']
            elif kind=='text_query':
                assert [branch['outcome'] for branch in branches] == ['full_answer','insufficient_evidence']
            else:
                assert answer['advanced_rag']['query']['schema']=='query-plan-mixed-v1-draft'
                assert [branch['status'] for branch in branches]==['complete','complete','unknown']
                assert branches[1]['sources']==branches[0]['sources']
                assert 'text_answer' not in answer['advanced_rag']['query']
            source_key = 'cited_sources' if kind=='text_query' else 'sources'
            assert {source['locator']['cell'] for source in branches[0][source_key]} == {'B2','B3'}
            assert 'secret_debug' not in str(answer['advanced_rag']) and 'D99' not in str(answer['advanced_rag'])
            import json
            with shared.connect() as conn:
                original_payload = conn.execute(text('SELECT AdvancedRagJson FROM ChatEvidenceManifest WHERE ChatID=11')).scalar()
            assert 'original_url' not in original_payload and 'source_hash' not in original_payload
            for invalid in ('not-json', '{"schema":"future"}',
                            json.dumps({**json.loads(original_payload), 'result':{'schema':'invalid'}})):
                with shared.begin() as conn:
                    conn.execute(text('UPDATE ChatEvidenceManifest SET AdvancedRagJson=:payload WHERE ChatID=11'),{'payload':invalid})
                hidden = client.post('/api/chat/history',json={'session_id':'s1'}).json()['messages'][-1]
                assert hidden['sources_redacted'] and hidden['citations']==[] and 'advanced_rag' not in hidden
            with shared.begin() as conn:
                conn.execute(text('UPDATE ChatEvidenceManifest SET AdvancedRagJson=:payload WHERE ChatID=11'),{'payload':original_payload})
            with shared.begin() as conn:
                conn.execute(text('UPDATE StructuredSourceSnapshot SET CurrentSnapshot=0'))
            app.state.runtime.app_support_queries.answer_sources_for_chat_ids = lambda ids:[(11,1,'Nội dung',2,'2',0.5,0)]
            revoked = client.post('/api/chat/history',json={'session_id':'s1'}).json()['messages'][-1]
            assert revoked['citations']==[] and revoked['ref_text']==''
            assert 'advanced_rag' not in revoked
            assert 'batch-1' not in str(revoked) and 'row-a' not in str(revoked)
    finally:
        shared.dispose()
