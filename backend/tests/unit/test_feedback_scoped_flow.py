"""Offline SQL/HTTP coverage of complete evidence and current reviewer access."""
import json
from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, text
from sqlalchemy.pool import StaticPool

from mech_chatbot.auth import core
from mech_chatbot.api import dependencies
from mech_chatbot.api.routers import operations
from mech_chatbot.db.repositories import chat, feedback, ui_queries
from mech_chatbot.db.repositories.feedback_access import FeedbackSchemaUnavailable
from mech_chatbot.db.repositories.feedback_access import pin_generation_sources
from mech_chatbot.rag import regression
from mech_chatbot.rag.execution import RagCompleted, RagPrepared, RagToken, RagFailed, RagCancelled
from .test_rag_pipeline_public_characterization import offline_pipeline

pytestmark = pytest.mark.unit


@pytest.fixture
def scoped_db(monkeypatch):
    engine = create_engine('sqlite://',poolclass=StaticPool,connect_args={'check_same_thread':False})
    schema = [
        'CREATE TABLE Users(UserID INTEGER,Username TEXT,DisplayName TEXT,Department TEXT,IsActive INTEGER,PreferredLanguage TEXT)',
        'CREATE TABLE Roles(RoleID INTEGER,RoleName TEXT)', 'CREATE TABLE UserRoles(UserID INTEGER,RoleID INTEGER)',
        'CREATE TABLE UserDepartments(UserID INTEGER,Department TEXT)',
        'CREATE TABLE UserSites(UserID INTEGER,Site TEXT)', 'CREATE TABLE UserSecurityClearance(UserID INTEGER,MaxLevel TEXT)',
        'CREATE TABLE Departments(DeptCode TEXT,Status TEXT,IsActive INTEGER)', 'CREATE TABLE Sites(SiteCode TEXT,IsActive INTEGER)',
        'CREATE TABLE TaiLieu(DocID INTEGER,VersionNo INTEGER,TenFile TEXT,FilePath TEXT,ThuMuc TEXT,SecurityLevel TEXT,Site TEXT,'
            'LifecycleStatus TEXT,ReviewStatus TEXT,Servable INTEGER,PublicationState TEXT,IsCurrent INTEGER,EffectiveStatus TEXT,EffectiveDate TEXT,ExpiryDate TEXT)',
        'CREATE TABLE PhongBanChiaSe(DocID INTEGER,DeptCode TEXT)',
        'CREATE TABLE DocumentPages(DocID INTEGER,PageNo INTEGER,TextExtract TEXT,LocalOCRText TEXT,VisionSummary TEXT)',
        'CREATE TABLE TechnicalAttributes(AttributeID INTEGER,DocID INTEGER,PageNo INTEGER,AttributeType TEXT,AttributeValue TEXT,Unit TEXT,HumanVerified INTEGER,ExtractedBy TEXT,Confidence REAL)',
        'CREATE TABLE ChatEvidenceManifest(ChatID INTEGER PRIMARY KEY,RequiresAuthorization INTEGER,IsComplete INTEGER,EvidenceCount INTEGER,SchemaVersion TEXT,IsStructuredComplete INTEGER)',
        'CREATE TABLE AnswerEvidence(EvidenceID INTEGER PRIMARY KEY,ChatID INTEGER,DocID INTEGER,PageNo INTEGER,SourceRef TEXT,SecurityLevel TEXT,RankNo INTEGER,SourceVersion INTEGER,SourceLocatorJson TEXT)',
        'CREATE TABLE FeedbackReview(FeedbackID INTEGER PRIMARY KEY,ChatID INTEGER,Question TEXT,BotAnswer TEXT,FailureType TEXT,CorrectAnswer TEXT,AddedToGoldenSet INTEGER DEFAULT 0,'
            'CreatedAt TEXT DEFAULT CURRENT_TIMESTAMP,DocVersionNo INTEGER,Department TEXT,Site TEXT,IsStale INTEGER DEFAULT 0,SourceDocID INTEGER,ReviewerNote TEXT)',
        'CREATE TABLE GoldenAnswer(GoldenID INTEGER PRIMARY KEY,FeedbackID INTEGER,QuestionHash TEXT,QuestionText TEXT,GoldenAnswer TEXT,SourceDocID INTEGER,Department TEXT,Site TEXT,CreatedBy TEXT,IsActive INTEGER,CreatedAt TEXT DEFAULT CURRENT_TIMESTAMP,AccessBasisJson TEXT)',
        'CREATE TABLE RegressionQuestion(RegQID INTEGER PRIMARY KEY,QuestionText TEXT,ExpectedDocID INTEGER,ExpectedKeywords TEXT,Department TEXT,Site TEXT,CreatedBy TEXT,IsActive INTEGER,CreatedAt TEXT DEFAULT CURRENT_TIMESTAMP,AccessBasisJson TEXT)',
        'CREATE TABLE RegressionRun(RunID INTEGER PRIMARY KEY,RegQID INTEGER,RunBatchID TEXT,AnswerText TEXT,MatchedDocIDs TEXT,DocHit INTEGER,KeywordHit INTEGER,Passed INTEGER,DurationMs INTEGER,ErrorText TEXT,CreatedAt TEXT DEFAULT CURRENT_TIMESTAMP,ResultManifestJson TEXT)',
    ]
    with engine.begin() as conn:
        for sql in schema:
            conn.execute(text(sql))
        for sql in ["INSERT INTO Users VALUES(9,'reviewer','Reviewer','ENG',1,'vi')",
                    "INSERT INTO Roles VALUES(1,'reviewer')", 'INSERT INTO UserRoles VALUES(9,1)',
                    "INSERT INTO UserDepartments VALUES(9,'ENG')", "INSERT INTO UserSites VALUES(9,'HN')",
                    "INSERT INTO UserSecurityClearance VALUES(9,'internal')", "INSERT INTO Departments VALUES('ENG','active',1)",
                    "INSERT INTO Departments VALUES('HR','active',1)", "INSERT INTO Sites VALUES('HN',1)",
                    "INSERT INTO Sites VALUES('HCM',1)"]:
            conn.execute(text(sql))
        for doc in (1,2):
            conn.execute(text("INSERT INTO TaiLieu VALUES(:doc,2,'source.pdf','source.pdf','ENG','internal','HN','published','approved',1,'published',1,'effective',NULL,NULL)"), {'doc':doc})
            conn.execute(text("INSERT INTO PhongBanChiaSe VALUES(:doc,'ENG')"),{'doc':doc})
            conn.execute(text("INSERT INTO DocumentPages VALUES(:doc,1,'approved content',NULL,NULL)"),{'doc':doc})
        for column in ('Title','Summary','Tags','DocNumber','IssuedDate','OwnerSigner','DocLanguage'):
            conn.execute(text('ALTER TABLE TaiLieu ADD '+column+' TEXT'))
    for module in (feedback, ui_queries, core):
        monkeypatch.setattr(module,'engine',engine)
    for module in (feedback,chat):
        monkeypatch.setattr(module,'_ensure_engine',lambda:None)
    actor = core.load_user_profile(user_id=9,username='reviewer',strict=True)
    yield engine,actor
    engine.dispose()


