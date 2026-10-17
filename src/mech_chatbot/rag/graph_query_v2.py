"""Evaluation Graph v2 in the shared chat event/deadline lifecycle."""
from dataclasses import replace
from functools import cache
from mech_chatbot.domain.graph_query import graph_fingerprint,graph_query_context

from mech_chatbot.application.chat_citations import build_citation_list,citation_ref_text,structured_citation
from mech_chatbot.db.repositories.graph_v2 import retrieve_graph_v2
from mech_chatbot.db.repositories import graph_v2
from mech_chatbot.db.evidence_snapshot import evidence_read_snapshot
from mech_chatbot.db.repositories.source_page_images import load_source_page_hash
from mech_chatbot.rag.query_decomposition import fixed_task_guard


def graph_source_records(value,query,*,occurrences=False):
    records=[]
    page_hash=cache(load_source_page_hash)
    digest=graph_fingerprint(value)
    edges=(value.get('manifest') or {}).get('occurrences',()) if occurrences else value.get('edges',())
    if not edges and value['status']=='complete':
        edges=tuple({'occurrence_id':'assembly:'+str(source['doc_id']),'sources':(source,)}
                    for source in (value.get('manifest') or {}).get('assembly_sources',()))
    for edge in edges:
        for source in edge['sources']:
            snapshot=source.get('source_snapshot_id') or source.get('snapshot_id')
            records.append({'doc_id':int(source['doc_id']),'version_no':int(source['version']),
                'locator':{'doc_id':str(source['doc_id']),'version':str(source['version']),'page':source['page'],
                           'row_key':str(edge['occurrence_id'])},
                'snapshot_id':snapshot,
                'image_hash':page_hash(source['doc_id'],source['version'],snapshot,source['source_hash'],source['page']),
                'source_hash':source['source_hash'],'graph_context':graph_query_context(query),'graph_hash':digest})
    return records


def execute_graph_query(state):
    state.checkpoint('graph_v2')
    if state.invocation.mode not in {'evaluation','test'}:
        raise ValueError('Graph v2 requires evaluation context')
    access=state.request.access
    if not {role.lower() for role in access.roles}.intersection({'reviewer','knowledge_approver','admin'}):
        raise PermissionError('Evaluation reviewer required')
    terminal=fixed_task_guard(state)
    if terminal is not None:
        return terminal
    with evidence_read_snapshot(state.checkpoint,state.budget.deadline_monotonic,db_engine=graph_v2.engine):
        return _read_graph(state)


def _edge_line(edge, cards, english):
    ids = [card['source_id'] for card in cards if card['locator']['row_key'] == str(edge['occurrence_id'])]
    line = f"- {edge['source_key']} → {edge['target_key']}"
    if edge.get('quantity_path'):
        basis = 'per parent' if english else 'mỗi cụm cha'
        path_label = 'quantity path' if english else 'đường số lượng'
        path = ' → '.join(edge['quantity_path'])
        line += f" ({edge['occurrence_id']}): {edge['quantity']} {edge['unit']} {basis}; {path_label}: {path}"
    return line + ' ' + ' '.join(f'[SRC:{key}]' for key in ids)


def _read_graph(state):
    access=state.request.access
    query=bounded_graph_query(state,state.request.graph_query)
    profile={'roles':tuple(access.roles),'allowed_departments':tuple(access.allowed_departments),
             'allowed_sites':tuple(access.allowed_sites),'max_security_level':access.max_security_level}
    value=retrieve_graph_v2(query.root_key,profile,product_scope=query.product_scope,site=query.site,
                            direction=query.direction,deadline=state.budget.deadline_monotonic,
                            max_relations=query.max_relations,max_generation_edges=query.max_generation_edges)
    state.checkpoint('graph_v2')
    records=graph_source_records(value,query)
    if ('snapshot_mismatch' in value.get('reasons',())
            or any(structured_citation(record,page_has_vision=lambda *_:False) is None for record in records)):
        value={'contract_version':'graph-v2','status':'unknown','reasons':('invalid_source_provenance',),
               'edges':(),'manifest':None,'examined_relations':value.get('examined_relations',0)}
        records=[]
    cards=build_citation_list(records,page_has_vision=lambda *_:False)
    state.budget.record('graph_edges',len(value.get('edges',())),cumulative=True)
    state.budget.record('graph_examined_relations',value.get('examined_relations',0),cumulative=True)
    complete=value['status']=='complete' and value.get('examined_relations',0)==len(value.get('edges',()))
    english=state.request.response_language.startswith('en')
    lines=['Reviewed assembly relations:' if english else 'Quan hệ cụm–chi tiết đã xác nhận:']
    for edge in value.get('edges',()):
        lines.append(_edge_line(edge,cards,english))
    if not complete:
        if value['status']=='complete':
            shown, total = len(value.get('edges',())), value.get('examined_relations',0)
            lines.append(f'Displaying {shown}/{total} relations; the source coverage is confirmed complete.' if english else
                         f'Đang hiển thị {shown}/{total} quan hệ; phạm vi nguồn đã xác nhận đầy đủ.')
        else:
            lines.append('Coverage is not confirmed complete.' if english else 'Chưa xác nhận được đầy đủ phạm vi quan hệ.')
    elif not value.get('edges') and cards:
        lines.append(('The reviewed assembly has no contained-part relations.' if english else 'Cụm đã duyệt không có quan hệ chứa chi tiết.')+
                     ' '+' '.join(f"[SRC:{card['source_id']}]" for card in cards))
    if not cards:
        state.refuse('graph_evidence_unavailable')
        lines=['Insufficient reviewed relations.' if english else 'Chưa đủ quan hệ có chứng cứ đã duyệt để trả lời.']
    if cards and complete:
        lines.append('The source coverage is confirmed complete.' if english else 'Phạm vi nguồn đã xác nhận đầy đủ.')
    elif not cards:
        lines.append('Source coverage is unknown.' if english else 'Chưa xác định được độ đầy đủ của nguồn.')
    unavailable_previews = [card['source_id'] for card in cards if not card.get('image_hash')]
    if unavailable_previews:
        notice = 'Verified source preview is unavailable.' if english else 'Chưa có ảnh xem trước nguồn đã xác minh.'
        lines.append(notice+' '+' '.join(f'[SRC:{key}]' for key in unavailable_previews))
    metadata={**value,'source_status':value['status'],'status':value['status'] if complete else 'partial' if cards else 'unknown',
              'graph_context':graph_query_context(query),'graph_hash':graph_fingerprint(value),'accepted':False,'offline_only':True,
              'read_consistency':'serializable'}
    diagnostics={'graph_v2':metadata,'citation_docs':records,'retrieved_docs':records,
                 'answer_outcome':'full_answer' if cards and complete else 'partial_answer' if cards else 'refused',
                 'graph_examined_relations':value.get('examined_relations',0)}
    return state.prepared((iter(('\n'.join(lines),)),citation_ref_text(cards),(),(),diagnostics))


def bounded_graph_query(state,query):
    return replace(query,max_relations=min(query.max_relations,state.budget.limits.graph_examined_relations-state.budget.graph_examined_relations),
                   max_generation_edges=min(query.max_generation_edges,state.budget.limits.graph_edges-state.budget.graph_edges))
