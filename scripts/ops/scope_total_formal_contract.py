"""Bound one-scope formal evidence; passing never grants release authority."""
from decimal import Decimal
import hashlib
import json

from mech_chatbot.evaluation.adjudication import _bound_observation, _RUN_BINDINGS
from mech_chatbot.evaluation.department_readiness import _math_outcome_matches
from mech_chatbot.evaluation.metrics import nearest_rank
from mech_chatbot.evaluation.scope_total import evaluate_scope_total
from mech_chatbot.governance.artifact_references import load_json_reference
from mech_chatbot.governance.scope_total_activation import CONTRACT
from scripts.ops import grounded_math_pilot_gate as common
from scripts.ops.math_v2_pilot_contract import (
    _baseline_metrics_bound, _comparison_citations, _zero_execution_budget,
)

FORMAL_SCHEMA = 'grounded-math-scope-total-formal-gate-v1'
LIMITS = {'max_domain_steps_per_template': 1, 'required_exact_rate': 1,
          'max_latency_ratio': 1.25, 'max_cost_ratio': 1.5}


def _answer(actual, scope, citations):
    step = actual['steps'][0]
    answer = f"Tổng: {step['display_value']} {step['unit']} (trước làm tròn: {step['value']} {step['unit']}).\n"
    answer += f"Cộng các dòng đã duyệt: {scope.expected_row_count}. "
    answer += ' '.join(f"[SRC:{card['source_id']}]" for card in citations)
    return answer if scope.facts else answer + '\nPhạm vi rỗng đã duyệt: 0 dòng dữ liệu.'


def _bound_total(baseline, candidate, envelope, base):
    from mech_chatbot.db.repositories.structured_evidence import _fact, _manifest
    try:
        if any(baseline.get(key) != candidate.get(key) for key in ('case_id', 'case_reference', 'math_reference')):
            return None
        case_ref, reference_ref = candidate['case_reference'], candidate['math_reference']
        observed = _bound_observation(case_ref, candidate['observation_reference'], base)
        case = load_json_reference(case_ref, root=base)
        reference = load_json_reference(reference_ref, root=base)
        if (observed['case_id'] != candidate['case_id']
                or any(case[key] != envelope['git_sha' if key == 'source_commit' else key] for key in _RUN_BINDINGS)
                or case['dataset_sha256'] != envelope['dataset_hash']
                or hashlib.sha256(json.dumps({'actor': case['actor'], 'config_hash': case['config_hash']},
                    sort_keys=True, separators=(',', ':')).encode()).hexdigest() != envelope['evaluation_context_hash']
                or not isinstance(reference, dict) or reference.get('schema') != 'fixed-template-math-reference-v2'
                or reference.get('case_sha256') != case_ref['sha256']
                or any(reference.get(key) != case[key] for key in
                       (*_RUN_BINDINGS, 'run_id', 'case_id', 'department', 'actor', 'dataset_sha256'))
                or not isinstance(reference.get('records'), list) or len(reference['records']) != 1
                or not isinstance(observed.get('math_results'), list) or len(observed['math_results']) != 1
                or observed.get('query_branches') != [] or observed.get('query_branch_schema') is not None
                or type(observed.get('calculation_count')) is not int or observed['calculation_count'] not in {0, 1}
                or not _zero_execution_budget(observed.get('execution_budget'))
                or common._decimal(candidate['latency_ms']) != common._decimal(observed['latency_ms'])
                or common._decimal(candidate['estimated_cost']) != 0
                or common._decimal(observed.get('estimated_cost', 0)) != 0):
            return None
        source, actual = reference['records'][0], observed['math_results'][0]
        scopes = source.get('scopes')
        if (source.get('template') != 'sum_reviewed_scope' or actual.get('template') != 'sum_reviewed_scope'
                or not isinstance(scopes, list) or len(scopes) != 1
                or case.get('selection') != {'scope_total': {'scope_id': scopes[0]['scope_id']}}
                or type(actual.get('domain_step_count')) is not int
                or actual['domain_step_count'] != observed['calculation_count']
                or not _math_outcome_matches(observed.get('actual_outcome'), [actual], [])
                or observed.get('answer_outcome') != {'complete': 'full_answer', 'blocked': 'refused'}.get(actual.get('status'))):
            return None
        value = scopes[0]
        if (not isinstance(value['unit'], str) or not isinstance(value.get('currency', ''), str)
                or any(f.get('value') is not None and not isinstance(f['value'], str) for f in value['facts'])):
            return None
        scope = _manifest(json.dumps(value), tuple(_fact(fact) for fact in value['facts']))
        if not evaluate_scope_total(actual, scope=scope)['passed']:
            return None
        if actual['status'] == 'blocked':
            return 0 if (observed['citations'] == [] and observed['actual_outcome'] == 'insufficient_evidence'
                and observed['answer'] == 'Chưa đủ dữ liệu đã duyệt để tính tổng này.') else None
        return 1 if (_comparison_citations(observed, scopes)
            and observed['answer'] == _answer(actual, scope, observed['citations'])) else None
    except (KeyError, TypeError, ValueError, AttributeError, ArithmeticError):
        return None


