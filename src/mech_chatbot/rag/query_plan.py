"""Compose at most three fixed actions with one deadline and budget ledger."""
from dataclasses import replace
import logging
from time import monotonic
from sqlalchemy.exc import SQLAlchemyError
from mech_chatbot.application.chat_citations import build_citation_list,citation_ref_text
from mech_chatbot.db.evidence_snapshot import evidence_read_snapshot, evidence_snapshot_active
from mech_chatbot.domain.graph_query import GraphQuery, graph_fingerprint
from mech_chatbot.rag.bom_rollup import execute_bom_rollup
from mech_chatbot.rag.graph_query_v2 import execute_graph_query
from mech_chatbot.rag.scope_comparison import execute_scope_comparison
from mech_chatbot.rag.query_decomposition import fixed_task_guard

logger = logging.getLogger(__name__)


def _dependency_graph(branch, prior_results):
    # Only earlier branches of this request can supply evidence within the active SQL snapshot.
    if not branch.bom_rollup or not evidence_snapshot_active():
        return None
    requested = branch.bom_rollup.graph
    for result in prior_results:
        graph = result.get('graph')
        if (result['branch_id'] not in branch.depends_on or not result.get('data_complete')
                or not graph or graph.get('source_status') != 'complete'):
            continue
        query = GraphQuery(**graph['graph_context'])
        if (any(getattr(query,key) != getattr(requested,key) for key in ('root_key','product_scope','site','direction'))
                or graph['examined_relations'] > requested.max_relations):
            continue
        manifest = graph.get('manifest') or {}
        if not all(manifest.get(key) is True for key in ('authorized','current','completeness_reviewed')):
            continue
        tree = {key:graph[key] for key in ('contract_version','reasons','edges','manifest','examined_relations')}
        tree = {**tree, 'status':graph['source_status']}
        if graph_fingerprint(tree) == graph.get('graph_hash'):
            return result['branch_id'], tree, query
    return None


def _execute_branch(state,branch,prior_results=()):
    request=replace(state.request,question=branch.question,query_plan=None,scope_comparison=branch.scope_comparison,
                    graph_query=branch.graph_query,bom_rollup=branch.bom_rollup)
    child=replace(state,request=request,refusal_reason=None,generation_outcome=None)
    if state.task_binding is not None:
        child=replace(child,task_binding=state.task_binding.child(request,branch.branch_id),task_slot=branch.branch_id)
    execute=execute_graph_query if branch.graph_query else execute_bom_rollup if branch.bom_rollup else execute_scope_comparison
    dependency = _dependency_graph(branch,prior_results)
    prepared=execute(child,graph_dependency=dependency) if dependency else execute(child)
    outcome=prepared.diagnostics['answer_outcome']
    status={'full_answer':'complete','partial_answer':'partial','refused':'unknown'}[outcome]
    records=prepared.diagnostics.get('citation_docs',())
    graph=prepared.diagnostics.get('graph_v2') or {}
    return {'branch_id':branch.branch_id,'question':branch.question,'status':status,
            'answer':''.join(prepared.stream),'reason':child.refusal_reason,
            'data_complete':status=='complete' or graph.get('source_status')=='complete',
            'sources':build_citation_list(records,page_has_vision=lambda *_:False),
            **{label:prepared.diagnostics[key] for label,key in (('graph','graph_v2'),('math','math_v2'))
               if key in prepared.diagnostics}},records,prepared.observed_math_results


def execute_query_plan(state):
    state.checkpoint('query_plan')
    if state.invocation.mode not in {'evaluation','test'}:
        raise ValueError('Query v2 requires evaluation context')
    if not {role.lower() for role in state.request.access.roles}.intersection({'reviewer','knowledge_approver','admin'}):
        raise PermissionError('Evaluation reviewer required')
    if (any(branch.text_query for branch in state.request.query_plan.branches) and state.invocation.task_routing_contract
            and state.invocation.task_routing_contract != 'semantic-mixed-candidate-task-routing-v5-draft'):
        raise ValueError("Mixed draft requires explicit branches without a fixed routing contract")
    terminal=fixed_task_guard(state)
    if terminal is not None:
        return terminal
    with evidence_read_snapshot(state.checkpoint, state.budget.deadline_monotonic):
        return _execute_branches(state)


