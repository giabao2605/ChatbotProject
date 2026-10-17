"""Bounded isolated diagnostic windows through the public assay seam."""
import hashlib
import json
from pathlib import Path
import sqlite3

import pytest
from sqlalchemy import event
from sqlalchemy.engine import Engine
from sqlalchemy.exc import OperationalError

from .test_semantic_task_assay import SETTINGS, assay_inputs, transport
from .test_rag_scope_comparison import store
from .test_semantic_task_compilation_grading import artifact


def capped_inputs(root,engine,count=3,cap=3):
    reference,source_root=assay_inputs(root,engine,count=count)
    config=json.loads((root/reference['path']).read_text(encoding='utf8'))
    proposal_ref=config['proposal_reference']
    proposal=json.loads((root/proposal_ref['path']).read_text(encoding='utf8'))
    proposal_ref=artifact(root,proposal_ref['path'],{**proposal,'maximum_case_count':cap,'maximum_provider_calls':cap})
    auth_ref=config['authorization_reference']
    auth=json.loads((root/auth_ref['path']).read_text(encoding='utf8'))
    auth_ref=artifact(root,auth_ref['path'],{**auth,'proposal_reference':proposal_ref,
        'maximum_case_count':cap,'maximum_provider_calls':cap})
    return artifact(root,reference['path'],{**config,'proposal_reference':proposal_ref,'authorization_reference':auth_ref}),source_root


def closed_profile(out):
    conn=sqlite3.connect((out/'provider-audit.sqlite').as_uri()+'?mode=ro',uri=True)
    try:
        return conn.execute('SELECT IsActive FROM ExternalAIProviderProfile').fetchone()[0]==0
    finally:conn.close()


def test_three_call_authorization_emits_redacted_sidecars_and_closes_policy(store,tmp_path,monkeypatch):
    from scripts.eval.run_semantic_task_assay import run_assay
    reference,source_root=capped_inputs(tmp_path,store);calls=transport(monkeypatch)
    out=tmp_path/'run'
    result=run_assay(reference,root=tmp_path,source_root=source_root,output_directory=out,llm_settings=SETTINGS)
    assert len(calls)==result['attempted_provider_calls']==3
    assert closed_profile(out)
    assert result['policy_closure_reference']['sha256']==hashlib.sha256(Path(result['policy_closure_reference']['path']).read_bytes()).hexdigest()
    for row in result['cases']:
        ref=row['routing_diagnostics_reference'];path=Path(ref['path'])
        assert hashlib.sha256(path.read_bytes()).hexdigest()==ref['sha256']
        sidecar=json.loads(path.read_text(encoding='utf8'))
        assert sidecar['diagnostics']['failure_code'] is None
        assert sidecar['case_id']==row['case_id'] and sidecar['diagnostics']['trace_id']==sidecar['trace_id']
        assert 'question' not in sidecar and 'actor' not in sidecar
    config=json.loads((out/'config.json').read_text(encoding='utf8'))
    assert config['maximum_provider_calls']==3
    assert result['accepted'] is False and result['release_authorized'] is False


@pytest.mark.parametrize('cap',[0,-1,22,True,3.0,'3'])
def test_invalid_reduced_caps_never_claim_or_send(store,tmp_path,monkeypatch,cap):
    from scripts.eval.run_semantic_task_assay import run_assay
    reference,source_root=capped_inputs(tmp_path,store,count=1,cap=cap);calls=transport(monkeypatch)
    with pytest.raises(ValueError):
        run_assay(reference,root=tmp_path,source_root=source_root,output_directory=tmp_path/'run',llm_settings=SETTINGS)
    assert not calls and not (tmp_path/'semantic-assay-claims').exists()


def test_selected_cases_cannot_exceed_the_approved_reduced_cap(store,tmp_path,monkeypatch):
    from scripts.eval.run_semantic_task_assay import run_assay
    reference,source_root=capped_inputs(tmp_path,store,count=3,cap=2);calls=transport(monkeypatch)
    with pytest.raises(ValueError):
        run_assay(reference,root=tmp_path,source_root=source_root,output_directory=tmp_path/'run',llm_settings=SETTINGS)
    assert not calls and not (tmp_path/'semantic-assay-claims').exists()


def test_proposal_and_authorization_caps_must_match(store,tmp_path,monkeypatch):
    from scripts.eval.run_semantic_task_assay import run_assay
    reference,source_root=capped_inputs(tmp_path,store);config=json.loads((tmp_path/reference['path']).read_text(encoding='utf8'))
    authref=config['authorization_reference'];auth=json.loads((tmp_path/authref['path']).read_text(encoding='utf8'))
    authref=artifact(tmp_path,authref['path'],{**auth,'maximum_provider_calls':2})
    reference=artifact(tmp_path,reference['path'],{**config,'authorization_reference':authref});calls=transport(monkeypatch)
    with pytest.raises(ValueError):
        run_assay(reference,root=tmp_path,source_root=source_root,output_directory=tmp_path/'run',llm_settings=SETTINGS)
    assert not calls


