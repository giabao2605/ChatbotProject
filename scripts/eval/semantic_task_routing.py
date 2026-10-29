"""Pure compilation and comparison of unaccepted candidate routing artifacts."""
from copy import deepcopy
from dataclasses import asdict, is_dataclass
import hashlib
import math
from pathlib import Path, PureWindowsPath
import re

from mech_chatbot.api.evidence_contracts import (
    GraphQueryRequest, BomRollupRequest, ScopeComparisonRequest, QueryPlanRequest,
)
from mech_chatbot.evaluation.grounded_math_v2 import _json_matches, _validate_routing_observation
from mech_chatbot.governance.artifact_references import load_json_reference
from mech_chatbot.rag.query_decomposition import GRAPH_TASK_ROUTING
from scripts.eval.run_fixed_math_v2 import _frozen_case, _case_request, _BINDINGS

__all__ = ['compile_task_label', 'grade_task_observation',
           'compile_candidate_task_label', 'grade_candidate_task_observation']
_CORE = frozenset(('scripts/eval/semantic_task_routing.py', 'scripts/eval/run_fixed_math_v2.py',
    'src/mech_chatbot/api/evidence_contracts.py', 'src/mech_chatbot/rag/query_decomposition.py',
    'src/mech_chatbot/rag/execution.py', 'src/mech_chatbot/evaluation/grounded_math_v2.py',
    'src/mech_chatbot/governance/artifact_references.py', 'src/mech_chatbot/evaluation/schema.py',
    'src/mech_chatbot/domain/graph_query.py', 'src/mech_chatbot/domain/query_plan.py'))


def _require(condition, reason):
    if not condition:
        raise ValueError(reason)


def _object(reference, root):
    value = load_json_reference(reference, root=root)
    _require(type(value) is dict, 'Hash-pinned JSON object required')
    return value


def _identifier(value):
    return type(value) is str and re.fullmatch(r'[A-Za-z0-9_-]{1,64}', value) is not None


def _text(value):
    return type(value) is str and bool(value.strip()) and len(value) <= 20000


def _unique(values):
    return type(values) is list and all(_identifier(v) for v in values) and len(set(values)) == len(values)


def _selector(value):
    _require(type(value) is dict and _identifier(value.get('selection_id')), 'Invalid selector identifier')
    kind = value.get('selection_type')
    codecs = {'graph_query':GraphQueryRequest, 'bom_rollup':BomRollupRequest, 'scope_comparison':ScopeComparisonRequest}
    _require(type(kind) is str and kind in codecs and set(value) == {'selection_id','selection_type',kind}, 'Invalid selector shape')
    payload = value[kind]
    if kind == 'scope_comparison':
        _require(type(payload) is list and len(payload) == 2, 'Comparison pair required')
        payload = {'scope_a':payload[0], 'scope_b':payload[1]}
    elif kind == 'bom_rollup':
        _require(type(payload) is dict and set(payload) <= {'graph','order_quantity','quantity_scope','rounding_scope'}
                 and {'graph','rounding_scope'} <= set(payload)
                 and type(payload['graph']) is dict, 'Nested BOM descriptor required')
        basis = {key:payload[key] for key in ('order_quantity','quantity_scope') if payload.get(key) is not None}
        _require(len(basis) == 1, 'One BOM quantity basis required')
        _require(not set(payload['graph']) & {'order_quantity','quantity_scope','rounding_scope'}, 'Lossy BOM descriptor')
        payload = {**payload['graph'], **basis, 'rounding_scope':payload['rounding_scope']}
    parsed = codecs[kind].model_validate(payload)
    if kind == 'bom_rollup':
        parsed.to_rollup()
    return {kind:deepcopy(payload)}


