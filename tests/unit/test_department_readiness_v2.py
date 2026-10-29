from copy import deepcopy
import hashlib
import json

import pytest

from mech_chatbot.evaluation.department_readiness import (
    CANONICAL_DEPARTMENTS, REQUIRED_ARMS, evaluate_department_readiness, required_arm_limits,
)


def reference(root, name, payload):
    path = root / f"{name}.json"
    raw = json.dumps(payload, sort_keys=True).encode()
    path.write_bytes(raw)
    return {"path": path.name, "sha256": hashlib.sha256(raw).hexdigest(), "schema": payload["schema"]}


def ready_fixture(root, *, technical_math=None, technical_outcome='full_answer', bom_departments=('Technical','Production'),
                  technical_other_branches=(), technical_query_branches=None):
    from dataclasses import asdict
    from .test_grounded_math_v2 import scope
    from mech_chatbot.rag.grounded_math_v2 import compare_two_scopes,serialize_result
    binding = {"source_commit": "a" * 40, "snapshot_fingerprint": "b" * 64,
               "config_hash": "c" * 64, "gate_hash": "d" * 64, "eval_hash": "e" * 64}
    result = {"schema": "department-readiness-v2", **binding,
              "baseline_source_commit": "f" * 40,
              "catalog": [{"code": c, "active": True} for c in CANONICAL_DEPARTMENTS],
              "departments": [], "runs": []}
    all_cases = []
    for code in CANONICAL_DEPARTMENTS:
        owner = {"identity": f"{code}-owner", "verified": True, "synthetic": False}
        owner["verification"] = reference(root, f"{code}-identity", {
            "schema": "business-identity-v1", "identity": owner["identity"],
            "department": code, "active": True, "synthetic": False,
        })
        questions = [{"id": f"{code}-{i}", "meaning_key": f"meaning-{i}", "question": f"Question {i}",
                      "ground_truth": "Reference answer", "citations": [{"doc_id": 1, "version": 1, "page": 1, "source_id": "s1"}],
                      "actor": {"user_department": code, "user_roles": ["viewer"],
                                "allowed_departments": [code], "allowed_sites": [], "max_security_level": "internal"},
                      "critical": i == 0, "math": i == 0, "expected_outcome": "full_answer"}
                     for i in range(75)]
        scopes=(scope('A',['10']),scope('B',['15']))
        math_result=serialize_result(compare_two_scopes(*scopes))
        math_results=[math_result]
        math_inputs=[{'template':'compare_two_scopes',
                      'scopes':json.loads(json.dumps([asdict(s) for s in scopes],default=str))}]
        if code in bom_departments:
            inputs,actual=bom_fixture(1)
            math_inputs,math_results=([inputs],[actual])
        if code=='Technical' and technical_math is not None:
            math_inputs,math_results=technical_math
            questions[0]['expected_outcome']=technical_outcome
        questions[0]['math_reference']=reference(root,f'{code}-math-reference',{
            'schema':'fixed-template-math-reference-v2',**binding,'department':code,
            'case_id':questions[0]['id'],'reviewer_id':owner['identity'],
            'records':math_inputs,'other_branches':list(technical_other_branches) if code=='Technical' else [],
            **({'query_branch_schema':'query-branch-execution-v2','query_branches':technical_query_branches}
               if code=='Technical' and technical_query_branches is not None else {})})
        dataset = reference(root, f"{code}-dataset", {
            "schema": "department-dataset-v2", "frozen": True, "real": True,
            "department": code, "reviewer_id": owner["identity"], "questions": questions,
        })
        cases = [{"case_id": q["id"], "department": code, "dataset_sha256": dataset["sha256"],
                  "expected_outcome": q['expected_outcome'], "actual_outcome": q['expected_outcome'],
                  "answer_correct": True, "citation_correct": True, "leaked": False,
                  "wrong_version": False, "answer": "Reference answer", "citations": q["citations"],
                  "reviewer_id": owner["identity"], "latency_ms": 100, "estimated_cost": 0.01,
                  **binding} for q in questions]
        cases=[{**case,'calculation_count':sum(r['domain_step_count'] for r in math_results) if q['math'] else 0,
                'math_results':math_results if q['math'] else [],
                'other_branches':list(technical_other_branches) if code=='Technical' and q['math'] else [],
                **({'query_branch_schema':'query-branch-execution-v2','query_branches':technical_query_branches}
                   if code=='Technical' and q['math'] and technical_query_branches is not None else {})}
               for case,q in zip(cases,questions)]
        verification = reference(root, f"{code}-cases", {"schema": "department-case-evaluation-v2", "cases": cases})
        result["departments"].append({"code": code, "owner": owner, "approver": owner,
                                       "questions": questions, "dataset_reference": dataset,
                                       "verification": verification})
        all_cases.extend(cases)
    result["contract_reference"] = reference(root, "contract", {
        "schema": "rag-integration-contract-v2", "frozen": True, "approved": True,
        "baseline_source_commit": result['baseline_source_commit'],
        "reviewer_id": result["departments"][0]["approver"]["identity"], **binding,
        "limits": {arm: required_arm_limits(arm) for arm in REQUIRED_ARMS},
        "baseline_limits": {arm: required_arm_limits(arm,variant='baseline') for arm in REQUIRED_ARMS},
    })
    for arm in REQUIRED_ARMS:
        for concurrency in (1, 5):
            for variant in ("baseline", "candidate"):
                run = {"arm": arm, "concurrency": concurrency, "variant": variant, **binding,
                       'source_commit':result['baseline_source_commit'] if variant=='baseline' else binding['source_commit']}
                evaluation = reference(root, f"{arm}-{concurrency}-{variant}-evaluation", {
                    "schema": "department-arm-evaluation-v2", **run,
                    "cases": all_cases if variant=='candidate' else [
                        {**{k:v for k,v in c.items() if k!='math_results'},
                         'source_commit':result['baseline_source_commit'],
                         'calculation_count':1 if c['calculation_count'] else 0} for c in all_cases],
                })
                gate = reference(root, f"{arm}-{concurrency}-{variant}-gate", {
                    "schema": "department-arm-gate-v2", **run, "passed": True,
                    "checks": {"source": True, "quality": True}, "limits": required_arm_limits(arm,variant=variant),
                    "evaluation_sha256": evaluation["sha256"],
                })
                result["runs"].append({**run, "evaluation_reference": evaluation, "gate_reference": gate})
    return result


