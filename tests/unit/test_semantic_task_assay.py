"""Isolated assay through real provider policy/audit and offline SQL boundaries."""
from dataclasses import replace
import hashlib
import json
from pathlib import Path
import sqlite3

import httpx
import pytest
from sqlalchemy import text

from mech_chatbot.config.settings import LlmSettings
from .test_semantic_task_compilation_grading import artifact, comparison_label, labels, captured_sources
from .test_rag_scope_comparison import store, approved_pair
from .test_fixed_math_runner_v2 import ACTOR, BINDING, actor_tables


SETTINGS = LlmSettings('test-key-never-persisted','https://openrouter.ai/api/v1',
    'openai/gpt-5.6-luna',0,8000,300,0)


def assay_inputs(root,engine,count=1):
    approved_pair(engine); actor_tables(engine)
    snapshot = root/'source.sqlite'
    raw = engine.raw_connection(); target = sqlite3.connect(snapshot)
    raw.driver_connection.backup(target); target.close(); raw.close()
    case_ids = ['EXAMPLE','SECOND',*[f'CASE{i}' for i in range(3,count+1)]]
    case_labels = [{**comparison_label(),'case_id':key} for key in case_ids[:count]]
    label_ref = labels(root,*case_labels)
    binding = {**BINDING,'snapshot_fingerprint':hashlib.sha256(snapshot.read_bytes()).hexdigest()}
    case = {'schema':'fixed-task-routing-case-v3-draft',**binding,'run_id':'prepared','case_id':'EXAMPLE',
        'department':'ENG','dataset_sha256':label_ref['sha256'],'actor':ACTOR,
        'question':comparison_label()['question'],'selection':{'scope_comparison':{'scope_a':'a','scope_b':'b'}},
        'task_routing_contract':'semantic-graph-fixed-task-routing-v3-draft','response_language':'en'}
    case_refs = [artifact(root,'prepared-case-'+item['case_id']+'.json',{**case,'case_id':item['case_id']}) for item in case_labels]
    manifest = artifact(root,'prepared.json',{'schema':'synthetic-routing-assay-preparation-v1',
        'status':'prepared_unrun','source_provenance':'synthetic only','case_references':case_refs,
        'run_binding':binding,
        'files':[{'path':snapshot.name,'sha256':hashlib.sha256(snapshot.read_bytes()).hexdigest()}],
        'human_acceptance':False,'runtime_activation':False})
    proposal = artifact(root,'proposal.json',{'schema':'semantic-synthetic-provider-policy-proposal-v1',
        'provider':'openrouter','endpoint':SETTINGS.base_url,'model':SETTINGS.model_name,
        'maximum_provider_calls':21,'maximum_case_count':21,'concurrency':1,'provider_retries':0,
        'planner_calls_per_case':1,'request_deadline_seconds':120,'maximum_output_tokens_per_call':8000,
        'allowed_surfaces':['query_decomposition'],'proposed_validity_minutes':60,'source_manifest':manifest})
    approval = artifact(root,'authorization.json',{'schema':'synthetic-evaluation-egress-authorization-v1',
        'authorization_status':'human_authorized_for_one_scoped_assay','proposal_reference':proposal,
        'provider':'openrouter','endpoint':SETTINGS.base_url,'model':SETTINGS.model_name,
        'maximum_case_count':21,'maximum_provider_calls':21,'concurrency':1,'provider_retries':0,
        'allowed_surfaces':['query_decomposition'],'maximum_run_window_minutes':60,
        'source_manifest_reference':manifest,'synthetic_sources_only':True,
        'native_provider_policy_update_authorized':False,'runtime_activation_authorized':False,
        'business_acceptance_created':False,'release_authorized':False,
        'approval_source':'Synthetic test authorization fixture, not a human acceptance'})
    capture_ref, source_root = captured_sources(root)
    capture = json.loads((root/'capture.json').read_text())
    additional = ['scripts/eval/run_semantic_task_assay.py','src/mech_chatbot/llm/llm_client.py',
        'src/mech_chatbot/llm/external_ai.py','src/mech_chatbot/db/repositories/external_ai.py',
        'src/mech_chatbot/config/repository_runtime.py','src/mech_chatbot/db/engine.py',
        'src/mech_chatbot/db/evidence_snapshot.py','src/mech_chatbot/auth/core.py',
        'tests/unit/test_graph_v2_repository.py','tests/unit/test_rag_bom_chat.py']
    capture['files'] += [{'path':p,'sha256':hashlib.sha256((source_root/p).read_bytes()).hexdigest()} for p in additional]
    capture_ref = artifact(root,'capture.json',capture)
    config = {'schema':'semantic-task-assay-request-v1','run_id':'assay-test','authorization_reference':approval,
        'proposal_reference':proposal,'prepared_manifest_reference':manifest,'label_reference':label_ref,
        'source_capture_reference':capture_ref,'source_snapshot_reference':{'path':snapshot.name,'sha256':binding['snapshot_fingerprint']},
        'case_ids':[item['case_id'] for item in case_labels],'provider':'openrouter','endpoint':SETTINGS.base_url,'model':SETTINGS.model_name}
    return artifact(root,'request.json',config),source_root


