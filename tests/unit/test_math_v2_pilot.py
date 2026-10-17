"""V2 pilot evidence comes from drained real comparisons, never relabeled v1."""
import json

from mech_chatbot.config.repository_runtime import bind_repository_runtime
from mech_chatbot.rag.execution import DefaultRagExecutor,RagInvocation,collect_rag_events,RagCompleted
from .test_math_v2_live_comparison import live_store,capability
from .test_rag_scope_comparison import request
from .test_structured_evidence_repository import store


def test_completed_comparison_emits_metadata_only_v2_evidence(monkeypatch,live_store):
    from mech_chatbot.config import logging
    calls=[]
    monkeypatch.setattr(logging,'log_trace',lambda event,trace,**kw:calls.append({'event':event,'trace_id':trace,**kw}))
    cap=capability(live_store)
    with bind_repository_runtime(db_engine=live_store):
        result=collect_rag_events(DefaultRagExecutor(math_v2_authorizer=lambda:cap).run(request(),RagInvocation('v2')))
    events=[event for event in calls if event['event']=='math_v2_comparison_completed']
    assert len(events)==1
    event=events[0]
    assert event['contract']==cap.contract and event['calculations']==4
    assert event['calculation_result_status']=='valid' and event['completion_outcome']=='answered'
    assert all(event[key] is True for key in ('security_passed','citation_structure_passed','provenance_passed'))
    assert event['estimated_cost']==0 and event['provider_retries']==event['final_generations']==0
    assert event['leakage_detected'] is False and result.diagnostics.budget.calculations==4
    raw=json.dumps(event)
    assert not any(key in event for key in ('question','answer','scopes','steps','scope_pins','scope_hash','value'))
    assert 'batch-1' not in raw and 'a'*64 not in raw
    assert not any(event['event']=='pilot_request_evidence' for event in calls)


def test_closing_at_completed_event_does_not_count_as_drained_request(monkeypatch,live_store):
    from mech_chatbot.config import logging
    calls=[]
    monkeypatch.setattr(logging,'log_trace',lambda event,*a,**kw:calls.append(event))
    with bind_repository_runtime(db_engine=live_store):
        cap=capability(live_store)
        stream=DefaultRagExecutor(math_v2_authorizer=lambda:cap).run(request(),RagInvocation('closed'))
        for event in stream:
            if isinstance(event,RagCompleted):
                stream.close()
                break
    assert 'math_v2_comparison_completed' not in calls


def test_failed_authorized_comparison_cannot_disappear_from_pilot_trace(monkeypatch,live_store):
    from mech_chatbot.config import logging
    calls=[]
    monkeypatch.setattr(logging,'log_trace',lambda event,trace,**kw:calls.append({'event':event,'trace_id':trace,**kw}))
    cap=capability(live_store)
    def fail(_):
        raise RuntimeError('private source detail')
    result=list(DefaultRagExecutor(math_v2_authorizer=lambda:cap,execute_pipeline=fail)
        .run(request(),RagInvocation('failed-comparison')))[-1]
    assert result.code=='RuntimeError'
    errors=[event for event in calls if event['event']=='rag_error']
    assert len(errors)==1 and errors[0]['trace_id']=='failed-comparison'
    assert errors[0]['execution_context']=='production'
    assert 'private source detail' not in json.dumps(errors)