def test_readiness_validates_all_fourteen_and_offline_is_never_acceptance(tmp_path):
    result = evaluate_department_readiness(ready_fixture(tmp_path), root=tmp_path)
    assert result["offline_passed"]
    assert result["department_count"] == 14
    assert not result["accepted"]
    assert result["matrix"]["required_run_count"] == 28


def test_math_v2_step_budget_does_not_retain_contradictory_v1_one_step_limit():
    from scripts.eval.retrieval_intelligence_gate import compare

    limits = required_arm_limits('math')
    assert limits['math_budget_version'] == 'fixed-template-math-budgets-v2'
    assert limits['max_domain_steps_per_template'] == {'compare_two_scopes':4,'bom_rollup':500}
    assert limits['max_recorded_domain_steps_per_request'] == 500
    assert required_arm_limits('math+query')['max_recorded_domain_steps_per_request'] == 508
    assert 'max_calculations' not in limits
    assert 'max_calculations_per_query' not in limits
    assert compare('grounded_math', {}, {})['limits']['max_calculations_per_query'] == 1
    limits['max_cost_ratio'] = 900
    assert required_arm_limits('math')['max_cost_ratio'] == 1.5


def test_baseline_limits_retain_the_original_gate_contract():
    from scripts.eval.retrieval_intelligence_gate import compare
    assert required_arm_limits('math',variant='baseline')==compare('grounded_math',{}, {})['limits']
    assert required_arm_limits('math+query',variant='baseline')==compare('integrated_hardening',{}, {})['limits']
    assert required_arm_limits('graph',variant='baseline')==compare('graph_retrieval',{}, {})['limits']


@pytest.mark.parametrize("change", [
    lambda p: p["catalog"].pop(), lambda p: p["departments"].pop(),
    lambda p: p["departments"][0]["questions"].pop(),
    lambda p: p["departments"][0]["owner"].update(synthetic=True),
    lambda p: p["departments"][0]["approver"].update(verified=False),
    lambda p: p["departments"][0]["questions"][1].update(meaning_key="meaning-0"),
    lambda p: p["departments"][0]["questions"][0].update(ground_truth="Changed"),
    lambda p: p["runs"].pop(), lambda p: p["runs"][0].update(source_commit="0" * 40),
    lambda p: p["contract_reference"].update(sha256="f" * 64),
])
def test_missing_real_evidence_or_unmatched_bindings_are_not_ready(tmp_path, change):
    data = ready_fixture(tmp_path)
    change(data)
    assert not evaluate_department_readiness(data, root=tmp_path)["offline_passed"]


