"""Versioned candidate inputs retain selectors without using expected answers."""
from copy import deepcopy
import pytest

from .test_semantic_task_compilation_grading import label, labels, GRAPH, plan_label, artifact, captured_sources
from .test_rag_scope_comparison import store, approved_pair


def alternatives_label():
    case = label()
    case['synthetic_selections'] = [
        {'selection_id': key, 'selection_type': 'graph_query',
         'graph_query': {**GRAPH, 'root_key': 'document:' + str(number)}}
        for number, key in enumerate(('G1', 'G2', 'G3', 'G4'), 1)]
    case['expected']['ordered_selection_ids'] = ['G2']
    case['expected']['tasks'][0]['selection_id'] = 'G2'
    return case


def test_candidate_compilation_keeps_four_alternatives_for_one_task(tmp_path):
    from scripts.eval.semantic_task_routing import compile_candidate_task_label

    case = alternatives_label()
    compiled = compile_candidate_task_label(labels(tmp_path, case), 'EXAMPLE', root=tmp_path)
    assert compiled['task_routing_contract'] == 'semantic-candidate-task-routing-v4-draft'
    assert compiled['status'] == 'representable_unbound'
    assert compiled['selection'] == {'candidate_selection_context': {'candidates': [
        {'selection_id': key, 'graph_query': {**GRAPH, 'root_key': 'document:' + str(number)}}
        for number, key in enumerate(('G1', 'G2', 'G3', 'G4'), 1)]}}
    assert compiled['expected_tasks'] == [{'selection_id': 'G2', 'template': 'graph_contains',
        'requested_outputs': ['citations', 'relations'], 'depends_on': []}]
    assert compiled['original_label'] == case
    assert compiled['accepted'] is False and compiled['offline_only'] is True


def test_candidate_input_does_not_change_when_expected_selection_changes(tmp_path):
    from scripts.eval.semantic_task_routing import compile_candidate_task_label

    case = alternatives_label()
    first = compile_candidate_task_label(labels(tmp_path, case), 'EXAMPLE', root=tmp_path)
    case['expected']['tasks'][0]['selection_id'] = 'G4'
    case['expected']['ordered_selection_ids'] = ['G4']
    second = compile_candidate_task_label(labels(tmp_path, case), 'EXAMPLE', root=tmp_path)
    assert first['selection'] == second['selection']
    assert first['expected_tasks'] != second['expected_tasks']


def test_candidate_projection_preserves_declared_child_questions_and_dependencies(tmp_path):
    from scripts.eval.semantic_task_routing import compile_candidate_task_label

    case = plan_label()
    compiled = compile_candidate_task_label(labels(tmp_path, case), 'EXAMPLE', root=tmp_path)
    candidates = compiled['selection']['candidate_selection_context']['candidates']
    assert [c['selection_id'] for c in candidates] == ['G', 'C']
    assert candidates[0]['question'] == 'Tra quan hệ cụm và dẫn nguồn.'
    assert candidates[1]['question'] == 'So sánh sau khi nguồn đủ.'
    assert candidates[1]['depends_on'] == ['G']
    assert compiled['expected_tasks'][1]['depends_on'] == ['G']


