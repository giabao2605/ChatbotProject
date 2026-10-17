"""Versioned comparison evidence plugged into the existing Math pilot gate."""
from collections import defaultdict
from decimal import Decimal
import hashlib
import json

from mech_chatbot.governance.math_v2_activation import CONTRACT,validate_math_v2_bundle

WINDOW_SCHEMA='math-comparison-pilot-window-v2'
GATE_SCHEMA='grounded-math-comparison-production-pilot-gate-v2'
FORMAL_SCHEMA='grounded-math-comparison-formal-gate-v2'
EVENT='math_v2_comparison_completed'
FIELDS=frozenset({'ts','event','trace_id','execution_context','runtime_identity_sha256','schema','contract',
    'template','route','completion_outcome','calculation_result_status','security_passed',
    'citation_structure_passed','provenance_passed','leakage_detected','calculations','provider_retries',
    'final_generations','estimated_cost','final_latency_ms','request_deadline_ms'})


LIMITS={'max_domain_steps_per_template':4,'required_exact_rate':1,
        'max_latency_ratio':1.25,'max_cost_ratio':1.5}


def _bound_comparison(baseline,candidate,envelope,base):
    from mech_chatbot.evaluation.adjudication import _bound_observation, _RUN_BINDINGS
    from mech_chatbot.evaluation.department_readiness import _verify_math_reference, _math_outcome_matches
    from mech_chatbot.governance.artifact_references import load_json_reference
    try:
        if any(baseline.get(key) != candidate.get(key) for key in ('case_id','case_reference','math_reference')):
            return None
        case_ref,reference_ref=baseline['case_reference'],baseline['math_reference']
        observed=_bound_observation(case_ref,candidate['observation_reference'],base)
        case=load_json_reference(case_ref,root=base)
        reference=load_json_reference(reference_ref,root=base)
        if (observed['case_id'] != candidate['case_id']
                or any(case[key] != envelope['git_sha' if key=='source_commit' else key] for key in _RUN_BINDINGS)
                or case['dataset_sha256'] != envelope['dataset_hash']
                or hashlib.sha256(json.dumps({'actor':case['actor'],'config_hash':case['config_hash']},
                    sort_keys=True,separators=(',',':')).encode()).hexdigest()
                    != envelope['evaluation_context_hash']
                or not isinstance(reference,dict) or reference.get('schema') != 'fixed-template-math-reference-v2'
                or reference.get('case_sha256') != case_ref['sha256']
                or any(reference.get(key) != case[key] for key in (*_RUN_BINDINGS,'run_id','case_id','department','actor','dataset_sha256'))
                or not isinstance(reference.get('records'),list) or len(reference['records']) != 1
                or not isinstance(observed.get('math_results'),list) or len(observed['math_results']) != 1
                or observed.get('query_branches') != [] or observed.get('query_branch_schema') is not None
                or type(observed.get('calculation_count')) is not int or observed['calculation_count'] not in {0,4}
                or not _zero_execution_budget(observed.get('execution_budget'))
                or Decimal(str(candidate['latency_ms'])) != Decimal(str(observed['latency_ms']))
                or Decimal(str(candidate['estimated_cost'])) != 0):
            return None
        actual,source=observed['math_results'][0],reference['records'][0]
        selection=case.get('selection')
        if (source.get('template') != 'compare_two_scopes' or actual.get('template') != 'compare_two_scopes'
                or type(actual.get('domain_step_count')) is not int
                or actual['domain_step_count'] != observed['calculation_count'] or not _verify_math_reference(actual,source)
                or not _math_outcome_matches(observed.get('actual_outcome'),[actual],[])
                or observed.get('answer_outcome') != {'complete':'full_answer','partial':'partial_answer','blocked':'refused'}.get(actual.get('status'))
                or not isinstance(selection,dict) or set(selection) != {'scope_comparison'}
                or selection['scope_comparison'] != dict(zip(('scope_a','scope_b'),(s['scope_id'] for s in source['scopes'])))
                or len(source['scopes']) != 2 or source['scopes'][0]['scope_id'] == source['scopes'][1]['scope_id']):
            return None
        if not isinstance(observed.get('answer'),str) or not observed['answer'].strip():
            return None
        if actual['status']=='blocked':
            return 0 if (observed['citations']==[] and observed['actual_outcome']=='insufficient_evidence'
                and observed['answer']=='Chưa đủ dữ liệu đã duyệt để thực hiện phép so sánh này.') else None
        from mech_chatbot.rag.scope_comparison import _answer
        return 4 if (_comparison_citations(observed,source['scopes'])
            and observed['answer']==_answer(actual,observed['citations'],'vi')) else None
    except (KeyError,TypeError,ValueError,AttributeError,ArithmeticError):
        return None