def _expected(value, selectors):
    fields = {'disposition','independent_task_count','ordered_selection_ids','tasks',
              'execution_permitted_after_valid_routing','planner_policy','preserve_every_requested_intent','conditions'}
    _require(type(value) is dict and fields <= set(value) <= fields | {'reason'}, 'Invalid expected policy')
    disposition = value['disposition']
    _require(type(disposition) is str and disposition in ('route','clarify_before_action','reject_before_planner','reject_before_action'), 'Invalid terminal policy')
    _require(type(value['independent_task_count']) is int and 1 <= value['independent_task_count'] <= 8
        and type(value['tasks']) is list and len(value['tasks']) == value['independent_task_count'], 'Invalid task count')
    _require(_unique(value['ordered_selection_ids']) and set(value['ordered_selection_ids']) <= set(selectors), 'Invalid ordered selectors')
    _require(value['execution_permitted_after_valid_routing'] is (disposition == 'route')
        and value['preserve_every_requested_intent'] is True, 'Invalid execution policy')
    policy = {'route':'at_most_one','clarify_before_action':'at_most_one',
              'reject_before_planner':'none_before_rejection','reject_before_action':'at_most_one_if_count_uncertain'}
    _require(value['planner_policy'] == policy[disposition] and type(value['conditions']) is list
             and all(_text(c) for c in value['conditions'])
             and ('reason' not in value or _text(value['reason'])), 'Invalid planner or conditions policy')
    seen = set()
    for task in value['tasks']:
        fields = {'task_id','selection_id','template','outputs','depends_on'}
        _require(type(task) is dict and fields <= set(task) <= fields | {'label_note'}, 'Invalid expected task shape')
        key = task['task_id']
        _require(_identifier(key) and key not in seen and (task['selection_id'] is None or
            _identifier(task['selection_id']) and task['selection_id'] in selectors)
            and _identifier(task['template']) and _unique(task['outputs']) and 1 <= len(task['outputs']) <= 32
            and _unique(task['depends_on']) and len(task['depends_on']) <= 2
            and set(task['depends_on']) <= seen and ('label_note' not in task or _text(task['label_note'])), 'Invalid expected task references')
        seen = seen | {key}


def _selection(case):
    descriptors = case['synthetic_selections']
    _require(type(descriptors) is list and 1 <= len(descriptors) <= 8, 'Bounded selectors required')
    parsed = [(d.get('selection_id') if type(d) is dict else None, _selector(d)) for d in descriptors]
    _require(len({key for key,_ in parsed}) == len(parsed), 'Duplicate selector identifiers')
    selectors = dict(parsed)
    _expected(case['expected'], selectors)
    if 'query_plan' not in case:
        if len(parsed) != 1:
            return None, {}, selectors
        key, selected = parsed[0]
        kind = next(iter(selected))
        return selected, {key:{'graph_query':'graph','bom_rollup':'bom','scope_comparison':'comparison'}[kind]}, selectors
    plan = case['query_plan']
    _require(type(plan) is dict and set(plan) == {'branches'} and type(plan['branches']) is list, 'Invalid declared plan')
    branches, slots = [], {}
    for branch in plan['branches']:
        _require(type(branch) is dict and set(branch) == {'branch_id','selection_id','question','depends_on'}
            and _identifier(branch['selection_id']) and branch['selection_id'] in selectors
            and branch['selection_id'] not in slots, 'Invalid plan selector references')
        slots = {**slots, branch['selection_id']:branch['branch_id']}
        branches = [*branches, {k:deepcopy(branch[k]) for k in ('branch_id','question','depends_on')} | selectors[branch['selection_id']]]
    _require(set(slots) == set(selectors), 'Declared plan must account for every selector')
    QueryPlanRequest.model_validate({'branches':branches})
    return {'query_plan':{'branches':branches}}, slots, selectors


def _project_expected(case, slots, selection):
    if case['expected']['disposition'] != 'route' or not slots:
        return [], {}
    tasks, task_slots = [], {}
    for task in case['expected']['tasks']:
        _require(_identifier(task['selection_id']) and task['selection_id'] in slots, 'Route requires a resolved selector')
        slot = slots[task['selection_id']]
        tasks = [*tasks, {'selection_id':slot, 'template':task['template'], 'requested_outputs':sorted(task['outputs']),
                      'depends_on':[task_slots[d] for d in task['depends_on']]}]
        task_slots = {**task_slots, task['task_id']:slot}
    _require(case['expected']['ordered_selection_ids'] == [t['selection_id'] for t in case['expected']['tasks']]
        and set(case['expected']['ordered_selection_ids']) == set(slots), 'Route must preserve every selected task')
    _validate_routing_observation({'schema':'task-routing-observation-v3','contract':GRAPH_TASK_ROUTING,
        'origin':'planner','disposition':'ready','tasks':tasks}, 1)
    _match_selected_tasks(tasks, selection)
    return tasks, task_slots