@pytest.mark.parametrize('policy', ['reject_before_planner', 'reject_before_action', 'clarify_before_action'])
def test_previous_unresolved_input_is_representable_without_resolved_plan(tmp_path, policy):
    from scripts.eval.semantic_task_routing import compile_candidate_task_label, compile_task_label

    case = alternatives_label()
    expected = case['expected']
    expected.update(disposition=policy, execution_permitted_after_valid_routing=False,
        planner_policy={'reject_before_planner': 'none_before_rejection',
                        'reject_before_action': 'at_most_one_if_count_uncertain',
                        'clarify_before_action': 'at_most_one'}[policy])
    if policy.startswith('reject'):
        expected['tasks'] = [{**deepcopy(expected['tasks'][0]), 'task_id': 'T' + str(n), 'selection_id': key}
                             for n, key in enumerate(('G1', 'G2', 'G3', 'G4'), 1)]
        expected['independent_task_count'] = 4
        expected['ordered_selection_ids'] = ['G1', 'G2', 'G3', 'G4']
    else:
        expected['tasks'][0]['selection_id'] = None
        expected['ordered_selection_ids'] = []
    reference = labels(tmp_path, case)
    previous = compile_task_label(reference, 'EXAMPLE', root=tmp_path)
    compiled = compile_candidate_task_label(reference, 'EXAMPLE', root=tmp_path)
    assert previous['status'] == 'unrepresentable_unrun' and previous['selection'] is None
    assert compiled['status'] == 'representable_unbound'
    assert len(compiled['selection']['candidate_selection_context']['candidates']) == 4
    assert compiled['expected_tasks'] == []
    assert compiled['original_expected'] == expected


def test_candidate_route_rejects_inconsistent_declared_expected_order(tmp_path):
    from scripts.eval.semantic_task_routing import compile_candidate_task_label

    case = alternatives_label()
    case['expected']['ordered_selection_ids'] = ['G4']
    with pytest.raises(ValueError, match='ordered'):
        compile_candidate_task_label(labels(tmp_path, case), 'EXAMPLE', root=tmp_path)


def bound_candidate_artifacts(root):
    from scripts.eval.semantic_task_routing import compile_candidate_task_label
    from scripts.eval.run_fixed_math_v2 import _case_request
    from mech_chatbot.rag.candidate_task_routing import candidate_context_sha256
    from .test_fixed_math_runner_v2 import BINDING, ACTOR

    reference = labels(root, alternatives_label())
    compiled = compile_candidate_task_label(reference, 'EXAMPLE', root=root)
    contract = 'semantic-candidate-task-routing-v4-draft'
    case = {'schema': 'fixed-task-routing-case-v4-draft', **BINDING, 'run_id': 'synthetic',
        'case_id': 'EXAMPLE', 'department': 'ENG', 'dataset_sha256': reference['sha256'], 'actor': ACTOR,
        'question': compiled['question'], 'selection': compiled['selection'],
        'task_routing_contract': contract, 'response_language': 'en'}
    case_ref = artifact(root, 'candidate-case.json', case)
    routing = {'schema': 'candidate-task-routing-observation-v4', 'contract': contract,
        'origin': 'planner', 'disposition': 'ready',
        'candidate_context_sha256': candidate_context_sha256(_case_request(case)),
        'resolved_request_sha256': 'e' * 64,
        'tasks': [{'selection_id': 'G2', 'template': 'graph_contains',
                   'requested_outputs': ['relations', 'citations'], 'depends_on': []}],
        'planner_count': 1, 'accepted': False, 'offline_only': True}
    observed = {'schema': 'fixed-task-routing-case-observation-v4-draft',
        **{k: case[k] for k in (*BINDING, 'run_id', 'case_id', 'department', 'dataset_sha256',
                                'actor', 'task_routing_contract', 'response_language')},
        'trace_id': 'fixed:synthetic:EXAMPLE', 'case_sha256': case_ref['sha256'],
        'task_routing': routing, 'math_results': [], 'calculation_count': 0,
        'query_branches': [], 'query_branch_schema': None, 'answer': 'Unavailable sources.',
        'citations': [], 'answer_outcome': 'refused', 'actual_outcome': 'insufficient_evidence',
        'accepted': False, 'offline_only': True, 'latency_ms': 1.5,
        'binding_verification': 'pending', 'adjudication_reference': None}
    return reference, case_ref, artifact(root, 'candidate-observation.json', observed), BINDING