def _zero_execution_budget(budget):
    counts={'planners','subqueries','corrections','repairs','graph_edges',
            'graph_examined_relations','provider_retries','final_generations'}
    return (isinstance(budget,dict) and set(budget)==counts|{'deadline_exceeded'}
            and all(type(budget[key]) is int and budget[key]==0 for key in counts)
            and budget['deadline_exceeded'] is False)


def _comparison_citations(observed,scopes):
    from dataclasses import asdict
    from mech_chatbot.application.chat_citations import structured_citation,citation_source_ids
    from mech_chatbot.db.repositories.structured_evidence import _fact,_manifest
    from mech_chatbot.domain.structured_evidence import reviewed_scope_fingerprint,source_bindings
    expected=set()
    expected_ids=set()
    for value in scopes:
        scope=_manifest(json.dumps(value),tuple(_fact(fact) for fact in value['facts']))
        digest=reviewed_scope_fingerprint(scope)
        for locator,source_hash in source_bindings(scope):
            expected.add((scope.scope_id,scope.snapshot_id,digest,str(locator.doc_id),str(locator.version),source_hash))
        groups={}
        for fact in scope.facts:
            loc=fact.locator
            row=(loc.row_key,loc.cell) if not (loc.page or loc.sheet) else ()
            groups.setdefault((loc.doc_id,loc.version,loc.page,loc.sheet,loc.table_index,row),[]).append(fact)
        records=[(facts[0].locator,facts[0].source_hash,len(facts)) for facts in groups.values()]
        if not scope.facts:
            records=[(loc,source_hash,0) for loc,source_hash in source_bindings(scope)]
        for loc,source_hash,count in records:
            locator=asdict(loc)
            if count>1:
                locator={**locator,'cell':None,'row_key':'','bbox':None}
            canonical=structured_citation({'doc_id':int(loc.doc_id),'version_no':int(loc.version),
                'locator':locator,'snapshot_id':scope.snapshot_id,'source_hash':source_hash,
                'scope_id':scope.scope_id,'scope_hash':digest},page_has_vision=lambda *_:False)
            if canonical is None:
                return False
            expected_ids.add(canonical['source_id'])
    cards=observed.get('citations')
    if not isinstance(cards,list) or not cards or any(not isinstance(card,dict) for card in cards):
        return False
    actual=set()
    ids=set()
    for card in cards:
        canonical=structured_citation(card,page_has_vision=lambda *_:False)
        if canonical is None or canonical['source_id'] != card.get('source_id') or card['source_id'] in ids:
            return False
        ids.add(card['source_id'])
        actual.add((card.get('scope_id'),card.get('snapshot_id'),card.get('scope_hash'),
                    str(card.get('doc_id')),str(card.get('version_no')),card.get('source_hash')))
    return expected == actual and ids == expected_ids == set(citation_source_ids(observed.get('answer')))


