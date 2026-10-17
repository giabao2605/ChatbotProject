"""Pure candidate input compilation and routing comparison at public seams."""
from copy import deepcopy
import hashlib
import json
from pathlib import Path

import pytest

from mech_chatbot.rag.query_decomposition import GRAPH_TASK_ROUTING
from .test_rag_scope_comparison import store, approved_pair


GRAPH = {'root_key':'document:1', 'product_scope':'product', 'site':'factory',
         'direction':'contains', 'max_relations':120, 'max_generation_edges':25}


def artifact(root, name, value):
    raw = json.dumps(value, ensure_ascii=False).encode('utf8')
    (root / name).write_bytes(raw)
    return {'path':name, 'sha256':hashlib.sha256(raw).hexdigest()}


def label(**changes):
    return {'case_id':'EXAMPLE', 'language':'en', 'question':'Explain selected contents and cite sources.',
        'semantic_tags':[], 'synthetic_selections':[{'selection_id':'G', 'selection_type':'graph_query', 'graph_query':GRAPH}],
        'expected':{'disposition':'route', 'independent_task_count':1, 'ordered_selection_ids':['G'],
            'tasks':[{'task_id':'T1', 'selection_id':'G', 'template':'graph_contains',
                      'outputs':['relations','citations'], 'depends_on':[]}],
            'execution_permitted_after_valid_routing':True, 'planner_policy':'at_most_one',
            'preserve_every_requested_intent':True, 'conditions':[]}, **changes}


def labels(root, *cases):
    return artifact(root, 'labels.json', {'artifact_type':'agent_authored_synthetic_semantic_routing_labels',
        'contract_version':GRAPH_TASK_ROUTING, 'cases':list(cases or (label(),))})


def test_compile_graph_preserves_original_input_bounds_and_unbound_status(tmp_path):
    from scripts.eval.semantic_task_routing import compile_task_label
    ref = labels(tmp_path)
    result = compile_task_label(ref, 'EXAMPLE', root=tmp_path)
    assert result['selection'] == {'graph_query':GRAPH}
    assert result['selector_slots'] == {'G':'graph'}
    assert result['expected_tasks'] == [{'selection_id':'graph', 'template':'graph_contains',
        'requested_outputs':['citations','relations'], 'depends_on':[]}]
    assert result['status'] == 'representable_unbound' and result['binding_verification'] == 'missing'
    assert result['question'] == 'Explain selected contents and cite sources.'
    assert result['response_language'] == 'en' and result['original_expected'] == label()['expected']


def captured_sources(root):
    names = ['scripts/eval/semantic_task_routing.py', 'scripts/eval/run_fixed_math_v2.py',
        'src/mech_chatbot/api/evidence_contracts.py', 'src/mech_chatbot/rag/query_decomposition.py',
        'src/mech_chatbot/rag/execution.py', 'src/mech_chatbot/evaluation/grounded_math_v2.py',
        'src/mech_chatbot/governance/artifact_references.py', 'src/mech_chatbot/evaluation/schema.py',
        'src/mech_chatbot/domain/graph_query.py', 'src/mech_chatbot/domain/query_plan.py']
    source_root = Path(__file__).resolve().parents[2]
    capture = {'files':[{'path':p, 'sha256':hashlib.sha256((source_root/p).read_bytes()).hexdigest()} for p in names]}
    return artifact(root, 'capture.json', capture), source_root


def bound_artifacts(root, case_label=None, disposition='ready'):
    from .test_fixed_math_runner_v2 import BINDING, ACTOR
    case_label = case_label or label()
    ref = labels(root, case_label)
    case = {'schema':'fixed-task-routing-case-v3-draft', **BINDING, 'run_id':'synthetic',
        'case_id':'EXAMPLE', 'department':'ENG', 'dataset_sha256':ref['sha256'], 'actor':ACTOR,
        'question':case_label['question'], 'selection':{'graph_query':GRAPH},
        'task_routing_contract':GRAPH_TASK_ROUTING, 'response_language':'en'}
    case_ref = artifact(root, 'case.json', case)
    routing = {'schema':'task-routing-observation-v3', 'contract':GRAPH_TASK_ROUTING, 'origin':'planner',
        'disposition':disposition, 'tasks':[{'selection_id':'graph','template':'graph_contains',
            'requested_outputs':['relations','citations'], 'depends_on':[]}] if disposition == 'ready' else [],
        'planner_count':1, 'accepted':False, 'offline_only':True}
    observed = {'schema':'fixed-task-routing-case-observation-v3-draft', **{k:case[k] for k in (
        *BINDING,'run_id','case_id','department','dataset_sha256','actor','task_routing_contract','response_language')},
        'trace_id':'fixed:synthetic:EXAMPLE', 'case_sha256':case_ref['sha256'], 'task_routing':routing,
        'math_results':[], 'calculation_count':0, 'query_branches':[], 'query_branch_schema':None,
        'answer':'Unavailable sources.', 'citations':[], 'answer_outcome':'refused', 'actual_outcome':'insufficient_evidence',
        'accepted':False, 'offline_only':True, 'latency_ms':1.5, 'binding_verification':'pending','adjudication_reference':None}
    return ref, case_ref, artifact(root,'observation.json',observed), BINDING