def grade_candidate(root, refs):
    import json
    import hashlib
    from scripts.eval.semantic_task_routing import grade_candidate_task_observation

    capture, source_root = captured_sources(root)
    value = json.loads((root / capture['path']).read_text(encoding='utf-8'))
    extra = ['src/mech_chatbot/domain/task_routing.py', 'src/mech_chatbot/rag/candidate_task_routing.py',
             'src/mech_chatbot/rag/pipeline.py', 'src/mech_chatbot/rag/query_plan.py']
    value['files'] += [{'path': p, 'sha256': hashlib.sha256((source_root / p).read_bytes()).hexdigest()} for p in extra]
    capture = artifact(root, 'candidate-capture.json', value)
    return grade_candidate_task_observation(*refs[:3], root=root, run_binding=refs[3],
        source_capture_reference=capture, source_root=source_root)


def test_candidate_routing_match_is_separate_from_action_and_acceptance(tmp_path):
    result = grade_candidate(tmp_path, bound_candidate_artifacts(tmp_path))
    assert result['schema'] == 'candidate-task-routing-comparison-v4-draft'
    assert result['routing_matches'] is True and result['routing_quality'] == 'pass'
    assert result['action_correctness'] == 'unrun' and result['model_evaluated'] == 'unknown'
    assert result['accepted'] is False and result['offline_only'] is True


@pytest.mark.parametrize('fault', ['hash', 'schema', 'extra', 'accepted', 'contract', 'trace', 'case_sha'])
def test_candidate_grader_rejects_forged_observation_context(tmp_path, fault):
    import json

    refs = list(bound_candidate_artifacts(tmp_path))
    observed = json.loads((tmp_path / refs[2]['path']).read_text(encoding='utf-8'))
    if fault == 'hash':
        observed['task_routing']['candidate_context_sha256'] = 'f' * 64
    elif fault == 'schema':
        observed['schema'] = 'fixed-task-routing-case-observation-v3-draft'
    elif fault == 'extra':
        observed['private'] = 'restricted'
    elif fault == 'accepted':
        observed['accepted'] = True
    elif fault == 'contract':
        observed['task_routing_contract'] = 'semantic-graph-fixed-task-routing-v3-draft'
    elif fault == 'trace':
        observed['trace_id'] = 'another-request'
    else:
        observed['case_sha256'] = '0' * 64
    refs[2] = artifact(tmp_path, 'candidate-observation.json', observed)
    with pytest.raises(ValueError):
        grade_candidate(tmp_path, refs)


def test_candidate_grader_rejects_changed_alternative_even_when_unused(tmp_path):
    import json

    refs = list(bound_candidate_artifacts(tmp_path))
    case = json.loads((tmp_path / refs[1]['path']).read_text(encoding='utf-8'))
    case['selection']['candidate_selection_context']['candidates'][3]['graph_query']['root_key'] = 'document:999'
    refs[1] = artifact(tmp_path, 'candidate-case.json', case)
    with pytest.raises(ValueError, match='candidate'):
        grade_candidate(tmp_path, refs)


def test_candidate_grader_preserves_failed_routing_without_claiming_execution(tmp_path):
    import json

    refs = list(bound_candidate_artifacts(tmp_path))
    observed = json.loads((tmp_path / refs[2]['path']).read_text(encoding='utf-8'))
    observed['task_routing']['tasks'][0]['selection_id'] = 'G4'
    refs[2] = artifact(tmp_path, 'candidate-observation.json', observed)
    result = grade_candidate(tmp_path, refs)
    assert result['routing_matches'] is False and result['routing_quality'] == 'fail'
    assert result['action_correctness'] == 'unrun'


def test_candidate_grade_requires_new_resolver_and_dispatch_source_pins(tmp_path):
    from scripts.eval.semantic_task_routing import grade_candidate_task_observation

    refs = bound_candidate_artifacts(tmp_path)
    capture, source_root = captured_sources(tmp_path)
    with pytest.raises(ValueError, match='required source seams'):
        grade_candidate_task_observation(*refs[:3], root=tmp_path, run_binding=refs[3],
            source_capture_reference=capture, source_root=source_root)


