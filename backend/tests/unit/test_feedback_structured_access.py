"""Structured feedback uses the same reviewed-source proof as real chat history."""
import json

import pytest
from sqlalchemy import text

from mech_chatbot.auth import core
from mech_chatbot.config.repository_runtime import bind_repository_runtime
from mech_chatbot.db.repositories import feedback,ui_queries
from mech_chatbot.db.repositories.feedback_access import capture_sources
from .test_structured_chat_history import history_store,persist_comparison
from .test_structured_evidence_repository import store,PROFILE
from .test_rag_bom_chat import bom_store,run as run_graph,PROFILE as GRAPH_PROFILE
from .test_graph_v2_repository import connection

pytestmark = pytest.mark.unit


@pytest.fixture
def structured_feedback_store(history_store,monkeypatch):
    with history_store.begin() as conn:
        for sql in (
            'ALTER TABLE Users ADD DisplayName TEXT', 'ALTER TABLE Users ADD Department TEXT',
            'ALTER TABLE Users ADD PreferredLanguage TEXT',
            "UPDATE Users SET DisplayName='Approver',Department='ENG',PreferredLanguage='vi'",
            'CREATE TABLE UserDepartments(UserID INTEGER,Department TEXT)',
            'CREATE TABLE UserSites(UserID INTEGER,Site TEXT)',
            'CREATE TABLE UserSecurityClearance(UserID INTEGER,MaxLevel TEXT)',
            'CREATE TABLE Departments(DeptCode TEXT,Status TEXT,IsActive INTEGER)',
            'CREATE TABLE Sites(SiteCode TEXT,IsActive INTEGER)',
            "INSERT INTO UserDepartments VALUES(7,'ENG')", "INSERT INTO UserSites VALUES(7,'factory')",
            "INSERT INTO UserSecurityClearance VALUES(7,'internal')",
            "INSERT INTO Departments VALUES('ENG','active',1)", "INSERT INTO Sites VALUES('factory',1)",
            'CREATE TABLE FeedbackReview(FeedbackID INTEGER PRIMARY KEY,ChatID INTEGER,Question TEXT,BotAnswer TEXT,FailureType TEXT,CorrectAnswer TEXT,AddedToGoldenSet INTEGER DEFAULT 0,CreatedAt TEXT DEFAULT CURRENT_TIMESTAMP,DocVersionNo INTEGER,Department TEXT,Site TEXT,IsStale INTEGER DEFAULT 0,SourceDocID INTEGER,ReviewerNote TEXT)',
            'CREATE TABLE GoldenAnswer(GoldenID INTEGER PRIMARY KEY,FeedbackID INTEGER,QuestionHash TEXT,QuestionText TEXT,GoldenAnswer TEXT,SourceDocID INTEGER,Department TEXT,Site TEXT,CreatedBy TEXT,IsActive INTEGER,CreatedAt TEXT DEFAULT CURRENT_TIMESTAMP,AccessBasisJson TEXT)',
            'CREATE TABLE RegressionQuestion(RegQID INTEGER PRIMARY KEY,QuestionText TEXT,ExpectedDocID INTEGER,ExpectedKeywords TEXT,Department TEXT,Site TEXT,CreatedBy TEXT,IsActive INTEGER,CreatedAt TEXT DEFAULT CURRENT_TIMESTAMP,AccessBasisJson TEXT)',
            'CREATE TABLE RegressionRun(RunID INTEGER PRIMARY KEY,RegQID INTEGER,RunBatchID TEXT,AnswerText TEXT,MatchedDocIDs TEXT,DocHit INTEGER,KeywordHit INTEGER,Passed INTEGER,DurationMs INTEGER,ErrorText TEXT,CreatedAt TEXT DEFAULT CURRENT_TIMESTAMP,ResultManifestJson TEXT)',
        ):
            conn.execute(text(sql))
    for module in (core,feedback,ui_queries):
        monkeypatch.setattr(module,'engine',history_store)
    monkeypatch.setattr(feedback,'_ensure_engine',lambda:None)
    result = persist_comparison(history_store)
    with history_store.begin() as conn:
        conn.execute(text("INSERT INTO FeedbackReview(FeedbackID,ChatID,Question,BotAnswer,Department,Site,SourceDocID) "
                          "VALUES(7,11,'Compare',:answer,'ENG','factory',1)"),{'answer':result.answer})
    return history_store,result