def _pair_budget_maxima(before,after,base,commit,snapshot):
    from scripts.ops import grounded_math_pilot_gate as common
    from mech_chatbot.evaluation.metrics import nearest_rank
    if not (all(e.get('schema')=='rag-labeled-eval-v4' and e.get('git_sha')==commit for e in (before,after))
            and isinstance(commit,str) and bool(commit.strip())
            and before.get('snapshot_fingerprint')==snapshot
            and all(common._is_sha256(before.get(k)) and before[k]==after.get(k)
                for k in ('dataset_hash','snapshot_fingerprint','evaluation_context_hash','config_hash','gate_hash','eval_hash'))):
        return None
    a,b=before.get('cases'),after.get('cases')
    if not (isinstance(a,list) and isinstance(b,list) and a and len(a)==len(b)
            and all(isinstance(c,dict) and isinstance(c.get('case_id'),str) and c['case_id'].strip() for c in a+b)
            and len({c['case_id'] for c in a})==len(a)
            and [c['case_id'] for c in a]==[c['case_id'] for c in b]):
        return None
    counts=[_bound_comparison(left,right,before,base) for left,right in zip(a,b)]
    if any(count is None for count in counts) or 4 not in counts:
        return None
    try:
        latencies=[[common._decimal(c['latency_ms']) for c in cases] for cases in (a,b)]
        costs=[sum((common._decimal(c['estimated_cost']) for c in cases),Decimal(0)) for cases in (a,b)]
    except (KeyError,TypeError,ValueError):
        return None
    if (nearest_rank(latencies[1],.95)>nearest_rank(latencies[0],.95)*Decimal('1.25')
            or costs[1]>costs[0]*Decimal('1.5')):
        return None
    return max(latencies[0]),max(common._decimal(c['estimated_cost']) for c in a)


def build_formal_gate(baseline_path,candidate_path,*,root):
    from scripts.ops import grounded_math_pilot_gate as common
    before,before_raw=common._load_json(baseline_path)
    after,after_raw=common._load_json(candidate_path)
    commit=before.get('git_sha')
    maxima=_pair_budget_maxima(before,after,root,commit,before.get('snapshot_fingerprint'))
    return {'schema':FORMAL_SCHEMA,'contract':CONTRACT,'source_commit':commit,
        'passed':maxima is not None,'decision':'pending_review' if maxima is not None else 'rejected',
        'accepted':False,'production_eligible':False,'limits':dict(LIMITS),
        'failure_reason':None if maxima is not None else 'invalid_or_failed_comparison_pair',
        'inputs':{'baseline_eval_sha256':common._sha256(before_raw),'candidate_eval_sha256':common._sha256(after_raw)}}


def formal_budget_maxima(sources,base,commit,snapshot):
    from scripts.ops import grounded_math_pilot_gate as common
    if not isinstance(sources,dict) or sources.get('source_commit')!=commit:
        return None
    pairs=sources.get('pairs')
    if not isinstance(pairs,list) or len(pairs)!=3:
        return None
    maxima=[]
    identities=set()
    for pair in pairs:
        if not isinstance(pair,dict):
            return None
        loaded=[common._material(pair.get(key),base) for key in ('eval','candidate_eval','gate')]
        if not all(loaded):
            return None
        baseline,candidate,gate=loaded
        check=gate[0]
        identities.add(tuple(item[1] for item in loaded))
        if not (check.get('schema')==FORMAL_SCHEMA and check.get('passed') is True
                and check.get('contract')==CONTRACT and check.get('source_commit')==commit
                and check.get('limits')==LIMITS
                and check.get('inputs')=={'baseline_eval_sha256':baseline[1],'candidate_eval_sha256':candidate[1]}):
            return None
        pair_maxima=_pair_budget_maxima(baseline[0],candidate[0],base,commit,snapshot)
        if pair_maxima is None:
            return None
        maxima.append(pair_maxima)
    return (max(v[0] for v in maxima),max(v[1] for v in maxima)) if len(identities)==3 else None


def bundle_valid(bundle,window,base,digest):
    from scripts.ops.grounded_math_pilot_gate import _timestamp
    pilot=window['expected_runtime']['pilot']
    environment={**bundle.get('feature_flags',{}),**bundle.get('versions',{}),
        'RAG_ACTIVATION_SCOPE':'controlled_demo','RAG_ACTIVATION_PROFILE':'selective',
        'RAG_DEPLOYMENT_GIT_SHA':pilot['git_sha'],'RAG_SNAPSHOT_FINGERPRINT':pilot['snapshot_fingerprint']}
    cap=validate_math_v2_bundle(bundle,root=base,environ=environment,current_commit=pilot['git_sha'],bundle_sha256=digest)
    return (cap is not None and window.get('contract')==cap.contract and window.get('template')==cap.template
        and _timestamp(cap.expires_at)>=_timestamp(window['minimum_runtime_until']))


