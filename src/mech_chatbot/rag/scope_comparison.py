"""Fixed Math v2 comparison over selected scopes in the normal event lifecycle."""
from dataclasses import asdict
from decimal import Decimal
import hashlib
import json
from mech_chatbot.db.evidence_snapshot import evidence_read_snapshot

from mech_chatbot.application.chat_citations import build_citation_list, citation_ref_text
from mech_chatbot.db.repositories.structured_evidence import load_approved_scope
from mech_chatbot.db.repositories.source_page_images import load_source_page_hash
from mech_chatbot.domain.structured_evidence import reviewed_scope_fingerprint, source_bindings
from mech_chatbot.rag.grounded_math_v2 import MathResult, compare_two_scopes, serialize_result
from mech_chatbot.services.rag_evidence_service import scope_is_current


def _sources(scopes):
    records = []
    for scope in scopes:
        scope_hash = reviewed_scope_fingerprint(scope)
        if not scope.facts:
            for loc, digest in source_bindings(scope):
                records.append({'doc_id':int(loc.doc_id), 'version_no':int(loc.version),
                                'locator':asdict(loc), 'snapshot_id':scope.snapshot_id, 'source_hash':digest,
                                'scope_id':scope.scope_id, 'scope_hash':scope_hash, 'fact_count':0,
                                'image_hash':load_source_page_hash(loc.doc_id, loc.version, scope.snapshot_id, digest, loc.page) if loc.page else None})
        groups = {}
        for fact in scope.facts:
            loc = fact.locator
            row = (loc.row_key, loc.cell) if not (loc.page or loc.sheet) else ()
            groups.setdefault((loc.doc_id, loc.version, loc.page, loc.sheet, loc.table_index, row), []).append(fact)
        for facts in groups.values():
            first = facts[0]
            locator = asdict(first.locator)
            if len(facts) > 1:
                locator = {**locator, 'cell': None, 'row_key': '', 'bbox': None}
            records.append({'doc_id': int(first.locator.doc_id), 'version_no': int(first.locator.version),
                            'locator': locator, 'snapshot_id': scope.snapshot_id, 'source_hash': first.source_hash,
                            'scope_id': scope.scope_id, 'scope_hash':scope_hash, 'fact_count': len(facts),
                            'image_hash':load_source_page_hash(first.locator.doc_id,first.locator.version,scope.snapshot_id,
                                                             first.source_hash,first.locator.page) if first.locator.page else None})
    return records


def _scope_manifest(scope):
    return {'scope_id': scope.scope_id, 'snapshot_id': scope.snapshot_id, 'definition': scope.definition,
            'scope_hash':reviewed_scope_fingerprint(scope),
            'period': scope.period, 'unit': scope.unit, 'currency': scope.currency,
            'row_count': len(scope.facts), 'source_revisions': scope.source_revisions,
            'row_set_sha256': hashlib.sha256(json.dumps(sorted(scope.expected_row_ids)).encode('utf-8')).hexdigest()}


def _answer(result, cards, language, outputs=()):
    english = str(language).lower().startswith('en')
    labels = ('Total A', 'Total B', 'Difference B − A', 'Change') if english else ('Tổng A', 'Tổng B', 'Chênh lệch B − A', 'Tỷ lệ thay đổi')
    lines = [('Comparison of the selected scopes' if english else 'So sánh hai phạm vi đã chọn')]
    requested = set(outputs).intersection(('sum_a','sum_b','delta','percent_change'))
    for label, step in zip(labels, result['steps']):
        if requested and step['step_id'] not in requested:
            continue
        value = step['display_value'] if step['value'] is not None else ('Undefined (total A is zero)' if english else 'Không xác định (tổng A bằng 0)')
        sources = [c['source_id'] for c in cards if c['scope_id'] in step['source_ids']]
        line = f"- {label}: {value} {step['unit']}"
        if step['value'] is not None and ('pre_round_value' in outputs or Decimal(step['value']) != Decimal(step['display_value'])):
            before = 'value before rounding' if english else 'giá trị trước làm tròn'
            line += f" ({before}: {step['value']} {step['unit']})"
        if step['step_id'] in {'sum_a', 'sum_b'} and any(
                c['scope_id'] in step['source_ids'] and type(c.get('fact_count')) is int and c['fact_count'] == 0 for c in cards):
            empty = 'reviewed empty scope: 0 data rows' if english else 'phạm vi rỗng đã duyệt: 0 dòng dữ liệu'
            line += f" ({empty})"
        if 'formula' in outputs:
            line += f" ({'formula' if english else 'công thức'}: {step['formula']})"
        lines.append(line+' '+' '.join(f'[SRC:{source}]' for source in sources))
    return '\n'.join(lines)