def test_manifest_correct_flags_do_not_bypass_missing_evidence(tmp_path):
    data = ready_fixture(tmp_path)
    data["departments"][0]["verification"] = None
    for question in data["departments"][0]["questions"]:
        question.update(correct=True, actual_outcome="full_answer")
    assert not evaluate_department_readiness(data, root=tmp_path)["offline_passed"]


def test_candidate_math_without_per_step_evidence_never_passes_a_claimed_gate(tmp_path):
    data=ready_fixture(tmp_path)
    run=next(r for r in data['runs'] if r['arm']=='math' and r['variant']=='candidate')
    raw=json.loads((tmp_path/run['evaluation_reference']['path']).read_text())
    raw['cases'][0].pop('math_results')
    run['evaluation_reference']=reference(tmp_path,'missing-math-result',raw)
    gate=json.loads((tmp_path/run['gate_reference']['path']).read_text())
    gate['evaluation_sha256']=run['evaluation_reference']['sha256']
    run['gate_reference']=reference(tmp_path,'missing-math-gate',gate)
    report=evaluate_department_readiness(data,root=tmp_path)
    assert not report['offline_passed']
    assert any('math_evidence_or_budget' in reason for reason in report['matrix']['failures'])


@pytest.mark.parametrize('tamper',[
    lambda c:c.update(calculation_count=True),
    lambda c:c.update(calculation_count=1),
    lambda c:c.update(calculation_count=-1),
    lambda c:c.update(math_results='invalid'),
    lambda c:c.update(citations=None),
    lambda c:c['math_results'][0].update(domain_step_count=True),
    lambda c:c['math_results'][0].update(domain_step_count=1),
    lambda c:c['math_results'][0].update(template='expression'),
    lambda c:c['math_results'][0].update(template=[]),
    lambda c:c['math_results'][0].update(template={}),
    lambda c:c['math_results'][0]['steps'].append(c['math_results'][0]['steps'][0]),
    lambda c:c['math_results'][0]['steps'][0].update(value='999'),
    lambda c:c['math_results'][0]['steps'][0].update(unit='kg'),
    lambda c:c['math_results'][0]['steps'][0].update(display_value='10.01'),
    lambda c:c['math_results'][0]['sources'][0].update(snapshot_id='stale'),
    lambda c:c['math_results'][0]['sources'][0]['facts'][0]['locator'].update(version=True),
    lambda c:c['math_results'][0]['rounding_rule'].update(scale=2.0),
])
def test_claimed_math_gate_cannot_hide_bad_counts_values_units_or_sources(tmp_path,tamper):
    data=ready_fixture(tmp_path)
    run=next(r for r in data['runs'] if r['arm']=='math' and r['variant']=='candidate')
    raw=json.loads((tmp_path/run['evaluation_reference']['path']).read_text())
    tamper(raw['cases'][0])
    run['evaluation_reference']=reference(tmp_path,'tampered-math-result',raw)
    gate=json.loads((tmp_path/run['gate_reference']['path']).read_text())
    gate['evaluation_sha256']=run['evaluation_reference']['sha256']
    run['gate_reference']=reference(tmp_path,'tampered-math-gate',gate)
    report=evaluate_department_readiness(data,root=tmp_path)
    assert not report['offline_passed']
    assert any('math_evidence_or_budget' in reason for reason in report['matrix']['failures'])


def bom_fixture(count):
    from decimal import Decimal
    from .test_grounded_math_v2 import manifest
    from mech_chatbot.domain.structured_evidence import RoundingRule
    from mech_chatbot.rag.grounded_math_v2 import bom_rollup,serialize_result
    source=manifest(); occurrence=source['occurrences'][0]
    data={**source,'expected_occurrence_ids':tuple(f'part-{i}' for i in range(count)),
          'occurrences':tuple({**occurrence,'occurrence_id':f'part-{i}','part_key':f'part-{i}',
                               'quantity_path':('2',)} for i in range(count))}
    inputs={'template':'bom_rollup','manifest':data,'order_quantity':'3',
            'rounding_rule':{'scale':0,'mode':'ROUND_HALF_UP','reviewed':True}}
    actual=serialize_result(bom_rollup(data,Decimal('3'),RoundingRule(0,reviewed=True)))
    assert all(step['value']=='6' for step in actual['steps'])
    return inputs,actual