def persist_feedback(engine,actor):
    docs = evidence_docs(engine,actor,(1,2))
    assert chat.save_answer_evidence(31,docs,db_engine=engine,actor=actor)
    with engine.begin() as conn:
        conn.execute(text("INSERT INTO FeedbackReview(FeedbackID,ChatID,Question,BotAnswer,DocVersionNo,Department,Site,SourceDocID) "
                          "VALUES(7,31,'Pressure?','sensitive combined answer',2,'ENG','HN',1)"))


def evidence_docs(engine,actor,doc_ids=(1,)):
    with engine.connect() as conn:
        return pin_generation_sources(conn,[{'doc_id':doc,'version_no':2,'trang':1,'security_level':'internal','text':'approved content'} for doc in doc_ids],actor)


def test_new_plain_chat_feedback_promotes_and_revocation_hides_whole_answer(scoped_db):
    engine,actor = scoped_db
    persist_feedback(engine,actor)
    assert len(ui_queries.list_feedbacks(True,actor=actor)) == 1
    result = ui_queries.classify_feedback_and_get_source(7,'wrong','reviewed answer','note',actor=actor)
    assert result['golden_hash']
    assert result['regression_qid']
    golden = feedback.find_golden_answer('Pressure?',admitted_doc_ids=[1,2],actor=actor)
    assert golden['answer'] == 'reviewed answer'
    assert feedback.find_golden_answer('Pressure?',admitted_doc_ids=[1],actor=actor) is None
    with engine.begin() as conn:
        conn.execute(text("UPDATE PhongBanChiaSe SET DeptCode='HR' WHERE DocID=2"))
    assert ui_queries.list_feedbacks(False,actor=actor) == []
    assert feedback.list_regression_questions(actor=actor) == []
    assert feedback.find_golden_answer('Pressure?',admitted_doc_ids=[1,2],actor=actor) is None
    with pytest.raises(PermissionError):
        ui_queries.delete_feedback(7,actor=actor)