def grade(root, refs):
    from scripts.eval.semantic_task_routing import grade_task_observation
    capture, source_root = captured_sources(root)
    return grade_task_observation(*refs[:3], root=root, run_binding=refs[3],
        source_capture_reference=capture, source_root=source_root)


def test_routing_match_with_missing_sources_is_not_business_or_model_success(tmp_path):
    result = grade(tmp_path, bound_artifacts(tmp_path))
    assert result['routing_matches'] is True and result['routing_quality'] == 'pass'
    assert result['input_fidelity'] == 'verified' and result['terminal_consistency'] == 'not_applicable'
    assert result['action_correctness'] == 'unrun' and result['safety'] == 'unverified'
    assert result['execution_provenance'] == 'pending' and result['model_evaluated'] == 'unknown'
    assert result['accepted'] is False and result['offline_only'] is True and 'passed' not in result


def test_route_label_cannot_reverse_selected_graph_direction(tmp_path):
    from scripts.eval.semantic_task_routing import compile_task_label
    case = deepcopy(label())
    case['expected']['tasks'][0]['template'] = 'graph_where_used'
    with pytest.raises(ValueError, match='selected action'):
        compile_task_label(labels(tmp_path, case), 'EXAMPLE', root=tmp_path)


def comparison_label():
    case = deepcopy(label())
    case['synthetic_selections'] = [{'selection_id':'C','selection_type':'scope_comparison','scope_comparison':['a','b']}]
    case['expected']['ordered_selection_ids'] = ['C']
    case['expected']['tasks'] = [{'task_id':'T1','selection_id':'C','template':'compare_two_scopes',
        'outputs':['sum_a','sum_b','delta','percent_change'],'depends_on':[]}]
    return case


def plan_label():
    case = deepcopy(label(language='vi'))
    case['synthetic_selections'] = [*case['synthetic_selections'], comparison_label()['synthetic_selections'][0]]
    case['query_plan'] = {'branches':[
        {'branch_id':'contents','selection_id':'G','question':'Tra quan hệ cụm và dẫn nguồn.','depends_on':[]},
        {'branch_id':'compare','selection_id':'C','question':'So sánh sau khi nguồn đủ.','depends_on':['contents']}]}
    case['expected'] = {**case['expected'], 'independent_task_count':2, 'ordered_selection_ids':['G','C'],
        'tasks':[case['expected']['tasks'][0], {'task_id':'T2','selection_id':'C','template':'compare_two_scopes',
            'outputs':['delta'],'depends_on':['T1']}]}
    return case


