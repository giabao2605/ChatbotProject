"""Synthetic pairs test formal bindings, never authorize a release."""
from dataclasses import asdict
from decimal import Decimal
import hashlib
import json

import pytest

from mech_chatbot.config.repository_runtime import bind_repository_runtime
from mech_chatbot.db.repositories.structured_evidence import load_approved_scope
from scripts.eval.run_fixed_math_v2 import run_fixed_case
from scripts.ops import scope_total_formal_contract as contract
from .test_fixed_math_runner_v2 import ACTOR, BINDING, current_profile, frozen_case
from .test_math_v2_activation import reference
from .test_rag_scope_comparison import approved_pair, store
from .test_structured_evidence_repository import PROFILE


@pytest.fixture
def formal_inputs(store, tmp_path):
    approved_pair(store)
    with bind_repository_runtime(db_engine=store):
        scope = load_approved_scope('a', PROFILE)
    records = [{'template': 'sum_reviewed_scope',
        'scopes': json.loads(json.dumps([asdict(scope)], default=str))}]
    pairs = []
    for index in range(3):
        case_ref = frozen_case(tmp_path, run_id=f'total-{index}', case_id='TOTAL-1',
            question='Total selected scope', selection={'scope_total': {'scope_id': 'a'}})
        (tmp_path / case_ref['path']).rename(tmp_path / f'case-{index}.json')
        case_ref = {**case_ref, 'path': f'case-{index}.json'}
        case = json.loads((tmp_path / case_ref['path']).read_text())
        math_ref = reference(tmp_path, f'reference-{index}.json', {
            'schema': 'fixed-template-math-reference-v2', **BINDING,
            **{key: case[key] for key in ('run_id', 'case_id', 'department', 'dataset_sha256', 'actor')},
            'case_sha256': case_ref['sha256'], 'records': records})
        with bind_repository_runtime(db_engine=store):
            observed = run_fixed_case(case_ref, root=tmp_path, run_binding=BINDING, profile_loader=current_profile)
        # A synthetic baseline tests binding checks; it is not an actual model run.
        baseline = {**observed, 'trace_id': f'baseline-fixture-{index}', 'math_results': [], 'calculation_count': 0,
            'latency_ms': 30000, 'estimated_cost': '0.01'}
        row = {'case_id': case['case_id'], 'case_reference': case_ref, 'math_reference': math_ref}
        envelope = {'schema': 'rag-labeled-eval-v4', 'git_sha': BINDING['source_commit'],
            **{key: BINDING[key] for key in ('snapshot_fingerprint', 'config_hash', 'gate_hash', 'eval_hash')},
            'dataset_hash': case['dataset_sha256'], 'evaluation_context_hash': hashlib.sha256(
                json.dumps({'actor': ACTOR, 'config_hash': BINDING['config_hash']}, sort_keys=True,
                    separators=(',', ':')).encode()).hexdigest()}
        before = {**envelope, 'cases': [{**row, 'latency_ms': baseline['latency_ms'], 'estimated_cost': '0.01',
            'observation_reference': reference(tmp_path, f'baseline-observed-{index}.json', baseline)}]}
        after = {**envelope, 'cases': [{**row, 'latency_ms': observed['latency_ms'], 'estimated_cost': 0,
            'observation_reference': reference(tmp_path, f'candidate-observed-{index}.json', observed)}]}
        pairs.append((reference(tmp_path, f'before-{index}.json', before),
                      reference(tmp_path, f'after-{index}.json', after)))
    return tmp_path, pairs