@pytest.mark.parametrize('change', ["UPDATE TaiLieu SET VersionNo=3 WHERE DocID=2",
    "UPDATE DocumentPages SET TextExtract='changed' WHERE DocID=2",
    "UPDATE ChatEvidenceManifest SET EvidenceCount=3", "UPDATE AnswerEvidence SET SourceLocatorJson=NULL WHERE DocID=2",
    "UPDATE AnswerEvidence SET SourceLocatorJson='{' WHERE DocID=2", "UPDATE ChatEvidenceManifest SET IsComplete=0",
    "UPDATE TaiLieu SET IsCurrent=0 WHERE DocID=2", "UPDATE TaiLieu SET ExpiryDate='2000-01-01' WHERE DocID=2",
    "UPDATE TaiLieu SET OwnerSigner='new signer' WHERE DocID=2",
    "INSERT INTO TechnicalAttributes VALUES(1,2,1,'pressure','changed','bar',1,'reviewer',1.0)",
    "DELETE FROM UserSites WHERE UserID=9"])
def test_unknown_or_changed_basis_never_allows_writes(scoped_db,change):
    engine,actor = scoped_db
    persist_feedback(engine,actor)
    with engine.begin() as conn:
        conn.execute(text(change))
    assert ui_queries.list_feedbacks(False,actor=actor) == []
    with pytest.raises(PermissionError):
        ui_queries.classify_feedback_and_get_source(7,'wrong','should not save','note',actor=actor)
    with pytest.raises(PermissionError):
        ui_queries.delete_feedback(7,actor=actor)
    with engine.connect() as conn:
        assert conn.execute(text('SELECT CorrectAnswer FROM FeedbackReview WHERE FeedbackID=7')).scalar() is None
        assert conn.execute(text('SELECT COUNT(*) FROM GoldenAnswer')).scalar() == 0


def test_manual_scope_and_dedupe_use_current_authority(scoped_db):
    engine,actor = scoped_db
    qid = feedback.ensure_regression_question('Pressure?',expected_doc_id=1,department='ENG',site='HN',actor=actor)
    assert qid
    assert feedback.ensure_regression_question('Pressure?',expected_doc_id=1,department='ENG',site='HN',actor=actor) == qid
    with pytest.raises(ValueError):
        feedback.add_regression_question('Q',department='HR',site='HN',actor=actor)
    with engine.begin() as conn:
        conn.execute(text("INSERT INTO UserSites VALUES(9,'HCM')"))
    other = feedback.ensure_regression_question('Pressure?',department='ENG',site='HCM',actor=actor)
    assert other != qid
    rows = feedback.list_regression_questions(actor=actor)
    assert next(row for row in rows if row['reg_qid']==qid)['access_basis']['sources']
    with engine.begin() as conn:
        conn.execute(text('UPDATE TaiLieu SET VersionNo=3 WHERE DocID=1'))
    assert qid not in {row['reg_qid'] for row in feedback.list_regression_questions(actor=actor)}


@pytest.mark.parametrize('terminal,status', [('complete','completed'),('fail','failed'),('cancel','cancelled'),('missing','incomplete')])
def test_scoped_batch_uses_real_actor_and_rechecks_old_results(scoped_db,terminal,status):
    engine,actor = scoped_db
    feedback.add_regression_question('Q',expected_keywords='approved',department='ENG',site='HN',actor=actor)
    seen = []
    diagnostics = {'retrieved_docs':evidence_docs(engine,actor),'evidence_complete':True}
    class Executor:
        def run(self,request,invocation):
            seen.append(request.access)
            yield RagPrepared('',(),(),diagnostics)
            yield RagToken('approved answer')
            if terminal=='complete':
                yield RagCompleted('answered','trace',diagnostics)
            elif terminal=='fail':
                yield RagFailed('failed','bad',False,RuntimeError())
            elif terminal=='cancel':
                yield RagCancelled('stopped')
    summary = regression.run_regression_batch(actor=actor,executor=Executor())
    assert seen[0].roles == frozenset({'reviewer'})
    assert seen[0].allowed_departments == frozenset({'ENG'})
    assert seen[0].max_security_level == 'internal'
    assert summary['passed'] == int(terminal=='complete')
    rows = feedback.get_regression_runs(batch_id=summary['batch_id'],actor=actor)
    assert rows[0]['status'] == status
    assert rows[0]['answer'] == ('approved answer' if terminal=='complete' else '')
    with engine.begin() as conn:
        conn.execute(text("DELETE FROM UserSites WHERE UserID=9"))
    assert feedback.get_regression_runs(batch_id=summary['batch_id'],actor=actor) == []


def test_missing_criteria_is_ungraded(scoped_db):
    _,actor = scoped_db
    feedback.add_regression_question('Q',department='ENG',site='HN',actor=actor)
    executor = SimpleNamespace(run=lambda *_: iter([RagPrepared('',(),(),{}),RagToken('hello'),RagCompleted('answered','t',{})]))
    summary = regression.run_regression_batch(actor=actor,executor=executor)
    assert summary['graded'] == summary['passed'] == summary['failed'] == 0
    assert summary['ungraded'] == 1
    assert summary['pass_rate'] is None


