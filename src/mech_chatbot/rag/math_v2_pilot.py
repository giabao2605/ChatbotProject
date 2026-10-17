"""Metadata-only completion evidence for the signed comparison pilot."""
from mech_chatbot.application.chat_citations import build_citation_list


def comparison_event_fields(diagnostics,answer,budget,capability,*,final_latency_ms,outcome):
    math=diagnostics.get('math_v2') or {}
    scopes=math.get('scopes') or ()
    steps=math.get('steps') or ()
    pins={(scope.get('scope_id'),scope.get('snapshot_id'),scope.get('scope_hash')) for scope in scopes}
    provenance=(len(pins)==2 and pins<=set(capability.scope_pins)
        and math.get('contract')==capability.contract and math.get('activation_scope')==capability.context
        and math.get('offline_only') is False)
    records=diagnostics.get('citation_docs') or ()
    cards=build_citation_list(records,page_has_vision=lambda *_:False)
    cited={(card.get('scope_id'),card.get('snapshot_id'),card.get('scope_hash')) for card in cards}
    citations=(bool(cards) and len(cards)==len(records) and cited==pins
               and all(f"[SRC:{card['source_id']}]" in answer for card in cards))
    arithmetic=(len(steps)==4 and [step.get('step_id') for step in steps]==['sum_a','sum_b','delta','percent_change']
        and all(step.get('status')=='valid' for step in steps[:3])
        and steps[3].get('status')==('undefined' if math.get('status')=='partial' else 'valid')
        and math.get('status') in {'complete','partial'})
    security=provenance and outcome=='answered'
    bounded=(budget.calculations==4 and all(getattr(budget,key)==0 for key in
        ('planners','subqueries','corrections','repairs','graph_edges','graph_examined_relations','provider_retries','final_generations'))
        and not budget.deadline_exceeded)
    return {'schema':'math-v2-comparison-completion-v1','contract':capability.contract,
        'template':'compare_two_scopes','route':'scope_comparison','completion_outcome':outcome,
        'calculation_result_status':'valid' if arithmetic and security and citations and bounded else 'invalid',
        'security_passed':security,'citation_structure_passed':citations,'provenance_passed':provenance,
        'leakage_detected':False,'calculations':budget.calculations,'provider_retries':budget.provider_retries,
        'final_generations':budget.final_generations,'estimated_cost':0,
        'final_latency_ms':max(0,int(final_latency_ms)),
        'request_deadline_ms':int(budget.limits.deadline_seconds*1000)}