def test_formal_total_uses_frozen_scope_and_independent_oracle(formal_inputs):
    root, pairs = formal_inputs
    sources = []
    for index, (before, after) in enumerate(pairs):
        result = contract.build_formal_gate(root / before['path'], root / after['path'], root=root)
        assert result['passed'] and result['contract'] == contract.CONTRACT
        assert result['decision'] == 'pending_review'
        assert result['accepted'] is result['production_eligible'] is False
        sources.append({'eval': before, 'candidate_eval': after,
            'gate': reference(root, f'gate-{index}.json', result)})
    record = {'source_commit': BINDING['source_commit'], 'pairs': sources}
    assert contract.formal_budget_maxima(record, root, BINDING['source_commit'],
        BINDING['snapshot_fingerprint']) == (30000, Decimal('0.01'))
    for invalid in (sources[:2], [sources[0]]*3):
        assert contract.formal_budget_maxima({**record, 'pairs': invalid}, root,
            BINDING['source_commit'], BINDING['snapshot_fingerprint']) is None


@pytest.mark.parametrize('fault', ['result', 'answer', 'citation', 'provider', 'planner', 'steps',
    'copied_baseline', 'unbound_baseline', 'candidate_cost', 'observed_cost', 'latency', 'context', 'reference', 'selection'])
def test_formal_total_rejects_inaccurate_or_unbound_evidence(formal_inputs, fault):
    root, pairs = formal_inputs
    before_ref, after_ref = pairs[0]
    before, after = [json.loads((root / item['path']).read_text()) for item in pairs[0]]
    row = after['cases'][0]
    observed = json.loads((root / row['observation_reference']['path']).read_text())
    if fault == 'result': observed['math_results'][0]['steps'][0]['value'] = '999'
    elif fault == 'answer': observed['answer'] = 'Total: 999 VND'
    elif fault == 'citation': observed['citations'] = []
    elif fault == 'provider': observed['execution_budget']['final_generations'] = 1
    elif fault == 'planner': observed['execution_budget']['planners'] = 1
    elif fault == 'steps': observed['calculation_count'] = 4
    elif fault == 'copied_baseline': before['cases'][0] = row
    elif fault == 'unbound_baseline': before['cases'][0]['latency_ms'] = 100000
    elif fault == 'candidate_cost': row['estimated_cost'] = 1
    elif fault == 'observed_cost': observed['estimated_cost'] = 1
    elif fault == 'latency': observed['latency_ms'] = row['latency_ms'] = 50000
    elif fault == 'context': after['evaluation_context_hash'] = '0'*64
    elif fault == 'reference':
        source = json.loads((root / row['math_reference']['path']).read_text())
        source['records'][0]['scopes'][0]['facts'][0]['value'] = '999'
        row['math_reference'] = before['cases'][0]['math_reference'] = reference(root, 'bad-reference.json', source)
    elif fault == 'selection':
        case = json.loads((root / row['case_reference']['path']).read_text())
        case['selection'] = {'scope_total': {'scope_id': 'other'}}
        row['case_reference'] = before['cases'][0]['case_reference'] = reference(root, 'bad-case.json', case)
    row['observation_reference'] = reference(root, 'bad-observed.json', observed)
    reference(root, before_ref['path'], before)
    reference(root, after_ref['path'], after)
    assert not contract.build_formal_gate(root / before_ref['path'], root / after_ref['path'], root=root)['passed']


@pytest.mark.parametrize('fault', ['schema', 'template', 'count', 'multiple', 'query'])
def test_sum_exporter_rejects_downgrade_or_query_gate_label(store, fault):
    from dataclasses import replace
    from mech_chatbot.evaluation.grounded_math_v2 import export_math_observation
    from mech_chatbot.rag.execution import DefaultRagExecutor, RagInvocation, collect_rag_events
    from .test_rag_scope_total import selected
    approved_pair(store)
    with bind_repository_runtime(db_engine=store):
        result = collect_rag_events(DefaultRagExecutor().run(selected(),
            RagInvocation('sum-export-boundary', mode='evaluation', capture_math_observations=True)))
    record = result.observed_math_results[0]
    if fault == 'schema': record = {**record, 'schema': 'grounded-calculation-v2'}
    elif fault == 'template': record = {**record, 'template': 'compare_two_scopes'}
    elif fault == 'count': record = {**record, 'domain_step_count': 4}
    if fault == 'query':
        diagnostics = replace(result.diagnostics, _raw={**result.diagnostics,
            'query_v2': {'schema': 'query-plan-scope-total-v1', 'branches': []}})
        result = replace(result, diagnostics=diagnostics)
    result = replace(result, observed_math_results=(record, record) if fault == 'multiple' else (record,))
    with pytest.raises(ValueError): export_math_observation(result)