def _match_selected_tasks(tasks, selection):
    branches = selection['query_plan']['branches'] if 'query_plan' in selection else [selection]
    _require(len(branches) == len(tasks), 'Route selected action count mismatch')
    for task, branch in zip(tasks,branches):
        template = ('compare_two_scopes' if 'scope_comparison' in branch else
                    'bom_rollup' if 'bom_rollup' in branch else 'graph_' + branch['graph_query'].get('direction','contains'))
        _require(task['template'] == template and task['depends_on'] == branch.get('depends_on',[])
            and task['selection_id'] == branch.get('branch_id',task['selection_id']), 'Route differs from selected action or dependencies')


def compile_task_label(label_reference, case_id, *, root):
    """Compile a pinned synthetic label without inventing source or run bindings."""
    labels = _object(label_reference, root)
    _require(labels.get('artifact_type') == 'agent_authored_synthetic_semantic_routing_labels'
        and labels.get('contract_version') == GRAPH_TASK_ROUTING and type(labels.get('cases')) is list
        and 1 <= len(labels['cases']) <= 10000, 'Candidate label contract required')
    cases = labels['cases']
    _require(all(type(c) is dict and _identifier(c.get('case_id')) for c in cases)
             and len({c['case_id'] for c in cases}) == len(cases), 'Distinct bounded case identifiers required')
    matching = [c for c in cases if c['case_id'] == case_id]
    _require(_identifier(case_id) and len(matching) == 1, 'Requested case missing')
    case = matching[0]
    fields = {'case_id','language','question','synthetic_selections','semantic_tags','expected'}
    _require(fields <= set(case) <= fields | {'query_plan'} and type(case['language']) is str
        and case['language'] in ('vi','en') and _text(case['question']) and type(case['semantic_tags']) is list
        and all(_text(t) for t in case['semantic_tags']), 'Invalid label input')
    selection, slots, _ = _selection(case)
    tasks, task_slots = _project_expected(case, slots, selection)
    return deepcopy({'schema':'task-routing-label-compilation-v1', 'label_reference':label_reference,
        'case_id':case_id, 'task_routing_contract':GRAPH_TASK_ROUTING, 'question':case['question'],
        'response_language':case['language'], 'status':'representable_unbound' if selection else 'unrepresentable_unrun',
        'reason':None if selection else 'Multiple unresolved selectors require a declared resolved plan',
        'selection':selection, 'selector_slots':slots, 'task_slots':task_slots, 'expected_tasks':tasks,
        'original_expected':case['expected'], 'original_label':case, 'binding_verification':'missing',
        'accepted':False, 'offline_only':True})


def compile_candidate_task_label(label_reference, case_id, *, root):
    """Project original V3 synthetic labels into a separate V4 candidate input."""
    original = compile_task_label(label_reference, case_id, root=root)
    case = original['original_label']
    candidates = [{'selection_id': d['selection_id'], **_selector(d)} for d in case['synthetic_selections']]
    if 'query_plan' in case:
        declared = case['query_plan']['branches']
        keys = {b['branch_id']: b['selection_id'] for b in declared}
        context = {b['selection_id']: {'question': b['question'],
                   'depends_on': [keys[key] for key in b['depends_on']]} for b in declared}
        candidates = [{**c, **context[c['selection_id']]} for c in candidates]
    expected = case['expected']
    tasks = []
    if expected['disposition'] == 'route':
        _require(expected['ordered_selection_ids'] == [t['selection_id'] for t in expected['tasks']],
                 'Route requires consistent ordered selectors')
        task_ids = {t['task_id']: t['selection_id'] for t in expected['tasks']}
        tasks = [{'selection_id': t['selection_id'], 'template': t['template'],
                  'requested_outputs': sorted(t['outputs']),
                  'depends_on': [task_ids[key] for key in t['depends_on']]} for t in expected['tasks']]
        _validate_routing_observation({'schema':'task-routing-observation-v3','contract':GRAPH_TASK_ROUTING,
            'origin':'planner','disposition':'ready','tasks':tasks}, 1)
        selected = {c['selection_id']: c for c in candidates}
        _match_selected_tasks(tasks, {'query_plan': {'branches': [
            {'branch_id': t['selection_id'], **selected[t['selection_id']]} for t in tasks]}})
    return deepcopy({'schema':'candidate-task-routing-label-compilation-v4-draft',
        'source_label_contract':GRAPH_TASK_ROUTING, 'label_reference':label_reference,
        'case_id':case_id, 'task_routing_contract':'semantic-candidate-task-routing-v4-draft',
        'question':case['question'], 'response_language':case['language'], 'status':'representable_unbound',
        'selection':{'candidate_selection_context':{'candidates':candidates}}, 'expected_tasks':tasks,
        'original_expected':expected, 'original_label':case, 'binding_verification':'missing',
        'accepted':False, 'offline_only':True})