def test_invalid_model_plan_has_bounded_code_and_preserves_old_observation(store,tmp_path,monkeypatch):
    from scripts.eval.run_semantic_task_assay import run_assay
    reference,source_root=capped_inputs(tmp_path,store,count=1,cap=1);calls=transport(monkeypatch,'invalid')
    out=tmp_path/'run';result=run_assay(reference,root=tmp_path,source_root=source_root,output_directory=out,llm_settings=SETTINGS)
    row=result['cases'][0];receipt=json.loads(Path(row['routing_diagnostics_reference']['path']).read_text(encoding='utf8'))
    assert receipt['diagnostics']['failure_code']=='contract' and len(calls)==1 and closed_profile(out)
    observation=json.loads(Path(row['observation_reference']['path']).read_text(encoding='utf8'))
    assert observation['task_routing']['disposition']=='invalid'
    assert 'failure_code' not in observation['task_routing'] and 'failure_code' not in observation
    assert receipt['diagnostics']['schema']=='task-routing-diagnostics-v1'


@pytest.mark.parametrize('outcome',['error',401])
def test_policy_closes_after_provider_failure(store,tmp_path,monkeypatch,outcome):
    from scripts.eval.run_semantic_task_assay import run_assay
    reference,source_root=capped_inputs(tmp_path,store,count=2,cap=2);calls=transport(monkeypatch,outcome)
    out=tmp_path/'run';result=run_assay(reference,root=tmp_path,source_root=source_root,output_directory=out,llm_settings=SETTINGS)
    assert len(calls)==(1 if outcome==401 else 2) and closed_profile(out)
    assert result['policy_closure_reference'] and (tmp_path/'semantic-assay-claims').exists()


def test_policy_closes_after_cancellation_during_provider(store,tmp_path,monkeypatch):
    import threading
    from scripts.eval.run_semantic_task_assay import run_assay
    reference,source_root=capped_inputs(tmp_path,store,count=2,cap=2);cancel=threading.Event()
    calls=transport(monkeypatch,callback=cancel.set);out=tmp_path/'run'
    result=run_assay(reference,root=tmp_path,source_root=source_root,output_directory=out,llm_settings=SETTINGS,cancellation=cancel)
    assert len(calls)==1 and result['unrun_case_ids']==['SECOND'] and closed_profile(out)


@pytest.mark.parametrize('stage',['audit_preflight','audit_terminal','construction','diagnostic_write','profile_table_creation'])
def test_isolated_policy_closes_on_initialization_audit_or_observer_failure(store,tmp_path,monkeypatch,stage):
    from scripts.eval.run_semantic_task_assay import run_assay
    reference,source_root=capped_inputs(tmp_path,store,count=2,cap=2);out=tmp_path/'run';calls=transport(monkeypatch)
    original=Path.open
    def open_path(path,*args,**kwargs):
        if path.parent==out and path.name=='config.json' and stage=='construction':raise OSError('synthetic write failed')
        if path.parent==out/'routing-diagnostics' and stage=='diagnostic_write':raise OSError('synthetic diagnostic write failed')
        return original(path,*args,**kwargs)
    monkeypatch.setattr(Path,'open',open_path)
    def fault(conn,cursor,statement,parameters,context,many):
        if stage=='profile_table_creation' and statement.startswith('CREATE TABLE ExternalAIProviderProfile'):
            raise OperationalError('synthetic creation failed',None,None)
        if 'INSERT INTO' in statement and 'ExternalAICallAudit' in statement:
            if stage=='audit_preflight' or stage=='audit_terminal' and parameters[15]=='success':
                raise OperationalError('synthetic audit failed',None,None)
    event.listen(Engine,'before_cursor_execute',fault)
    try:
        if stage in ('construction','profile_table_creation'):
            with pytest.raises((OSError,OperationalError)):
                run_assay(reference,root=tmp_path,source_root=source_root,output_directory=out,llm_settings=SETTINGS)
        else:
            result=run_assay(reference,root=tmp_path,source_root=source_root,output_directory=out,llm_settings=SETTINGS)
            assert result['cases'][0]['stop_window'] and result['unrun_case_ids']==['SECOND']
    finally:event.remove(Engine,'before_cursor_execute',fault)
    assert len(calls)==(1 if stage in ('audit_terminal','diagnostic_write') else 0)
    closure=json.loads((out/'policy-closure.json').read_text(encoding='utf8'))
    if stage=='profile_table_creation':assert closure['status']=='not_issued'
    else:assert closed_profile(out)


def test_policy_close_failure_is_explicit_and_does_not_publish_success(store,tmp_path,monkeypatch):
    from scripts.eval.run_semantic_task_assay import run_assay
    reference,source_root=capped_inputs(tmp_path,store,count=1,cap=1);calls=transport(monkeypatch);out=tmp_path/'run'
    def fault(conn,cursor,statement,parameters,context,many):
        if statement.startswith('UPDATE ExternalAIProviderProfile SET IsActive=0'):
            raise OperationalError('synthetic close failed',None,None)
    event.listen(Engine,'before_cursor_execute',fault)
    try:
        with pytest.raises(OperationalError):
            run_assay(reference,root=tmp_path,source_root=source_root,output_directory=out,llm_settings=SETTINGS)
    finally:event.remove(Engine,'before_cursor_execute',fault)
    assert len(calls)==1 and not (out/'result.json').exists()
    assert (out/'policy-close-failure.json').exists() and (tmp_path/'semantic-assay-claims').exists()
