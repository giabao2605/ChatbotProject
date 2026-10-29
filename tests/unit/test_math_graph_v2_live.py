"""A shared release keeps Math and Graph isolated in the existing chat paths."""
from dataclasses import replace
from types import SimpleNamespace

import pytest

from mech_chatbot.api.dependencies import current_profile
from mech_chatbot.config.repository_runtime import bind_repository_runtime
from mech_chatbot.db.repositories.structured_evidence import load_approved_scope
from mech_chatbot.domain.structured_evidence import reviewed_scope_fingerprint
from mech_chatbot.governance.math_v2_activation import CONTRACT as MATH_CONTRACT,MathV2Capability
from .test_graph_v2_live_query import capability,request,threaded_graph_store,bom_store,store,connection
from .test_rag_bom_chat import PROFILE
from .test_rag_evidence_api import client
from .test_math_graph_v2_activation import pair_grant,grant
from .test_rag_server_endpoints import rag_client,SERVICE_HEADERS,_replace_runtime,_replace_server_state


def paired_caps(engine):
    graph=capability(engine,request())
    scopes=[load_approved_scope(key,PROFILE,db_engine=engine) for key in ('quantity-scope','policy')]
    assert all(scopes)
    math=MathV2Capability(MATH_CONTRACT,'compare_two_scopes',tuple(
        (s.scope_id,s.snapshot_id,reviewed_scope_fingerprint(s)) for s in scopes),
        graph.source_commit,graph.bundle_sha256,graph.snapshot_fingerprint,graph.expires_at)
    return math,graph


@pytest.mark.parametrize('matching_parent',[True,False])
def test_combined_picker_advertises_only_caps_with_same_parent(threaded_graph_store,monkeypatch,matching_parent):
    engine=threaded_graph_store; math,graph=paired_caps(engine)
    if not matching_parent:math=replace(math,bundle_sha256='d'*64)
    with client(monkeypatch,mode='production',roles=('viewer',)) as api:
        api.app.state.runtime=SimpleNamespace(math_v2_authorizer=lambda:math,graph_v2_authorizer=lambda:graph)
        api.app.dependency_overrides[current_profile]=lambda:{**PROFILE,'roles':['viewer']}
        response=api.get('/api/rag-evidence/evaluation-capabilities')
        assert response.status_code==200
        if matching_parent:
            assert response.json()['contract']=='math-graph-v2-controlled-demo-v1'
            assert response.json()['actions']==['contains','where_used','scope_comparison']
            assert response.json()['max_branches']==1 and response.json()['text_query'] is False
            with bind_repository_runtime(db_engine=engine):
                graph_page=api.get('/api/rag-evidence/documents/1/graph-scopes',params={'site':'factory'})
                math_page=api.get('/api/rag-evidence/documents/1/approved-scopes')
            assert graph_page.status_code==200 and graph_page.json()['items']
            assert math_page.status_code==200 and math_page.json()['items']
        else:assert response.json()['actions']==['contains','where_used']


@pytest.mark.parametrize('path',['/chat','/chat/stream'])
def test_signed_pair_alternates_native_graph_and_decimal_math_then_revokes(path,threaded_graph_store,pair_grant,rag_client,monkeypatch):
    from mech_chatbot.auth import core
    from mech_chatbot.rag.execution_contracts import RagRuntimeContract
    from mech_chatbot.rag.execution import DefaultRagExecutor
    from mech_chatbot.governance.math_v2_activation import load_math_v2_capability
    from mech_chatbot.governance.graph_v2_activation import load_graph_v2_capability,graph_pins_fingerprint
    from mech_chatbot.domain.graph_query import graph_query_context
    import json
    engine=threaded_graph_store; math,graph=paired_caps(engine)
    root,math_gate,graph_gate,issue,environment=pair_grant
    math_gate['scope_pins']=[{'scope_id':sid,'snapshot_id':snapshot,'scope_hash':digest} for sid,snapshot,digest in math.scope_pins]
    graph_gate['graph_pins']=[{'graph_context':json.loads(context),'graph_hash':digest} for context,digest in graph.graph_pins]
    graph_gate['snapshot_fingerprint']=graph_pins_fingerprint(graph_gate['graph_pins'])
    bundle,digest=issue(); env=environment(bundle,digest)
    math_authorizer=lambda:load_math_v2_capability(env,root=root,current_commit='candidate')
    graph_authorizer=lambda:load_graph_v2_capability(env,root=root,current_commit='candidate')
    assert math_authorizer().bundle_sha256==graph_authorizer().bundle_sha256==digest
    monkeypatch.setattr(core,'load_user_profile',lambda **_:{**PROFILE,'roles':['viewer']})
    _replace_server_state(rag_client,database_runtime=SimpleNamespace(engine=engine))
    _replace_runtime(rag_client,executor=DefaultRagExecutor(math_v2_authorizer=math_authorizer,graph_v2_authorizer=graph_authorizer),
        math_v2_authorizer=math_authorizer,graph_v2_authorizer=graph_authorizer,
        runtime_contract=RagRuntimeContract('production',False,120))
    headers={**SERVICE_HEADERS,'X-RAG-Activation-Bundle-SHA256':digest}
    graph_body={'user_id':7,'user_question':'Cụm chứa gì?','graph_query':graph_query_context(request().graph_query)}
    math_body={'user_id':7,'user_question':'So sánh hai phạm vi','scope_comparison':{'scope_a':'quantity-scope','scope_b':'policy'}}
    for body,kind in [(graph_body,'graph_v2'),(math_body,'math_v2'),(graph_body,'graph_v2')]:
        response=rag_client.post(path,headers=headers,json=body)
        assert response.status_code==200
        if path=='/chat':
            diagnostics=response.json()['debug_info']
            assert diagnostics[kind]['offline_only'] is False and response.json()['citations']
            if kind=='math_v2':assert [s['display_value'] for s in diagnostics[kind]['steps']]==['2.00','1.00','-1.00','-50.00']
            else:assert 'part:p' in response.json()['response']
        else:assert 'event: citation' in response.text and 'event: done' in response.text
    (root/'pair-bundle.json').unlink()
    assert rag_client.post(path,headers=headers,json=graph_body).status_code==409
    assert rag_client.post(path,headers=headers,json=math_body).status_code==409


def test_combined_bundle_never_enables_legacy_enrichment_or_query(tmp_path):
    from mech_chatbot.config.settings import Settings,RagProcessSettings
    from mech_chatbot.composition.rag_runtime import _retrieval_feature_settings
    path=tmp_path/'pair.json';path.write_text('{"schema":"rag-activation-bundle-v4"}')
    settings=Settings(RAG_GRAPH_RETRIEVAL_ENABLED=True,RAG_GROUNDED_MATH_ENABLED=True,RAG_ACTIVATION_BUNDLE_PATH=str(path))
    features=_retrieval_feature_settings(settings,RagProcessSettings.from_settings(settings))
    assert not features['graph_retrieval_enabled'] and not features['grounded_math_enabled']
    assert not features['query_decomposition_enabled']