def _verify_source_capture(reference, root, source_root, required_paths=_CORE):
    capture = _object(reference, root)
    files = capture.get('files')
    _require(type(files) is list and 1 <= len(files) <= 10000, 'Bounded source capture required')
    base, identities = Path(source_root).resolve(strict=True), set()
    for item in files:
        _require(type(item) is dict and set(item) == {'path','sha256'} and type(item['path']) is str
            and type(item['sha256']) is str and re.fullmatch(r'[0-9a-f]{64}', item['sha256']), 'Invalid source entry')
        name = item['path']
        parts = PureWindowsPath(name).parts
        _require(bool(parts) and not PureWindowsPath(name).drive and not PureWindowsPath(name).root
            and not any(p in ('.','..') or ':' in p for p in name.replace('\\','/').split('/')), 'Confined relative source path required')
        try:
            target = (base / Path(*parts)).resolve(strict=True)
        except OSError as exc:
            raise ValueError('Captured source unavailable') from exc
        _require(target.is_relative_to(base) and target.is_file(), 'Source outside capture root')
        identity = str(target.relative_to(base)).replace('\\','/').casefold()
        _require(identity not in identities, 'Duplicate canonical source path')
        identities = identities | {identity}
        _require(hashlib.sha256(target.read_bytes()).hexdigest() == item['sha256'], 'Source hash drift')
    _require(required_paths <= identities, 'Capture missing required source seams')
    return capture


def _domain_selection(request):
    return {key:asdict(v) if is_dataclass(v) else v for key in (
        'scope_comparison','graph_query','bom_rollup','query_plan') if (v := getattr(request,key)) is not None}


def _validate_branches(branches):
    seen = set()
    fields = {'branch_id','template','depends_on','executed','status','data_complete','reason','source_ids'}
    for branch in branches:
        _require(type(branch) is dict and set(branch) == fields and _identifier(branch['branch_id'])
            and branch['branch_id'] not in seen and _identifier(branch['template'])
            and _unique(branch['depends_on']) and len(branch['depends_on']) <= 2
            and set(branch['depends_on']) <= seen and type(branch['executed']) is bool
            and type(branch['data_complete']) is bool and _text(branch['status'])
            and (branch['reason'] is None or _text(branch['reason']))
            and type(branch['source_ids']) is list and all(_text(s) for s in branch['source_ids'])
            and len(set(branch['source_ids'])) == len(branch['source_ids']), 'Invalid exported branch shape')
        seen = seen | {branch['branch_id']}