@pytest.mark.parametrize('kind', ['comparison','bom','plan'])
def test_compilation_preserves_decimal_scope_and_branch_dependencies(tmp_path, kind):
    from scripts.eval.semantic_task_routing import compile_task_label
    case = comparison_label() if kind == 'comparison' else plan_label() if kind == 'plan' else deepcopy(label())
    if kind == 'bom':
        case['synthetic_selections'] = [{'selection_id':'B','selection_type':'bom_rollup',
            'bom_rollup':{'graph':GRAPH,'order_quantity':'3.50','rounding_scope':'rounding-policy'}}]
        case['expected']['ordered_selection_ids'] = ['B']
        case['expected']['tasks'] = [{'task_id':'T1','selection_id':'B','template':'bom_rollup',
            'outputs':['totals_by_part','quantity_basis'],'depends_on':[]}]
    result = compile_task_label(labels(tmp_path,case), 'EXAMPLE', root=tmp_path)
    if kind == 'comparison':
        assert result['selection'] == {'scope_comparison':{'scope_a':'a','scope_b':'b'}}
        assert result['selector_slots'] == {'C':'comparison'}
    elif kind == 'bom':
        assert result['selection'] == {'bom_rollup':{**GRAPH,'order_quantity':'3.50','rounding_scope':'rounding-policy'}}
        assert result['selector_slots'] == {'B':'bom'}
    else:
        assert result['response_language'] == 'vi' and result['selector_slots'] == {'G':'contents','C':'compare'}
        assert result['task_slots'] == {'T1':'contents','T2':'compare'}
        assert result['expected_tasks'][1]['depends_on'] == ['contents']
        assert result['selection']['query_plan']['branches'][1]['question'] == 'So sánh sau khi nguồn đủ.'
    assert result['original_label'] == case
    result['original_label']['question'] = 'caller mutation'
    assert case['question'] == 'Explain selected contents and cite sources.'


def test_unresolved_multiple_selectors_keep_every_intent_and_original_policy(tmp_path):
    from scripts.eval.semantic_task_routing import compile_task_label
    case = plan_label()
    case.pop('query_plan')
    case['expected'] = {**case['expected'],'disposition':'clarify_before_action',
        'execution_permitted_after_valid_routing':False,'reason':'No resolved antecedent.'}
    result = compile_task_label(labels(tmp_path,case), 'EXAMPLE', root=tmp_path)
    assert result['status'] == 'unrepresentable_unrun' and result['selection'] is None
    assert len(result['original_label']['synthetic_selections']) == 2
    assert result['original_expected'] == case['expected'] and result['expected_tasks'] == []


@pytest.mark.parametrize('fault', ['language','question','extra','selectors','selector_id','selector_kind','selector_extra',
    'duplicate_selector','task_id','duplicate_task','task_selection','outputs','duplicate_output','future_dependency',
    'count_bool','policy','disposition','permission','preserve','conditions','task_extra','direction','bound_bool',
    'bound_float','bound_text','bound_negative','bound_large','edge_large','missing_case','bad_hash','duplicate_case'])
def test_bad_label_is_rejected_without_compiling_partial_input(tmp_path, fault):
    from scripts.eval.semantic_task_routing import compile_task_label
    case = deepcopy(label())
    task = case['expected']['tasks'][0]
    if fault == 'language': case['language'] = True
    elif fault == 'question': case['question'] = ' '
    elif fault == 'extra': case['extra'] = 'lost data'
    elif fault == 'selectors': case['synthetic_selections'] = []
    elif fault == 'selector_id': case['synthetic_selections'][0]['selection_id'] = []
    elif fault == 'selector_kind': case['synthetic_selections'][0]['selection_type'] = 'unknown'
    elif fault == 'selector_extra': case['synthetic_selections'][0]['extra'] = 'lost data'
    elif fault == 'duplicate_selector': case['synthetic_selections'] *= 2
    elif fault == 'task_id': task['task_id'] = ''
    elif fault == 'duplicate_task': case['expected']['tasks'] *= 2; case['expected']['independent_task_count'] = 2
    elif fault == 'task_selection': task['selection_id'] = []
    elif fault == 'outputs': task['outputs'] = ['unknown']
    elif fault == 'duplicate_output': task['outputs'] = ['relations','relations']
    elif fault == 'future_dependency': task['depends_on'] = ['T2']
    elif fault == 'count_bool': case['expected']['independent_task_count'] = True
    elif fault == 'policy': case['expected']['planner_policy'] = 'unlimited'
    elif fault == 'disposition': case['expected']['disposition'] = []
    elif fault == 'permission': case['expected']['execution_permitted_after_valid_routing'] = 1
    elif fault == 'preserve': case['expected']['preserve_every_requested_intent'] = 1
    elif fault == 'conditions': case['expected']['conditions'] = [None]
    elif fault == 'task_extra': task['extra'] = 'lost data'
    elif fault == 'direction': case['synthetic_selections'][0]['graph_query']['direction'] = 'history'
    elif fault.startswith('bound_'):
        case['synthetic_selections'][0]['graph_query']['max_relations'] = {
            'bound_bool':True,'bound_float':120.0,'bound_text':'120','bound_negative':-1,'bound_large':501}[fault]
    elif fault == 'edge_large': case['synthetic_selections'][0]['graph_query']['max_generation_edges'] = 51
    ref = labels(tmp_path, case, case) if fault == 'duplicate_case' else labels(tmp_path,case)
    if fault == 'bad_hash': ref = {**ref,'sha256':'0'*64}
    with pytest.raises(ValueError):
        compile_task_label(ref, 'ABSENT' if fault == 'missing_case' else 'EXAMPLE', root=tmp_path)


