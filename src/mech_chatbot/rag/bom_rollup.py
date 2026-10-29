"""Fixed BOM totals from the complete reviewed manifest, never top-k edges."""
from dataclasses import asdict
from decimal import Decimal
from mech_chatbot.db.evidence_snapshot import evidence_read_snapshot

from mech_chatbot.application.chat_citations import build_citation_list,citation_ref_text,structured_citation
from mech_chatbot.db.repositories.graph_v2 import retrieve_graph_v2
from mech_chatbot.db.repositories.structured_evidence import load_approved_scope
from mech_chatbot.rag.grounded_math_v2 import MathResult,bom_rollup,bom_order_quantity,serialize_result
from mech_chatbot.rag.graph_query_v2 import graph_source_records,bounded_graph_query
from mech_chatbot.rag.scope_comparison import _sources,_scope_manifest
from mech_chatbot.rag.query_decomposition import fixed_task_guard, fixed_task_outputs
from mech_chatbot.services.rag_evidence_service import scope_is_current


def execute_bom_rollup(state,*,graph_dependency=None):
    state.checkpoint('bom_rollup')
    if state.invocation.mode not in {'evaluation','test'} and state.query_v2_capability is None:
        raise ValueError('BOM v2 requires evaluation context')
    access=state.request.access
    if state.query_v2_capability is None and not {role.lower() for role in access.roles}.intersection({'reviewer','knowledge_approver','admin'}):
        raise PermissionError('Evaluation reviewer required')
    state.require_query_authority()
    terminal=fixed_task_guard(state)
    if terminal is not None:
        return terminal
    with evidence_read_snapshot(state.checkpoint,state.budget.deadline_monotonic):
        return _read_bom(state,graph_dependency=graph_dependency)


def _read_bom(state,*,graph_dependency=None):
    access=state.request.access
    selection=state.request.bom_rollup
    query=graph_dependency[2] if graph_dependency else bounded_graph_query(state,selection.graph)
    profile={'roles':tuple(access.roles),'allowed_departments':tuple(access.allowed_departments),
             'allowed_sites':tuple(access.allowed_sites),'max_security_level':access.max_security_level}
    policy=load_approved_scope(selection.rounding_scope,profile)
    state.checkpoint('bom_rollup')
    tree=graph_dependency[1] if graph_dependency else None
    if tree is None and policy:
        tree=retrieve_graph_v2(query.root_key,profile,product_scope=query.product_scope,site=query.site,
                              deadline=state.budget.deadline_monotonic,max_relations=query.max_relations,
                              max_generation_edges=query.max_generation_edges)
    state.checkpoint('bom_rollup')
    state.require_query_authority()
    cap = state.query_v2_capability
    if cap is not None and tree is not None and not cap.graph.permits_result(query, tree):
        raise PermissionError('BOM Graph source authority unavailable')
    if policy and not scope_is_current(policy,profile):
        policy=None
    order_scope=load_approved_scope(selection.quantity_scope,profile) if selection.quantity_scope else None
    if order_scope and not scope_is_current(order_scope,profile):
        order_scope=None
    quantity=(bom_order_quantity(order_scope,query,(tree or {}).get('manifest') or {}) if selection.quantity_scope
              else Decimal(selection.order_quantity))
    quantity_text=format(quantity.to_integral_value(),'f') if selection.quantity_scope and quantity is not None else selection.order_quantity
    state.checkpoint('bom_rollup')
    calculated=(bom_rollup(tree.get('manifest') or {},quantity,policy.rounding_rule,rounding_scope=policy,
                          order_scope=order_scope,quantity_target=query)
                if tree and policy and quantity is not None else MathResult('bom_rollup','blocked',reason='bom_evidence_unavailable'))
    records=[]
    if calculated.status=='complete':
        policy_records=_sources((policy, *((order_scope,) if order_scope else ())), profile)
        records=graph_source_records(tree,query,occurrences=True)+policy_records
        if not policy_records or any(structured_citation(record,page_has_vision=lambda *_:False) is None for record in records):
            calculated=MathResult('bom_rollup','blocked',reason='invalid_source_provenance')
            records=[]
    result=serialize_result(calculated)
    cards=build_citation_list(records,page_has_vision=lambda *_:False)
    state.budget.record('calculations',len(result['steps']),cumulative=True)
    examined=0 if graph_dependency else (tree or {}).get('examined_relations',0)
    state.budget.record('graph_edges',0 if graph_dependency else len((tree or {}).get('edges',())),cumulative=True)
    state.budget.record('graph_examined_relations',examined,cumulative=True)
    english=state.request.response_language.startswith('en')
    if result['status']=='complete':
        lines=_answer(result,cards,quantity_text,selection.rounding_scope,english,fixed_task_outputs(state),order_scope=order_scope)
    else:
        state.refuse('bom_evidence_unavailable')
        lines=['Insufficient complete reviewed BOM or rounding policy.' if english else 'Chưa đủ BOM đầy đủ đã duyệt hoặc quy tắc làm tròn để tính tổng.']
    quantity_metadata=({**_scope_manifest(order_scope),'quantity_binding':asdict(order_scope.quantity_binding)}
                       if order_scope is not None and result['status']=='complete' else None)
    metadata={**result,'order_quantity':quantity_text if result['status']=='complete' or not selection.quantity_scope else None,
              **({'quantity_scope':quantity_metadata} if selection.quantity_scope else {}),
              'accepted':False,'offline_only':cap is None,'read_consistency':'serializable',
              **({'contract':cap.contract,'activation_scope':cap.context} if cap else {}),
              'rounding_scope':_scope_manifest(policy) if result['status']=='complete' else None,
              **({'graph_reused_from':graph_dependency[0]} if graph_dependency else {})}
    diagnostics={'math_v2':metadata,'citation_docs':records,'retrieved_docs':records,
                 'generation_metrics':{'calculation_count':len(result['steps']),'calculation_result_status':result['status']},
                 'graph_examined_relations':examined,
                 'answer_outcome':'full_answer' if result['status']=='complete' else 'refused'}
    return state.prepared((iter(('\n'.join(lines),)),citation_ref_text(cards),(),(),diagnostics), observed_math_results=(result,))