def _pair_budget_maxima(before, after, base, commit, snapshot):
    if not (all(e.get('schema') == 'rag-labeled-eval-v4' and e.get('git_sha') == commit for e in (before, after))
            and isinstance(commit, str) and bool(commit.strip()) and before.get('snapshot_fingerprint') == snapshot
            and all(common._is_sha256(before.get(key)) and before[key] == after.get(key) for key in
                    ('dataset_hash', 'snapshot_fingerprint', 'evaluation_context_hash', 'config_hash', 'gate_hash', 'eval_hash'))):
        return None
    a, b = before.get('cases'), after.get('cases')
    if not (isinstance(a, list) and isinstance(b, list) and a and len(a) == len(b)
            and all(isinstance(c, dict) and isinstance(c.get('case_id'), str) and c['case_id'].strip() for c in a+b)
            and len({c['case_id'] for c in a}) == len(a)
            and [c['case_id'] for c in a] == [c['case_id'] for c in b]):
        return None
    if any(not _baseline_metrics_bound(left, right, base) for left, right in zip(a, b)):
        return None
    counts = [_bound_total(left, right, before, base) for left, right in zip(a, b)]
    if any(count is None for count in counts) or 1 not in counts:
        return None
    try:
        latencies = [[common._decimal(c['latency_ms']) for c in cases] for cases in (a, b)]
        costs = [sum((common._decimal(c['estimated_cost']) for c in cases), Decimal(0)) for cases in (a, b)]
    except (KeyError, TypeError, ValueError):
        return None
    if (nearest_rank(latencies[1], .95) > nearest_rank(latencies[0], .95)*Decimal('1.25')
            or costs[1] > costs[0]*Decimal('1.5')):
        return None
    return max(latencies[0]), max(common._decimal(c['estimated_cost']) for c in a)


def build_formal_gate(baseline_path, candidate_path, *, root):
    before, before_raw = common._load_json(baseline_path)
    after, after_raw = common._load_json(candidate_path)
    commit = before.get('git_sha')
    maxima = _pair_budget_maxima(before, after, root, commit, before.get('snapshot_fingerprint'))
    return {'schema': FORMAL_SCHEMA, 'contract': CONTRACT, 'source_commit': commit,
        'passed': maxima is not None, 'decision': 'pending_review' if maxima is not None else 'rejected',
        'accepted': False, 'production_eligible': False, 'limits': dict(LIMITS),
        'failure_reason': None if maxima is not None else 'invalid_or_failed_scope_total_pair',
        'inputs': {'baseline_eval_sha256': common._sha256(before_raw), 'candidate_eval_sha256': common._sha256(after_raw)}}


def formal_budget_maxima(sources, base, commit, snapshot):
    if not isinstance(sources, dict) or sources.get('source_commit') != commit:
        return None
    pairs = sources.get('pairs')
    if not isinstance(pairs, list) or len(pairs) != 3:
        return None
    maxima, identities = [], set()
    seen_baseline, seen_candidate = set(), set()
    seen_cases, seen_traces = set(), set()
    for pair in pairs:
        if not isinstance(pair, dict):
            return None
        loaded = [common._material(pair.get(key), base) for key in ('eval', 'candidate_eval', 'gate')]
        if not all(loaded):
            return None
        baseline, candidate, gate = loaded
        check = gate[0]
        identities.add(tuple(item[1] for item in loaded))
        if not (check.get('schema') == FORMAL_SCHEMA and check.get('passed') is True
                and check.get('contract') == CONTRACT and check.get('source_commit') == commit
                and check.get('limits') == LIMITS
                and check.get('inputs') == {'baseline_eval_sha256': baseline[1], 'candidate_eval_sha256': candidate[1]}):
            return None
        pair_maxima = _pair_budget_maxima(baseline[0], candidate[0], base, commit, snapshot)
        if pair_maxima is None:
            return None
        baseline_refs, candidate_refs = [
            {row['observation_reference']['sha256'] for row in envelope['cases']}
            for envelope in (baseline[0], candidate[0])]
        if seen_baseline.intersection(baseline_refs) or seen_candidate.intersection(candidate_refs):
            return None
        for left, right in zip(baseline[0]['cases'], candidate[0]['cases']):
            try:
                observations = [_bound_observation(row['case_reference'], row['observation_reference'], base)
                                for row in (left, right)]
            except (KeyError, TypeError, ValueError):
                return None
            identity = (observations[1]['run_id'], observations[1]['case_id'])
            if identity in seen_cases:
                return None
            seen_cases.add(identity)
            for observed in observations:
                trace = observed.get('trace_id')
                if not isinstance(trace, str) or not trace.strip() or len(trace) > 255 or trace in seen_traces:
                    return None
                seen_traces.add(trace)
        seen_baseline.update(baseline_refs)
        seen_candidate.update(candidate_refs)
        maxima.append(pair_maxima)
    return (max(v[0] for v in maxima), max(v[1] for v in maxima)) if len(identities) == 3 else None