@pytest.mark.parametrize('fault', ['drop','missing_selector','duplicate_selector','selection_list','forward_dependency',
    'child_question','branch_id','expected_dependency','expected_order'])
def test_declared_plan_cannot_drop_or_reorder_bindings(tmp_path, fault):
    from scripts.eval.semantic_task_routing import compile_task_label
    case = plan_label()
    branches = case['query_plan']['branches']
    if fault == 'drop': case['query_plan']['branches'] = branches[:1]
    elif fault == 'missing_selector': branches[1]['selection_id'] = 'missing'
    elif fault == 'duplicate_selector': branches[1]['selection_id'] = 'G'
    elif fault == 'selection_list': branches[1]['selection_id'] = []
    elif fault == 'forward_dependency': branches[0]['depends_on'] = ['compare']
    elif fault == 'child_question': branches[1]['question'] = ' '
    elif fault == 'branch_id': branches[1]['branch_id'] = 'contents'
    elif fault == 'expected_dependency': case['expected']['tasks'][1]['depends_on'] = []
    elif fault == 'expected_order': case['expected']['tasks'] = list(reversed(case['expected']['tasks']))
    with pytest.raises(ValueError):
        compile_task_label(labels(tmp_path,case), 'EXAMPLE', root=tmp_path)


def test_grader_consumes_genuine_runner_observation_without_claiming_actual_model(store,tmp_path):
    from scripts.eval.semantic_task_routing import compile_task_label
    from scripts.eval.run_fixed_math_v2 import run_fixed_case
    from mech_chatbot.config.repository_runtime import bind_repository_runtime
    from mech_chatbot.rag.execution import DefaultRagExecutor
    from .test_fixed_math_runner_v2 import BINDING, ACTOR, current_profile
    from .test_semantic_graph_task_routing import Planner, payload
    case_label = comparison_label()
    ref = labels(tmp_path,case_label)
    compiled = compile_task_label(ref,'EXAMPLE',root=tmp_path)
    case = {'schema':'fixed-task-routing-case-v3-draft', **BINDING, 'run_id':'synthetic','case_id':'EXAMPLE',
        'department':'ENG','dataset_sha256':ref['sha256'],'actor':ACTOR,'question':case_label['question'],
        'selection':compiled['selection'],'response_language':'en','task_routing_contract':GRAPH_TASK_ROUTING}
    case_ref = artifact(tmp_path,'case.json',case)
    approved_pair(store)
    planner = Planner(payload(case['question'],(('comparison','compare_two_scopes'),)))
    with bind_repository_runtime(db_engine=store):
        observed = run_fixed_case(case_ref,root=tmp_path,run_binding=BINDING,profile_loader=current_profile,
            executor=DefaultRagExecutor(provider_adapter=planner),task_routing_contract=GRAPH_TASK_ROUTING)
    result = grade(tmp_path,(ref,case_ref,artifact(tmp_path,'observation.json',observed),BINDING))
    assert observed['calculation_count'] == 4 and len(planner.calls) == 1
    assert result['routing_matches'] is True and result['model_evaluated'] == 'unknown'
    assert result['action_correctness'] == 'unrun' and result['execution_provenance'] == 'pending'


@pytest.mark.parametrize('disposition,quality', [('clarify','pass'),('unsupported','pass'),('invalid','fail'),('ready','fail')])
def test_invalid_decoder_is_not_valid_clarification(tmp_path,disposition,quality):
    case = deepcopy(label())
    case['expected'] = {**case['expected'],'disposition':'clarify_before_action','execution_permitted_after_valid_routing':False}
    case['expected']['tasks'][0]['outputs'] = ['relations','unsupported_quantity']
    result = grade(tmp_path,bound_artifacts(tmp_path,case,disposition))
    assert result['routing_quality'] == quality
    assert result['terminal_consistency'] == ('fail' if disposition == 'ready' else 'pass')
    assert result['unsupported_intent_fidelity'] == 'unobservable' and result['safety'] == 'unverified'