def test_structured_feedback_promotes_complete_reviewed_source_basis(structured_feedback_store):
    engine,result = structured_feedback_store
    assert len(ui_queries.list_feedbacks(False,actor=PROFILE)) == 1
    with engine.connect() as conn:
        sources = capture_sources(conn,list(result.diagnostics['retrieved_docs']),PROFILE)
    assert len(sources)==2 and all(source['provenance']['scope_hash'] for source in sources)
    promoted = ui_queries.classify_feedback_and_get_source(7,'other',result.answer,'reviewed',actor=PROFILE)
    assert promoted['golden_hash'] and promoted['regression_qid']
    assert feedback.find_golden_answer('Compare',admitted_doc_ids=[1],actor=PROFILE)['answer'] == result.answer
    assert len(feedback.list_regression_questions(actor=PROFILE)) == 1


@pytest.mark.parametrize('change', ['scope_review','hash','locator','source_id','count','missing_payload','corrupt_payload','schema','requires','unknown_schema'])
def test_structured_feedback_and_golden_deny_invalid_complete_basis(structured_feedback_store,change):
    engine,result = structured_feedback_store
    ui_queries.classify_feedback_and_get_source(7,'other',result.answer,'reviewed',actor=PROFILE)
    with engine.begin() as conn:
        if change=='scope_review':
            conn.execute(text("UPDATE StructuredEvidenceScope SET CompletenessReviewed=0 WHERE ScopeID='a'"))
        elif change=='hash':
            conn.execute(text('UPDATE StructuredSourceSnapshot SET SourceHash=:hash'),{'hash':'b'*64})
        elif change in {'locator','source_id'}:
            payload = json.loads(conn.execute(text('SELECT AccessBasisJson FROM GoldenAnswer')).scalar())
            source = payload['sources'][0]['provenance']
            source['source_id'] = 'D1L0000000000000000' if change=='source_id' else source['source_id']
            if change=='locator': source['locator']['cell']='B99'
            conn.execute(text('UPDATE GoldenAnswer SET AccessBasisJson=:value'),{'value':json.dumps(payload)})
            conn.execute(text('UPDATE AnswerEvidence SET SourceLocatorJson=:value WHERE RankNo=1'),{'value':json.dumps(source)})
        else:
            statements = {'count':'UPDATE ChatEvidenceManifest SET EvidenceCount=3',
                'missing_payload':'UPDATE AnswerEvidence SET SourceLocatorJson=NULL',
                'corrupt_payload':"UPDATE AnswerEvidence SET SourceLocatorJson='{'",
                'schema':'UPDATE ChatEvidenceManifest SET IsStructuredComplete=0',
                'requires':'UPDATE ChatEvidenceManifest SET RequiresAuthorization=0',
                'unknown_schema':"UPDATE ChatEvidenceManifest SET SchemaVersion='unknown'"}
            conn.execute(text(statements[change]))
            # Approved answers have independent immutable bases; corrupt only that basis for this same test.
            conn.execute(text('UPDATE GoldenAnswer SET AccessBasisJson=NULL'))
    assert ui_queries.list_feedbacks(False,actor=PROFILE) == []
    assert feedback.find_golden_answer('Compare',admitted_doc_ids=[1],actor=PROFILE) is None
    with pytest.raises(PermissionError):
        ui_queries.delete_feedback(7,actor=PROFILE)


def test_structured_source_pin_rejects_changed_locator_even_when_file_hash_is_current(structured_feedback_store):
    from mech_chatbot.application.feedback_access import access_basis
    engine,result = structured_feedback_store
    with engine.connect() as conn:
        source = dict(result.diagnostics['retrieved_docs'][0])
        source['locator'] = {**source['locator'],'cell':'B99'}
        with pytest.raises(PermissionError):
            capture_sources(conn,[source],PROFILE)