@pytest.mark.parametrize('count,passes',[(51,True),(500,True),(501,False)])
def test_bom_uses_its_own_full_manifest_budget_instead_of_comparison_or_context_limit(tmp_path,count,passes):
    inputs,actual=bom_fixture(count)
    data=ready_fixture(tmp_path,technical_math=([inputs],[actual]))
    report=evaluate_department_readiness(data,root=tmp_path)
    assert report['offline_passed']==passes
    assert not report['accepted']


def test_example_budget_contract_matches_code_and_stays_unapproved():
    from pathlib import Path
    contract=json.loads((Path(__file__).resolve().parents[2]/'docs/examples/advanced-rag-integration-contract-v2.example.json').read_text(encoding='utf-8'))
    assert contract['limits']=={arm:required_arm_limits(arm) for arm in REQUIRED_ARMS}
    assert contract['baseline_limits']=={arm:required_arm_limits(arm,variant='baseline') for arm in REQUIRED_ARMS}
    assert contract['approved'] is False and contract['frozen'] is False and contract['accepted'] is False


def test_math_stack_needs_a_positive_case_of_both_fixed_templates(tmp_path):
    report=evaluate_department_readiness(ready_fixture(tmp_path,bom_departments=()),root=tmp_path)
    assert not report['offline_passed']
    assert any('math_templates_missing' in reason for reason in report['matrix']['failures'])


@pytest.mark.parametrize('sizes,comparison_count,passes',[
    ([500],2,True),([501],2,False),([251,250],1,False),([],3,True),([],4,False),
])
def test_query_math_enforces_selected_templates_and_the_shared_request_budget(tmp_path,sizes,comparison_count,passes):
    from dataclasses import asdict
    from .test_grounded_math_v2 import scope
    from mech_chatbot.rag.grounded_math_v2 import compare_two_scopes,serialize_result
    scopes=(scope('A',['10']),scope('B',['15']))
    comparison=({'template':'compare_two_scopes','scopes':json.loads(json.dumps([asdict(s) for s in scopes],default=str))},
                serialize_result(compare_two_scopes(*scopes)))
    pairs=[bom_fixture(size) for size in sizes]+[comparison]*comparison_count
    data=ready_fixture(tmp_path,technical_math=([p[0] for p in pairs],[p[1] for p in pairs]))
    report=evaluate_department_readiness(data,root=tmp_path)
    failures=[f for f in report['matrix']['failures'] if "'math+query'," in f and ':Technical:math_evidence_or_budget' in f]
    assert (not failures)==passes
    assert not report['accepted']


@pytest.mark.parametrize('expected,passes',[('full_answer',False),('partial_answer',False),
                                           ('clarification_required',False),('insufficient_evidence',True)])
def test_math_refusal_must_match_the_frozen_business_outcome(tmp_path,expected,passes):
    from dataclasses import asdict,replace
    from .test_grounded_math_v2 import scope
    from mech_chatbot.rag.grounded_math_v2 import compare_two_scopes,serialize_result
    scopes=(scope('A',['10']),replace(scope('B',['15']),authorized=False))
    inputs={'template':'compare_two_scopes','scopes':json.loads(json.dumps([asdict(s) for s in scopes],default=str))}
    actual=serialize_result(compare_two_scopes(*scopes))
    data=ready_fixture(tmp_path,technical_math=([inputs],[actual]),technical_outcome=expected)
    report=evaluate_department_readiness(data,root=tmp_path)
    assert report['offline_passed']==passes


@pytest.mark.parametrize('bad_count',[True,-1,2])
def test_baseline_keeps_its_one_calculation_limit_even_if_the_gate_claims_pass(tmp_path,bad_count):
    data=ready_fixture(tmp_path)
    run=next(r for r in data['runs'] if r['arm']=='math' and r['variant']=='baseline')
    raw=json.loads((tmp_path/run['evaluation_reference']['path']).read_text())
    raw['cases'][0]['calculation_count']=bad_count
    run['evaluation_reference']=reference(tmp_path,'bad-legacy-count',raw)
    gate=json.loads((tmp_path/run['gate_reference']['path']).read_text())
    gate['evaluation_sha256']=run['evaluation_reference']['sha256']
    run['gate_reference']=reference(tmp_path,'bad-legacy-gate',gate)
    report=evaluate_department_readiness(data,root=tmp_path)
    assert not report['offline_passed']
    assert any('legacy_math_budget_failed' in f for f in report['matrix']['failures'])


