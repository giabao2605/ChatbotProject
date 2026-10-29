"""One isolated synthetic model assay; credentials are injected, never loaded here."""
from contextlib import contextmanager
from dataclasses import dataclass, replace
from datetime import datetime, timedelta, timezone
import hashlib
import json
import math
from pathlib import Path
import sqlite3
import time

from sqlalchemy import create_engine, text
from mech_chatbot.config.repository_runtime import bind_repository_runtime
from mech_chatbot.config.settings import LlmSettings, ExternalAiSettings
from mech_chatbot.llm.llm_client import build_llm_adapter
from mech_chatbot.llm.external_ai import (
    external_document_context, external_processing_context, ExternalProcessingDenied,
    ExternalAIComplianceAuditUnavailable, ExternalAICallCancelled, external_error_metadata,
)
from mech_chatbot.rag.execution import DefaultRagExecutor, RequestBudgetLimits, NEVER_CANCELLED
from mech_chatbot.rag.query_decomposition import GRAPH_TASK_ROUTING
from mech_chatbot.domain.task_routing import CANDIDATE_TASK_ROUTING
from mech_chatbot.governance.artifact_references import load_json_reference, resolve_path
from scripts.eval.run_fixed_math_v2 import run_fixed_case, _frozen_case, _case_request
from scripts.eval.semantic_task_routing import (
    compile_task_label, grade_task_observation, compile_candidate_task_label,
    grade_candidate_task_observation, _verify_source_capture,
)
from tests.unit.test_rag_bom_chat import FixtureEngine
from tests.unit.test_graph_v2_repository import SqliteConnection

__all__ = ['run_assay']
_PROVIDER = {'provider':'openrouter','endpoint':'https://openrouter.ai/api/v1','model':'openai/gpt-5.6-luna'}
_REQUIRED = {'scripts/eval/run_semantic_task_assay.py','src/mech_chatbot/llm/llm_client.py',
    'src/mech_chatbot/llm/external_ai.py','src/mech_chatbot/db/repositories/external_ai.py',
    'src/mech_chatbot/config/repository_runtime.py','src/mech_chatbot/db/engine.py',
    'src/mech_chatbot/db/evidence_snapshot.py','src/mech_chatbot/auth/core.py',
    'tests/unit/test_graph_v2_repository.py','tests/unit/test_rag_bom_chat.py'}


class _StopAssay(RuntimeError):
    pass


class _Connection(SqliteConnection):
    def execute(self,statement,params=None):
        return super().execute(statement,{} if params is None else params)


class _Engine(FixtureEngine):
    @contextmanager
    def connect(self):
        with self.store.connect() as conn:
            wrapper = _Connection(conn); wrapper.dialect = conn.dialect
            yield wrapper


def _safe_string(value):
    return value if type(value) is str and len(value)<=256 and all(c.isprintable() for c in value) else None


def _require(condition, message):
    if not condition:
        raise ValueError(message)


def _load(reference, root):
    value = load_json_reference(reference,root=root)
    _require(type(value) is dict,'Pinned JSON object required')
    return value


