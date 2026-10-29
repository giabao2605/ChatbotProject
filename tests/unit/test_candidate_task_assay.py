"""Candidate assay through the injected HTTP transport and sealed source snapshot."""
import hashlib
import json

import pytest

from .test_semantic_task_assay import assay_inputs, SETTINGS
from .test_semantic_task_compilation_grading import artifact
from .test_rag_scope_comparison import store

VERSION = 'semantic-candidate-task-routing-v4-draft'


def candidate_inputs(root, engine, count=1):
    from scripts.eval.semantic_task_routing import compile_candidate_task_label
    reference, source_root = assay_inputs(root, engine, count)
    config = json.loads((root / reference['path']).read_text(encoding='utf8'))
    manifest = json.loads((root / config['prepared_manifest_reference']['path']).read_text(encoding='utf8'))
    refs = []
    for ref in manifest['case_references']:
        case = json.loads((root / ref['path']).read_text(encoding='utf8'))
        compiled = compile_candidate_task_label(config['label_reference'], case['case_id'], root=root)
        refs.append(artifact(root, ref['path'], {**case, 'schema': 'fixed-task-routing-case-v4-draft',
            'task_routing_contract': VERSION, 'selection': compiled['selection']}))
    manifest_ref = artifact(root, 'prepared.json', {**manifest,
        'schema': 'synthetic-candidate-routing-assay-preparation-v4-draft',
        'task_routing_contract': VERSION, 'case_references': refs})
    proposal = json.loads((root / config['proposal_reference']['path']).read_text(encoding='utf8'))
    proposal_ref = artifact(root, 'proposal.json', {**proposal,
        'schema': 'candidate-synthetic-provider-policy-proposal-v4-draft', 'task_routing_contract': VERSION,
        'source_manifest': manifest_ref, 'maximum_case_count': 24, 'maximum_provider_calls': 24})
    auth = json.loads((root / config['authorization_reference']['path']).read_text(encoding='utf8'))
    auth_ref = artifact(root, 'authorization.json', {**auth,
        'schema': 'synthetic-candidate-evaluation-egress-authorization-v4-draft', 'task_routing_contract': VERSION,
        'proposal_reference': proposal_ref, 'source_manifest_reference': manifest_ref,
        'maximum_case_count': 24, 'maximum_provider_calls': 24})
    capture = json.loads((root / config['source_capture_reference']['path']).read_text(encoding='utf8'))
    extra = ['src/mech_chatbot/domain/task_routing.py', 'src/mech_chatbot/rag/candidate_task_routing.py',
             'src/mech_chatbot/rag/pipeline.py', 'src/mech_chatbot/rag/query_plan.py']
    capture_ref = artifact(root, 'capture.json', {**capture, 'files': [*capture['files'], *[
        {'path': path, 'sha256': hashlib.sha256((source_root / path).read_bytes()).hexdigest()} for path in extra]]})
    return artifact(root, 'request.json', {**config, 'schema': 'candidate-task-assay-request-v4-draft',
        'task_routing_contract': VERSION, 'prepared_manifest_reference': manifest_ref,
        'proposal_reference': proposal_ref, 'authorization_reference': auth_ref,
        'source_capture_reference': capture_ref}), source_root


def candidate_transport(monkeypatch, outcome='ready', callback=None):
    import httpx
    calls = []
    def send(client, request, **kwargs):
        from .test_candidate_task_runner import response
        body = json.loads(request.content)
        assert 'original_expected' not in str(body) and 'expected_tasks' not in str(body)
        calls.append(body)
        prompt = json.loads(body['messages'][1]['content'])
        if callback:
            callback()
        if outcome == 'error':
            raise httpx.ReadTimeout('Synthetic timeout', request=request)
        if type(outcome) is int:
            return httpx.Response(outcome, request=request, json={'error': {'message': 'private failure'}})
        slots = ((('C', 'compare_two_scopes'),) if outcome == 'ready' else
                 tuple((f'C{i}', 'compare_two_scopes') for i in range(4)) if outcome == 'overflow' else ())
        routing = response(prompt['question'], outcome, slots=slots)
        return httpx.Response(200, request=request, json={'id': 'synthetic-http-response',
            'object': 'chat.completion', 'created': 1, 'model': SETTINGS.model_name,
            'choices': [{'index': 0, 'message': {'role': 'assistant', 'content': json.dumps(routing)},
                         'finish_reason': 'stop'}],
            'usage': {'prompt_tokens': 10, 'completion_tokens': 20, 'total_tokens': 30}})
    monkeypatch.setattr(httpx.Client, 'send', send)
    return calls


