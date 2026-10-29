"""Actual generated explanation pins both computed and retrieved sources in history."""
import pytest
from sqlalchemy import text
from .test_structured_chat_history import history_store, store
from .test_rag_pipeline_public_characterization import offline_pipeline
from .test_computed_text_dependencies import _run_dependency
from mech_chatbot.db.repositories import chat


@pytest.mark.parametrize('change',['revision','clearance','scope'])
def test_generated_dependency_history_is_hidden_when_computed_basis_changes(history_store,offline_pipeline,monkeypatch,change):
    result,_,_=_run_dependency(history_store,offline_pipeline,monkeypatch)
    assert result.diagnostics['query_v2']['branches'][1]['status']=='complete'
    with history_store.begin() as conn:
        conn.execute(text("INSERT INTO TaiLieu SELECT 17,3,'hr-policy.pdf',FilePath,'HR','HCM',SecurityLevel,"
            "LifecycleStatus,ReviewStatus,PublicationState,Servable,IsCurrent,EffectiveStatus,EffectiveDate,ExpiryDate,"
            "KnowledgeApproverUserID,ExternalProcessingPolicy FROM TaiLieu WHERE DocID=1"))
        conn.execute(text("INSERT INTO PhongBanChiaSe VALUES(17,'HR')"))
        conn.execute(text("INSERT INTO LichSuChat VALUES(11,'s1','Compare and explain',:answer,NULL,NULL,NULL,'approver','2026-10-14')"),
            {'answer':result.answer+result.ref_text})
    assert chat.save_answer_evidence(11,list(result.diagnostics['retrieved_docs']),db_engine=history_store,
        advanced_rag={'query_v2':result.diagnostics['query_v2']},citation_cards=list(result.citations))
    assert chat.save_answer_sources(11,[{**card,'file_goc':card['file_name'],'trang':card['page_no']} for card in result.citations],db_engine=history_store)
    def read():
        return chat.get_chat_history('s1',username='approver',user_clearance='internal',
            allowed_departments=('ENG','HR'),allowed_sites=('factory','HCM'),db_engine=history_store)[-1]
    answer=read()
    assert answer['content']==result.answer+result.ref_text and not answer.get('sources_redacted')
    with history_store.begin() as conn:
        if change=='revision': conn.execute(text('UPDATE TaiLieu SET VersionNo=3 WHERE DocID=1'))
        elif change=='clearance': conn.execute(text("UPDATE TaiLieu SET SecurityLevel='confidential' WHERE DocID=1"))
        else: conn.execute(text('UPDATE StructuredEvidenceScope SET CompletenessReviewed=0'))
    answer=read()
    assert answer['sources_redacted'] and '200' not in answer['content']
    assert '_advanced_rag_payload' not in answer and 'structured_citations' not in answer