@pytest.mark.parametrize('fault', ['template','slot','outputs'])
def test_valid_but_wrong_observable_routing_is_a_semantic_failure(tmp_path,fault):
    refs = bound_artifacts(tmp_path)
    observed = json.loads((tmp_path/'observation.json').read_text())
    task = observed['task_routing']['tasks'][0]
    if fault == 'template': task['template'] = 'graph_where_used'
    elif fault == 'slot': task['selection_id'] = 'other'
    else: task['requested_outputs'] = ['relations']
    result = grade(tmp_path,(*refs[:2],artifact(tmp_path,'observation.json',observed),refs[3]))
    assert result['routing_quality'] == 'fail' and result['routing_matches'] is False


@pytest.mark.parametrize('fault', ['schema','extra','trace','case_hash','run','dataset','actor_bool','binding','language',
    'accepted','offline','verified','adjudicated','contract','routing_extra','routing_missing','routing_accepted',
    'routing_offline','planner_bool','planner_count','routing_invalid','duplicate_output','dependency',
    'calculation_bool','calculation_unrecorded','latency_nan','latency_bool','latency_negative','answer_type',
    'branch_schema','branches_type','math_type','citation_type','refused_answered'])
def test_observation_context_shape_and_claims_are_strict(tmp_path,fault):
    refs = bound_artifacts(tmp_path)
    observed = json.loads((tmp_path/'observation.json').read_text())
    modifications = {'schema':('schema','fixed-template-case-observation-v2'), 'extra':('model_evaluated',True),
        'trace':('trace_id','other'), 'case_hash':('case_sha256','0'*64),'run':('run_id','other'),
        'dataset':('dataset_sha256','0'*64),'binding':('gate_hash','0'*64),'language':('response_language','vi'),
        'accepted':('accepted',1),'offline':('offline_only',1),'verified':('binding_verification','verified'),
        'adjudicated':('adjudication_reference',{}),'contract':('task_routing_contract','old'),
        'calculation_bool':('calculation_count',False),'calculation_unrecorded':('calculation_count',1),
        'latency_nan':('latency_ms',float('nan')),'latency_bool':('latency_ms',True),
        'latency_negative':('latency_ms',-1),'answer_type':('answer',[]), 'branch_schema':('query_branch_schema','wrong'),
        'branches_type':('query_branches',{}),'math_type':('math_results',{}),'citation_type':('citations',[[]]),
        'refused_answered':('actual_outcome','full_answer')}
    if fault in modifications:
        key,value = modifications[fault]; observed[key] = value
    elif fault == 'actor_bool': observed['actor']['user_id'] = True
    elif fault == 'routing_extra': observed['task_routing']['private'] = 'provider text'
    elif fault == 'routing_missing': observed['task_routing'].pop('contract')
    elif fault == 'routing_accepted': observed['task_routing']['accepted'] = 0
    elif fault == 'routing_offline': observed['task_routing']['offline_only'] = 1
    elif fault == 'planner_bool': observed['task_routing']['planner_count'] = True
    elif fault == 'planner_count': observed['task_routing']['planner_count'] = 0
    elif fault == 'routing_invalid': observed['task_routing']['disposition'] = 'unbounded'
    elif fault == 'duplicate_output': observed['task_routing']['tasks'][0]['requested_outputs'] = ['relations','relations']
    elif fault == 'dependency': observed['task_routing']['tasks'][0]['depends_on'] = ['future']
    with pytest.raises(ValueError):
        grade(tmp_path,(*refs[:2],artifact(tmp_path,'observation.json',observed),refs[3]))


@pytest.mark.parametrize('fault', ['question','language','selection','bound','dataset','actor','schema','extra','hash'])
def test_bound_case_cannot_substitute_label_context(tmp_path,fault):
    refs = bound_artifacts(tmp_path)
    case = json.loads((tmp_path/'case.json').read_text())
    if fault == 'question': case['question'] = 'Different question.'
    elif fault == 'language': case['response_language'] = 'vi'
    elif fault == 'selection': case['selection']['graph_query']['product_scope'] = 'other'
    elif fault == 'bound': case['selection']['graph_query']['max_relations'] = 500
    elif fault == 'dataset': case['dataset_sha256'] = '0'*64
    elif fault == 'actor': case['actor']['user_id'] = True
    elif fault == 'schema': case['schema'] = 'fixed-template-case-v2'
    elif fault == 'extra': case['extra'] = True
    case_ref = {**refs[1],'sha256':'0'*64} if fault == 'hash' else artifact(tmp_path,'case.json',case)
    with pytest.raises(ValueError):
        grade(tmp_path,(refs[0],case_ref,refs[2],refs[3]))


