"""The same dependency guards apply to REST and SSE, before answer delivery."""
from contextlib import nullcontext
from types import SimpleNamespace
import re

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.pool import StaticPool
from langchain_core.messages import AIMessage
from langchain_core.runnables import RunnableLambda

from .test_rag_scope_comparison import approved_pair, store
from .test_rag_pipeline_public_characterization import offline_pipeline, _document
from .test_rag_server_endpoints import rag_client, SERVICE_HEADERS, _replace_runtime, _replace_server_state
from mech_chatbot.rag.execution import DefaultRagExecutor
from mech_chatbot.rag.execution_contracts import RagRuntimeContract


@pytest.mark.parametrize('path', ['/chat', '/chat/stream'])
@pytest.mark.parametrize('wrong_number', [False, True])
def test_computed_dependency_http_delivers_only_verified_numbers(path, wrong_number, rag_client, store, offline_pipeline, monkeypatch):
    from mech_chatbot.auth import core
    from mech_chatbot.rag import pipeline_steps
    approved_pair(store, values=('1000', '1200'))
    with store.begin() as conn:
        conn.execute(text("INSERT INTO TaiLieu SELECT 17,3,'guide.pdf',FilePath,'HR','HCM',SecurityLevel,"
            "LifecycleStatus,ReviewStatus,PublicationState,Servable,IsCurrent,EffectiveStatus,EffectiveDate,ExpiryDate,"
            "KnowledgeApproverUserID FROM TaiLieu WHERE DocID=1"))
        conn.execute(text("INSERT INTO PhongBanChiaSe VALUES(17,'HR')"))
        conn.execute(text('ALTER TABLE TaiLieu ADD COLUMN ExternalProcessingPolicy TEXT'))
        conn.execute(text("UPDATE TaiLieu SET ExternalProcessingPolicy='all_external'"))
    docs, _, _, runtime = offline_pipeline
    guide = _document('The difference is B minus A. The percentage compares the difference to the first total.')
    docs.append(type(guide)(page_content=guide.page_content,
        metadata={**guide.metadata, 'external_processing_policy':'all_external'}))
    prompts = []
    def generate(prompt):
        prompts.append(str(prompt))
        locator = re.findall(r'D\d+L[0-9A-Fa-f]{16}', prompts[-1])[0]
        explanation = 'The difference is 999 VND.' if wrong_number else 'The delta subtracts the first total from the second total.'
        return AIMessage(content=explanation+' '
            f'[SRC:{locator}] [SRC:D17P2]')
    monkeypatch.setattr(pipeline_steps, 'audited_external_call', lambda **kwargs: nullcontext())
    profile = {'user_id':7, 'username':'approver', 'department':'ENG', 'roles':['reviewer'],
        'allowed_departments':['ENG','HR'], 'allowed_sites':['factory','HCM'], 'max_security_level':'internal'}
    monkeypatch.setattr(core, 'load_user_profile', lambda **kwargs: profile)
    provider = SimpleNamespace(client=RunnableLambda(generate),
        settings=SimpleNamespace(model_name='fixture', base_url='http://127.0.0.1/fixture'),
        invoke=lambda *_a, **_kw: SimpleNamespace(content=''))
    shared = create_engine('sqlite://', connect_args={'check_same_thread':False}, poolclass=StaticPool)
    with store.connect() as source, shared.connect() as target:
        source.connection.driver_connection.backup(target.connection.driver_connection)
    _replace_runtime(rag_client, executor=DefaultRagExecutor(retrieval_adapter=runtime, provider_adapter=provider),
        runtime_contract=RagRuntimeContract('evaluation', False, 120))
    _replace_server_state(rag_client, database_runtime=SimpleNamespace(engine=shared))
    try:
        response = rag_client.post(path, headers=SERVICE_HEADERS, json={'user_id':7,
            'user_question':'Compare the chosen scopes and explain the computed difference.', 'response_language':'en',
            'query_plan':{'branches':[
                {'branch_id':'cost', 'question':'Compare the chosen scopes',
                 'scope_comparison':{'scope_a':'a','scope_b':'b'}},
                {'branch_id':'explain', 'question':'Explain the computed difference',
                 'text_query':True, 'depends_on':['cost']}]}})
        assert response.status_code == 200 and len(prompts) == 1
        assert '999' not in response.text
        if path == '/chat':
            query = response.json()['debug_info']['query_v2']
            assert [branch['status'] for branch in query['branches']] == ['complete', 'unknown' if wrong_number else 'complete']
            if not wrong_number:
                assert any('L' in source['source_id'] for source in query['branches'][1]['sources'])
        else:
            assert 'event: done' in response.text and 'event: citation' in response.text
            assert 'query-plan-mixed-v1-draft' in response.text
        assert '200.00 VND' in response.text
    finally:
        shared.dispose()