def transport(monkeypatch, outcome='ready', callback=None):
    calls = []
    def send(client,request,**kwargs):
        body = json.loads(request.content); calls.append(body)
        assert 'original_expected' not in request.content.decode() and 'expected_tasks' not in request.content.decode()
        prompt = json.loads(body['messages'][1]['content']); question = prompt['question']
        if callback: callback()
        if outcome == 'error': raise httpx.ReadTimeout('synthetic provider timeout',request=request)
        if type(outcome) is int: return httpx.Response(outcome,request=request,json={'error':{'message':'synthetic sensitive error','type':'synthetic'}})
        routing = {'schema':prompt['schema'],'disposition':outcome,'tasks':[],'unresolved':[]}
        if outcome=='ready':
            routing['tasks'] = [{'task_id':'T1','selection_id':'comparison','template':'compare_two_scopes',
                'spans':[{'start':0,'end':len(question),'text':question}],
                'requested_outputs':['sum_a','sum_b','delta','percent_change']}]
        return httpx.Response(200,request=request,json={'id':'synthetic-http-response','object':'chat.completion',
            'created':1,'model':SETTINGS.model_name,'choices':[{'index':0,'message':{'role':'assistant',
            'content':json.dumps(routing)},'finish_reason':'stop'}],
            'usage':{'prompt_tokens':10,'completion_tokens':20,'total_tokens':30}})
    monkeypatch.setattr(httpx.Client,'send',send)
    return calls


def test_assay_runs_real_adapter_policy_audit_and_runner_without_native_database(store,tmp_path,monkeypatch):
    from scripts.eval.run_semantic_task_assay import run_assay
    config,source_root = assay_inputs(tmp_path,store)
    calls = transport(monkeypatch)
    result = run_assay(config,root=tmp_path,source_root=source_root,output_directory=tmp_path/'run',llm_settings=SETTINGS)
    assert len(calls)==result['attempted_provider_calls']==1
    evaluation = json.loads((tmp_path/'run/evaluation.json').read_text(encoding='utf8'))
    assert result['denominator']==evaluation['denominator']==len(evaluation['case_ids'])==1
    assert result['cases'][0]['status']=='observed' and result['cases'][0]['routing_matches'] is True
    assert result['accepted'] is False and result['runtime_activation'] is False
    assert result['cases'][0]['provider_receipt']['usage']['total_tokens']==30
    assert result['cases'][0]['provider_receipt']['cost'] is None
    assert calls[0].get('temperature') is None and result['effective_temperature'] is None
    assert 'test-key-never-persisted' not in '\n'.join(p.read_text(encoding='utf8') for p in (tmp_path/'run').glob('*.json'))


def changed_request(root,reference,key,value):
    config = json.loads((root/reference['path']).read_text(encoding='utf8'))
    return artifact(root,'request.json',{**config,key:value})


@pytest.mark.parametrize('key,value',[
    ('model','wrong'),('endpoint','https://invalid.example'),('case_ids',[]),
    ('case_ids',['EXAMPLE','EXAMPLE']),('case_ids',['MISSING']),('case_ids',[True]),
    ('case_ids',[f'CASE{i}' for i in range(22)]),
    ('run_id','../escape'),('schema','unknown'),('extra',1),
    ('source_snapshot_reference',{'path':'source.sqlite','sha256':'0'*64}),
])
def test_invalid_request_stops_before_claim_or_http(store,tmp_path,monkeypatch,key,value):
    from scripts.eval.run_semantic_task_assay import run_assay
    reference,source_root = assay_inputs(tmp_path,store)
    reference = changed_request(tmp_path,reference,key,value); calls = transport(monkeypatch)
    with pytest.raises(ValueError):
        run_assay(reference,root=tmp_path,source_root=source_root,output_directory=tmp_path/'run',llm_settings=SETTINGS)
    assert not calls and not (tmp_path/'run').exists() and not (tmp_path/'semantic-assay-claims').exists()


