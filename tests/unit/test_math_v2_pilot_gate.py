"""Synthetic v2 evidence verifies gate wiring, never pilot acceptance."""
import json
import hashlib
from copy import deepcopy
from datetime import datetime, timezone

import pytest

from scripts.ops import grounded_math_pilot_gate as gate
from mech_chatbot.governance.math_v2_activation import CONTRACT
from .test_grounded_math_pilot_gate import _inputs, _runtime_identity, _sha256, _write_json
from .test_math_v2_activation import grant, reference
from .test_rag_scope_comparison import approved_pair, store
from .test_fixed_math_runner_v2 import ACTOR, BINDING, frozen_case, current_profile


@pytest.fixture
def comparison_inputs(grant, store):
    from dataclasses import asdict
    from mech_chatbot.config.repository_runtime import bind_repository_runtime
    from mech_chatbot.db.repositories.structured_evidence import load_approved_scope
    from .test_structured_evidence_repository import PROFILE
    from scripts.eval.run_fixed_math_v2 import run_fixed_case
    root, _, authorization, decision, issue, _ = grant
    commit = 'a' * 40
    authorization = {**authorization, 'source_commit': commit}
    decision = {**decision, 'source_commit': commit,
        'evidence': [reference(root, 'gate.json', authorization)]}
    bundle, digest = issue(source_commit=commit, decision=reference(root, 'decision.json', decision))
    approved_pair(store)
    scopes = tuple(load_approved_scope(key, PROFILE, db_engine=store) for key in ('a', 'b'))
    records = [{'template': 'compare_two_scopes',
        'scopes': json.loads(json.dumps([asdict(scope) for scope in scopes], default=str))}]
    binding = {**BINDING, 'snapshot_fingerprint': 'c' * 64}
    inputs = _inputs(root)
    window = inputs['window']
    restore = json.loads(inputs['restore_path'].read_text())
    restore.update(git_sha=commit, snapshot_fingerprint='c' * 64)
    _write_json(inputs['restore_path'], restore)
    for arm, runtime in window['expected_runtime'].items():
        runtime.update(git_sha=commit, snapshot_fingerprint='c' * 64,
            restore_evidence_sha256=_sha256(inputs['restore_path']))
        if arm == 'pilot':
            runtime['activation_bundle_sha256'] = digest
        runtime['runtime_identity_sha256'] = _runtime_identity(runtime)
    pairs = []
    for index in range(3):
        case_ref = frozen_case(root, **binding, run_id=f'formal-{index}',
            case_id=f'comparison-{index}', dataset_sha256=str(index) * 64)
        (root / case_ref['path']).rename(root / f'case-{index}.json')
        case_ref = {**case_ref, 'path': f'case-{index}.json'}
        math_ref = reference(root, f'math-reference-{index}.json', {
            'schema': 'fixed-template-math-reference-v2', **binding,
            'case_sha256': case_ref['sha256'], 'case_id': f'comparison-{index}',
            'run_id': f'formal-{index}', 'department': 'ENG', 'actor': ACTOR,
            'dataset_sha256': str(index) * 64, 'records': records})
        before = {'schema': 'rag-labeled-eval-v4', 'git_sha': commit,
            **{key: binding[key] for key in ('config_hash', 'gate_hash', 'eval_hash')},
            'dataset_hash': str(index) * 64, 'snapshot_fingerprint': 'c' * 64,
            'evaluation_context_hash': hashlib.sha256(json.dumps({'actor': ACTOR, 'config_hash': binding['config_hash']}, sort_keys=True, separators=(',', ':')).encode()).hexdigest(),
            'cases': [{'case_id': f'comparison-{index}', 'case_reference': case_ref,
                'math_reference': math_ref, 'latency_ms': 30000, 'estimated_cost': 0}]}
        with bind_repository_runtime(db_engine=store):
            observation = run_fixed_case(case_ref, root=root, run_binding=binding, profile_loader=current_profile)
        observation_ref = reference(root, f'observed-{index}.json', observation)
        after = {**before, 'cases': [{**before['cases'][0], 'observation_reference': observation_ref,
            'latency_ms': observation['latency_ms']}]}
        baseline = reference(root, f'baseline-{index}.json', before)
        candidate = reference(root, f'candidate-{index}.json', after)
        formal = {'schema': 'grounded-math-comparison-formal-gate-v2', 'passed': True,
            'contract': CONTRACT, 'source_commit': commit,
            'limits': {'max_domain_steps_per_template': 4, 'required_exact_rate': 1,
                'max_latency_ratio': 1.25, 'max_cost_ratio': 1.5},
            'inputs': {'baseline_eval_sha256': baseline['sha256'], 'candidate_eval_sha256': candidate['sha256']}}
        pairs.append({'eval': baseline, 'candidate_eval': candidate,
            'gate': reference(root, f'formal-{index}.json', formal)})
    window.update(schema='math-comparison-pilot-window-v2', contract=CONTRACT, template='compare_two_scopes')
    window['formal_budget_sources'].update(source_commit=commit, pairs=pairs)
    window['pilot_budget'].update(max_calculations=4, max_final_generations=0,
        max_final_latency_ms=37500, max_estimated_cost=0)
    _write_json(inputs['window_path'], window)
    inputs['state'].update(window_sha256=_sha256(inputs['window_path']),
        expected_runtime=deepcopy(window['expected_runtime']),
        activation_bundle={'path': 'bundle.json', 'sha256': digest},
        restore_receipt={'path': str(inputs['restore_path']), 'sha256': _sha256(inputs['restore_path'])})
    _write_json(inputs['state_path'], inputs['state'])
    for arm, runtime in window['expected_runtime'].items():
        inputs['health'][arm] = {**runtime, 'status': 'ok', 'rag_loaded': True}
    _write_json(inputs['health_path'], inputs['health'])
    event = {'schema': 'math-v2-comparison-completion-v1', 'event': 'math_v2_comparison_completed',
        'contract': CONTRACT, 'template': 'compare_two_scopes', 'route': 'scope_comparison',
        'execution_context': 'production', 'ts': inputs['health']['checked_at'],
        'runtime_identity_sha256': window['expected_runtime']['pilot']['runtime_identity_sha256'],
        'completion_outcome': 'answered', 'calculation_result_status': 'valid',
        'security_passed': True, 'citation_structure_passed': True, 'provenance_passed': True,
        'leakage_detected': False, 'calculations': 4, 'provider_retries': 0, 'final_generations': 0,
        'estimated_cost': 0, 'final_latency_ms': 1000, 'request_deadline_ms': 120000}
    rows = [{**event, 'trace_id': f'synthetic-{index}'} for index in range(100)]
    inputs['trace_path'].write_text(''.join(json.dumps(row) + '\n' for row in rows))
    return inputs