@pytest.mark.parametrize('fault', ['hash','missing_core','duplicate','duplicate_windows','traversal','absolute',
    'drive_relative','empty','wrong_sha','missing_file','outside_symlink','entries_type','entry_extra'])
def test_source_capture_requires_confined_current_unique_critical_files(tmp_path,fault):
    from scripts.eval.semantic_task_routing import grade_task_observation
    refs = bound_artifacts(tmp_path)
    capture_ref, source_root = captured_sources(tmp_path)
    capture = json.loads((tmp_path/'capture.json').read_text())
    if fault == 'hash': capture_ref = {**capture_ref,'sha256':'0'*64}
    else:
        if fault == 'missing_core': capture['files'] = capture['files'][1:]
        elif fault == 'duplicate': capture['files'] += [capture['files'][0]]
        elif fault == 'duplicate_windows': capture['files'] += [{**capture['files'][0],'path':capture['files'][0]['path'].upper()}]
        elif fault == 'traversal': capture['files'][0]['path'] = '../outside.py'
        elif fault == 'absolute': capture['files'][0]['path'] = str(source_root/'scripts/eval/semantic_task_routing.py')
        elif fault == 'drive_relative': capture['files'][0]['path'] = 'C:escape.py'
        elif fault == 'empty': capture['files'] = []
        elif fault == 'wrong_sha': capture['files'][0]['sha256'] = '0'*64
        elif fault == 'missing_file': capture['files'][0]['path'] = 'missing.py'
        elif fault == 'outside_symlink':
            outside = tmp_path/'outside'; outside.mkdir()
            (outside/'external.py').write_text('outside')
            capture['files'][0]['path'] = '../outside/external.py'
        elif fault == 'entries_type': capture['files'] = {}
        elif fault == 'entry_extra': capture['files'][0]['extra'] = True
        capture_ref = artifact(tmp_path,'capture.json',capture)
    with pytest.raises(ValueError):
        grade_task_observation(*refs[:3],root=tmp_path,run_binding=refs[3],
            source_capture_reference=capture_ref,source_root=source_root)


@pytest.mark.parametrize('fault', [None,'shape','bool','future','duplicate','source_type'])
def test_exported_branch_shape_is_checked_without_evaluating_its_action(tmp_path,fault):
    refs = bound_artifacts(tmp_path)
    observed = json.loads((tmp_path/'observation.json').read_text())
    branch = {'branch_id':'contents','template':'graph_query','depends_on':[],'executed':True,
        'status':'unknown','data_complete':False,'reason':'source_unavailable','source_ids':[]}
    observed['query_branches'] = [branch]
    observed['query_branch_schema'] = 'query-branch-execution-v2'
    if fault == 'shape': branch.pop('reason')
    elif fault == 'bool': branch['executed'] = 1
    elif fault == 'future': branch['depends_on'] = ['future']
    elif fault == 'duplicate': observed['query_branches'] = [branch,branch]
    elif fault == 'source_type': branch['source_ids'] = [[]]
    refs = (*refs[:2],artifact(tmp_path,'observation.json',observed),refs[3])
    if fault:
        with pytest.raises(ValueError): grade(tmp_path,refs)
    else:
        assert grade(tmp_path,refs)['action_correctness'] == 'unrun'


def test_nonready_prefix_action_fails_terminal_consistency_without_hiding_routing_result(tmp_path):
    case = deepcopy(label())
    case['expected'] = {**case['expected'],'disposition':'clarify_before_action','execution_permitted_after_valid_routing':False}
    refs = bound_artifacts(tmp_path,case,'clarify')
    observed = json.loads((tmp_path/'observation.json').read_text())
    observed['citations'] = [{'source_id':'prefix-source'}]
    result = grade(tmp_path,(*refs[:2],artifact(tmp_path,'observation.json',observed),refs[3]))
    assert result['routing_quality'] == 'pass' and result['terminal_consistency'] == 'fail'
    assert result['safety'] == 'unverified' and result['accepted'] is False