def _execute_branches(state):
    results,records,observed=[],[],[]
    text_branches=[]
    for branch in state.request.query_plan.branches:
        if branch.text_query:
            text_branches.append(branch)
            continue
        state.checkpoint('query_branch')
        started=monotonic()
        complete={b['branch_id'] for b in results if b.get('data_complete')}
        executed=False
        if not set(branch.depends_on)<=complete:
            result={'branch_id':branch.branch_id,'question':branch.question,'status':'blocked','reason':'dependency_unavailable',
                    'answer':'Nhánh phụ thuộc chưa đủ dữ liệu đã xác nhận.','sources':[]}
        else:
            state.budget.record('subqueries',1,cumulative=True)
            try:
                executed=True
                result,sources,calculations=_execute_branch(state,branch,results)
                records.extend(sources)
                observed.extend({**record, 'branch_id': branch.branch_id} for record in calculations or ())
            except (SQLAlchemyError,PermissionError):
                logger.warning('Query branch evidence unavailable', exc_info=True)
                result={'branch_id':branch.branch_id,'question':branch.question,'status':'unknown','reason':'evidence_unavailable',
                        'answer':'Chưa đủ chứng cứ khả dụng cho ý này.','sources':[]}
        results.append({**result,'template':'graph_query' if branch.graph_query else 'bom_rollup' if branch.bom_rollup else 'compare_two_scopes',
                        'depends_on':list(branch.depends_on),'executed':executed,'data_complete':result.get('data_complete',False),
                        'elapsed_ms':int((monotonic()-started)*1000)})
    text_answer=""
    text_usage={}
    if text_branches:
        text_results,text_records,text_answer,text_usage=_execute_text_batch(state,text_branches,results)
        results.extend(text_results)
        records.extend(text_records)
        by_id={result["branch_id"]:result for result in results}
        results=[by_id[branch.branch_id] for branch in state.request.query_plan.branches]
    return _prepared_result(state,results,records,observed,text_answer,text_usage)