def execute_scope_comparison(state):
    state.checkpoint('scope_comparison')
    if state.invocation.mode not in {'evaluation', 'test'} and state.math_v2_capability is None:
        raise ValueError('Math v2 comparison requires evaluation context')
    access = state.request.access
    if state.math_v2_capability is None and not {role.lower() for role in access.roles}.intersection({'reviewer', 'knowledge_approver', 'admin'}):
        raise PermissionError('Evaluation reviewer required')
    from mech_chatbot.rag.query_decomposition import fixed_task_guard
    terminal = fixed_task_guard(state)
    if terminal is not None:
        return terminal
    with evidence_read_snapshot(state.checkpoint,state.budget.deadline_monotonic):
        return _read_comparison(state)


def _read_comparison(state):
    access=state.request.access
    profile = {'roles': tuple(access.roles), 'allowed_departments': tuple(access.allowed_departments),
               'allowed_sites': tuple(access.allowed_sites), 'max_security_level': access.max_security_level}
    scopes = tuple(load_approved_scope(scope, profile) for scope in state.request.scope_comparison)
    state.checkpoint('scope_comparison')
    current=all(scopes) and all(scope_is_current(scope,profile) for scope in scopes)
    cap = state.math_v2_capability
    if cap is not None:
        fresh = state.math_v2_authorizer()
        if fresh != cap or not cap.permits_scopes(scopes):
            raise PermissionError('Math v2 comparison source authority unavailable')
    state.checkpoint('scope_comparison')
    result = serialize_result(compare_two_scopes(*scopes) if current else
                              MathResult('compare_two_scopes', 'blocked', reason='invalid_scope_evidence'))
    usable = result['status'] in {'complete', 'partial'}
    records = _sources(scopes) if usable else []
    cards = build_citation_list(records, page_has_vision=lambda *_: False)
    if usable and len(cards) != len(records):
        usable, records = False, []
        cards = []
        result = serialize_result(MathResult('compare_two_scopes', 'blocked', reason='invalid_source_provenance'))
    state.budget.record('calculations', len(result['steps']),cumulative=True)
    metadata = {'schema': 'grounded-calculation-v2', 'template': 'compare_two_scopes',
                'status': result['status'], 'steps': result['steps'], 'domain_step_count': len(result['steps']),
                'rounding_rule': result.get('rounding_rule'), 'accepted': False, 'offline_only': cap is None,'read_consistency':'serializable',
                **({'activation_scope':cap.context, 'contract':cap.contract} if cap else {}),
                'scopes': tuple(_scope_manifest(scope) for scope in scopes) if usable else ()}
    if usable:
        from mech_chatbot.rag.query_decomposition import fixed_task_outputs
        answer = _answer(result, cards, state.request.response_language, fixed_task_outputs(state))
    else:
        state.refuse('scope_unavailable')
        answer = 'Insufficient reviewed data for this comparison.' if state.request.response_language.startswith('en') else 'Chưa đủ dữ liệu đã duyệt để thực hiện phép so sánh này.'
    diagnostics = {'math_v2': metadata, 'retrieved_docs': records, 'citation_docs': records,
                   'generation_metrics': {'calculation_count': len(result['steps']), 'calculation_result_status': result['status']},
                   'answer_outcome': ('partial_answer' if result['status'] == 'partial' else 'full_answer') if usable else 'refused'}
    state.checkpoint('scope_comparison')
    return state.prepared((iter((answer,)), citation_ref_text(cards), (), (), diagnostics), observed_math_results=(result,))