@pytest.mark.parametrize('policy,planners,quality', [('reject_before_planner',0,'pass'),
    ('reject_before_planner',1,'fail'),('reject_before_action',0,'pass'),('reject_before_action',1,'pass')])
def test_rejection_policy_checks_planner_allowance_separately(tmp_path,policy,planners,quality):
    case = deepcopy(label())
    case['expected'] = {**case['expected'],'disposition':policy,'execution_permitted_after_valid_routing':False,
        'planner_policy':'none_before_rejection' if policy == 'reject_before_planner' else 'at_most_one_if_count_uncertain',
        'independent_task_count':4,'tasks':[{'task_id':f'T{i}','selection_id':'G','template':'graph_contains',
            'outputs':['relations'],'depends_on':[]} for i in range(1,5)]}
    refs = bound_artifacts(tmp_path,case,'overflow')
    observed = json.loads((tmp_path/'observation.json').read_text())
    observed['actual_outcome'] = 'clarification_required'
    observed['task_routing'] = {**observed['task_routing'],'planner_count':planners,
        'origin':'planner' if planners else 'fast_path','tasks':[
            {'selection_id':f'slot{i}','template':'graph_contains','requested_outputs':['relations'],'depends_on':[]}
            for i in range(1,5)] if planners else []}
    result = grade(tmp_path,(*refs[:2],artifact(tmp_path,'observation.json',observed),refs[3]))
    assert result['routing_quality'] == quality and result['terminal_consistency'] == 'pass'
    assert result['safety'] == 'unverified'


def test_grading_rechecks_source_bytes_before_returning(tmp_path,monkeypatch):
    from scripts.eval.semantic_task_routing import grade_task_observation
    refs = bound_artifacts(tmp_path)
    capture_ref, actual_root = captured_sources(tmp_path)
    capture = json.loads((tmp_path/'capture.json').read_text())
    source_root = tmp_path/'source'; source_root.mkdir()
    for item in capture['files']:
        target = source_root/item['path']; target.parent.mkdir(parents=True,exist_ok=True)
        target.write_bytes((actual_root/item['path']).read_bytes())
    reads = []
    original = Path.read_bytes
    def boundary_read(path):
        if path.name == 'observation.json':
            reads.append(True)
            if len(reads) == 2:
                (source_root/'scripts/eval/semantic_task_routing.py').write_bytes(b'changed during grade')
        return original(path)
    monkeypatch.setattr(Path,'read_bytes',boundary_read)
    with pytest.raises(ValueError,match='Source hash drift'):
        grade_task_observation(*refs[:3],root=tmp_path,run_binding=refs[3],
            source_capture_reference=capture_ref,source_root=source_root)


@pytest.mark.parametrize('fault', ['drop','order','dependency','child_question'])
def test_case_plan_preserves_every_branch_input_and_dependency(tmp_path,fault):
    from scripts.eval.semantic_task_routing import compile_task_label
    case_label = plan_label()
    refs = bound_artifacts(tmp_path,case_label)
    case = json.loads((tmp_path/'case.json').read_text())
    case['response_language'] = 'vi'
    case['selection'] = compile_task_label(refs[0],'EXAMPLE',root=tmp_path)['selection']
    if fault == 'drop': case['selection']['query_plan']['branches'] = case['selection']['query_plan']['branches'][:1]
    elif fault == 'order': case['selection']['query_plan']['branches'] = list(reversed(case['selection']['query_plan']['branches']))
    elif fault == 'dependency': case['selection']['query_plan']['branches'][1]['depends_on'] = []
    else: case['selection']['query_plan']['branches'][1]['question'] = 'Altered child.'
    with pytest.raises(ValueError):
        grade(tmp_path,(refs[0],artifact(tmp_path,'case.json',case),refs[2],refs[3]))


def test_compiler_and_grader_do_not_open_database_connections(tmp_path,monkeypatch):
    from sqlalchemy.engine import Engine
    from scripts.eval.semantic_task_routing import compile_task_label
    def forbidden(*_a,**_k): pytest.fail('Pure comparison must not read a database')
    monkeypatch.setattr(Engine,'connect',forbidden)
    refs = bound_artifacts(tmp_path)
    assert compile_task_label(refs[0],'EXAMPLE',root=tmp_path)['status'] == 'representable_unbound'
    assert grade(tmp_path,refs)['routing_matches'] is True