def test_zero_denominator_is_a_valid_partial_math_answer(tmp_path):
    from dataclasses import asdict
    from .test_grounded_math_v2 import scope
    from mech_chatbot.rag.grounded_math_v2 import compare_two_scopes,serialize_result
    scopes=(scope('A',['0']),scope('B',['15']))
    inputs={'template':'compare_two_scopes','scopes':json.loads(json.dumps([asdict(s) for s in scopes],default=str))}
    actual=serialize_result(compare_two_scopes(*scopes))
    assert actual['domain_step_count']==4 and actual['steps'][-1]['value'] is None
    report=evaluate_department_readiness(ready_fixture(tmp_path,technical_math=([inputs],[actual]),
        technical_outcome='partial_answer'),root=tmp_path)
    assert report['offline_passed'] and not report['accepted']


def test_clarification_has_zero_executed_math_actions(tmp_path):
    report=evaluate_department_readiness(ready_fixture(tmp_path,technical_math=([],[]),
        technical_outcome='clarification_required'),root=tmp_path)
    assert report['offline_passed'] and not report['accepted']


@pytest.mark.parametrize('source_ids,passes',[(['s1'],True),(['hidden-source'],False),([],False)])
def test_partial_query_can_answer_an_independent_grounded_branch_when_math_is_blocked(tmp_path,source_ids,passes):
    from dataclasses import asdict,replace
    from .test_grounded_math_v2 import scope
    from mech_chatbot.rag.grounded_math_v2 import compare_two_scopes,serialize_result
    scopes=(scope('A',['10']),replace(scope('B',['15']),authorized=False))
    inputs={'template':'compare_two_scopes','scopes':json.loads(json.dumps([asdict(s) for s in scopes],default=str))}
    actual=serialize_result(compare_two_scopes(*scopes))
    other={'branch_id':'g1','kind':'graph','status':'complete','source_ids':source_ids}
    data=ready_fixture(tmp_path,technical_math=([inputs],[actual]),technical_outcome='partial_answer',
        technical_other_branches=(other,))
    report=evaluate_department_readiness(data,root=tmp_path)
    failures=[f for f in report['matrix']['failures'] if "'math+query'," in f and ':Technical:math_evidence_or_budget' in f]
    assert (not failures)==passes
    assert not report['accepted']


def skipped_math_branches():
    return [
        {'branch_id':'relations','template':'graph_query','depends_on':[], 'executed':True,
         'status':'partial','data_complete':False,'reason':None,'source_ids':['s1']},
        {'branch_id':'total','template':'bom_rollup','depends_on':['relations'],'executed':False,
         'status':'blocked','data_complete':False,'reason':'dependency_unavailable','source_ids':[]},
    ]


def test_query_gate_accepts_dependency_skipped_math_without_fabricating_a_result(tmp_path):
    data=ready_fixture(tmp_path,technical_math=([],[]),technical_outcome='partial_answer',
                       technical_query_branches=skipped_math_branches())
    report=evaluate_department_readiness(data,root=tmp_path)
    assert not any("'math+query'," in f and ':Technical:math_evidence_or_budget' in f
                   for f in report['matrix']['failures'])
    assert any("'math'," in f and ':Technical:math_evidence_or_budget' in f
               for f in report['matrix']['failures'])
    assert not report['accepted']