def test_candidate_assay_executes_and_grades_math_with_own_contract(store, tmp_path, monkeypatch):
    from scripts.eval.run_semantic_task_assay import run_assay
    ref, source_root = candidate_inputs(tmp_path, store)
    calls = candidate_transport(monkeypatch)
    result = run_assay(ref, root=tmp_path, source_root=source_root,
                       output_directory=tmp_path / 'run', llm_settings=SETTINGS)
    assert len(calls) == result['attempted_provider_calls'] == 1
    row = result['cases'][0]
    assert row['status'] == 'observed' and row['routing_matches'] is True
    observed = json.loads((tmp_path / 'run/observations/EXAMPLE.json').read_text(encoding='utf8'))
    assert observed['task_routing_contract'] == VERSION and observed['calculation_count'] == 4
    assert [step['value'] for step in observed['math_results'][0]['steps']] == ['4', '8', '4', '100']
    assert result['schema'] == 'candidate-task-assay-result-v4-draft'
    evaluation = json.loads((tmp_path / 'run/evaluation.json').read_text(encoding='utf8'))
    assert result['denominator'] == evaluation['denominator'] == len(evaluation['case_ids']) == 1
    assert result['accepted'] is False and result['runtime_activation'] is False


def read(root, reference):
    from pathlib import Path
    return json.loads((root / Path(reference['path'])).read_text(encoding='utf8'))


def closed(root):
    value = json.loads((root / 'run/policy-closure.json').read_text(encoding='utf8'))
    assert value['status'] == 'closed' and value['profile_after']['IsActive'] == 0
    assert value['native_policy_changed'] is False and value['reusing_window_permitted'] is False


@pytest.mark.parametrize('fault', ['request_schema', 'request_contract', 'auth_schema', 'auth_contract',
    'proposal_schema', 'proposal_contract', 'manifest_schema', 'manifest_contract', 'case_schema',
    'case_contract', 'authorization_status', 'cap25', 'cap_bool', 'cap_mismatch'])
def test_cross_version_or_unapproved_inputs_are_rejected_before_claim_and_egress(store, tmp_path, monkeypatch, fault):
    from scripts.eval.run_semantic_task_assay import run_assay
    reference, source_root = candidate_inputs(tmp_path, store)
    config = read(tmp_path, reference)
    if fault.startswith('request'):
        config['schema' if fault.endswith('schema') else 'task_routing_contract'] = 'old-v3'
    else:
        manifest = read(tmp_path, config['prepared_manifest_reference'])
        proposal = read(tmp_path, config['proposal_reference'])
        auth = read(tmp_path, config['authorization_reference'])
        if fault.startswith('case'):
            case_ref = manifest['case_references'][0]
            case = read(tmp_path, case_ref)
            case['schema' if fault.endswith('schema') else 'task_routing_contract'] = 'old-v3'
            manifest = {**manifest, 'case_references': [artifact(tmp_path, case_ref['path'], case)]}
        elif fault.startswith('manifest'):
            manifest['schema' if fault.endswith('schema') else 'task_routing_contract'] = 'old-v3'
        elif fault.startswith('proposal'):
            proposal['schema' if fault.endswith('schema') else 'task_routing_contract'] = 'old-v3'
        elif fault.startswith('auth_'):
            auth['schema' if fault.endswith('schema') else 'task_routing_contract'] = 'old-v3'
        elif fault == 'authorization_status':
            auth['authorization_status'] = 'pending_human_authorization'
        else:
            auth['maximum_provider_calls'] = {'cap25': 25, 'cap_bool': True, 'cap_mismatch': 1}[fault]
        manifest_ref = artifact(tmp_path, 'prepared.json', manifest)
        proposal_ref = artifact(tmp_path, 'proposal.json', {**proposal, 'source_manifest': manifest_ref})
        auth_ref = artifact(tmp_path, 'authorization.json', {**auth,
            'source_manifest_reference': manifest_ref, 'proposal_reference': proposal_ref})
        config = {**config, 'prepared_manifest_reference': manifest_ref,
                  'proposal_reference': proposal_ref, 'authorization_reference': auth_ref}
    reference = artifact(tmp_path, 'request.json', config)
    calls = candidate_transport(monkeypatch)
    with pytest.raises(ValueError):
        run_assay(reference, root=tmp_path, source_root=source_root,
                   output_directory=tmp_path / 'run', llm_settings=SETTINGS)
    assert not calls and not (tmp_path / 'run').exists() and not (tmp_path / 'semantic-assay-claims').exists()