def evaluate(inputs):
    return gate.build_artifact(inputs['window_path'], inputs['state_path'], inputs['health_path'],
        inputs['trace_path'], inputs['provider_smoke_path'], now=datetime.now(timezone.utc))


def test_comparison_uses_separate_gate_with_signed_bundle(comparison_inputs):
    result = evaluate(comparison_inputs)
    assert result['schema'] == 'grounded-math-comparison-production-pilot-gate-v2'
    assert result['passed'] and result['decision'] == 'pending_review'
    assert result['eligible_trace_count'] == 100
    assert 'synthetic-' not in json.dumps(result)


def test_fixed_comparison_needs_no_provider_smoke(comparison_inputs):
    inputs=comparison_inputs
    inputs['window'].pop('provider_smoke')
    _write_json(inputs['window_path'],inputs['window'])
    inputs['state']['window_sha256']=_sha256(inputs['window_path'])
    _write_json(inputs['state_path'],inputs['state'])
    inputs['provider_smoke_path']=None
    result=evaluate(inputs)
    assert result['passed']
    assert result['provider_smoke_reason']=='not_applicable_no_provider_calls'
    assert result['provider_smoke_sha256'] is None


def test_formal_gate_cli_produces_hash_bound_evidence_consumed_by_pilot(comparison_inputs):
    from scripts.ops import math_v2_pilot_contract as contract
    inputs=comparison_inputs
    pair=inputs['window']['formal_budget_sources']['pairs'][0]
    root=inputs['window_path'].parent
    output=root / 'produced-formal.json'
    assert contract.main(['--baseline',str(root / pair['eval']['path']),
        '--candidate',str(root / pair['candidate_eval']['path']),
        '--root',str(root),'--output',str(output)])==0
    result=json.loads(output.read_text())
    assert result['passed'] and result['decision']=='pending_review'
    assert result['accepted'] is False and result['production_eligible'] is False
    assert result['inputs']=={'baseline_eval_sha256':pair['eval']['sha256'],
        'candidate_eval_sha256':pair['candidate_eval']['sha256']}
    pair['gate']={'path':str(output),'sha256':_sha256(output)}
    _write_json(inputs['window_path'],inputs['window'])
    inputs['state']['window_sha256']=_sha256(inputs['window_path'])
    _write_json(inputs['state_path'],inputs['state'])
    assert evaluate(inputs)['passed']