def test_missing_schema_has_explicit_failure(scoped_db):
    engine,actor = scoped_db
    with engine.begin() as conn:
        conn.execute(text('ALTER TABLE RegressionQuestion DROP COLUMN AccessBasisJson'))
    with pytest.raises(FeedbackSchemaUnavailable,match='V0048'):
        feedback.list_regression_questions(actor=actor)


def test_http_review_and_configured_service_batch_preserve_scope_and_outcome(scoped_db,monkeypatch):
    from mech_chatbot.adapters.chat_runtime import HttpRagStreamAdapter
    engine,actor = scoped_db
    persist_feedback(engine,actor)
    calls = []
    diagnostics = {'retrieved_docs':evidence_docs(engine,actor),'evidence_complete':True}
    class Response:
        ok = True
        def __enter__(self): return self
        def __exit__(self,*_): return False
        def iter_lines(self,**_):
            for name,data in [('metadata',{'debug_info':diagnostics}),('token',{'text':'approved answer'}),
                              ('done',{'outcome':'answered','debug_info':diagnostics})]:
                yield 'event: '+name
                yield 'data: '+json.dumps(data)
                yield ''
    def post(url,**kwargs):
        calls.append(kwargs['json'])
        return Response()
    app = FastAPI()
    app.include_router(operations.router)
    app.state.runtime = SimpleNamespace(rag_stream=HttpRagStreamAdapter(post=post))
    app.dependency_overrides[dependencies.current_profile] = lambda: actor
    app.dependency_overrides[dependencies.csrf_profile] = lambda: actor
    monkeypatch.setattr(operations.audit_service,'write_audit_log',lambda **_:None)
    with TestClient(app) as client:
        assert len(client.get('/api/feedback').json()['feedbacks']) == 1
        classified = client.post('/api/feedback/7/classify',json={'correct_answer':'approved answer'})
        assert classified.status_code == 200
        assert classified.json()['golden_hash']
        assert client.post('/api/regression/questions',json={'question':'no scope'}).status_code == 422
        assert client.post('/api/regression/questions',json={'question':'manual','department':'ENG','site':'HN'}).json()['ok']
        response = client.post('/api/regression/run',json={})
        assert response.status_code == 200
        assert response.json()['summary']['graded'] == 1
        assert response.json()['summary']['ungraded'] == 1
        assert response.json()['summary']['passed'] == 1
        assert calls[0]['user_id'] == 9
        assert calls[0]['regression_scope'] == {'department':'ENG','site':'HN','max_security_level':'internal'}
        with engine.begin() as conn:
            conn.execute(text("UPDATE PhongBanChiaSe SET DeptCode='HR' WHERE DocID=2"))
        assert client.get('/api/feedback').json()['feedbacks'] == []
        for fid in (7,999):
            assert client.post(f'/api/feedback/{fid}/classify',json={'correct_answer':'denied'}).json() == {'detail':'Record unavailable'}
            assert client.delete(f'/api/feedback/{fid}').status_code == 404


@pytest.mark.parametrize('outcome,passed', [('answered',1),('refused',0)])
def test_configured_adapter_preserves_refusal(scoped_db,outcome,passed):
    from mech_chatbot.application.chat_turn import RagMetadata,RagDelta,RagStreamDone
    engine,actor = scoped_db
    feedback.add_regression_question('Q',expected_keywords='approved',department='ENG',site='HN',actor=actor)
    diagnostics = {'retrieved_docs':evidence_docs(engine,actor),'evidence_complete':True}
    stream = SimpleNamespace(stream=lambda *_:iter([RagMetadata(debug_info=diagnostics),RagDelta('approved'),RagStreamDone(outcome=outcome,debug_info=diagnostics)]))
    result = regression.run_regression_batch(actor=actor,rag_stream=stream)
    assert result['passed'] == passed


@pytest.mark.parametrize('role', ['reviewer','admin','knowledge_approver','Admin',' admin '])
def test_rag_server_narrows_real_actor_without_global_bypass(scoped_db,role):
    from mech_chatbot.api import rag_server
    from mech_chatbot.application.chat_turn import RagMetadata,RagDelta,RagStreamDone
    _,actor = scoped_db
    captured = []
    runtime = SimpleNamespace(executor=SimpleNamespace(run=lambda request,*_,**kw:captured.append(request) or iter(())))
    req = rag_server.ChatRequest(user_id=9,username='reviewer',user_question='Q',
        regression_scope={'department':'ENG','site':'HN','max_security_level':'internal'})
    list(rag_server._open_rag_events(req,{**actor,'roles':[role]},'trace',None,runtime=runtime))
    access = captured[0].access
    assert access.user_id == 9
    assert access.username == 'reviewer'
    assert 'admin' not in access.roles
    assert access.roles == frozenset({'viewer' if role.strip().lower()=='admin' else role})
    assert access.allowed_departments == frozenset({'ENG'})
    assert access.allowed_sites == frozenset({'HN'})
    assert access.max_security_level == 'internal'