def test_grouped_reviewed_rows_keep_their_complete_locator_basis(structured_feedback_store):
    from decimal import Decimal
    from mech_chatbot.domain.structured_evidence import NumericFact,SourceLocator,ScopeManifest,RoundingRule
    from mech_chatbot.db.repositories.structured_evidence import save_extracted_scope,review_numeric_fact,review_scope_completeness
    from mech_chatbot.rag.execution import AccessScope,DefaultRagExecutor,RagRequest,RagInvocation,collect_rag_events
    engine,_ = structured_feedback_store
    facts = tuple(NumericFact('group-'+str(row),'Value',str(row),Decimal(row),'VND',
        SourceLocator('1','2',sheet='CSV',cell='C'+str(row),row_key='group-row-'+str(row)),
        source_hash='a'*64,currency='VND',extraction_method='csv') for row in (2,3))
    manifest = ScopeManifest('grouped','batch-1',facts,source_revisions=(('1','2'),),unit='VND',currency='VND')
    save_extracted_scope(manifest,db_engine=engine)
    for fact in facts:
        review_numeric_fact(fact.fact_id,PROFILE,action='approve',note='Reviewed row',db_engine=engine)
    review_scope_completeness('grouped',PROFILE,expected_row_ids=tuple(f.locator.row_key for f in facts),
        rounding_rule=RoundingRule(2),definition='Reviewed amount',note='Reviewed complete scope',db_engine=engine)
    with bind_repository_runtime(db_engine=engine):
        result = collect_rag_events(DefaultRagExecutor().run(RagRequest('Compare',AccessScope(roles=frozenset({'reviewer'}),
            allowed_departments=frozenset({'ENG'}),allowed_sites=frozenset({'factory'}),max_security_level='internal'),
            scope_comparison=('grouped','b')),RagInvocation('grouped',mode='test')))
        assert result.outcome == 'answered', dict(result.diagnostics)
        with engine.connect() as conn:
            sources = capture_sources(conn,list(result.diagnostics['retrieved_docs']),PROFILE)
    assert sources[0]['provenance']['fact_count'] == 2
    assert sources[0]['provenance']['locator']['cell'] is None


@pytest.mark.parametrize('change',[None,'empty','hash','review','rights','stale_pin'])
def test_graph_provenance_uses_real_reviewed_sql_sources(bom_store,change):
    from .test_rag_graph_query_v2 import request as graph_request
    store,engine = bom_store
    if change=='empty':
        with store.begin() as conn:
            conn.execute(text('DELETE FROM ReviewedGraphOccurrences WHERE ParentDocID=1'))
            conn.execute(text('UPDATE ReviewedGraphDeclarations SET ExpectedRows=0 WHERE DocID=1'))
    result = run_graph(engine,graph_request())
    assert result.outcome == 'answered'
    with engine.connect() as conn:
        sources = capture_sources(conn,list(result.diagnostics['retrieved_docs']),GRAPH_PROFILE)
    assert sources and sources[0]['provenance']['graph_hash']
    if change in (None,'empty'):
        return
    with store.begin() as conn:
        if change=='hash':
            conn.execute(text('UPDATE StructuredSourceSnapshot SET SourceHash=:hash WHERE DocID=1'),{'hash':'b'*64})
        elif change=='review':
            conn.execute(text("UPDATE ReviewedGraphOccurrences SET ReviewStatus='pending'"))
        elif change=='rights':
            conn.execute(text('DELETE FROM PhongBanChiaSe WHERE DocID=1'))
    docs = [dict(item) for item in result.diagnostics['retrieved_docs']]
    if change=='stale_pin':
        docs[0] = {**docs[0],'graph_hash':'b'*64}
    with engine.connect() as conn,pytest.raises(PermissionError):
        capture_sources(conn,docs,GRAPH_PROFILE)