def test_shared_pass_report_without_bound_case_observations_cannot_pass(tmp_path):
    from scripts.ops import math_v2_pilot_contract as contract
    report = {'schema': 'grounded-calculation-evaluation-v2', 'template': 'compare_two_scopes',
        'passed': True, 'domain_step_count': 4, 'checks': {key: True for key in
            ('reference_available', 'schema', 'template', 'status', 'reason', 'domain_step_count', 'steps', 'sources', 'rounding_rule')}}
    report_ref = reference(tmp_path, 'borrowed-report.json', report)
    baseline = {'schema': 'rag-labeled-eval-v4', 'git_sha': 'a' * 40,
        'dataset_hash': 'b' * 64, 'snapshot_fingerprint': 'c' * 64, 'evaluation_context_hash': 'd' * 64,
        'cases': [{'case_id': key, 'latency_ms': 1000, 'estimated_cost': 0} for key in ('one', 'unrelated-two')]}
    candidate = {**baseline, 'cases': [{**row, 'math_evaluation': report_ref} for row in baseline['cases']]}
    reference(tmp_path, 'before.json', baseline)
    reference(tmp_path, 'after.json', candidate)
    result = contract.build_formal_gate(tmp_path / 'before.json', tmp_path / 'after.json', root=tmp_path)
    assert result['passed'] is False