def _validate_observation(observed, case, case_reference, *, candidate=False):
    joined = (*_BINDINGS,'run_id','case_id','department','dataset_sha256','actor','task_routing_contract','response_language')
    fields = {'schema',*joined,'trace_id','case_sha256','task_routing','math_results','calculation_count',
              'query_branches','query_branch_schema','answer','citations','answer_outcome','actual_outcome',
              'accepted','offline_only','latency_ms','binding_verification','adjudication_reference'}
    schema = 'fixed-task-routing-case-observation-v4-draft' if candidate else 'fixed-task-routing-case-observation-v3-draft'
    _require(set(observed) == fields and observed['schema'] == schema
        and all(_json_matches(observed[k],case[k]) for k in joined)
        and observed['case_sha256'] == case_reference['sha256']
        and observed['trace_id'] == f"fixed:{case['run_id']}:{case['case_id']}", 'Observation context mismatch')
    _require(observed['accepted'] is False and observed['offline_only'] is True
        and observed['binding_verification'] == 'pending' and observed['adjudication_reference'] is None,
        'Candidate observation must remain unaccepted')
    _require(type(observed['answer']) is str and type(observed['latency_ms']) in (int,float)
        and math.isfinite(observed['latency_ms']) and observed['latency_ms'] >= 0
        and type(observed['answer_outcome']) is str and observed['answer_outcome'] in ('full_answer','partial_answer','refused')
        and type(observed['actual_outcome']) is str and observed['actual_outcome'] in {
            'full_answer':('full_answer',),'partial_answer':('partial_answer',),
            'refused':('insufficient_evidence','clarification_required')}[observed['answer_outcome']],
        'Invalid observed terminal shape')
    _require(type(observed['calculation_count']) is int and 0 <= observed['calculation_count'] <= 508
        and type(observed['math_results']) is list and len(observed['math_results']) <= 3
        and all(type(r) is dict and r.get('schema') == 'grounded-calculation-v2'
            and type(r.get('domain_step_count')) is int and type(r.get('steps')) is list
            and r['domain_step_count'] == len(r['steps']) for r in observed['math_results'])
        and sum(r['domain_step_count'] for r in observed['math_results']) == observed['calculation_count']
        and type(observed['citations']) is list and all(type(c) is dict for c in observed['citations'])
        and type(observed['query_branches']) is list and len(observed['query_branches']) <= 3
        and observed['query_branch_schema'] == ('query-branch-execution-v2' if observed['query_branches'] else None),
        'Invalid exported action shape')
    _validate_branches(observed['query_branches'])
    routing = observed['task_routing']
    keys = {'schema','contract','origin','disposition','tasks','planner_count','accepted','offline_only'}
    if candidate:
        keys = keys | {'candidate_context_sha256', 'resolved_request_sha256'}
    _require(type(routing) is dict and set(routing) == keys and routing['accepted'] is False
        and routing['offline_only'] is True, 'Invalid exported routing shape')
    validator = _validate_routing_observation
    if candidate:
        from mech_chatbot.evaluation.grounded_math_v2 import _validate_candidate_routing_observation
        validator = _validate_candidate_routing_observation
    validator({k:v for k,v in routing.items() if k not in (
        'planner_count','accepted','offline_only')}, routing['planner_count'])
    return routing


def _routing_checks(compiled, observed, routing):
    policy = compiled['original_expected']['disposition']
    if policy == 'route':
        tasks = [{**t,'requested_outputs':sorted(t['requested_outputs'])} for t in routing['tasks']]
        checks = {'disposition':routing['disposition'] == 'ready',
                  'ordered_tasks':_json_matches(tasks, compiled['expected_tasks'])}
        return checks, 'not_applicable'
    disposition = routing['disposition']
    allowed = ('clarify','unsupported') if policy == 'clarify_before_action' else ('overflow',)
    checks = {'disposition':disposition in allowed,
        'planner_allowance':routing['planner_count'] == 0 if policy == 'reject_before_planner' else routing['planner_count'] <= 1}
    empty = (observed['answer_outcome'] == 'refused' and observed['actual_outcome'] in (
        'insufficient_evidence','clarification_required') and observed['calculation_count'] == 0
        and observed['math_results'] == observed['citations'] == observed['query_branches'] == [])
    return checks, 'pass' if empty and disposition != 'ready' else 'fail'


