"""Synthetic branch ranking fixtures; no provider or document-source access."""
from types import SimpleNamespace
import threading
import time

import pytest
from langchain_core.documents import Document


@pytest.fixture
def module():
    from mech_chatbot.rag.phases import branch_evidence
    return branch_evidence


def docs(branch, count=3, policy='all_external'):
    return [Document(page_content=f'Synthetic {branch} {index}', metadata={
        'doc_id': branch * 10 + index, 'trang_so': 1, 'external_processing_policy': policy,
        'loai_du_lieu': 'text'}) for index in range(count)]


def runtime(timeout=0.5):
    return SimpleNamespace(rerank_provider='jina', rerank_enabled=True,
        rerank_runtime=SimpleNamespace(api_key='fake'), rerank_timeout_seconds=timeout)


def rank(module, branches, **kwargs):
    return module.rerank_branch_sets(branches, runtime(), trace_id='synthetic',
        checkpoint=kwargs.pop('checkpoint', lambda _: None),
        deadline_monotonic=kwargs.pop('deadline_monotonic', time.monotonic()+1),
        top_n=max(2, 8//len(branches)), **kwargs)


@pytest.mark.parametrize('branch_count,candidate_count,expected_each', [(3,3,2),(2,5,4)])
def test_each_branch_uses_own_query_and_cap_in_parallel(module, monkeypatch, branch_count, candidate_count, expected_each):
    barrier = threading.Barrier(branch_count)
    calls = []
    def jina(documents, query, **kwargs):
        calls.append((query, kwargs['top_n']))
        barrier.wait(timeout=1)
        return list(reversed(documents))[:kwargs['top_n']]
    monkeypatch.setattr(module, 'jina_rerank_documents', jina)
    branches = [(f'question {index}', docs(index, candidate_count)) for index in range(1,branch_count+1)]
    result = rank(module, branches, deadline_monotonic=time.monotonic()+2)
    assert sorted(calls) == [(query, expected_each) for query,_ in branches]
    assert all(len(items)==expected_each for items in result)
    assert sum(map(len,result)) <= 8
    assert [[d.page_content for d in items] for items in result] == [
        [d.page_content for d in reversed(items)][:expected_each] for _,items in branches]


def test_certification_filters_before_provider_without_reordering(module, monkeypatch):
    candidates = docs(1)
    from mech_chatbot.services import feedback_service
    calls=[]
    def certify(items, actor, **kwargs):
        calls.append(kwargs)
        return items[1:]
    monkeypatch.setattr(feedback_service,'pin_generation_evidence',certify)
    state=SimpleNamespace(request=SimpleNamespace(access=object()),checkpoint=lambda _:None,
                          budget=SimpleNamespace(deadline_monotonic=time.monotonic()+1))
    valid = module.certify_branch_documents(candidates,state,allow_history=True,image_analysis=None)
    def jina(items,query,**kwargs):
        assert [d.page_content for d in items]==[d.page_content for d in candidates[1:]]
        return items
    monkeypatch.setattr(module,'jina_rerank_documents',jina)
    assert rank(module,[('own query',valid)])[0]==valid
    assert calls[0]['allow_history'] is True
    assert calls[0]['deadline']==state.budget.deadline_monotonic


def test_all_uncertified_is_empty_without_provider(module, monkeypatch):
    from mech_chatbot.services import feedback_service
    def certify(*args,**kwargs):
        raise feedback_service.EvidenceCertificationError('synthetic')
    monkeypatch.setattr(feedback_service,'pin_generation_evidence',certify)
    state=SimpleNamespace(request=SimpleNamespace(access=object()),checkpoint=lambda _:None,
                          budget=SimpleNamespace(deadline_monotonic=time.monotonic()+1))
    valid=module.certify_branch_documents(docs(1),state)
    monkeypatch.setattr(module,'jina_rerank_documents',lambda *a,**k:pytest.fail('No uncertified send'))
    assert rank(module,[('own query',valid)]) == ([],)


def test_policy_is_checked_separately_for_every_new_branch_set(module, monkeypatch):
    calls=[]
    monkeypatch.setattr(module,'jina_rerank_documents',lambda items,query,**kwargs:calls.append(query) or items[:kwargs['top_n']])
    external,private = docs(1),docs(2,policy='internal_only')
    result=rank(module,[('public query',external),('private query',private)])
    assert calls==['public query']
    assert result[1]==private


def test_one_expired_stage_keeps_rrf_and_does_not_cancel_sibling(module, monkeypatch):
    release=threading.Event()
    slow=docs(1)
    def jina(items,query,**kwargs):
        if query=='slow':
            release.wait(1)
            items[0].metadata['relevance_score']=999
            return list(reversed(items))
        return list(reversed(items))
    monkeypatch.setattr(module,'jina_rerank_documents',jina)
    fast=docs(2)
    started=time.monotonic()
    try:
        result=rank(module,[('slow',slow),('fast',fast)],deadline_monotonic=started+0.08)
        assert time.monotonic()-started < 0.4
        assert result[0]==slow
        assert [d.page_content for d in result[1]]==[d.page_content for d in reversed(fast)]
    finally:
        release.set()
    assert all('relevance_score' not in d.metadata for d in slow)


@pytest.mark.parametrize('failure', [TimeoutError, RuntimeError, ValueError])
def test_rerank_error_falls_back_to_original_rrf_order(module, monkeypatch, failure):
    candidates=docs(1)
    def jina(*args,**kwargs):raise failure('synthetic')
    monkeypatch.setattr(module,'jina_rerank_documents',jina)
    assert rank(module,[('own query',candidates)])[0]==candidates


@pytest.mark.parametrize('failure_name',['cancel','budget','deadline','security','audit'])
def test_request_and_security_failures_are_not_converted_to_rank_fallback(module, monkeypatch, failure_name):
    from mech_chatbot.llm.external_ai import ExternalAICallCancelled,ExternalAIComplianceAuditUnavailable
    from mech_chatbot.rag.execution import RequestBudgetExceeded,RequestDeadlineExceeded
    failure={'cancel':ExternalAICallCancelled,'budget':RequestBudgetExceeded,'deadline':RequestDeadlineExceeded,
             'security':PermissionError,'audit':ExternalAIComplianceAuditUnavailable}[failure_name]
    def jina(*args,**kwargs):raise failure('synthetic')
    monkeypatch.setattr(module,'jina_rerank_documents',jina)
    with pytest.raises(failure):rank(module,[('own query',docs(1))])


def test_served_branch_marker_preserves_ranked_set_but_single_branch_stays_old():
    from mech_chatbot.rag.query_decomposition import served_branch_documents
    candidates=docs(1)
    assert served_branch_documents(candidates,'general+branch_ranked')==candidates
    assert served_branch_documents(candidates,'general')==candidates[:1]



def test_complex_pipeline_policy_and_merged_evidence_use_each_ranked_set(module, monkeypatch):
    from unittest.mock import Mock
    from mech_chatbot.rag.phases import retrieval, retrieval_enrichment
    from mech_chatbot.rag.query_decomposition import BranchPlan
    from mech_chatbot.services import feedback_service
    from mech_chatbot.rag import demo_sources
    monkeypatch.setattr(demo_sources,'refresh_applicable_sources',lambda documents,*_:documents)
    monkeypatch.setattr(retrieval_enrichment,'enrich_branch_candidates',lambda decision,state,query,ids,documents:documents)
    events=[]
    def certify(documents,actor,**kwargs):
        events.append(('certify',tuple(d.metadata['doc_id'] for d in documents)))
        return documents
    monkeypatch.setattr(feedback_service,'pin_generation_evidence',certify)
    def jina(documents,query,**kwargs):
        events.append(('jina',query))
        assert len([e for e in events if e[0]=='certify'])==3
        return list(reversed(documents))[:kwargs['top_n']]
    monkeypatch.setattr(module,'jina_rerank_documents',jina)
    candidates=[docs(index) for index in range(1,4)]
    request=SimpleNamespace(user_question='Original full question',image_analysis=None)
    decision=SimpleNamespace(request=request,intent_data={},crag_enabled=False,rbac_filter=None)
    context=SimpleNamespace(runtime=runtime(),decision=decision,effective_question=request.user_question,
        new_part_ids=(),user_roles=(),allowed_departments=(),allowed_sites=(),trace_id='test',dependencies=())
    state=SimpleNamespace(explicit_text_intents=(),request=SimpleNamespace(access=object()),checkpoint=Mock(),
        budget=SimpleNamespace(deadline_monotonic=time.monotonic()+10,limits=SimpleNamespace(corrections=1),
                               record=Mock(),deadline_exceeded=False),
        retrieve_many=lambda requests:tuple((items,5,'general',time.time(),None) for items in candidates))
    monkeypatch.setattr(retrieval,'_branch_filters',lambda *_:([],None,None))
    observed=[]
    def policy(context,query,documents,**kwargs):
        observed.append((query,tuple(d.metadata['doc_id'] for d in documents)))
        assert len(documents)==2
        return SimpleNamespace(state=SimpleNamespace(value='SUPPORTED')),SimpleNamespace(
            correction_allowed=False,evidence_state=SimpleNamespace(value='SUPPORTED'),
            outcome=SimpleNamespace(value='full_answer'))
    monkeypatch.setattr(retrieval,'_branch_answer_policy',policy)
    monkeypatch.setattr(retrieval,'_log_decomposition',lambda *a:None)
    monkeypatch.setattr(retrieval,'_decomposition_usage',lambda *a:{})
    monkeypatch.setattr(retrieval,'make_source_snapshot',lambda documents:[])
    queries=('first question','second question','third question')
    plan=BranchPlan(request.user_question,True,queries,intents=queries,intent_coverage=(True,)*3)
    result=retrieval._run_complex_plan(context,state,{},retrieval._PlannerResult(plan,0,0,0))
    assert len(result.batch.documents)==6
    assert [d.metadata['doc_id'] for d in result.batch.documents]==[12,11,22,21,32,31]
    assert '+branch_ranked' in result.batch.mode
    assert len(observed)==6


def test_corrected_branch_is_enriched_recertified_and_reranked_before_policy(module,monkeypatch):
    from unittest.mock import Mock
    from mech_chatbot.rag.phases import retrieval,retrieval_enrichment
    from mech_chatbot.services import feedback_service
    from mech_chatbot.rag import demo_sources
    monkeypatch.setattr(demo_sources,'refresh_applicable_sources',lambda documents,*_:documents)
    monkeypatch.setattr(retrieval_enrichment,'enrich_branch_candidates',lambda decision,state,query,ids,documents:documents)
    seen=[]
    def certify(documents,actor,**kwargs):
        seen.append(('certify',len(documents)))
        return documents[1:]
    monkeypatch.setattr(feedback_service,'pin_generation_evidence',certify)
    def jina(documents,query,**kwargs):
        seen.append(('rerank',query,len(documents)))
        return list(reversed(documents))[:kwargs['top_n']]
    monkeypatch.setattr(module,'jina_rerank_documents',jina)
    original=docs(1,2)
    corrected=docs(2,3)
    request=SimpleNamespace(user_question='Original full question',image_analysis=None)
    context=SimpleNamespace(runtime=runtime(),decision=SimpleNamespace(request=request,intent_data={},crag_enabled=True),
                            user_roles=(),trace_id='test')
    state=SimpleNamespace(request=SimpleNamespace(access=object()),checkpoint=Mock(),
        budget=SimpleNamespace(deadline_monotonic=time.monotonic()+10))
    monkeypatch.setattr(retrieval,'_branch_answer_policy',lambda *a,**k:(object(),object()))
    correction=retrieval._BranchCorrection((corrected,5,'general+branch_ranked+corrected',time.time(),None),True)
    monkeypatch.setattr(retrieval,'_maybe_correct_branch',lambda *a:correction)
    monkeypatch.setattr(retrieval,'_probe_branch_access',lambda *a:False)
    result=retrieval._finish_retrieved_branch(context,state,'own branch question',{},object(),
        state.budget.deadline_monotonic,[],None,None,(original,5,'general+branch_ranked',time.time(),None),
        time.perf_counter(),branch_cap=2,rerank_deadline_monotonic=time.monotonic()+1,branch_index=3)
    assert seen==[('certify',3),('rerank','own branch question',2)]
    assert [d.metadata['doc_id'] for d in result.documents]==[22,21]
    assert result.correction_attempted


def test_global_rerank_cannot_recut_or_hydrate_final_branch_membership(monkeypatch):
    from mech_chatbot.rag.phases import retrieval_rerank as rerank
    from mech_chatbot.services import feedback_service
    from mech_chatbot.rag import demo_sources
    monkeypatch.setattr(demo_sources,'refresh_applicable_sources',lambda documents,*_:documents)
    monkeypatch.setattr(rerank,'refresh_applicable_sources',lambda documents,*_:documents)
    monkeypatch.setattr(feedback_service,'pin_generation_evidence',lambda documents,*a,**k:documents)
    for name in ('_prepare_candidates','_late_interaction_rerank','_apply_rerank_backend','_hydrate_reranked_context','long_context_reorder'):
        monkeypatch.setattr(rerank,name,lambda *a,**k:pytest.fail('Final branch membership must stay intact'))
    candidates=docs(1,2)+docs(2,2)+docs(3,2)
    decision=SimpleNamespace(request=SimpleNamespace(trace_id='test',user_question='full question',
        user_roles=(),image_analysis=None),effective_question='full question',intent_data={})
    enrichment=SimpleNamespace(new_part_ids=(),documents=candidates,graph_documents=(),served_graph_documents=(),
        community_documents=(),retrieval_mode='decomposed_general+branch_ranked')
    state=SimpleNamespace(retrieval_adapter=runtime(),checkpoint=lambda _:None,
        request=SimpleNamespace(access=object()),budget=SimpleNamespace(deadline_monotonic=time.monotonic()+1))
    result=rerank.rerank_retrieval(decision,enrichment,state,answerable_branch_count=3)
    assert list(result.documents)==candidates



def test_expired_worker_cannot_emit_duplicate_terminal_log_or_late_checkpoint(module,monkeypatch):
    release,finished=threading.Event(),threading.Event()
    logs,checkpoints=[],[]
    monkeypatch.setattr(module,'log_trace',lambda event,trace,**values:logs.append((event,values)))
    def jina(items,query,**kwargs):
        release.wait(1)
        return list(reversed(items))
    original_rank=module._rank_branch
    def track_worker(*args):
        try:
            return original_rank(*args)
        finally:
            finished.set()
    monkeypatch.setattr(module,'_rank_branch',track_worker)
    monkeypatch.setattr(module,'jina_rerank_documents',jina)
    result=rank(module,[('slow',docs(1))],deadline_monotonic=time.monotonic()+0.03,
                checkpoint=lambda stage:checkpoints.append(stage))
    before=list(checkpoints)
    release.set()
    assert finished.wait(1)
    assert checkpoints==before
    assert len(logs)==1 and logs[0][1]['reason']=='stage_deadline'
    assert logs[0][1]['branch_index']==1
    assert len(result[0])==3


@pytest.mark.parametrize('kind,expected',[('split','branches'),('whole','original'),('simple','original'),('overflow','overflow'),('clarify','clarification')])
def test_planner_decision_log_is_metadata_only(monkeypatch,kind,expected):
    from mech_chatbot.rag.phases import retrieval
    from mech_chatbot.rag.query_decomposition import BranchPlan
    calls=[]
    monkeypatch.setattr(retrieval,'log_trace',lambda event,trace,**data:calls.append(data))
    question='private question text'
    queries=(question,) if kind=='whole' else ('branch one','branch two') if kind=='split' else ()
    plan=BranchPlan(question,kind!='simple',queries,intents=queries,
                    intent_overflow=kind=='overflow',clarification_required=kind=='clarify')
    retrieval._log_planner_decision(SimpleNamespace(trace_id='test'),plan,'planner')
    assert calls[0]['decision']==expected
    assert calls[0]['clarification']==(kind=='clarify')
    assert question not in repr(calls) and 'branch one' not in repr(calls)



def test_run_retrieval_serves_six_ranked_chunks_without_global_recut(module,monkeypatch):
    from mech_chatbot.rag import pipeline
    from mech_chatbot.rag.phases import retrieval,retrieval_enrichment,retrieval_rerank
    from mech_chatbot.rag.query_decomposition import BranchPlan
    from mech_chatbot.services import feedback_service
    from .test_rag_phase_feature_orchestration import _prepared_request,_route_decision,_run_phase,_retrieval_adapter
    questions=('first question','second question','third question')
    candidates=[docs(index) for index in range(1,4)]
    calls=[]
    def jina(documents,question,**kwargs):
        calls.append(question)
        return list(reversed(documents))[:kwargs['top_n']]
    monkeypatch.setattr(module,'jina_rerank_documents',jina)
    monkeypatch.setattr(feedback_service,'pin_generation_evidence',lambda documents,*a,**k:documents)
    plan=BranchPlan('full question',True,questions,intents=questions,intent_coverage=(True,)*3)
    monkeypatch.setattr(retrieval,'_compile_plan',lambda *a:retrieval._PlannerResult(plan,0,0,0))
    def policy(context,question,documents,**kwargs):
        assert len(documents)==2
        return SimpleNamespace(state=SimpleNamespace(value='SUPPORTED')),SimpleNamespace(
            correction_allowed=False,evidence_state=SimpleNamespace(value='SUPPORTED'),
            outcome=SimpleNamespace(value='full_answer'))
    monkeypatch.setattr(retrieval,'_branch_answer_policy',policy)
    def disambiguate(context,state,documents,*args):
        assert len(documents)==6
        return documents,None
    monkeypatch.setattr(retrieval_enrichment,'_disambiguate_documents',disambiguate)
    for name in ('_prepare_candidates','_late_interaction_rerank','_apply_rerank_backend','_hydrate_reranked_context','long_context_reorder'):
        monkeypatch.setattr(retrieval_rerank,name,lambda *a,**k:pytest.fail('Global recut after branch ranking'))
    adapter=_retrieval_adapter(query_decomposition_enabled=True,rerank_provider='jina',rerank_enabled=True,
        rerank_runtime=SimpleNamespace(api_key='fake'),rerank_timeout_seconds=1,
        retrieve_many=lambda requests,**kwargs:tuple((items,5,'general',time.time(),None) for items in candidates))
    decision=_route_decision(_prepared_request(question='full question'),part_ids=())
    result=_run_phase(lambda state:pipeline._run_retrieval(decision,state),retrieval=adapter,
        provider=SimpleNamespace(invoke=lambda *a,**k:pytest.fail('No provider requested')))
    assert sorted(calls)==sorted(questions)
    assert [d.metadata['doc_id'] for d in result.reranked.documents]==[12,11,22,21,32,31]
    assert all(len(branch['citations'])==2 for branch in result.primary.decomposition_branches)



def test_empty_ranked_primary_does_not_escape_branch_policy_through_hyde(monkeypatch):
    from dataclasses import replace
    from mech_chatbot.rag.phases import retrieval
    from .test_rag_phase_feature_orchestration import _prepared_request,_route_decision,_retrieval_adapter
    decision=replace(_route_decision(_prepared_request(question='full question'),part_ids=()),hyde_eligible=True)
    state=SimpleNamespace(retrieval_adapter=_retrieval_adapter(query_decomposition_enabled=True),
        explicit_text_intents=(),text_dependencies=(),transition=lambda stage:None,checkpoint=lambda stage:None,
        request=SimpleNamespace(),invoke_provider=lambda *a,**k:pytest.fail('No global HyDE after branch gate'),
        retrieve=lambda *a,**k:pytest.fail('No replacement branch set'))
    batch=retrieval._RetrievalBatch((),5,'decomposed_general+branch_ranked',time.time())
    monkeypatch.setattr(retrieval,'_run_decomposition',lambda *a:retrieval._DecompositionResult(batch))
    result=retrieval.retrieve_primary(decision,state)
    assert not result.documents
    assert result.retrieval_mode=='decomposed_general+branch_ranked'