@pytest.mark.parametrize('policy', ['route', 'clarify_before_action', 'reject_before_planner', 'reject_before_action'])
def test_candidate_compiler_runner_and_grader_join_without_golden_input(store, tmp_path, policy):
    from mech_chatbot.config.repository_runtime import bind_repository_runtime
    from mech_chatbot.rag.execution import DefaultRagExecutor
    from scripts.eval.run_fixed_math_v2 import run_fixed_case
    from scripts.eval.semantic_task_routing import compile_candidate_task_label
    from .test_semantic_task_compilation_grading import comparison_label
    from .test_fixed_math_runner_v2 import BINDING, ACTOR, current_profile
    from .test_candidate_task_runner import response
    from .test_semantic_fixed_task_routing import Planner

    approved_pair(store)
    case_label = comparison_label()
    case_label['synthetic_selections'] = [
        {'selection_id': key, 'selection_type': 'scope_comparison', 'scope_comparison': ['a', 'b']}
        for key in ('C1', 'C2', 'C3', 'C4')]
    question = {'route': 'Compare the selected pair using totals, difference and percentage change.',
        'clarify_before_action': 'Compare that previous pair.',
        'reject_before_planner': 'Do four independent tasks: compare each selected pair separately.',
        'reject_before_action': 'Compare the first pair; also the second pair; then the third; and the fourth as its own result.'}[policy]
    case_label['question'] = question
    expected = case_label['expected']
    expected.update(disposition=policy, execution_permitted_after_valid_routing=policy == 'route',
        planner_policy={'route': 'at_most_one', 'clarify_before_action': 'at_most_one',
                        'reject_before_planner': 'none_before_rejection',
                        'reject_before_action': 'at_most_one_if_count_uncertain'}[policy])
    expected['ordered_selection_ids'] = ['C2'] if policy == 'route' else []
    expected['tasks'][0]['selection_id'] = 'C2' if policy == 'route' else None
    if policy.startswith('reject'):
        expected['independent_task_count'] = 4
        expected['ordered_selection_ids'] = ['C1', 'C2', 'C3', 'C4']
        expected['tasks'] = [{**deepcopy(expected['tasks'][0]), 'task_id': 'T' + str(n), 'selection_id': key}
                             for n, key in enumerate(expected['ordered_selection_ids'], 1)]
    label_ref = labels(tmp_path, case_label)
    compiled = compile_candidate_task_label(label_ref, 'EXAMPLE', root=tmp_path)
    contract = compiled['task_routing_contract']
    case = {'schema': 'fixed-task-routing-case-v4-draft', **BINDING, 'run_id': 'synthetic',
        'case_id': 'EXAMPLE', 'department': 'ENG', 'dataset_sha256': label_ref['sha256'], 'actor': ACTOR,
        'question': question, 'selection': compiled['selection'],
        'task_routing_contract': contract, 'response_language': 'en'}
    case_ref = artifact(tmp_path, 'candidate-case.json', case)
    disposition = 'ready' if policy == 'route' else 'clarify' if policy == 'clarify_before_action' else 'overflow'
    slots = (('C2', 'compare_two_scopes'),) if policy == 'route' else () if disposition == 'clarify' else tuple(
        (key, 'compare_two_scopes') for key in ('C1', 'C2', 'C3', 'C4'))
    planner = Planner(response(question, disposition, slots))
    with bind_repository_runtime(db_engine=store):
        observed = run_fixed_case(case_ref, root=tmp_path, run_binding=BINDING, profile_loader=current_profile,
            executor=DefaultRagExecutor(provider_adapter=planner), task_routing_contract=contract)
    assert 'execution_budget' not in observed
    observed_ref = artifact(tmp_path, 'candidate-observation.json', observed)
    result = grade_candidate(tmp_path, (label_ref, case_ref, observed_ref, BINDING))
    assert result['routing_matches'] is True and result['accepted'] is False
    assert len(planner.calls) == (0 if policy == 'reject_before_planner' else 1)
    assert observed['calculation_count'] == (4 if policy == 'route' else 0)
    assert result['terminal_consistency'] == ('not_applicable' if policy == 'route' else 'pass')