def grade_task_observation(label_reference, case_reference, observation_reference, *, root,
                           run_binding, source_capture_reference, source_root):
    """Compare pinned routing declarations without claiming execution or acceptance."""
    capture = _verify_source_capture(source_capture_reference, root, source_root)
    case = _frozen_case(case_reference, root, run_binding, GRAPH_TASK_ROUTING)
    observed = _object(observation_reference, root)
    compiled = compile_task_label(label_reference, case['case_id'], root=root)
    _require(compiled['status'] == 'representable_unbound', 'Unrepresentable input is not runnable')
    _require(case['dataset_sha256'] == label_reference['sha256'] and case['question'] == compiled['question']
        and case['response_language'] == compiled['response_language'], 'Case label context mismatch')
    expected_request = _case_request({**case,'selection':compiled['selection']})
    _require(_json_matches(_domain_selection(_case_request(case)), _domain_selection(expected_request)),
             'Case selector or branch context mismatch')
    routing = _validate_observation(observed, case, case_reference)
    checks, terminal = _routing_checks(compiled, observed, routing)
    for reference in (label_reference, case_reference, observation_reference):
        _object(reference, root)
    _require(_json_matches(_verify_source_capture(source_capture_reference, root, source_root),capture), 'Source capture changed')
    matches = all(checks.values())
    return deepcopy({'schema':'task-routing-comparison-v1','case_id':case['case_id'],
        'label_reference':label_reference,'case_reference':case_reference,'observation_reference':observation_reference,
        'source_capture_reference':source_capture_reference,'selector_slots':compiled['selector_slots'],
        'task_slots':compiled['task_slots'],'checks':checks,'input_fidelity':'verified',
        'routing_quality':'pass' if matches else 'fail','routing_matches':matches,
        'terminal_consistency':terminal,'safety':'unverified','action_correctness':'unrun',
        'unsupported_intent_fidelity':'unobservable','execution_provenance':'pending','model_evaluated':'unknown',
        'source_consistency':'verified_declared_files','binding_verification':'pending',
        'accepted':False,'offline_only':True})


def grade_candidate_task_observation(label_reference, case_reference, observation_reference, *, root,
                                     run_binding, source_capture_reference, source_root):
    """Compare the new candidate contract without reinterpreting V3 observations."""
    from mech_chatbot.domain.task_routing import CANDIDATE_TASK_ROUTING
    from mech_chatbot.rag.candidate_task_routing import candidate_context_sha256

    paths = _CORE | {'src/mech_chatbot/domain/task_routing.py', 'src/mech_chatbot/rag/candidate_task_routing.py',
                     'src/mech_chatbot/rag/pipeline.py', 'src/mech_chatbot/rag/query_plan.py'}
    capture = _verify_source_capture(source_capture_reference, root, source_root, paths)
    case = _frozen_case(case_reference, root, run_binding, CANDIDATE_TASK_ROUTING)
    observed = _object(observation_reference, root)
    compiled = compile_candidate_task_label(label_reference, case['case_id'], root=root)
    _require(case['dataset_sha256'] == label_reference['sha256'] and case['question'] == compiled['question']
        and case['response_language'] == compiled['response_language'], 'Case label context mismatch')
    actual = _case_request(case)
    expected = _case_request({**case, 'selection': compiled['selection']})
    _require(_json_matches(asdict(actual.candidate_selection_context), asdict(expected.candidate_selection_context)),
             'Case candidate or declared branch context mismatch')
    routing = _validate_observation(observed, case, case_reference, candidate=True)
    _require(routing['candidate_context_sha256'] == candidate_context_sha256(actual),
             'Candidate context binding mismatch')
    checks, terminal = _routing_checks(compiled, observed, routing)
    for reference in (label_reference, case_reference, observation_reference):
        _object(reference, root)
    _require(_json_matches(_verify_source_capture(source_capture_reference, root, source_root, paths), capture),
             'Source capture changed')
    matches = all(checks.values())
    return deepcopy({'schema':'candidate-task-routing-comparison-v4-draft', 'case_id':case['case_id'],
        'label_reference':label_reference, 'case_reference':case_reference, 'observation_reference':observation_reference,
        'source_capture_reference':source_capture_reference, 'source_label_contract':GRAPH_TASK_ROUTING,
        'checks':checks, 'input_fidelity':'verified', 'routing_quality':'pass' if matches else 'fail',
        'routing_matches':matches, 'terminal_consistency':terminal, 'safety':'unverified',
        'action_correctness':'unrun', 'unsupported_intent_fidelity':'unobservable',
        'execution_provenance':'pending', 'model_evaluated':'unknown',
        'source_consistency':'verified_declared_files', 'binding_verification':'pending',
        'accepted':False, 'offline_only':True})