@pytest.mark.parametrize('kind', ['zero_denominator', 'reference_blocked'])
def test_formal_pair_preserves_reference_partial_and_blocked_outcomes(comparison_inputs, store, kind):
    from dataclasses import asdict
    from mech_chatbot.config.repository_runtime import bind_repository_runtime
    from mech_chatbot.db.repositories.structured_evidence import load_approved_scope, review_numeric_fact, review_scope_completeness
    from .test_structured_evidence_repository import PROFILE
    from scripts.eval.run_fixed_math_v2 import run_fixed_case
    from scripts.ops import math_v2_pilot_contract as contract
    root = comparison_inputs['window_path'].parent
    pair = comparison_inputs['window']['formal_budget_sources']['pairs'][0]
    before = json.loads((root / pair['eval']['path']).read_text())
    after = json.loads((root / pair['candidate_eval']['path']).read_text())
    scope = load_approved_scope('a' if kind == 'zero_denominator' else 'b', PROFILE, db_engine=store)
    if kind == 'zero_denominator':
        review_numeric_fact(scope.facts[0].fact_id, PROFILE, action='approve', value_text='0',
            note='Synthetic zero-denominator source', db_engine=store)
    review_scope_completeness(scope.scope_id, PROFILE, expected_row_ids=scope.expected_row_ids,
        rounding_rule=scope.rounding_rule, definition='Other definition' if kind == 'reference_blocked' else scope.definition,
        note='Synthetic reviewed reference', db_engine=store)
    scopes = tuple(load_approved_scope(key, PROFILE, db_engine=store) for key in ('a', 'b'))
    binding = {key: before['git_sha' if key == 'source_commit' else key]
        for key in ('source_commit', 'snapshot_fingerprint', 'config_hash', 'gate_hash', 'eval_hash')}
    case_ref = frozen_case(root, **binding, case_id=kind, dataset_sha256=before['dataset_hash'])
    math_ref = reference(root, 'additional-math-reference.json', {
        'schema': 'fixed-template-math-reference-v2', **binding, 'case_sha256': case_ref['sha256'],
        'case_id': kind, 'run_id': 'fixture-run', 'department': 'ENG', 'actor': ACTOR,
        'dataset_sha256': before['dataset_hash'], 'records': [{'template': 'compare_two_scopes',
            'scopes': json.loads(json.dumps([asdict(value) for value in scopes], default=str))}]})
    row = {'case_id': kind, 'case_reference': case_ref, 'math_reference': math_ref,
        'latency_ms': 30000, 'estimated_cost': 0}
    before['cases'].append(row)
    reference(root, pair['eval']['path'], before)
    with bind_repository_runtime(db_engine=store):
        observed = run_fixed_case(case_ref, root=root, run_binding=binding, profile_loader=current_profile)
    after['cases'].append({**row, 'latency_ms': observed['latency_ms'],
        'observation_reference': reference(root, 'additional-observation.json', observed)})
    reference(root, pair['candidate_eval']['path'], after)
    if kind == 'zero_denominator':
        assert observed['actual_outcome'] == 'partial_answer' and observed['calculation_count'] == 4
        assert observed['math_results'][0]['steps'][3]['value'] is None
    else:
        assert observed['actual_outcome'] == 'insufficient_evidence' and observed['calculation_count'] == 0
        assert observed['citations'] == []
    assert contract.build_formal_gate(root / pair['eval']['path'], root / pair['candidate_eval']['path'], root=root)['passed']
    if kind == 'reference_blocked':
        observed['actual_outcome'] = 'access_denied'
        after['cases'][-1]['observation_reference'] = reference(root, 'changed-refusal.json', observed)
        reference(root, pair['candidate_eval']['path'], after)
        assert not contract.build_formal_gate(root / pair['eval']['path'], root / pair['candidate_eval']['path'], root=root)['passed']