def test_rights_revoked_between_cases_stop_before_next_execution(scoped_db):
    engine,actor = scoped_db
    for question in ('A','B'):
        feedback.add_regression_question(question,expected_keywords='approved',department='ENG',site='HN',actor=actor)
    executed = []
    class Executor:
        def run(self,request,*_):
            executed.append(request.question)
            yield RagPrepared('',(),(),{})
            yield RagToken('approved')
            yield RagCompleted('answered','trace',{})
    class RevokingExecutor(Executor):
        def run(self,request,*args):
            try:
                yield from super().run(request,*args)
            finally:
                with engine.begin() as conn:
                    conn.execute(text('DELETE FROM UserRoles WHERE UserID=9'))
    with pytest.raises(PermissionError):
        regression.run_regression_batch(actor=actor,executor=RevokingExecutor())
    assert len(executed) <= 1


def test_persistence_does_not_recertify_stale_plain_text(scoped_db):
    from mech_chatbot.db.repositories.feedback_access import page_hash
    engine,actor = scoped_db
    old_pin = {'schema':'answer-source-pin-v1','doc_id':1,'version_no':2,'page_no':1,
               'source_hash':page_hash(('approved content',None,None))}
    with engine.begin() as conn:
        conn.execute(text("UPDATE DocumentPages SET TextExtract='new content' WHERE DocID=1"))
    assert chat.save_answer_evidence(31,[{'doc_id':1,'version_no':2,'trang':1,'evidence_pin':old_pin}],db_engine=engine,actor=actor) is False


def test_source_free_answer_does_not_pass_keyword_criterion(scoped_db):
    from mech_chatbot.application.chat_turn import RagMetadata,RagDelta,RagStreamDone
    _,actor = scoped_db
    feedback.add_regression_question('Q',expected_keywords='approved',department='ENG',site='HN',actor=actor)
    stream = SimpleNamespace(stream=lambda *_:iter([RagMetadata(debug_info={}),RagDelta('approved'),RagStreamDone(outcome='answered')]))
    result = regression.run_regression_batch(actor=actor,rag_stream=stream)
    assert result['passed'] == 0