@pytest.mark.parametrize('changes',[
    {'model_name':'wrong'},{'base_url':'https://invalid.example'},{'temperature':1},
    {'max_output_tokens':8001},{'max_output_tokens':True},{'timeout_seconds':0},
    {'timeout_seconds':float('nan')},{'min_interval_seconds':1},
])
def test_settings_must_match_concrete_approved_limits(store,tmp_path,monkeypatch,changes):
    from scripts.eval.run_semantic_task_assay import run_assay
    reference,source_root = assay_inputs(tmp_path,store); calls = transport(monkeypatch)
    with pytest.raises(ValueError):
        run_assay(reference,root=tmp_path,source_root=source_root,output_directory=tmp_path/'run',llm_settings=replace(SETTINGS,**changes))
    assert not calls


@pytest.mark.parametrize('outcome',[401,403,404,'error','invalid'])
def test_real_adapter_has_zero_retries_and_stops_global_configuration_failures(store,tmp_path,monkeypatch,outcome):
    from scripts.eval.run_semantic_task_assay import run_assay
    reference,source_root = assay_inputs(tmp_path,store,count=2); calls = transport(monkeypatch,outcome)
    result = run_assay(reference,root=tmp_path,source_root=source_root,output_directory=tmp_path/'run',llm_settings=SETTINGS)
    expected = 1 if outcome in (401,403,404) else 2
    assert len(calls)==result['attempted_provider_calls']==expected
    assert len(result['unrun_case_ids'])==2-expected
    evaluation = json.loads((tmp_path/'run/evaluation.json').read_text(encoding='utf8'))
    assert result['denominator']==evaluation['denominator']==len(evaluation['case_ids'])==2
    assert all(row['provider_receipt']['audit_complete'] for row in result['cases'])
    assert all(row['provider_receipt']['cost'] is None for row in result['cases'])
    if type(outcome) is int:
        assert result['cases'][0]['stop_window'] is True
        assert result['cases'][0]['fatal_reason']=='provider_policy_or_configuration_failure'
    persisted=''.join(p.read_text(encoding='utf8') for p in (tmp_path/'run').rglob('*.json'))
    assert 'synthetic sensitive error' not in persisted


def test_authorization_is_consumed_across_output_destinations(store,tmp_path,monkeypatch):
    from scripts.eval.run_semantic_task_assay import run_assay
    reference,source_root = assay_inputs(tmp_path,store); calls = transport(monkeypatch)
    run_assay(reference,root=tmp_path,source_root=source_root,output_directory=tmp_path/'first',llm_settings=SETTINGS)
    with pytest.raises(FileExistsError):
        run_assay(reference,root=tmp_path,source_root=source_root,output_directory=tmp_path/'second',llm_settings=SETTINGS)
    assert len(calls)==1 and not (tmp_path/'second').exists()


@pytest.mark.parametrize('race_iteration',range(32))
def test_exclusive_authorization_claim_wins_only_once_under_race(store,tmp_path,monkeypatch,race_iteration):
    from concurrent.futures import ThreadPoolExecutor
    from scripts.eval.run_semantic_task_assay import run_assay
    reference,source_root = assay_inputs(tmp_path,store); calls = transport(monkeypatch)
    def attempt(name):
        try:
            return run_assay(reference,root=tmp_path,source_root=source_root,output_directory=tmp_path/name,llm_settings=SETTINGS)
        except FileExistsError:
            return 'claimed'
    with ThreadPoolExecutor(max_workers=2) as pool: results=list(pool.map(attempt,['first','second']))
    assert results.count('claimed')==1 and len(calls)==1


def test_cancelled_before_issuance_does_not_consume_authorization(store,tmp_path,monkeypatch):
    import threading
    from scripts.eval.run_semantic_task_assay import run_assay
    from mech_chatbot.llm.external_ai import ExternalAICallCancelled
    reference,source_root=assay_inputs(tmp_path,store); calls=transport(monkeypatch); cancel=threading.Event();cancel.set()
    with pytest.raises(ExternalAICallCancelled):
        run_assay(reference,root=tmp_path,source_root=source_root,output_directory=tmp_path/'run',llm_settings=SETTINGS,cancellation=cancel)
    assert not calls and not (tmp_path/'semantic-assay-claims').exists()


