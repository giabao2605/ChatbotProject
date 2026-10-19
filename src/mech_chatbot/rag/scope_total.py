"""One fixed total over a selected complete scope with separate source authority."""
from mech_chatbot.application.chat_citations import build_citation_list, citation_ref_text
from mech_chatbot.db.evidence_snapshot import evidence_read_snapshot
from mech_chatbot.db.repositories.structured_evidence import load_approved_scope
from mech_chatbot.rag.grounded_math_v2 import MathResult, SCOPE_TOTAL_CONTRACT, serialize_result, sum_reviewed_scope
from mech_chatbot.rag.scope_comparison import _sources, _scope_manifest
from mech_chatbot.services.rag_evidence_service import scope_is_current


def execute_scope_total(state):
    state.checkpoint('scope_total')
    cap = state.math_v2_capability
    if state.invocation.mode not in {'evaluation', 'test'} and cap is None:
        raise ValueError('Scope total requires evaluation context')
    from mech_chatbot.rag.query_decomposition import SCOPE_TOTAL_TASK_ROUTING, fixed_task_guard
    if state.invocation.task_routing_contract not in (None, SCOPE_TOTAL_TASK_ROUTING):
        raise ValueError('Scope total does not use a planner')
    access = state.request.access
    if cap is None and not {role.lower() for role in access.roles}.intersection({'reviewer', 'knowledge_approver', 'admin'}):
        raise PermissionError('Evaluation reviewer required')
    if state.invocation.task_routing_contract == SCOPE_TOTAL_TASK_ROUTING:
        terminal = fixed_task_guard(state)
        if terminal is not None:
            return terminal
    profile = {'roles': tuple(access.roles), 'allowed_departments': tuple(access.allowed_departments),
               'allowed_sites': tuple(access.allowed_sites), 'max_security_level': access.max_security_level}
    with evidence_read_snapshot(state.checkpoint, state.budget.deadline_monotonic):
        scope = load_approved_scope(state.request.scope_total, profile)
        current = scope is not None and scope_is_current(scope, profile)
        if cap is not None and (state.math_v2_authorizer() != cap or not current or not cap.permits_scopes((scope,))):
            raise PermissionError('Scope total source authority unavailable')
        result = serialize_result(sum_reviewed_scope(scope) if current else
            MathResult('sum_reviewed_scope', 'blocked', reason='invalid_scope_evidence'))
        usable = result['status'] == 'complete'
        records = _sources((scope,), profile) if usable else []
        cards = build_citation_list(records, page_has_vision=lambda *_: False)
        if usable and len(cards) != len(records):
            usable, records, cards = False, [], []
            result = serialize_result(MathResult('sum_reviewed_scope', 'blocked', reason='invalid_source_provenance'))
        state.checkpoint('scope_total')
        metadata = {key: result[key] for key in ('schema', 'template', 'status', 'steps', 'domain_step_count', 'rounding_rule')}
        metadata = {**metadata, 'contract': cap.contract if cap else SCOPE_TOTAL_CONTRACT,
                    'accepted': False, 'offline_only': cap is None,
                    **({'activation_scope': cap.context} if cap else {}),
                    'read_consistency': 'serializable', 'scopes': (_scope_manifest(scope),) if usable else ()}
    state.budget.record('calculations', len(result['steps']), cumulative=True)
    english = state.request.response_language.startswith('en')
    if usable:
        step = result['steps'][0]
        label, raw, formula = ('Total', 'before rounding', 'Sum of reviewed rows') if english else (
            'Tổng', 'trước làm tròn', 'Cộng các dòng đã duyệt')
        answer = f"{label}: {step['display_value']} {step['unit']} ({raw}: {step['value']} {step['unit']}).\n"
        answer += f"{formula}: {scope.expected_row_count}. " + ' '.join(f"[SRC:{card['source_id']}]" for card in cards)
        if not scope.facts:
            answer += '\n' + ('Reviewed empty scope: 0 data rows.' if english else 'Phạm vi rỗng đã duyệt: 0 dòng dữ liệu.')
    else:
        state.refuse('scope_unavailable')
        answer = 'Insufficient reviewed data for this total.' if english else 'Chưa đủ dữ liệu đã duyệt để tính tổng này.'
    diagnostics = {'math_v2': metadata, 'retrieved_docs': records, 'citation_docs': records,
                   'generation_metrics': {'calculation_count': len(result['steps']), 'calculation_result_status': result['status']},
                   'answer_outcome': 'full_answer' if usable else 'refused'}
    return state.prepared((iter((answer,)), citation_ref_text(cards), (), (), diagnostics), observed_math_results=(result,))


def total_event_fields(diagnostics, answer, budget, capability, *, final_latency_ms, outcome):
    """Keep the one-step completion receipt separate from comparison evidence."""
    math = diagnostics.get('math_v2') or {}
    scopes, steps = math.get('scopes') or (), math.get('steps') or ()
    pins = {(s.get('scope_id'), s.get('snapshot_id'), s.get('scope_hash')) for s in scopes}
    provenance = (len(pins) == 1 and pins <= set(capability.scope_pins)
        and math.get('contract') == capability.contract and math.get('activation_scope') == capability.context
        and math.get('offline_only') is False)
    records = diagnostics.get('citation_docs') or ()
    cards = build_citation_list(records, page_has_vision=lambda *_: False)
    cited = {(c.get('scope_id'), c.get('snapshot_id'), c.get('scope_hash')) for c in cards}
    citations = (bool(cards) and len(cards) == len(records) and cited == pins
        and all(f"[SRC:{c['source_id']}]" in answer for c in cards))
    arithmetic = (math.get('status') == 'complete' and len(steps) == 1
        and steps[0].get('step_id') == 'sum' and steps[0].get('status') == 'valid')
    bounded = (budget.calculations == 1 and all(getattr(budget, key) == 0 for key in (
        'planners', 'subqueries', 'corrections', 'repairs', 'graph_edges', 'graph_examined_relations',
        'provider_retries', 'final_generations')) and not budget.deadline_exceeded)
    security = provenance and outcome == 'answered'
    return {'schema':'math-scope-total-completion-v1', 'contract':capability.contract,
        'template':'sum_reviewed_scope', 'route':'scope_total', 'completion_outcome':outcome,
        'calculation_result_status':'valid' if arithmetic and security and citations and bounded else 'invalid',
        'security_passed':security, 'citation_structure_passed':citations, 'provenance_passed':provenance,
        'leakage_detected':False, 'calculations':budget.calculations, 'provider_retries':budget.provider_retries,
        'final_generations':budget.final_generations, 'estimated_cost':0,
        'final_latency_ms':max(0, int(final_latency_ms)),
        'request_deadline_ms':int(budget.limits.deadline_seconds*1000)}