@pytest.mark.parametrize('title_mode',['none','with_body','only','string_page','derived'])
def test_real_plain_pipeline_preserves_ingestion_wrapper_pin_to_feedback(scoped_db,offline_pipeline,monkeypatch,title_mode):
    from langchain_core.documents import Document
    from langchain_core.runnables import RunnableLambda
    from mech_chatbot.config.repository_runtime import bind_repository_runtime
    from mech_chatbot.rag.execution import AccessScope,RagRequest,RagInvocation
    engine,actor = scoped_db
    documents,_,executor,runtime = offline_pipeline
    runtime.rerank_max_chunks_per_section = 4
    # SQL common metadata does not replace a chunk's type or original text.
    monkeypatch.setattr('mech_chatbot.db.repositories.doc_metadata.get_common_metadata_for_rag',
        lambda ids:{doc_id:{'effective_status':'effective'} for doc_id in ids})
    source_text = 'Quy trình kiểm tra áp suất nằm trong tài liệu nội bộ.'
    with engine.begin() as conn:
        conn.execute(text('UPDATE DocumentPages SET TextExtract=:value'),{'value':source_text})
    wrapped = 'Noi dung chi tiet trang 1 tai lieu source.pdf (Ma: [PUMP]):\n'+source_text
    documents.append(Document(page_content=wrapped,metadata={'doc_id':1,'version_no':2,'trang_so':1,
        'file_goc':'source.pdf','loai_du_lieu':'text','noi_dung_goc':wrapped,'domain':'hr',
        'phong_ban_quyen':['ENG'],'site':'HN','security_level':'internal','external_processing_policy':'internal_only',
        'servable':True,'publication_state':'published','lifecycle_status':'published','review_status':'approved','is_current':True}))
    synthetic = 'Thong tin tai lieu source.pdf:\n- Ten tai lieu/san pham: SYNTHETIC-TITLE-ONLY'
    if title_mode != 'none':
        title = Document(page_content=synthetic,metadata={**documents[0].metadata,
            'loai_du_lieu':'title_block','noi_dung_goc':synthetic})
        if title_mode == 'only':
            documents.clear()
        documents.append(title)
    if title_mode == 'string_page':
        documents[0].metadata['trang_so']='1'
    if title_mode == 'derived':
        documents[0].page_content=documents[0].metadata['noi_dung_goc']='unverified derived content'
    prompts = []
    executor._provider_adapter.client = RunnableLambda(
        lambda value,**_:prompts.append(value.to_string()) or source_text+' [SRC:D1P1]')
    access = AccessScope(user_id=9,username='reviewer',department='ENG',roles=frozenset({'reviewer'}),
        allowed_departments=frozenset({'ENG'}),allowed_sites=frozenset({'HN'}),max_security_level='internal')
    with bind_repository_runtime(db_engine=engine):
        events = list(executor.run(RagRequest('Quy trình kiểm tra áp suất như thế nào?',access),RagInvocation('text-pin',mode='test')))
    assert isinstance(events[-1],RagCompleted)
    if title_mode in {'only','derived'}:
        assert events[-1].outcome != 'answered'
        assert not prompts
        assert not events[-1].diagnostics.get('retrieved_docs')
        return
    assert events[-1].outcome == 'answered',dict(events[-1].diagnostics)
    assert prompts and all('SYNTHETIC-TITLE-ONLY' not in prompt for prompt in prompts)
    debug = dict(events[-1].diagnostics)
    assert debug['evidence_complete'] is True
    assert debug['retrieved_docs'][0]['evidence_pin']['page_no'] == 1
    assert chat.save_answer_evidence(31,debug['retrieved_docs'],db_engine=engine,actor=actor)
    with engine.begin() as conn:
        assert tuple(conn.execute(text('SELECT IsComplete,EvidenceCount FROM ChatEvidenceManifest WHERE ChatID=31')).one()) == (1,1)
        conn.execute(text('CREATE TABLE LichSuChat(ChatID INTEGER,SessionID TEXT,CauHoi_User TEXT,TraLoi_Bot TEXT,HinhAnhUpload TEXT,DanhGia INTEGER,RefImages TEXT,Username TEXT,ThoiGian TEXT)'))
        conn.execute(text("INSERT INTO LichSuChat VALUES(31,'pin-history','Pressure?',:answer,NULL,NULL,NULL,'reviewer','2026-10-30')"),
                     {'answer':source_text+' [SRC:D1P1]'})
        conn.execute(text("INSERT INTO FeedbackReview(FeedbackID,ChatID,Question,BotAnswer,Department,Site,SourceDocID) "
                          "VALUES(7,31,'Pressure?','source answer','ENG','HN',1)"))
    history = chat.get_chat_history('pin-history',username='reviewer',user_clearance='internal',
        allowed_departments=['ENG'],allowed_sites=['HN'],db_engine=engine)
    assert history[-1]['content'] == source_text+' [SRC:D1P1]'
    assert not history[-1].get('sources_redacted')
    assert len(ui_queries.list_feedbacks(False,actor=actor)) == 1


@pytest.mark.parametrize('answer,expected_doc,expected_keywords,passed', [
    ('approved alpha beta',1,'alpha; beta',1), ('approved alpha',1,'alpha; beta',0),
    ('approved alpha beta',2,'alpha; beta',0)])
def test_batch_preserves_doc_and_semicolon_keyword_grading(scoped_db,answer,expected_doc,expected_keywords,passed):
    engine,actor = scoped_db
    feedback.add_regression_question('Q',expected_doc_id=expected_doc,expected_keywords=expected_keywords,department='ENG',site='HN',actor=actor)
    diagnostics = {'retrieved_docs':evidence_docs(engine,actor),'evidence_complete':True}
    executor = SimpleNamespace(run=lambda *_:iter([RagPrepared('',(),(),diagnostics),RagToken(answer),RagCompleted('answered','t',diagnostics)]))
    summary = regression.run_regression_batch(actor=actor,executor=executor)
    assert summary['passed'] == passed
    assert summary['failed'] == 1-passed


def test_empty_batch_and_limit_and_setup_failure_keep_correct_counts(scoped_db):
    engine,actor = scoped_db
    calls = []
    diagnostics = {'retrieved_docs':evidence_docs(engine,actor),'evidence_complete':True}
    def run(request,*_):
        calls.append(request.question)
        if request.question == 'A':
            raise RuntimeError('provider failed before iterator')
        return iter([RagPrepared('',(),(),diagnostics),RagToken('approved'),RagCompleted('answered','t',diagnostics)])
    executor = SimpleNamespace(run=run)
    empty = regression.run_regression_batch(actor=actor,executor=executor)
    assert {key:empty[key] for key in ('total','passed','failed','graded','ungraded','pass_rate')} == {
        'total':0,'passed':0,'failed':0,'graded':0,'ungraded':0,'pass_rate':None}
    for question in ('A','B'):
        feedback.add_regression_question(question,expected_keywords='approved',department='ENG',site='HN',actor=actor)
    summary = regression.run_regression_batch(actor=actor,executor=executor)
    assert summary['total'] == summary['graded'] == 2
    assert summary['passed'] == summary['failed'] == 1
    assert summary['pass_rate'] == 0.5
    rows = feedback.get_regression_runs(batch_id=summary['batch_id'],actor=actor)
    failed = next(row for row in rows if row['question']=='A')
    assert failed['status'] == 'failed' and failed['error'] and failed['answer'] == ''
    calls.clear()
    limited = regression.run_regression_batch(limit=1,actor=actor,executor=executor)
    assert limited['total'] == len(calls) == 1