def test_three_envelopes_cannot_reuse_the_same_pair_observations(formal_inputs):
    root, pairs = formal_inputs
    original = [json.loads((root / item['path']).read_text()) for item in pairs[0]]
    copies = []
    for index in range(3):
        before, after = [reference(root, f'clone-{arm}-{index}.json', {**envelope, 'run_label': f'clone-{index}'})
            for arm, envelope in zip(('before', 'after'), original)]
        gate = contract.build_formal_gate(root / before['path'], root / after['path'], root=root)
        assert gate['passed']
        copies.append({'eval': before, 'candidate_eval': after,
            'gate': reference(root, f'clone-gate-{index}.json', gate)})
    assert contract.formal_budget_maxima({'source_commit': BINDING['source_commit'], 'pairs': copies},
        root, BINDING['source_commit'], BINDING['snapshot_fingerprint']) is None


def test_new_observation_bytes_cannot_relabel_one_frozen_run_as_three(formal_inputs):
    root, pairs = formal_inputs
    original = [json.loads((root / item['path']).read_text()) for item in pairs[0]]
    copies = []
    for index in range(3):
        references = []
        for arm, envelope in zip(('baseline', 'candidate'), original):
            row = envelope['cases'][0]
            observed = json.loads((root / row['observation_reference']['path']).read_text())
            observed = {**observed, 'run_label': f'clone-{index}', 'trace_id': f'clone-{arm}-{index}'}
            observed_ref = reference(root, f'cloned-observation-{arm}-{index}.json', observed)
            references.append(reference(root, f'cloned-envelope-{arm}-{index}.json', {
                **envelope, 'cases': [{**row, 'observation_reference': observed_ref}]}))
        before, after = references
        gate = contract.build_formal_gate(root / before['path'], root / after['path'], root=root)
        assert gate['passed']
        copies.append({'eval': before, 'candidate_eval': after,
            'gate': reference(root, f'cloned-observation-gate-{index}.json', gate)})
    assert contract.formal_budget_maxima({'source_commit': BINDING['source_commit'], 'pairs': copies},
        root, BINDING['source_commit'], BINDING['snapshot_fingerprint']) is None


@pytest.mark.parametrize('fault', ['same_arm_trace', 'blank_trace', 'long_trace'])
def test_formal_series_requires_distinct_bounded_arm_traces(formal_inputs, fault):
    root, pairs = formal_inputs
    sources = []
    for index, (before, after) in enumerate(pairs):
        candidate = json.loads((root / after['path']).read_text())
        row = candidate['cases'][0]
        observed = json.loads((root / row['observation_reference']['path']).read_text())
        if fault == 'same_arm_trace':
            baseline = json.loads((root / before['path']).read_text())
            trace = json.loads((root / baseline['cases'][0]['observation_reference']['path']).read_text())['trace_id']
        else:
            trace = '' if fault == 'blank_trace' else 'x'*256
        after = reference(root, f'trace-envelope-{index}.json', {**candidate, 'cases': [{**row,
            'observation_reference': reference(root, f'trace-observed-{index}.json', {**observed, 'trace_id': trace})}]})
        gate = contract.build_formal_gate(root / before['path'], root / after['path'], root=root)
        assert gate['passed']
        sources.append({'eval': before, 'candidate_eval': after,
            'gate': reference(root, f'trace-gate-{index}.json', gate)})
    assert contract.formal_budget_maxima({'source_commit': BINDING['source_commit'], 'pairs': sources},
        root, BINDING['source_commit'], BINDING['snapshot_fingerprint']) is None
