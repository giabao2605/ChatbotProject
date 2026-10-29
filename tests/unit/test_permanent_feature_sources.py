"""Permanent availability keeps live source review and access checks."""
from dataclasses import replace
import pytest
from mech_chatbot.config.repository_runtime import bind_repository_runtime
from mech_chatbot.db.repositories.structured_evidence import load_approved_scope
from mech_chatbot.rag.execution import DefaultRagExecutor, RagInvocation, collect_rag_events
from .test_query_v2_production import capability, selected_plan
from .test_rag_scope_comparison import approved_pair
from .test_structured_evidence_repository import PROFILE, store
from .test_rag_bom_chat import bom_store, connection


def permanent(grant):
    def child(cap):
        return replace(cap, context='default_rollout', expires_at=None, reviewed_sources=True)
    math=child(replace(grant.math, comparison=child(grant.math.comparison), total=child(grant.math.total)))
    return child(replace(grant, math=math, graph=child(grant.graph)))


def test_permanent_query_uses_current_reviewed_scopes_without_qa_pins(store):
    approved_pair(store)
    grant=permanent(capability(store))
    grant=replace(grant, math=replace(grant.math, total=replace(grant.math.total,scope_pins=()),
        comparison=replace(grant.math.comparison,scope_pins=())))
    with bind_repository_runtime(db_engine=store):
        result=collect_rag_events(DefaultRagExecutor(math_v2_authorizer=lambda:grant).run(
            selected_plan(),RagInvocation('permanent-reviewed')))
    assert result.diagnostics['query_v2']['status']=='complete'
    assert result.diagnostics['query_v2']['activation_scope']=='default_rollout'
    assert [b['math']['steps'][0]['display_value'] for b in result.diagnostics['query_v2']['branches']]==['4.00','4.00','8.00']


def test_permanent_math_rejects_unreviewed_or_unauthorized_scope(store):
    approved_pair(store)
    grant=permanent(capability(store))
    with bind_repository_runtime(db_engine=store):
        scope=load_approved_scope('a',PROFILE)
    assert grant.math.total.permits_scopes((scope,))
    assert not grant.math.total.permits_scopes((replace(scope,authorized=False),))
    assert not grant.math.total.permits_scopes((replace(scope,completeness_reviewed=False),))
    assert not replace(grant.math.total,context='controlled_demo').permits_scopes((scope,))


def test_permanent_query_still_rechecks_revocation(store):
    approved_pair(store)
    grant=permanent(capability(store))
    from mech_chatbot.rag.execution import RagToken
    active=[grant]
    with bind_repository_runtime(db_engine=store):
        events=DefaultRagExecutor(math_v2_authorizer=lambda:active[0]).run(selected_plan(),RagInvocation('revoke'))
        next(events)
        active[0]=None
        remainder=list(events)
    assert not any(isinstance(event,RagToken) for event in remainder)
    assert isinstance(remainder[-1].cause,PermissionError)


@pytest.mark.parametrize('partial',[False,True])
def test_permanent_bom_retains_completeness_gate(bom_store,partial):
    from sqlalchemy import text
    from .test_rag_bom_chat import request as bom_request
    from .test_query_v2_production import bom_capability
    store,engine=bom_store
    chosen=bom_request()
    if partial:
        with store.begin() as conn:
            conn.execute(text('UPDATE ReviewedGraphDeclarations SET ExpectedRows=2 WHERE DocID=1'))
    grant=permanent(bom_capability(engine,chosen))
    grant=replace(grant,graph=replace(grant.graph,graph_pins=()))
    with bind_repository_runtime(db_engine=engine):
        result=collect_rag_events(DefaultRagExecutor(math_v2_authorizer=lambda:grant).run(chosen,RagInvocation('permanent-bom')))
    assert result.outcome==('refused' if partial else 'answered')
    if partial:
        assert not result.citations and result.diagnostics['math_v2']['steps']==[]
    else:
        assert result.diagnostics['math_v2']['steps'][0]['display_value']=='6.00'


def test_permanent_query_history_roundtrip(store):
    from mech_chatbot.application.chat_result_history import serialize_history_result
    approved_pair(store)
    grant=permanent(capability(store))
    with bind_repository_runtime(db_engine=store):
        result=collect_rag_events(DefaultRagExecutor(math_v2_authorizer=lambda:grant).run(selected_plan(),RagInvocation('history')))
    query=result.diagnostics['query_v2']
    payload=serialize_history_result({'query':query},result.citations)
    assert 'default_rollout' in payload


@pytest.mark.parametrize('fault',['department','current_version','snapshot_hash','review'])
def test_permanent_availability_does_not_bypass_current_source_guards(store,fault):
    from sqlalchemy import text
    approved_pair(store)
    grant=permanent(capability(store))
    with store.begin() as conn:
        if fault=='department':
            conn.execute(text("UPDATE TaiLieu SET ThuMuc='OTHER'"))
            conn.execute(text("DELETE FROM PhongBanChiaSe"))
        elif fault=='current_version':
            conn.execute(text('UPDATE TaiLieu SET IsCurrent=0'))
        elif fault=='snapshot_hash':
            conn.execute(text("UPDATE StructuredSourceSnapshot SET SourceHash=:bad"),{'bad':'0'*64})
        else:
            conn.execute(text("UPDATE StructuredNumericFact SET ReviewStatus='pending'"))
    with bind_repository_runtime(db_engine=store):
        result=collect_rag_events(DefaultRagExecutor(math_v2_authorizer=lambda:grant).run(selected_plan(),RagInvocation('source-guard')))
    assert not result.citations
    assert all(branch['status']=='unknown' and branch['reason']=='evidence_unavailable'
               and not branch.get('math',{}).get('steps')
               for branch in result.diagnostics['query_v2']['branches'])
    assert not any(value in result.answer for value in ('4.00','8.00'))