def test_shared_department_grant_allows_feedback_and_promotion(scoped_db):
    engine,actor = scoped_db
    persist_feedback(engine,actor)
    with engine.begin() as conn:
        conn.execute(text("INSERT INTO PhongBanChiaSe VALUES(1,'HR'),(2,'HR')"))
        conn.execute(text("UPDATE UserDepartments SET Department='HR' WHERE UserID=9"))
    assert len(ui_queries.list_feedbacks(False,actor=actor)) == 1
    result = ui_queries.classify_feedback_and_get_source(7,'other','approved content','reviewed',actor=actor)
    assert result['department'] == 'HR'
    assert result['golden_hash'] and result['regression_qid']
    assert feedback.list_regression_questions(actor=actor)[0]['department'] == 'HR'


@pytest.mark.parametrize('displayed_primary',[1,2])
def test_individually_granted_departments_allow_composite_feedback(scoped_db,displayed_primary):
    engine,actor = scoped_db
    persist_feedback(engine,actor)
    with engine.begin() as conn:
        conn.execute(text("INSERT INTO UserDepartments VALUES(9,'HR')"))
        conn.execute(text("UPDATE PhongBanChiaSe SET DeptCode='HR' WHERE DocID=2"))
        if displayed_primary == 2:
            conn.execute(text("UPDATE FeedbackReview SET SourceDocID=2,Department='HR'"))
    assert len(ui_queries.list_feedbacks(False,actor=actor)) == 1
    result = ui_queries.classify_feedback_and_get_source(7,'other','approved content','reviewed',actor=actor)
    assert result['department'] == ('ENG' if displayed_primary==1 else 'HR')
    assert result['golden_hash'] and result['regression_qid']
    assert feedback.find_golden_answer('Pressure?',admitted_doc_ids=[1,2],actor=actor)
    with engine.begin() as conn:
        conn.execute(text("DELETE FROM UserDepartments WHERE Department='HR'"))
    assert ui_queries.list_feedbacks(False,actor=actor) == []
    assert feedback.find_golden_answer('Pressure?',admitted_doc_ids=[1,2],actor=actor) is None


def test_golden_read_obeys_effective_execution_scope(scoped_db):
    from mech_chatbot.application.feedback_access import access_basis
    engine,actor = scoped_db
    with engine.begin() as conn:
        conn.execute(text("INSERT INTO UserDepartments VALUES(9,'HR')"))
        conn.execute(text("INSERT INTO PhongBanChiaSe VALUES(1,'HR')"))
    basis = access_basis({'department':'HR','site':'HN','max_security_level':'internal'},
        [evidence_docs(engine,actor)[0]['evidence_pin']])
    feedback.upsert_golden_answer('Q','approved content',source_doc_id=1,department='HR',site='HN',actor=actor,access_basis=basis)
    assert feedback.find_golden_answer('Q',admitted_doc_ids=[1],actor=actor)
    effective = {**actor,'execution_scope':{'roles':['reviewer'],'allowed_departments':['ENG'],
        'allowed_sites':['HN'],'max_security_level':'internal'}}
    assert feedback.find_golden_answer('Q',admitted_doc_ids=[1],actor=effective) is None


@pytest.mark.parametrize('fault', ['actor','status','grading','question','expected_keywords','outcome','execution_basis','passed','invalid_json'])
def test_incomplete_or_corrupt_result_manifest_is_hidden(scoped_db,fault):
    engine,actor = scoped_db
    feedback.add_regression_question('Q',expected_keywords='approved',department='ENG',site='HN',actor=actor)
    diagnostics = {'retrieved_docs':evidence_docs(engine,actor),'evidence_complete':True}
    executor = SimpleNamespace(run=lambda *_:iter([RagPrepared('',(),(),diagnostics),RagToken('approved'),RagCompleted('answered','t',diagnostics)]))
    result = regression.run_regression_batch(actor=actor,executor=executor)
    with engine.begin() as conn:
        record = json.loads(conn.execute(text('SELECT ResultManifestJson FROM RegressionRun')).scalar())
        if fault=='execution_basis':
            record[fault]['sources'] = []
            record[fault]['evidence_count'] = 0
        elif fault=='passed':
            record[fault] = False
        elif fault != 'invalid_json':
            del record[fault]
        conn.execute(text('UPDATE RegressionRun SET ResultManifestJson=:value'),{'value':'{' if fault=='invalid_json' else json.dumps(record)})
    assert feedback.get_regression_runs(batch_id=result['batch_id'],actor=actor) == []