@pytest.mark.parametrize('mode', ['complete_bom','storage_failure','blocked_comparison','zero_denominator'])
def test_query_gate_checks_executed_math_and_branch_outcomes_together(tmp_path, mode):
    from dataclasses import asdict, replace
    from .test_grounded_math_v2 import scope
    from mech_chatbot.rag.grounded_math_v2 import compare_two_scopes, serialize_result
    branches=skipped_math_branches()
    branches[0]['data_complete']=True
    branches[1].update(executed=True, status='complete', data_complete=True, reason=None, source_ids=['s1'])
    if mode=='storage_failure':
        pairs=[]
        branches[1].update(status='unknown',data_complete=False,reason='evidence_unavailable',source_ids=[])
    elif mode=='complete_bom':
        pairs=[bom_fixture(1)]
    else:
        scopes=(scope('A',['0' if mode=='zero_denominator' else '10']),scope('B',['15']))
        if mode=='blocked_comparison':scopes=(scopes[0],replace(scopes[1],authorized=False))
        pairs=[({'template':'compare_two_scopes','scopes':json.loads(json.dumps([asdict(s) for s in scopes],default=str))},
                serialize_result(compare_two_scopes(*scopes)))]
        branches[1].update(template='compare_two_scopes',data_complete=False,
            status='partial' if mode=='zero_denominator' else 'unknown',
            reason=None if mode=='zero_denominator' else 'scope_unavailable',
            source_ids=['s1'] if mode=='zero_denominator' else [])
    inputs=[{**p[0],'branch_id':'total'} for p in pairs]
    actual=[{**p[1],'branch_id':'total'} for p in pairs]
    data=ready_fixture(tmp_path,technical_math=(inputs,actual),technical_outcome='partial_answer',
                       technical_query_branches=branches)
    report=evaluate_department_readiness(data,root=tmp_path)
    assert not any("'math+query'," in f and ':Technical:math_evidence_or_budget' in f
                   for f in report['matrix']['failures'])
    assert not report['accepted']


@pytest.mark.parametrize('tamper', [
    lambda b:b[0].update(executed=1),
    lambda b:b[0].update(data_complete=0),
    lambda b:b[0].update(template='text'),
    lambda b:b[0].update(branch_id=[]),
    lambda b:b[0].update(status='complete'),
    lambda b:b[0].update(status='unknown'),
    lambda b:b[0].update(reason=0),
    lambda b:b[0].update(source_ids=['hidden-source']),
    lambda b:b[0].update(source_ids=['s1','s1']),
    lambda b:b[0].update(source_ids=[]),
    lambda b:b[1].update(branch_id='relations'),
    lambda b:b[1].update(depends_on=[]),
    lambda b:b[1].update(depends_on=['total']),
    lambda b:b[1].update(depends_on=['relations','relations']),
    lambda b:b[1].update(depends_on=[True]),
    lambda b:b[1].update(executed=True),
    lambda b:b[1].update(data_complete=True),
    lambda b:b[1].update(status='unknown'),
    lambda b:b[1].update(reason='evidence_unavailable'),
    lambda b:b[1].update(unexpected=True),
    lambda b:b.extend(b),
])
def test_query_gate_rejects_invalid_frozen_branch_evidence_even_when_observation_matches(tmp_path,tamper):
    branches=skipped_math_branches()
    tamper(branches)
    data=ready_fixture(tmp_path,technical_math=([],[]),technical_outcome='partial_answer',
                       technical_query_branches=branches)
    report=evaluate_department_readiness(data,root=tmp_path)
    assert any("'math+query'," in f and ':Technical:math_evidence_or_budget' in f
               for f in report['matrix']['failures'])


@pytest.mark.parametrize('reason', ['intent_overflow','invented_reason'])
def test_query_gate_rejects_a_canonical_result_fabricated_for_an_unexecuted_calculation_path(tmp_path,reason):
    from dataclasses import asdict,replace
    from .test_grounded_math_v2 import scope
    from mech_chatbot.rag.grounded_math_v2 import compare_two_scopes,serialize_result
    scopes=(scope('A',['10']),replace(scope('B',['15']),authorized=False))
    inputs={'template':'compare_two_scopes','branch_id':'total',
            'scopes':json.loads(json.dumps([asdict(s) for s in scopes],default=str))}
    actual={**serialize_result(compare_two_scopes(*scopes)),'branch_id':'total'}
    branches=skipped_math_branches()
    branches[0]['data_complete']=True
    branches[1].update(template='compare_two_scopes',executed=True,status='unknown',reason=reason)
    report=evaluate_department_readiness(ready_fixture(tmp_path,technical_math=([inputs],[actual]),
        technical_outcome='partial_answer',technical_query_branches=branches),root=tmp_path)
    assert any("'math+query'," in f and ':Technical:math_evidence_or_budget' in f
               for f in report['matrix']['failures'])