def _answer(result,cards,quantity,rounding_scope,english,outputs=(),*,order_scope=None):
    label=(('BOM for source quantity: ' if english else 'BOM theo số lượng từ nguồn: ') if order_scope else
           ('BOM for requested quantity: ' if english else 'BOM theo số lượng người hỏi cung cấp: '))
    quantity_refs=' '.join('[SRC:'+c['source_id']+']' for c in cards if order_scope and c.get('scope_id')==order_scope.scope_id)
    lines=[label+quantity+(' '+quantity_refs if quantity_refs else '')]
    for step in result['steps']:
        ids=[c['source_id'] for c in cards if c['locator']['row_key'] in step['source_ids'] or c.get('scope_id') in step['source_ids'] or c.get('scope_id')==rounding_scope]
        lines.append(f"- {step['step_id']}: {step['display_value']} {step['unit']} "+' '.join(f'[SRC:{key}]' for key in ids))
        if 'formula' in outputs:
            lines.append(f"  {'Formula' if english else 'Công thức'}: {step['formula']} "+' '.join(f'[SRC:{key}]' for key in ids))
    if 'quantity_basis' in outputs:
        for occurrence in result['sources'][0]['occurrences']:
            ids=[c['source_id'] for c in cards if c['locator']['row_key']==occurrence['occurrence_id']]
            path=' → '.join(occurrence['quantity_path'])
            label='quantity path (per parent)' if english else 'đường số lượng (mỗi cụm cha)'
            lines.append(f"- {occurrence['occurrence_id']}: {label}: {path}; {occurrence['unit']} "+' '.join(f'[SRC:{key}]' for key in ids))
    if not result['steps']:
        reviewed=('Reviewed complete BOM: ' if english else 'BOM đầy đủ đã duyệt: ') if 'reviewed_empty_basis' in outputs else ''
        lines.append(reviewed+('No contained physical parts.' if english else 'Không có chi tiết vật lý trong cụm.')+
                     ' '+' '.join(f"[SRC:{c['source_id']}]" for c in cards))
    return lines