def test_metadata_change_invalidates_old_pin_and_allows_new_generation(scoped_db):
    engine,actor = scoped_db
    old = evidence_docs(engine,actor)
    with engine.begin() as conn:
        conn.execute(text("UPDATE TaiLieu SET OwnerSigner='new signer',Summary='new summary' WHERE DocID=1"))
    assert chat.save_answer_evidence(31,old,db_engine=engine,actor=actor) is False
    assert chat.save_answer_evidence(31,evidence_docs(engine,actor),db_engine=engine,actor=actor)


def test_stale_or_derived_text_cannot_be_certified_as_current_page(scoped_db):
    engine,actor = scoped_db
    with engine.connect() as conn:
        for text_value in ('outdated content','combined summary using another source'):
            with pytest.raises(PermissionError):
                pin_generation_sources(conn,[{'doc_id':1,'version_no':2,'trang':1,'text':text_value}],actor)


@pytest.mark.parametrize('cancelled',[False,True])
@pytest.mark.parametrize('stop_stage',['evidence_snapshot','evidence_read'])
def test_generation_pin_respects_existing_deadline_and_cancellation(scoped_db,cancelled,stop_stage):
    from time import monotonic
    from mech_chatbot.rag.execution import AccessScope,RequestDeadlineExceeded
    from mech_chatbot.llm.external_ai import ExternalAICallCancelled
    from mech_chatbot.services.feedback_service import pin_generation_evidence
    from mech_chatbot.config.repository_runtime import bind_repository_runtime
    engine,actor = scoped_db
    access = AccessScope(user_id=9,username='reviewer',roles=frozenset({'reviewer'}),
        allowed_departments=frozenset({'ENG'}),allowed_sites=frozenset({'HN'}),max_security_level='internal')
    deadline = monotonic()-1
    stages = []
    stopped = False
    expected = ExternalAICallCancelled if cancelled else RequestDeadlineExceeded
    def checkpoint(stage):
        nonlocal stopped
        stages.append(stage)
        stopped = stopped or stage == stop_stage
        if stopped and (cancelled or monotonic() >= deadline):
            raise expected('Request stopped before evidence read')
    with bind_repository_runtime(db_engine=engine),pytest.raises(expected):
        pin_generation_evidence([],access,checkpoint=checkpoint,deadline=deadline)
    assert stages[0] == 'evidence_snapshot'
    assert stop_stage in stages


@pytest.mark.parametrize('valid',[True,False])
def test_generation_certification_never_passes_unpinned_governed_text(scoped_db,valid):
    from langchain_core.documents import Document
    from mech_chatbot.rag.execution import AccessScope
    from mech_chatbot.services.feedback_service import pin_generation_evidence
    from mech_chatbot.config.repository_runtime import bind_repository_runtime
    engine,actor=scoped_db
    access=AccessScope(user_id=9,username='reviewer',roles=frozenset({'reviewer'}),
        allowed_departments=frozenset({'ENG'}),allowed_sites=frozenset({'HN'}),max_security_level='internal')
    doc=Document(page_content='approved content' if valid else 'derived unverified text',
        metadata={'doc_id':1,'version_no':2,'trang':1,'loai_du_lieu':'text'})
    with bind_repository_runtime(db_engine=engine):
        if valid:
            assert pin_generation_evidence([doc],access)[0].metadata['evidence_pin']['page_no']==1
        else:
            with pytest.raises(PermissionError):
                pin_generation_evidence([doc],access)


def test_generation_accepts_only_current_request_attachment(scoped_db):
    from mech_chatbot.rag.phases.retrieval_enrichment_support import prepend_image
    from mech_chatbot.rag.execution import AccessScope
    from mech_chatbot.services.feedback_service import pin_generation_evidence
    from mech_chatbot.config.repository_runtime import bind_repository_runtime
    engine,actor=scoped_db
    access=AccessScope(user_id=9,username='reviewer',roles=frozenset({'reviewer'}),
        allowed_departments=frozenset({'ENG'}),allowed_sites=frozenset({'HN'}),max_security_level='internal')
    docs=prepend_image([], '17 Nm')
    with bind_repository_runtime(db_engine=engine):
        assert pin_generation_evidence(docs,access,image_analysis='17 Nm')[0].page_content==docs[0].page_content
        with pytest.raises(PermissionError):
            pin_generation_evidence(docs,access,image_analysis='different image')