def replace_query_candidate_case(root,data,tamper):
    run=next(r for r in data['runs'] if r['arm']=='math+query' and r['variant']=='candidate' and r['concurrency']==1)
    evaluation=json.loads((root/run['evaluation_reference']['path']).read_text())
    case=next(c for c in evaluation['cases'] if c['case_id']=='Technical-0')
    tamper(case)
    run['evaluation_reference']=reference(root,'modified-query-evaluation',evaluation)
    gate=json.loads((root/run['gate_reference']['path']).read_text())
    gate['evaluation_sha256']=run['evaluation_reference']['sha256']
    run['gate_reference']=reference(root,'modified-query-gate',gate)


@pytest.mark.parametrize('branches', [{},False,0,'',None])
def test_legacy_math_gate_does_not_ignore_malformed_empty_branch_fields(tmp_path,branches):
    data=ready_fixture(tmp_path)
    replace_query_candidate_case(tmp_path,data,lambda c:c.update(query_branch_schema=None,query_branches=branches))
    report=evaluate_department_readiness(data,root=tmp_path)
    assert any("'math+query', 1," in f and ':Technical:math_evidence_or_budget' in f
               for f in report['matrix']['failures'])


def test_empty_standalone_observation_preserves_the_legacy_reference_contract(tmp_path):
    data=ready_fixture(tmp_path)
    replace_query_candidate_case(tmp_path,data,lambda c:c.update(query_branch_schema=None,query_branches=[]))
    assert evaluate_department_readiness(data,root=tmp_path)['offline_passed']


@pytest.mark.parametrize('tamper', [
    lambda c:c['query_branches'][0].update(data_complete=True),
    lambda c:c['query_branches'][0].update(source_ids=['other']),
    lambda c:c['query_branches'][1].update(executed=True),
    lambda c:c['query_branches'][1].update(reason='evidence_unavailable'),
    lambda c:c['query_branches'][1].update(depends_on=[]),
    lambda c:c['query_branches'].reverse(),
    lambda c:c.update(query_branch_schema='query-branch-execution-v1'),
    lambda c:c.pop('query_branch_schema'),
    lambda c:c.pop('query_branches'),
    lambda c:c.update(other_branches=[{'branch_id':'g','kind':'graph','status':'complete','source_ids':['s1']}]),
])
def test_observed_branch_changes_cannot_rewrite_the_frozen_reference(tmp_path,tamper):
    data=ready_fixture(tmp_path,technical_math=([],[]),technical_outcome='partial_answer',
                       technical_query_branches=skipped_math_branches())
    department=next(d for d in data['departments'] if d['code']=='Technical')
    golden=department['questions'][0]['math_reference']
    original=(tmp_path/golden['path']).read_bytes()
    replace_query_candidate_case(tmp_path,data,tamper)
    report=evaluate_department_readiness(data,root=tmp_path)
    assert any("'math+query', 1," in f and ':Technical:math_evidence_or_budget' in f
               for f in report['matrix']['failures'])
    assert (tmp_path/golden['path']).read_bytes()==original
    assert hashlib.sha256(original).hexdigest()==golden['sha256']


@pytest.mark.parametrize('tamper', [
    lambda c:c['math_results'][0].update(branch_id='relations'),
    lambda c:c['math_results'][0].pop('branch_id'),
    lambda c:c['math_results'].clear(),
    lambda c:c['math_results'].append(deepcopy(c['math_results'][0])),
    lambda c:c['query_branches'][0].update(template='graph_query'),
    lambda c:c['query_branches'][0].update(reason='evidence_unavailable'),
])
def test_executed_math_records_must_match_the_frozen_branch_identity(tmp_path,tamper):
    inputs,actual=bom_fixture(1)
    branch={'branch_id':'total','template':'bom_rollup','depends_on':[],'executed':True,
            'status':'complete','data_complete':True,'reason':None,'source_ids':['s1']}
    data=ready_fixture(tmp_path,technical_math=([{**inputs,'branch_id':'total'}],[{**actual,'branch_id':'total'}]),
                       technical_query_branches=[branch])
    replace_query_candidate_case(tmp_path,data,tamper)
    report=evaluate_department_readiness(data,root=tmp_path)
    assert any("'math+query', 1," in f and ':Technical:math_evidence_or_budget' in f
               for f in report['matrix']['failures'])