def _hash(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _path_identity(path):
    value = str(path)
    if value.startswith('\\\\?\\UNC\\'):
        value = '\\\\' + value[8:]
    elif value.startswith('\\\\?\\'):
        value = value[4:]
    return Path(value)


def _write(out, name, value):
    out = out.resolve()
    path = (out/name).resolve()
    _require(_path_identity(path).is_relative_to(_path_identity(out)),'Confined output path required')
    path.parent.mkdir(parents=True,exist_ok=True)
    with path.open('x',encoding='utf8') as stream:
        json.dump(value,stream,ensure_ascii=False,indent=2,allow_nan=False)
    return {'path':str(path),'sha256':_hash(path)}


def _same_reference(left,right,parent,root):
    return (type(left) is dict and type(right) is dict and left.get('sha256') == right.get('sha256')
        and resolve_path(left.get('path'),root).resolve() == resolve_path(right.get('path'),parent).resolve())


def _authorization(config,root):
    auth_ref, proposal_ref = config['authorization_reference'], config['proposal_reference']
    auth, proposal = _load(auth_ref,root), _load(proposal_ref,root)
    parent = resolve_path(auth_ref['path'],root).parent
    caps = {'concurrency':1,'provider_retries':0,'allowed_surfaces':['query_decomposition']}
    v4 = config.get('task_routing_contract') == CANDIDATE_TASK_ROUTING
    auth_schema = 'synthetic-candidate-evaluation-egress-authorization-v4-draft' if v4 else 'synthetic-evaluation-egress-authorization-v1'
    proposal_schema = 'candidate-synthetic-provider-policy-proposal-v4-draft' if v4 else 'semantic-synthetic-provider-policy-proposal-v1'
    _require(auth.get('schema')==auth_schema
        and auth.get('authorization_status')=='human_authorized_for_one_scoped_assay'
        and proposal.get('schema')==proposal_schema, 'Scoped authorization required')
    _require(not v4 or auth.get('task_routing_contract')==proposal.get('task_routing_contract')==CANDIDATE_TASK_ROUTING,
        'Matching V4 authorization contract required')
    _require(all(type(auth.get(k)) is type(v) and auth[k]==v and type(proposal.get(k)) is type(v)
        and proposal[k]==v for k,v in {**_PROVIDER,**caps}.items()),'Authorized model and caps required')
    _require(all(type(auth.get(k)) is int and 1<=auth[k]<=(24 if v4 else 21)
        and type(proposal.get(k)) is int and proposal[k]==auth[k]
        for k in ('maximum_case_count','maximum_provider_calls')),'Matching reduced call and case caps required')
    _require(auth.get('maximum_run_window_minutes')==60 and type(auth['maximum_run_window_minutes']) is int
        and proposal.get('request_deadline_seconds')==120 and type(proposal['request_deadline_seconds']) is int
        and proposal.get('planner_calls_per_case')==1 and type(proposal['planner_calls_per_case']) is int
        and proposal.get('maximum_output_tokens_per_call')==8000 and type(proposal['maximum_output_tokens_per_call']) is int
        and proposal.get('proposed_validity_minutes')==60 and type(proposal['proposed_validity_minutes']) is int,
        'Approved time and output limits required')
    _require(auth.get('synthetic_sources_only') is True and all(auth.get(k) is False for k in (
        'native_provider_policy_update_authorized','runtime_activation_authorized','business_acceptance_created','release_authorized')),
        'Synthetic isolated scope required')
    _require(_same_reference(proposal_ref,auth.get('proposal_reference'),parent,root)
        and _same_reference(config['prepared_manifest_reference'],auth.get('source_manifest_reference'),parent,root),
        'Authorization reference mismatch')
    _require(_same_reference(config['prepared_manifest_reference'],proposal.get('source_manifest'),resolve_path(proposal_ref['path'],root).parent,root),
        'Proposal source reference mismatch')
    return auth


def _verify_prepared_files(manifest,root):
    _require(type(manifest.get('files')) is list and 1<=len(manifest['files'])<=10000,'Prepared file pins required')
    seen = frozenset()
    for ref in manifest['files']:
        _require(type(ref) is dict and set(ref)=={'path','sha256'} and type(ref['path']) is str
            and type(ref['sha256']) is str,'Exact prepared file pin required')
        path = resolve_path(ref['path'],root).resolve()
        _require(_path_identity(path).is_relative_to(_path_identity(root)),'Prepared file must remain within artifact root')
        identity = str(path).casefold()
        _require(identity not in seen and _hash(path)==ref['sha256'],'Prepared file duplicate or hash mismatch')
        seen = seen | {identity}


def _verify_assay_source(config,root,source_root):
    capture = _verify_source_capture(config['source_capture_reference'],root,source_root)
    v4 = config.get('task_routing_contract') == CANDIDATE_TASK_ROUTING
    required = _REQUIRED | ({'src/mech_chatbot/domain/task_routing.py', 'src/mech_chatbot/rag/candidate_task_routing.py',
        'src/mech_chatbot/rag/pipeline.py', 'src/mech_chatbot/rag/query_plan.py'} if v4 else set())
    _require(required <= {f['path'].replace('\\','/').casefold() for f in capture['files']},'Assay source seams missing')


def _inputs(config_reference,root,source_root,settings):
    config = _load(config_reference,root)
    fields = {'schema','run_id','authorization_reference','proposal_reference','prepared_manifest_reference',
              'label_reference','source_capture_reference','source_snapshot_reference','case_ids',*_PROVIDER}
    v4 = config.get('schema')=='candidate-task-assay-request-v4-draft'
    if v4:
        fields = fields | {'task_routing_contract'}
    _require(set(config)==fields and config['schema'] in ('semantic-task-assay-request-v1','candidate-task-assay-request-v4-draft')
        and (not v4 or config['task_routing_contract']==CANDIDATE_TASK_ROUTING)
        and type(config['run_id']) is str and config['run_id'].isascii()
        and config['run_id'].replace('-','').replace('_','').isalnum() and len(config['run_id'])<=64,
        'Exact assay request required')
    _require(all(config[k]==v for k,v in _PROVIDER.items()),'Fixed selected provider required')
    _require(isinstance(settings,LlmSettings) and settings.base_url==_PROVIDER['endpoint']
        and settings.model_name==_PROVIDER['model'] and settings.temperature==0
        and type(settings.max_output_tokens) is int and 1<=settings.max_output_tokens<=8000
        and type(settings.timeout_seconds) in (int,float) and math.isfinite(settings.timeout_seconds)
        and settings.timeout_seconds>0 and type(settings.min_interval_seconds) in (int,float)
        and math.isfinite(settings.min_interval_seconds) and settings.min_interval_seconds==0,
        'Explicit matching bounded LLM settings required')
    authorization = _authorization(config,root)
    _verify_assay_source(config,root,source_root)
    manifest = _load(config['prepared_manifest_reference'],root)
    _require(manifest.get('schema')==('synthetic-candidate-routing-assay-preparation-v4-draft' if v4 else 'synthetic-routing-assay-preparation-v1')
        and (not v4 or manifest.get('task_routing_contract')==CANDIDATE_TASK_ROUTING)
        and manifest.get('status')=='prepared_unrun' and manifest.get('source_provenance')=='synthetic only'
        and manifest.get('human_acceptance') is False and manifest.get('runtime_activation') is False,
        'Unaccepted synthetic source manifest required')
    _verify_prepared_files(manifest,root)
    snapshot = resolve_path(config['source_snapshot_reference']['path'],root).resolve()
    _require(config['source_snapshot_reference'] in manifest['files']
        and _hash(snapshot)==manifest['run_binding']['snapshot_fingerprint'],'Pinned source snapshot required')
    ids = config['case_ids']
    _require(type(ids) is list and 1<=len(ids)<=min(authorization['maximum_case_count'],authorization['maximum_provider_calls']) and all(type(i) is str for i in ids)
        and len(set(ids))==len(ids),'Unique cases within the authorized cap required')
    contract = CANDIDATE_TASK_ROUTING if v4 else GRAPH_TASK_ROUTING
    cases = [_frozen_case(ref,root,manifest['run_binding'],contract) for ref in manifest['case_references']]
    _require(len({c['case_id'] for c in cases})==len(cases) and set(ids)<={c['case_id'] for c in cases},'Pinned case selection required')
    chosen = [next(c for c in cases if c['case_id']==i) for i in ids]
    for case in chosen:
        compiled = (compile_candidate_task_label if v4 else compile_task_label)(config['label_reference'],case['case_id'],root=root)
        _require(compiled['status']=='representable_unbound' and case['question']==compiled['question']
            and case['response_language']==compiled['response_language'] and case['selection']==compiled['selection']
            and case['dataset_sha256']==config['label_reference']['sha256'],'Original label input required')
        _case_request(case)
    return config, chosen, snapshot, manifest['run_binding']['source_commit']


def _source_engine(snapshot):
    def connect():
        conn = sqlite3.connect(snapshot.as_uri()+'?mode=ro',uri=True)
        conn.create_function('CHARINDEX',2,lambda needle,value:value.find(needle)+1)
        def json_value(value,path):
            result = json.loads(value) if value else {}
            for key in path[2:].split('.'): result = result.get(key) if isinstance(result,dict) else None
            return result
        conn.create_function('JSON_VALUE',2,json_value)
        return conn
    return create_engine('sqlite://',creator=connect)


def _policy_database(engine,out,auth_ref,expires):
    profile = {'Provider':'openrouter','Endpoint':_PROVIDER['endpoint'],'DefaultModel':_PROVIDER['model'],
        'SecretReference':'secret://isolated-assay/in-memory-key','AllowedSurfacesJson':'["query_decomposition"]',
        'RetentionMode':'openrouter_account_and_upstream_defaults','PolicyVersion':'evaluation-only-synthetic-assay-v1',
        'ApprovedBy':'thread-authorized-assay-operator','RiskAcceptanceRef':auth_ref['sha256'],
        'ReviewExpiresAt':expires.isoformat(),'IsActive':1,'UpdatedAt':datetime.now().isoformat(),'UpdatedBy':'isolated-assay'}
    columns = ','.join(k+(' INTEGER' if k=='IsActive' else ' TEXT') for k in profile)
    audit_fields = ('TraceID','ActorUsername','ActorIsAdmin','Provider','Endpoint','Model','Surface','DocIDsJson','SecurityJson',
        'PolicyVersion','RetentionMode','RiskAcceptanceRef','InputChars','InputBytes','InputTokenEstimate','Status','LatencyMs','ErrorType')
    with engine.begin() as conn:
        conn.exec_driver_sql('CREATE TABLE ExternalAIProviderProfile('+columns+')')
        conn.exec_driver_sql('CREATE TABLE ExternalAICallAudit('+','.join(k+' TEXT' for k in audit_fields)+')')
        conn.execute(text('INSERT INTO ExternalAIProviderProfile('+','.join(profile)+') VALUES('+','.join(':'+k for k in profile)+')'),profile)
    _write(out,'issued-provider-profile.json',{'metadata':profile,'authorization_reference':auth_ref,
        'issued_utc':datetime.now(timezone.utc).isoformat(),'expires_local_naive':expires.isoformat(),'accepted':False})
    return engine


def _close_policy(engine,out,auth_ref):
    try:
        before,after = None,None
        with engine.begin() as conn:
            exists = conn.exec_driver_sql("SELECT 1 FROM sqlite_master WHERE type='table' AND name='ExternalAIProviderProfile'").first()
            if exists:
                row = conn.exec_driver_sql("SELECT * FROM ExternalAIProviderProfile WHERE Provider='openrouter'").mappings().first()
                if row:
                    before = dict(row)
                    conn.exec_driver_sql("UPDATE ExternalAIProviderProfile SET IsActive=0 WHERE Provider='openrouter'")
                    after = dict(conn.exec_driver_sql("SELECT * FROM ExternalAIProviderProfile WHERE Provider='openrouter'").mappings().one())
                    _require(after['IsActive']==0,'Isolated provider policy must close')
        return _write(out,'policy-closure.json',{'schema':'isolated-assay-policy-closure-v1',
            'authorization_reference':auth_ref,'closed_utc':datetime.now(timezone.utc).isoformat(),
            'status':'closed' if before is not None else 'not_issued','profile_before':before,'profile_after':after,
            'native_policy_changed':False,'reusing_window_permitted':False,'accepted':False})
    except Exception as error:
        _write(out,'policy-close-failure.json',{'error_type':type(error).__name__,
            'reason':'isolated_policy_close_failed','reusing_window_permitted':False,'accepted':False})
        raise


@dataclass(frozen=True)
class _Meter:
    traces: tuple = ()
    receipts: tuple = ()
    fatal_reason: str | None = None


class _Provider:
    def __init__(self,settings,engine,out,cancellation,deadline,verify,call_limit):
        self.settings,self.engine,self.out = settings,engine,out
        self.cancellation,self.deadline = cancellation,deadline
        self.meter = _Meter()
        self.verify = verify
        self.expected_trace = None
        self.call_limit = call_limit

    def stop(self,reason):
        self.meter = replace(self.meter,fatal_reason=reason)
        _write(self.out,'provider/stop.json',{'reason':reason,'accepted':False})
        raise _StopAssay(reason)

    def invoke_once(self,messages,**kwargs):
        if self.cancellation.is_set(): raise ExternalAICallCancelled('Assay cancelled')
        remaining = self.deadline-time.monotonic()
        if remaining<=0: self.stop('window_expired')
        trace = kwargs.get('trace_id')
        if kwargs.get('surface')!='query_decomposition' or trace!=self.expected_trace or trace in self.meter.traces or len(self.meter.traces)>=self.call_limit:
            self.stop('provider_call_contract')
        try: self.verify()
        except Exception: self.stop('pinned_input_drift')
        self.meter = replace(self.meter,traces=(*self.meter.traces,trace))
        index = len(self.meter.traces)
        _write(self.out,f'provider/{index:02d}-reserved.json',{'trace_id':trace,'attempt':index,'status':'reserved_unknown_charge'})
        external = ExternalAiSettings('evaluation',False,'all_external','evaluation')
        adapter = build_llm_adapter(replace(self.settings,timeout_seconds=min(120,remaining,self.settings.timeout_seconds)),external_ai_settings=external)
        response, error = None, None
        try:
            with bind_repository_runtime(db_engine=_Engine(self.engine)):
                response = adapter.invoke_once(messages,**kwargs)
        except Exception as exc:
            error = exc
        receipt = self._receipt(trace,index,response,error)
        self.meter = replace(self.meter,receipts=(*self.meter.receipts,receipt))
        _write(self.out,f'provider/{index:02d}-result.json',receipt)
        if not receipt['audit_complete']: self.stop('audit_terminal_missing')
        if error is not None:
            if isinstance(error,(ExternalProcessingDenied,ExternalAIComplianceAuditUnavailable,ExternalAICallCancelled)) or receipt['http_status'] in (401,403,404):
                self.stop('provider_policy_or_configuration_failure')
            raise error
        if self.cancellation.is_set(): raise ExternalAICallCancelled('Assay cancelled after provider')
        if time.monotonic()>=self.deadline: self.stop('window_expired_after_provider')
        return response

    invoke = invoke_once

    def _receipt(self,trace,index,response,error):
        with self.engine.connect() as conn:
            states = list(conn.execute(text('SELECT Status FROM ExternalAICallAudit WHERE TraceID=:trace ORDER BY rowid'),{'trace':trace}).scalars())
        metadata = getattr(response,'response_metadata',{}) or {}
        usage = getattr(response,'usage_metadata',None)
        safe_usage = {k:v for k,v in (usage or {}).items() if k in ('input_tokens','output_tokens','total_tokens') and type(v) is int and v>=0}
        failure = getattr(error,'last_attempt',None)
        cause = failure.exception() if failure is not None else error
        status = external_error_metadata(cause).status_code if cause else None
        return {'trace_id':trace,'attempt':index,'audit_states':states,'audit_complete':states in (['started','success'],['started','error'],['started','cancelled']),
            'response_id':_safe_string(getattr(response,'id',None)),'returned_model':_safe_string(metadata.get('model_name')),
            'finish_reason':_safe_string(metadata.get('finish_reason')),'usage':safe_usage or None,'cost':None,
            'error_type':type(cause).__name__ if cause else None,'http_status':status,'actual_provider_response':response is not None,
            'model_evaluated':response is not None,'accepted':False}


def _run_files(out,config,config_reference,cases,commit,snapshot_hash,expires,settings,authorization):
    v4 = config.get('task_routing_contract') == CANDIDATE_TASK_ROUTING
    version = {'task_routing_contract':CANDIDATE_TASK_ROUTING} if v4 else {}
    run_config = _write(out,'config.json',{'schema':'candidate-task-assay-config-v4-draft' if v4 else 'semantic-task-assay-config-v1',**version,'request_reference':config_reference,
        **_PROVIDER,'configured_temperature':settings.temperature,'effective_temperature':None,
        'maximum_output_tokens':settings.max_output_tokens,'configured_timeout_seconds':settings.timeout_seconds,
        'min_interval_seconds':settings.min_interval_seconds,'deadline_seconds':120,
        'maximum_provider_calls':authorization['maximum_provider_calls'],'maximum_case_count':authorization['maximum_case_count'],
        'provider_retries':0,'window_minutes':60,'expires_local_naive':expires.isoformat(),
        'source_capture_reference':config['source_capture_reference'],'authorization_reference':config['authorization_reference']})
    gate = _write(out,'gate.json',{'schema':'candidate-task-assay-gate-v4-draft' if v4 else 'semantic-task-assay-gate-v1',**version,'status':'unapproved','accepted':False,'release_authorized':False})
    evaluation = _write(out,'evaluation.json',{'schema':'candidate-task-assay-evaluation-v4-draft' if v4 else 'semantic-task-assay-evaluation-v1',**version,'case_ids':config['case_ids'],
        'denominator':len(cases),'label_reference':config['label_reference'],'action_correctness':'unrun','business_acceptance':False})
    binding = {'source_commit':commit,'snapshot_fingerprint':snapshot_hash,'config_hash':run_config['sha256'],
        'gate_hash':gate['sha256'],'eval_hash':evaluation['sha256']}
    references = [_write(out,'cases/'+c['case_id']+'.json',{**c,**binding,'run_id':config['run_id']}) for c in cases]
    profile = {'path':str(out/'issued-provider-profile.json'),'sha256':_hash(out/'issued-provider-profile.json')}
    manifest = _write(out,'pre-run-manifest.json',{'case_references':references,'run_binding':binding,
        'request_reference':config_reference,'run_artifact_references':[run_config,gate,evaluation,profile],'accepted':False})
    return references,binding,[run_config,gate,evaluation,profile,manifest]


def _case_result(ref,config,root,source_root,out,binding,provider,cancel):
    case = _load(ref,root); key = case['case_id']
    provider.expected_trace = f"fixed:{case['run_id']}:{key}"
    _write(out,'cases/'+key+'-started.json',{'case_id':key,'status':'started_unknown_outcome','accepted':False})
    before = len(provider.meter.receipts)
    diagnostic_reference = None
    v4 = config.get('task_routing_contract') == CANDIDATE_TASK_ROUTING
    def capture(diagnostic):
        nonlocal diagnostic_reference
        try:
            _require(diagnostic['trace_id']==provider.expected_trace,'Diagnostic trace must match the current case')
            diagnostic_reference = _write(out,'routing-diagnostics/'+key+'.json',
                {'schema':'candidate-task-assay-routing-diagnostics-v4-draft' if v4 else 'semantic-task-assay-routing-diagnostics-v1','case_id':key,'case_reference':ref,
                 'trace_id':provider.expected_trace,'diagnostics':diagnostic,'accepted':False,'offline_only':True})
        except Exception:
            provider.stop('routing_diagnostics_capture_failed')
    try:
        observation = run_fixed_case(ref,root=root,run_binding=binding,cancellation=cancel,
            executor=DefaultRagExecutor(provider_adapter=provider,budget_limits=RequestBudgetLimits(deadline_seconds=120,provider_retries=0)),
            budget_limits=RequestBudgetLimits(deadline_seconds=120,provider_retries=0),
            task_routing_contract=CANDIDATE_TASK_ROUTING if v4 else GRAPH_TASK_ROUTING,task_routing_diagnostics_observer=capture)
        observed_ref = _write(out,'observations/'+key+'.json',observation)
        grade = (grade_candidate_task_observation if v4 else grade_task_observation)(config['label_reference'],ref,observed_ref,root=root,run_binding=binding,
            source_capture_reference=config['source_capture_reference'],source_root=source_root)
        grade_ref = _write(out,'grades/'+key+'.json',grade)
        row = {'case_id':key,'status':'observed','routing_matches':grade['routing_matches'],
               'observation_reference':observed_ref,'grade_reference':grade_ref,'accepted':False}
    except Exception as error:
        row = {'case_id':key,'status':'failed','error_type':type(error).__name__,'accepted':False}
    receipt = provider.meter.receipts[-1] if len(provider.meter.receipts)>before else None
    fatal = (provider.meter.fatal_reason is not None or row['status']=='failed' and receipt is None or cancel.is_set() or time.monotonic()>=provider.deadline or receipt is not None and
        (not receipt['audit_complete'] or receipt['http_status'] in (401,403,404)))
    row = {**row,'routing_diagnostics_reference':diagnostic_reference,
           'provider_receipt':receipt,'model_classified':bool(receipt and receipt['actual_provider_response']),
           'stop_window':fatal,'fatal_reason':provider.meter.fatal_reason,'provider_call_count':len(provider.meter.traces)}
    _write(out,'cases/'+key+'-result.json',row)
    return row


def _verify_pins(config_reference,config,root,source_root,snapshot,case_refs=()):
    _load(config_reference,root)
    for key in ('authorization_reference','proposal_reference','prepared_manifest_reference','label_reference'):
        _load(config[key],root)
    manifest = _load(config['prepared_manifest_reference'],root)
    _verify_prepared_files(manifest,root)
    for reference in (*manifest['case_references'],*case_refs): _load(reference,root)
    _verify_assay_source(config,root,source_root)
    _require(_hash(snapshot)==config['source_snapshot_reference']['sha256'],'Source snapshot drift')


def _claim(config,root,out):
    authorization = resolve_path(config['authorization_reference']['path'],root).resolve()
    claim_root = authorization.parent/'semantic-assay-claims'
    _write(claim_root,config['authorization_reference']['sha256']+'.json',
        {'authorization_reference':config['authorization_reference'],'output_directory':str(out),
         'status':'consumed_no_resume','accepted':False})


def run_assay(config_reference,*,root,source_root,output_directory,llm_settings,cancellation=NEVER_CANCELLED):
    """Execute exactly one newly pinned synthetic assay; never load credentials."""
    root = Path(root).resolve()
    config,cases,snapshot,commit = _inputs(config_reference,root,source_root,llm_settings)
    out = Path(output_directory).resolve()
    _require(_path_identity(out).is_relative_to(_path_identity(root))
        and _path_identity(out)!=_path_identity(root),'New confined output directory required')
    if cancellation.is_set(): raise ExternalAICallCancelled('Assay cancelled before issuance')
    _require(not out.exists(),'New output destination required')
    _verify_pins(config_reference,config,root,source_root,snapshot)
    _claim(config,root,out)
    out.mkdir(parents=True,exist_ok=False)
    expires,deadline = datetime.now()+timedelta(minutes=60),time.monotonic()+3600
    audit_engine = create_engine('sqlite:///'+(out/'provider-audit.sqlite').as_posix())
    source_engine = None
    try:
        _policy_database(audit_engine,out,config['authorization_reference'],expires)
        source_engine = _source_engine(snapshot)
        authorization = _authorization(config,root)
        references,binding,run_pins = _run_files(out,config,config_reference,cases,commit,
            config['source_snapshot_reference']['sha256'],expires,llm_settings,authorization)
        provider = _Provider(llm_settings,audit_engine,out,cancellation,deadline,
            lambda:_verify_pins(config_reference,config,root,source_root,snapshot,[*references,*run_pins]),authorization['maximum_provider_calls'])
        rows = []
        for index,ref in enumerate(references):
            if cancellation.is_set() or time.monotonic()>=deadline: break
            provider.verify()
            with bind_repository_runtime(db_engine=_Engine(source_engine)), external_processing_context(cases[index]['actor']['username'],False), external_document_context([],[],['all_external']):
                row = _case_result(ref,config,root,source_root,out,binding,provider,cancellation)
            rows = [*rows,row]
            if row['stop_window']: break
        provider.verify()
        v4 = config.get('task_routing_contract') == CANDIDATE_TASK_ROUTING
        result = {'schema':'candidate-task-assay-result-v4-draft' if v4 else 'semantic-task-assay-result-v1',
            **({'task_routing_contract':CANDIDATE_TASK_ROUTING} if v4 else {}),'cases':rows,'attempted_provider_calls':len(provider.meter.traces),
            'unrun_case_ids':[c['case_id'] for c in cases[len(rows):]],'denominator':len(cases),'effective_temperature':None,
            'source_snapshot_unchanged':True,'accepted':False,'runtime_activation':False,'release_authorized':False}
    finally:
        try:
            closure = _close_policy(audit_engine,out,config['authorization_reference'])
        finally:
            if source_engine is not None: source_engine.dispose()
            audit_engine.dispose()
    result = {**result,'policy_closure_reference':closure}
    _write(out,'result.json',result)
    return result