@pytest.mark.parametrize('fault', ['actual_step', 'answer_value', 'foreign_case', 'reference', 'actor', 'config', 'citations', 'citation_locator', 'provider_budget'])
def test_formal_gate_recomputes_exact_case_and_reference_instead_of_trusting_passed(comparison_inputs, fault):
    from scripts.ops import math_v2_pilot_contract as contract
    inputs = comparison_inputs
    root = inputs['window_path'].parent
    pair = inputs['window']['formal_budget_sources']['pairs'][0]
    baseline_path, candidate_path = (root / pair[key]['path'] for key in ('eval', 'candidate_eval'))
    candidate = json.loads(candidate_path.read_text())
    row = candidate['cases'][0]
    observation = json.loads((root / row['observation_reference']['path']).read_text())
    if fault == 'actual_step': observation['math_results'][0]['steps'][0]['value'] = '999'
    elif fault == 'answer_value':
        assert '4.00' in observation['answer']
        observation['answer'] = observation['answer'].replace('4.00', '999.00', 1)
    elif fault == 'foreign_case': observation['case_sha256'] = '9' * 64
    elif fault == 'actor': observation['actor'] = {**observation['actor'], 'allowed_departments': ['OTHER']}
    elif fault == 'config': observation['config_hash'] = '9' * 64
    elif fault == 'citations': observation['citations'] = []
    elif fault == 'citation_locator':
        from mech_chatbot.application.chat_citations import structured_citation
        card = observation['citations'][0]
        changed = {**card, 'locator': {**card['locator'], 'cell': 'Z999'}}
        changed = structured_citation(changed, page_has_vision=lambda *_: False)
        observation['citations'][0] = changed
        observation['answer'] = observation['answer'].replace(card['source_id'], changed['source_id'])
    elif fault == 'provider_budget': observation['execution_budget']['provider_retries'] = 1
    else:
        math = json.loads((root / row['math_reference']['path']).read_text())
        math['case_sha256'] = '9' * 64
        row['math_reference'] = reference(root, 'different-reference.json', math)
        baseline = json.loads(baseline_path.read_text())
        baseline['cases'][0]['math_reference'] = row['math_reference']
        reference(root, pair['eval']['path'], baseline)
    row['observation_reference'] = reference(root, 'changed-observation.json', observation)
    row['math_evaluation'] = reference(root, 'claimed-pass.json', {
        'schema': 'grounded-calculation-evaluation-v2', 'passed': True})
    reference(root, pair['candidate_eval']['path'], candidate)
    assert not contract.build_formal_gate(baseline_path, candidate_path, root=root)['passed']


@pytest.mark.parametrize('mutation', ['v1_event', 'one_calculation', 'private_field', 'unapproved_bundle',
    'failed_attempt', 'unexpected_provider_call', 'wrong_formal_snapshot'])
def test_comparison_gate_rejects_cross_version_or_unsafe_evidence(comparison_inputs, mutation):
    inputs = comparison_inputs
    rows = [json.loads(line) for line in inputs['trace_path'].read_text().splitlines()]
    if mutation == 'v1_event':
        rows = [{**row, 'event': 'grounded_math_generation'} for row in rows]
    elif mutation == 'one_calculation':
        rows[0]['calculations'] = 1
    elif mutation == 'private_field':
        rows[0]['scope_id'] = 'must-not-leak'
    elif mutation == 'unapproved_bundle':
        path = inputs['state_path'].parent / 'bundle.json'
        bundle = json.loads(path.read_text())
        bundle['release_signature']['value'] = 'unapproved'
        _write_json(path, bundle)
    elif mutation == 'failed_attempt':
        rows.append({'event': 'rag_error', 'execution_context': 'production', 'trace_id': 'failed',
            'code': 'comparison_not_completed'})
    elif mutation == 'unexpected_provider_call':
        rows.append({'event': 'external_ai_call', 'execution_context': 'production',
            'trace_id': rows[0]['trace_id'], 'status': 'ok'})
    else:
        window=inputs['window']
        for pair in window['formal_budget_sources']['pairs']:
            for key in ('eval', 'candidate_eval'):
                ref=pair[key]
                path=inputs['window_path'].parent / ref['path']
                value=json.loads(path.read_text())
                value['snapshot_fingerprint']='e' * 64
                pair[key]=reference(inputs['window_path'].parent, ref['path'], value)
            ref=pair['gate']
            path=inputs['window_path'].parent / ref['path']
            value=json.loads(path.read_text())
            value['inputs']={f'{name}_eval_sha256': pair[key]['sha256'] for name,key in
                (('baseline','eval'), ('candidate','candidate_eval'))}
            pair['gate']=reference(inputs['window_path'].parent, ref['path'], value)
        _write_json(inputs['window_path'],window)
        inputs['state']['window_sha256']=_sha256(inputs['window_path'])
        _write_json(inputs['state_path'],inputs['state'])
    inputs['trace_path'].write_text(''.join(json.dumps(row) + '\n' for row in rows))
    result = evaluate(inputs)
    assert not result['passed'] and result['decision'] == 'rejected'