def test_twenty_four_candidate_calls_are_serial_sealed_and_policy_closed(store, tmp_path, monkeypatch):
    from scripts.eval.run_semantic_task_assay import run_assay
    reference, source_root = candidate_inputs(tmp_path, store, count=24)
    active = []
    def callback():
        reserved = len(list((tmp_path / 'run/provider').glob('*-reserved.json')))
        complete = len(list((tmp_path / 'run/provider').glob('*-result.json')))
        assert reserved == complete + 1
        active.append(reserved)
    calls = candidate_transport(monkeypatch, callback=callback)
    result = run_assay(reference, root=tmp_path, source_root=source_root,
                       output_directory=tmp_path / 'run', llm_settings=SETTINGS)
    assert len(calls) == result['attempted_provider_calls'] == 24 and active == list(range(1, 25))
    evaluation = read(tmp_path, {'path': 'run/evaluation.json'})
    assert result['denominator'] == evaluation['denominator'] == len(evaluation['case_ids']) == 24
    assert result['unrun_case_ids'] == [] and all(row['routing_matches'] for row in result['cases'])
    assert all(row['provider_receipt']['audit_complete'] for row in result['cases'])
    assert all(row['routing_diagnostics_reference'] for row in result['cases'])
    closed(tmp_path)


@pytest.mark.parametrize('outcome', ['clarify', 'overflow', 'unsupported', 'invalid'])
def test_nonready_candidate_results_do_not_execute_math_or_reveal_selection(store, tmp_path, monkeypatch, outcome):
    from scripts.eval.run_semantic_task_assay import run_assay
    reference, source_root = candidate_inputs(tmp_path, store)
    calls = candidate_transport(monkeypatch, outcome)
    result = run_assay(reference, root=tmp_path, source_root=source_root,
                       output_directory=tmp_path / 'run', llm_settings=SETTINGS)
    assert len(calls) == result['attempted_provider_calls'] == 1
    row = result['cases'][0]
    assert row['status'] == 'observed' and row['routing_matches'] is False
    observed = read(tmp_path, row['observation_reference'])
    assert observed['calculation_count'] == 0 and observed['math_results'] == observed['citations'] == observed['query_branches'] == []
    assert all(task['selection_id'] is None for task in observed['task_routing']['tasks'])
    diagnostics = read(tmp_path, row['routing_diagnostics_reference'])['diagnostics']
    assert diagnostics['task_routing_contract'] == VERSION and diagnostics['disposition'] == outcome
    assert diagnostics['failure_code'] == ('contract' if outcome == 'invalid' else None)
    closed(tmp_path)


@pytest.mark.parametrize('failure', ['cancel', 401, 403, 404])
def test_cancel_or_configuration_error_closes_window_without_retry(store, tmp_path, monkeypatch, failure):
    import threading
    from scripts.eval.run_semantic_task_assay import run_assay
    reference, source_root = candidate_inputs(tmp_path, store, count=2)
    cancel = threading.Event()
    calls = candidate_transport(monkeypatch, 'ready' if failure == 'cancel' else failure,
                                callback=cancel.set if failure == 'cancel' else None)
    result = run_assay(reference, root=tmp_path, source_root=source_root,
                       output_directory=tmp_path / 'run', llm_settings=SETTINGS, cancellation=cancel)
    assert len(calls) == result['attempted_provider_calls'] == 1
    assert result['cases'][0]['stop_window'] is True and result['unrun_case_ids'] == ['SECOND']
    evaluation = read(tmp_path, {'path': 'run/evaluation.json'})
    assert result['denominator'] == evaluation['denominator'] == len(evaluation['case_ids']) == 2
    assert result['cases'][0]['provider_receipt']['audit_complete'] is True
    closed(tmp_path)