def test_cancellation_during_provider_preserves_reservation_and_audit(store,tmp_path,monkeypatch):
    import threading
    from scripts.eval.run_semantic_task_assay import run_assay
    reference,source_root=assay_inputs(tmp_path,store,count=2); cancel=threading.Event()
    def callback():
        assert (tmp_path/'run/provider/01-reserved.json').exists()
        cancel.set()
    calls=transport(monkeypatch,callback=callback)
    result=run_assay(reference,root=tmp_path,source_root=source_root,output_directory=tmp_path/'run',llm_settings=SETTINGS,cancellation=cancel)
    assert len(calls)==1 and result['unrun_case_ids']==['SECOND']
    assert result['cases'][0]['stop_window'] is True
    assert (tmp_path/'run/provider/01-result.json').exists()


@pytest.mark.parametrize('stage',['preflight','terminal','policy'])
def test_real_sqlite_audit_or_policy_failure_stops_window_without_retries(store,tmp_path,monkeypatch,stage):
    from sqlalchemy import event
    from sqlalchemy.engine import Engine
    from sqlalchemy.exc import OperationalError
    from scripts.eval.run_semantic_task_assay import run_assay
    reference,source_root=assay_inputs(tmp_path,store,count=2); calls=transport(monkeypatch)
    def fault(conn,cursor,statement,parameters,context,many):
        if stage=='policy' and 'SELECT' in statement and 'ExternalAIProviderProfile' in statement:
            cursor.execute("UPDATE ExternalAIProviderProfile SET IsActive=0")
        if 'INSERT INTO' in statement and 'ExternalAICallAudit' in statement:
            status=parameters[15]
            if stage=='preflight' or stage=='terminal' and status=='success':
                raise OperationalError('synthetic audit unavailable',None,None)
    event.listen(Engine,'before_cursor_execute',fault)
    try:
        result=run_assay(reference,root=tmp_path,source_root=source_root,output_directory=tmp_path/'run',llm_settings=SETTINGS)
    finally:event.remove(Engine,'before_cursor_execute',fault)
    assert len(calls)==(1 if stage=='terminal' else 0)
    assert result['cases'][0]['stop_window'] is True and result['unrun_case_ids']==['SECOND']
    assert result['cases'][0]['provider_receipt']['audit_complete'] is False


def test_outer_readonly_source_snapshot_context_survives_nested_provider_audit(store,tmp_path,monkeypatch):
    from scripts.eval.run_semantic_task_assay import run_assay
    from mech_chatbot.db.evidence_snapshot import evidence_read_connection,evidence_snapshot_active
    reference,source_root=assay_inputs(tmp_path,store)
    def callback():
        assert evidence_snapshot_active()
        with evidence_read_connection() as conn:
            assert conn.execute(text("SELECT CHARINDEX('a','abc'), JSON_VALUE(:value,'$.a.b'), JSON_VALUE(NULL,'$.x')"),{'value':'{"a":{"b":2}}'}).one()==(1,2,None)
            with pytest.raises(Exception):conn.execute(text('CREATE TABLE forbidden(value TEXT)'),{})
    transport(monkeypatch,callback=callback)
    result=run_assay(reference,root=tmp_path,source_root=source_root,output_directory=tmp_path/'run',llm_settings=SETTINGS)
    assert result['cases'][0]['routing_matches'] and not evidence_snapshot_active()


def test_twenty_one_calls_are_serial_and_larger_request_is_rejected(store,tmp_path,monkeypatch):
    from scripts.eval.run_semantic_task_assay import run_assay
    reference,source_root=assay_inputs(tmp_path,store,count=21)
    active=[]
    def callback():
        reservations=list((tmp_path/'run/provider').glob('*-reserved.json'))
        results=list((tmp_path/'run/provider').glob('*-result.json'))
        assert len(reservations)==len(results)+1
        active.append(len(reservations))
    calls=transport(monkeypatch,callback=callback)
    result=run_assay(reference,root=tmp_path,source_root=source_root,output_directory=tmp_path/'run',llm_settings=SETTINGS)
    assert result['attempted_provider_calls']==len(calls)==21 and active==list(range(1,22))
    assert result['unrun_case_ids']==[]


@pytest.mark.parametrize('fault',['authorization','proposal','manifest','labels','source','prepared_case'])
def test_pinned_input_drift_after_first_call_prevents_another_call(store,tmp_path,monkeypatch,fault):
    from scripts.eval.run_semantic_task_assay import run_assay
    reference,source_root=assay_inputs(tmp_path,store,count=2)
    paths={'authorization':'authorization.json','proposal':'proposal.json','manifest':'prepared.json',
           'labels':'labels.json','source':'source.sqlite','prepared_case':'prepared-case-SECOND.json'}
    def callback():
        path=tmp_path/paths[fault]
        path.write_bytes(path.read_bytes()+b' ')
    calls=transport(monkeypatch,callback=callback)
    with pytest.raises(ValueError):
        run_assay(reference,root=tmp_path,source_root=source_root,output_directory=tmp_path/'run',llm_settings=SETTINGS)
    assert len(calls)==1 and (tmp_path/'run/provider/01-result.json').exists()
    assert len(list((tmp_path/'semantic-assay-claims').glob('*.json')))==1


