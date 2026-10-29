"""Evaluation text decomposition through the public executor with one ledger."""
from dataclasses import replace
import pytest
from mech_chatbot.rag.execution import AccessScope,RagRequest,RagInvocation,RagCompleted,RagFailed,DefaultRagExecutor
from .test_rag_pipeline_public_characterization import offline_pipeline
from .test_rag_pipeline_public_characterization import _document
from .test_rag_server_endpoints import rag_client, SERVICE_HEADERS, VIEWER_PROFILE, _replace_runtime

pytestmark=pytest.mark.unit


def test_text_query_overflow_stops_before_provider_and_retrieval_without_mutating_runtime(offline_pipeline):
    _,calls,executor,runtime=offline_pipeline
    request=RagRequest('Tìm BOM; tìm vật liệu; tìm dung sai; tìm hướng dẫn hàn',
                       AccessScope(roles=frozenset({'reviewer'})),text_query=True)
    result=list(executor.run(request,RagInvocation('text-overflow',mode='test')))
    assert isinstance(result[-1],RagCompleted) and result[-1].refusal_reason=='intent_overflow'
    assert calls==[] and runtime.query_decomposition_enabled is False
    assert result[-1].diagnostics.budget.planners==0 and result[-1].diagnostics.budget.final_generations==0


@pytest.mark.parametrize('mode,roles,code',[('production',{'reviewer'},'ValueError'),('test',{'viewer'},'PermissionError')])
def test_text_query_is_evaluation_only_before_source_access(offline_pipeline,mode,roles,code):
    _,calls,executor,_=offline_pipeline
    events=list(executor.run(RagRequest('Tìm quy trình; tìm chính sách',AccessScope(roles=frozenset(roles)),text_query=True),
                             RagInvocation('guard',mode=mode)))
    assert isinstance(events[-1],RagFailed) and events[-1].code==code and calls==[]


@pytest.mark.parametrize('missing',[False,True])
def test_three_text_intents_share_one_generation_and_keep_branch_sources(offline_pipeline,monkeypatch,missing):
    from contextlib import nullcontext
    from types import SimpleNamespace
    from langchain_core.messages import AIMessage
    from langchain_core.runnables import RunnableLambda
    from mech_chatbot.rag import pipeline_steps
    _,_,_,runtime=offline_pipeline
    texts=['Quy trình nghỉ phép: nhân viên gửi đề nghị đến trưởng bộ phận.',
           'Chính sách bảo hiểm: nhân viên đăng ký theo hướng dẫn của phòng nhân sự.',
           'Hướng dẫn đào tạo: nhân viên tham gia chương trình đào tạo nội bộ.']
    documents=[_document(text) for text in texts]
    documents=[type(doc)(page_content=doc.page_content,metadata={**doc.metadata,'doc_id':17+i}) for i,doc in enumerate(documents)]
    class Store:
        def __init__(self,**kwargs): pass
        def similarity_search(self,query,**kwargs):
            for word,index in [('nghỉ',0),('bảo',1),('đào',2)]:
                if word in query: return [] if missing and index==2 else [documents[index]]
            return []
    monkeypatch.setattr('langchain_qdrant.QdrantVectorStore',Store)
    monkeypatch.setattr(pipeline_steps,'audited_external_call',lambda **kwargs:nullcontext())
    generations=[]
    def generate(_prompt):
        generations.append(True)
        return AIMessage(content='Nhân viên gửi đề nghị đến trưởng bộ phận [SRC:D17P2]. '
            'Đăng ký bảo hiểm theo hướng dẫn của phòng nhân sự [SRC:D18P2]. '+
            ('' if missing else 'Tham gia chương trình đào tạo nội bộ [SRC:D19P2].'))
    provider=SimpleNamespace(client=RunnableLambda(generate),settings=SimpleNamespace(
        model_name='fixture-model',base_url='http://127.0.0.1/fixture'),invoke=lambda *a,**k:SimpleNamespace(content=''))
    executor=DefaultRagExecutor(retrieval_adapter=runtime,provider_adapter=provider)
    request=RagRequest('Quy trình nghỉ phép; chính sách bảo hiểm; hướng dẫn đào tạo',
        AccessScope(roles=frozenset({'reviewer'}),allowed_departments=frozenset({'HR'}),allowed_sites=frozenset({'HCM'}),
                    max_security_level='internal'),text_query=True)
    result=list(executor.run(request,RagInvocation('three-text',mode='test')))
    assert isinstance(result[-1],RagCompleted) and result[-1].outcome=='answered'
    branches=result[-1].diagnostics['decomposition_branches']
    assert len(branches)==3 and [b['outcome'] for b in branches]==['full_answer','full_answer',
                                                            'insufficient_evidence' if missing else 'full_answer']
    assert [{c['doc_id'] for c in branch['citations']} for branch in branches]==[{17},{18},set() if missing else {19}]
    assert result[-1].diagnostics.budget.subqueries==3 and result[-1].diagnostics.budget.final_generations==1
    assert generations==[True] and runtime.query_decomposition_enabled is False