@pytest.mark.parametrize('kind',['scope_value','quantity'])
@pytest.mark.parametrize('number',[True,0.1])
def test_math_reference_does_not_coerce_boolean_or_float_inputs_into_decimal(tmp_path,kind,number):
    from dataclasses import asdict
    from decimal import Decimal
    from .test_grounded_math_v2 import scope
    from mech_chatbot.domain.structured_evidence import RoundingRule
    from mech_chatbot.rag.grounded_math_v2 import bom_rollup,compare_two_scopes,serialize_result
    if kind=='scope_value':
        scopes=(scope('A',[str(Decimal(number))]),scope('B',['15']))
        inputs={'template':'compare_two_scopes','scopes':json.loads(json.dumps([asdict(s) for s in scopes],default=str))}
        inputs['scopes'][0]['facts'][0]['value']=number
        actual=serialize_result(compare_two_scopes(*scopes))
    else:
        inputs,_=bom_fixture(1)
        inputs={**inputs,'order_quantity':number}
        actual=serialize_result(bom_rollup(inputs['manifest'],Decimal(number),RoundingRule(0,reviewed=True)))
    report=evaluate_department_readiness(ready_fixture(tmp_path,technical_math=([inputs],[actual])),root=tmp_path)
    assert not report['offline_passed']


def test_ready_gate_rejects_boolean_bom_units_even_when_observed_evidence_matches(tmp_path):
    inputs, actual = bom_fixture(1)
    inputs = deepcopy(inputs)
    for occurrence in inputs['manifest']['occurrences']:
        occurrence['unit'] = True
    for occurrence in actual['sources'][0]['occurrences']:
        occurrence['unit'] = True
    actual['steps'][0]['unit'] = True
    report = evaluate_department_readiness(ready_fixture(tmp_path, technical_math=([inputs], [actual])), root=tmp_path)
    assert not report['offline_passed']
    assert any(':Technical:math_evidence_or_budget_failed' in failure for failure in report['matrix']['failures'])


def test_ready_gate_rejects_boolean_reference_revision_even_when_observed_evidence_matches(tmp_path):
    from dataclasses import asdict
    from .test_grounded_math_v2 import scope
    from mech_chatbot.rag.grounded_math_v2 import compare_two_scopes, serialize_result
    scopes = (scope('A', ['10']), scope('B', ['15']))
    inputs = {'template': 'compare_two_scopes',
              'scopes': json.loads(json.dumps([asdict(s) for s in scopes], default=str))}
    actual = serialize_result(compare_two_scopes(*scopes))
    inputs['scopes'][0]['facts'][0]['locator']['version'] = True
    actual['sources'][0]['facts'][0]['locator']['version'] = True
    report = evaluate_department_readiness(ready_fixture(tmp_path, technical_math=([inputs], [actual])), root=tmp_path)
    assert not report['offline_passed']
    assert any(':Technical:math_evidence_or_budget_failed' in failure for failure in report['matrix']['failures'])


def test_blanket_refusal_and_critical_math_error_fail_even_above_ninety_percent(tmp_path):
    data = ready_fixture(tmp_path)
    department = data["departments"][0]
    raw = json.loads((tmp_path / department["verification"]["path"]).read_text())
    raw["cases"][0].update(answer_correct=False, actual_outcome="insufficient_evidence")
    department["verification"] = reference(tmp_path, "changed-cases", raw)
    result = evaluate_department_readiness(data, root=tmp_path)
    assert result["departments"][0]["correctness_rate"] > 0.9
    assert not result["departments"][0]["offline_passed"]


@pytest.mark.parametrize('field', ['leaked', 'wrong_version'])
@pytest.mark.parametrize('artifact', ['department', 'matrix'])
def test_any_noncritical_leak_or_stale_version_blocks_readiness(tmp_path, field, artifact):
    data = ready_fixture(tmp_path)
    holder = data['departments'][0] if artifact == 'department' else data['runs'][1]
    key = 'verification' if artifact == 'department' else 'evaluation_reference'
    raw = json.loads((tmp_path / holder[key]['path']).read_text())
    raw['cases'][1][field] = True
    holder[key] = reference(tmp_path, 'unsafe-case', raw)
    if artifact == 'matrix':
        gate = json.loads((tmp_path / holder['gate_reference']['path']).read_text())
        gate['evaluation_sha256'] = holder[key]['sha256']
        holder['gate_reference'] = reference(tmp_path, 'unsafe-gate', gate)
    report = evaluate_department_readiness(data, root=tmp_path)
    assert not report['offline_passed']