def _execute_text_batch(state,branches,fixed_results):
    from mech_chatbot.rag.pipeline import execute_pipeline
    from mech_chatbot.llm.external_ai import ExternalProcessingDenied
    from mech_chatbot.rag.semantic_cache import replay_cache_disabled
    complete={branch['branch_id'] for branch in fixed_results if branch.get('data_complete')}
    ready=[branch for branch in branches if set(branch.depends_on)<=complete]
    results=[{'branch_id':branch.branch_id,'question':branch.question,'status':'blocked',
        'template':'text_query','depends_on':list(branch.depends_on),'executed':False,'data_complete':False,
        'reason':'dependency_unavailable','answer':'Nhánh phụ thuộc chưa đủ dữ liệu đã xác nhận.',
        'sources':[],'elapsed_ms':0} for branch in branches if branch not in ready]
    from mech_chatbot.rag.query_dependencies import computed_dependencies, dependency_profile
    from mech_chatbot.services.rag_evidence_service import dependency_source_attributes
    from mech_chatbot.llm.external_ai import ALL_EXTERNAL
    dependencies = computed_dependencies(ready, fixed_results)
    permitted = []
    for branch, dependency in zip(ready, dependencies):
        try:
            if dependency.sources:
                attrs = dependency_source_attributes(dependency.sources, dependency_profile(state.request.access))
                if any(attribute['external_processing_policy'] != ALL_EXTERNAL for attribute in attrs):
                    raise PermissionError('Dependency external processing unavailable')
            permitted.append((branch, dependency))
        except (SQLAlchemyError, PermissionError):
            results.append({'branch_id':branch.branch_id,'question':branch.question,'status':'unknown',
                'template':'text_query','depends_on':list(branch.depends_on),'executed':False,'data_complete':False,
                'reason':'evidence_unavailable','answer':'Chưa đủ chứng cứ khả dụng cho ý này.',
                'sources':[],'elapsed_ms':0})
    ready = [branch for branch, _ in permitted]
    if not ready:return results,(),'',{}
    started=monotonic()
    request=replace(state.request,query_plan=None,text_query=True,
                    question='; '.join(branch.question for branch in ready))
    child=replace(state,request=request,refusal_reason=None,generation_outcome=None,
                  explicit_text_intents=tuple(branch.question for branch in ready))
    child=replace(child,text_dependencies=tuple(dependency for _, dependency in permitted))
    if state.task_binding is not None:
        child=replace(child,task_binding=state.task_binding.text_batch(
            request,ready,child.explicit_text_intents),task_slot='text-batch')
    try:
        with replay_cache_disabled(True):
            prepared=execute_pipeline(child)
            answer=''.join(prepared.stream)
    except (SQLAlchemyError,PermissionError,ExternalProcessingDenied):
        logger.warning('Mixed text evidence unavailable',exc_info=True)
        child.refuse('evidence_unavailable')
        prepared=child.prepared((iter(()),'',(),(),{}))
        answer='Chưa đủ chứng cứ khả dụng cho ý này.'
    completion,reason=child.completion()
    diagnostics=prepared.diagnostics
    evidence=diagnostics.get('decomposition_branches',())
    records=[]
    for index,branch in enumerate(ready):
        detail=evidence[index] if index<len(evidence) else {}
        rendered=set(detail.get('rendered_source_ids',()))
        usable=detail.get('outcome')=='full_answer' and completion=='answered' and bool(rendered)
        sources=[source for source in detail.get('citations',()) if source.get('source_id') in rendered] if usable else ()
        records.extend(sources)
        results.append({'branch_id':branch.branch_id,'question':branch.question,
            'status':'complete' if usable else 'unknown','template':'text_query',
            'depends_on':list(branch.depends_on),'executed':True,'data_complete':False,
            'reason':None if usable else reason or 'evidence_unavailable',
            'answer':'','sources':build_citation_list(sources,page_has_vision=lambda *_:False),
            'elapsed_ms':int((monotonic()-started)*1000)})
    usage={key:diagnostics[key] for key in ('generation_metrics','decomposition_usage') if key in diagnostics}
    return results,records,answer,usage


def _prepared_result(state,results,records,observed=(),text_answer='',text_usage=None):
    usable=any(b['status'] in {'complete','partial'} for b in results)
    status='complete' if all(b['status']=='complete' for b in results) else 'partial' if usable else 'unknown'
    if not usable:state.refuse('query_evidence_unavailable')
    cards=build_citation_list(records,page_has_vision=lambda *_:False)
    answer='\n\n'.join(f"{n}. {b['question']}\n{b['answer']}" for n,b in enumerate(results,1) if b['answer'])
    if text_answer:answer+='\n\n'+text_answer
    diagnostics={**(text_usage or {}),'query_v2':{'schema':'query-plan-mixed-v1-draft' if state.invocation.task_routing_contract=='semantic-mixed-candidate-task-routing-v5-draft' or any(b['template']=='text_query' for b in results) else 'query-plan-v2','status':status,'branches':results,'accepted':False,'offline_only':True,
                              'read_consistency':'sql_serializable_text_current' if text_answer else 'serializable',
                              **({'text_answer':text_answer} if text_answer else {})},
                 'citation_docs':records,'retrieved_docs':records,
                 'generation_metrics':{**(text_usage or {}).get('generation_metrics',{}),'calculation_count':state.budget.calculations},
                 'answer_outcome':'full_answer' if status=='complete' else 'partial_answer' if usable else 'refused'}
    state.checkpoint('query_plan')
    return state.prepared((iter((answer,)),citation_ref_text(cards),(),(),diagnostics), observed_math_results=observed)