@pytest.mark.parametrize('stage',['issuance','call'])
def test_sixty_minute_window_expiry_does_not_send_another_request(store,tmp_path,monkeypatch,stage):
    from types import SimpleNamespace
    from scripts.eval import run_semantic_task_assay as driver
    reference,source_root=assay_inputs(tmp_path,store,count=2)
    tick=[100.0]; counter=[0]
    def monotonic():
        counter[0]+=1
        if stage=='issuance' and counter[0]>1:return 3701.0
        return tick[0]
    monkeypatch.setattr(driver,'time',SimpleNamespace(monotonic=monotonic))
    calls=transport(monkeypatch,callback=lambda:tick.__setitem__(0,3701.0))
    result=driver.run_assay(reference,root=tmp_path,source_root=source_root,output_directory=tmp_path/'run',llm_settings=SETTINGS)
    assert len(calls)==(1 if stage=='call' else 0)
    assert result['unrun_case_ids']==(['SECOND'] if stage=='call' else ['EXAMPLE','SECOND'])


@pytest.mark.parametrize('destination',['existing','outside','root'])
def test_output_collision_or_escape_does_not_consume_approval(store,tmp_path,monkeypatch,destination):
    from scripts.eval.run_semantic_task_assay import run_assay
    reference,source_root=assay_inputs(tmp_path,store);calls=transport(monkeypatch)
    out={'existing':tmp_path/'occupied','outside':tmp_path.parent/'escape','root':tmp_path}[destination]
    if destination=='existing':out.mkdir()
    with pytest.raises(ValueError):
        run_assay(reference,root=tmp_path,source_root=source_root,output_directory=out,llm_settings=SETTINGS)
    assert not calls and not (tmp_path/'semantic-assay-claims').exists()


@pytest.mark.parametrize('artifact_name',['config.json','gate.json','evaluation.json',
    'issued-provider-profile.json','pre-run-manifest.json'])
def test_issued_run_artifact_drift_prevents_another_call(store,tmp_path,monkeypatch,artifact_name):
    from scripts.eval.run_semantic_task_assay import run_assay
    reference,source_root=assay_inputs(tmp_path,store,count=2)
    def callback():
        path=tmp_path/'run'/artifact_name
        path.write_bytes(path.read_bytes()+b' ')
    calls=transport(monkeypatch,callback=callback)
    with pytest.raises(ValueError):
        run_assay(reference,root=tmp_path,source_root=source_root,output_directory=tmp_path/'run',llm_settings=SETTINGS)
    assert len(calls)==1 and (tmp_path/'run/provider/01-result.json').exists()


@pytest.mark.skipif(__import__('os').name!='nt',reason='Windows namespace spellings')
@pytest.mark.parametrize('namespace',['drive_alias','unc_escape'])
def test_windows_namespace_identity_preserves_confinement(store,tmp_path,monkeypatch,namespace):
    from scripts.eval.run_semantic_task_assay import run_assay
    reference,source_root=assay_inputs(tmp_path,store);calls=transport(monkeypatch)
    out=tmp_path/'run';original=Path.resolve
    def resolve(path,*args,**kwargs):
        target=original(path,*args,**kwargs)
        if namespace=='unc_escape' and path==out:
            return Path('\\\\?\\UNC\\synthetic-offline-host\\share\\outside')
        if namespace=='drive_alias' and path.parent.name=='semantic-assay-claims' and path.suffix=='.json':
            value=str(target)
            return Path(value if value.startswith('\\\\?\\') else '\\\\?\\'+value)
        return target
    monkeypatch.setattr(Path,'resolve',resolve)
    if namespace=='unc_escape':
        with pytest.raises(ValueError):
            run_assay(reference,root=tmp_path,source_root=source_root,output_directory=out,llm_settings=SETTINGS)
        assert not calls and not (tmp_path/'semantic-assay-claims').exists()
    else:
        result=run_assay(reference,root=tmp_path,source_root=source_root,output_directory=out,llm_settings=SETTINGS)
        assert len(calls)==result['attempted_provider_calls']==1