def test_text_query_preserves_each_missing_branch_when_all_sources_are_absent(offline_pipeline):
    _,_,executor,_=offline_pipeline
    result=list(executor.run(RagRequest('Quy trình nghỉ phép; chính sách bảo hiểm; hướng dẫn đào tạo',
        AccessScope(roles=frozenset({'reviewer'})),text_query=True),RagInvocation('missing-text',mode='test')))
    assert isinstance(result[-1],RagCompleted) and result[-1].outcome=='refused'
    branches=result[-1].diagnostics['decomposition_branches']
    assert len(branches)==3 and all(branch['outcome']=='insufficient_evidence' for branch in branches)
    assert all(branch['citations']==[] for branch in branches)


def test_simple_text_query_avoids_planner_and_does_not_use_old_exact_cache(offline_pipeline,monkeypatch):
    from mech_chatbot.rag import semantic_cache
    _,_,executor,runtime=offline_pipeline
    runtime.semantic_cache_enabled=True
    monkeypatch.setattr(semantic_cache,'lookup_exact',lambda *a,**k:pytest.fail('evaluation cannot replay cache'))
    events=list(executor.run(RagRequest('Quy trình đào tạo là gì?',AccessScope(roles=frozenset({'reviewer'})),text_query=True),
                             RagInvocation('simple-text',mode='test')))
    assert isinstance(events[-1],RagCompleted) and events[-1].diagnostics.budget.planners==0
    assert events[-1].diagnostics['text_query_v2']['accepted'] is False


@pytest.mark.parametrize('path',['/chat','/chat/stream'])
def test_text_query_rest_and_sse_preserve_missing_branch_statuses(path,rag_client,offline_pipeline,monkeypatch):
    from mech_chatbot.auth import core
    from mech_chatbot.rag.execution_contracts import RagRuntimeContract
    _,_,executor,runtime=offline_pipeline
    monkeypatch.setattr(core,'load_user_profile',lambda **kwargs:{**VIEWER_PROFILE,'roles':['reviewer'],
        'allowed_departments':['HR'],'allowed_sites':['HCM']})
    _replace_runtime(rag_client,executor=executor,runtime_contract=RagRuntimeContract('evaluation',False,120))
    response=rag_client.post(path,headers=SERVICE_HEADERS,json={'user_id':7,'text_query':True,
        'user_question':'Quy trình nghỉ phép; chính sách bảo hiểm; hướng dẫn đào tạo'})
    assert response.status_code==200 and runtime.query_decomposition_enabled is False
    if path=='/chat':
        debug=response.json()['debug_info']
        assert debug['text_query_v2']=={'schema':'query-text-v2','accepted':False,'offline_only':True}
        assert len(debug['decomposition_branches'])==3
        assert all(branch['citations']==[] for branch in debug['decomposition_branches'])
    else:
        assert 'event: done' in response.text and 'query-text-v2' in response.text
        assert response.text.count('insufficient_evidence')>=3