def trace_checks(rows,window,now,parse_error):
    from scripts.ops import grounded_math_pilot_gate as common
    checks={k:True for k in common.CHECKS}
    checks['provider_errors']=not parse_error
    groups=defaultdict(list)
    for row in rows:
        if row.get('execution_context')!='production':
            continue
        if common._error_like(row) or row.get('event')=='external_ai_call':
            checks['provider_errors']=False
        if row.get('event')==EVENT:
            groups[row.get('trace_id')].append(row)
    ids=set()
    for trace,events in groups.items():
        if not isinstance(trace,str) or not trace.strip() or len(events)!=1:
            checks['runtime_identity']=False
            continue
        event=events[0]
        valid=(event.get('schema')=='math-v2-comparison-completion-v1' and event.get('contract')==CONTRACT
            and event.get('template')=='compare_two_scopes' and event.get('route')=='scope_comparison'
            and event.get('completion_outcome')=='answered' and event.get('calculation_result_status')=='valid'
            and event.get('runtime_identity_sha256')==window['expected_runtime']['pilot']['runtime_identity_sha256']
            and common._event_time_valid(event,common._timestamp(window['started_at']),now))
        checks['runtime_identity'] &= valid
        if valid:
            ids.add(trace)
        for check,field in (('security','security_passed'),('citation_structure','citation_structure_passed'),('provenance','provenance_passed')):
            checks[check] &= event.get(field) is True
        checks['leakage'] &= event.get('leakage_detected') is False and set(event)<=FIELDS
        try:
            checks['budgets'] &= (type(event.get('calculations')) is int and event['calculations']==4
                and type(event.get('provider_retries')) is int and event['provider_retries']==0
                and type(event.get('final_generations')) is int and event['final_generations']==0
                and common._decimal(event.get('estimated_cost'))==0
                and common._decimal(event.get('final_latency_ms'))<=common._decimal(window['pilot_budget']['max_final_latency_ms'])
                and common._decimal(event.get('final_latency_ms'))<=common._decimal(event.get('request_deadline_ms'))
                and common._decimal(event.get('request_deadline_ms'))==common._decimal(window['expected_runtime']['pilot']['request_deadline_seconds'])*1000)
        except ValueError:
            checks['budgets']=False
    checks['runtime_identity'] &= len(ids)>=window['minimum_eligible_requests']
    return {k:bool(value and ids) for k,value in checks.items()},ids


def main(argv=None):
    import argparse
    from pathlib import Path
    from scripts.ops import grounded_math_pilot_gate as common
    parser=argparse.ArgumentParser(description='Create comparison-v2 formal evidence; never grants activation')
    parser.add_argument('--baseline',type=Path,required=True)
    parser.add_argument('--candidate',type=Path,required=True)
    parser.add_argument('--root',type=Path,default=Path.cwd())
    parser.add_argument('--output',type=Path,required=True)
    args=parser.parse_args(argv)
    if args.output.resolve() in {args.baseline.resolve(),args.candidate.resolve()}:
        parser.error('output must not overwrite an input evaluation')
    try:
        artifact=build_formal_gate(args.baseline,args.candidate,root=args.root)
    except (OSError,UnicodeError,ValueError,KeyError,TypeError):
        artifact={'schema':FORMAL_SCHEMA,'contract':CONTRACT,'passed':False,'accepted':False,
            'production_eligible':False,'decision':'rejected','failure_reason':'invalid_evaluation_material'}
    common._atomic_json(args.output,artifact)
    return 0 if artifact['passed'] else 2


if __name__=='__main__':
    raise SystemExit(main())