def test_obvious_overflow_rejects_without_spending_provider_call(store, tmp_path, monkeypatch):
    from scripts.eval.run_semantic_task_assay import run_assay
    reference, source_root = candidate_inputs(tmp_path, store)
    config = read(tmp_path, reference)
    label = read(tmp_path, config['label_reference'])
    label['cases'][0]['question'] = 'Do four independent tasks: compare each pair separately.'
    label_ref = artifact(tmp_path, 'labels.json', label)
    manifest = read(tmp_path, config['prepared_manifest_reference'])
    case = read(tmp_path, manifest['case_references'][0])
    case_ref = artifact(tmp_path, 'prepared-case-EXAMPLE.json', {**case,
        'question': label['cases'][0]['question'], 'dataset_sha256': label_ref['sha256']})
    manifest_ref = artifact(tmp_path, 'prepared.json', {**manifest, 'case_references': [case_ref]})
    proposal_ref = artifact(tmp_path, 'proposal.json', {**read(tmp_path, config['proposal_reference']), 'source_manifest': manifest_ref})
    auth_ref = artifact(tmp_path, 'authorization.json', {**read(tmp_path, config['authorization_reference']),
        'source_manifest_reference': manifest_ref, 'proposal_reference': proposal_ref})
    reference = artifact(tmp_path, 'request.json', {**config, 'label_reference': label_ref,
        'prepared_manifest_reference': manifest_ref, 'proposal_reference': proposal_ref, 'authorization_reference': auth_ref})
    calls = candidate_transport(monkeypatch)
    result = run_assay(reference, root=tmp_path, source_root=source_root,
                       output_directory=tmp_path / 'run', llm_settings=SETTINGS)
    assert not calls and result['attempted_provider_calls'] == 0
    observed = read(tmp_path, result['cases'][0]['observation_reference'])
    assert observed['task_routing']['disposition'] == 'overflow' and observed['task_routing']['planner_count'] == 0
    assert observed['calculation_count'] == 0 and observed['citations'] == []
    closed(tmp_path)


def test_candidate_resolver_source_pin_is_required_before_egress(store, tmp_path, monkeypatch):
    from scripts.eval.run_semantic_task_assay import run_assay
    reference, source_root = candidate_inputs(tmp_path, store)
    config = read(tmp_path, reference)
    capture = read(tmp_path, config['source_capture_reference'])
    capture_ref = artifact(tmp_path, 'capture.json', {**capture, 'files': [ref for ref in capture['files']
        if ref['path'] != 'src/mech_chatbot/rag/candidate_task_routing.py']})
    reference = artifact(tmp_path, 'request.json', {**config, 'source_capture_reference': capture_ref})
    calls = candidate_transport(monkeypatch)
    with pytest.raises(ValueError, match='source seams'):
        run_assay(reference, root=tmp_path, source_root=source_root,
                   output_directory=tmp_path / 'run', llm_settings=SETTINGS)
    assert not calls and not (tmp_path / 'run').exists()


@pytest.mark.parametrize('count', [1, 2])
def test_reduced_v4_caps_match_authorization_and_block_excess_before_claim(store, tmp_path, monkeypatch, count):
    from scripts.eval.run_semantic_task_assay import run_assay
    reference, source_root = candidate_inputs(tmp_path, store, count=count)
    config = read(tmp_path, reference)
    proposal_ref = artifact(tmp_path, 'proposal.json', {**read(tmp_path, config['proposal_reference']),
        'maximum_case_count': 1, 'maximum_provider_calls': 1})
    auth_ref = artifact(tmp_path, 'authorization.json', {**read(tmp_path, config['authorization_reference']),
        'proposal_reference': proposal_ref, 'maximum_case_count': 1, 'maximum_provider_calls': 1})
    reference = artifact(tmp_path, 'request.json', {**config,
        'proposal_reference': proposal_ref, 'authorization_reference': auth_ref})
    calls = candidate_transport(monkeypatch)
    if count == 2:
        with pytest.raises(ValueError):
            run_assay(reference, root=tmp_path, source_root=source_root,
                       output_directory=tmp_path / 'run', llm_settings=SETTINGS)
        assert not calls and not (tmp_path / 'semantic-assay-claims').exists()
    else:
        result = run_assay(reference, root=tmp_path, source_root=source_root,
                           output_directory=tmp_path / 'run', llm_settings=SETTINGS)
        assert len(calls) == result['attempted_provider_calls'] == 1
        closed(tmp_path)
