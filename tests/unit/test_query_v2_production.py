"""Live typed Query actions keep child source authority and one request budget."""
from dataclasses import replace
from datetime import datetime, timedelta, timezone
import json

import pytest

from mech_chatbot.config.repository_runtime import bind_repository_runtime
from mech_chatbot.db.repositories.structured_evidence import load_approved_scope
from mech_chatbot.domain.query_plan import QueryBranch, QueryPlan
from mech_chatbot.domain.structured_evidence import reviewed_scope_fingerprint
from mech_chatbot.governance.graph_v2_activation import GraphV2Capability, CONTRACT as GRAPH_CONTRACT
from mech_chatbot.governance.math_templates_activation import MathTemplatesCapability
from mech_chatbot.governance.math_v2_activation import MathV2Capability, CONTRACT as COMPARISON_CONTRACT
from mech_chatbot.governance.scope_total_activation import ScopeTotalCapability, CONTRACT as TOTAL_CONTRACT
from mech_chatbot.rag.execution import DefaultRagExecutor, RagInvocation, RagToken, collect_rag_events
from .test_rag_scope_comparison import approved_pair, request
from .test_structured_evidence_repository import PROFILE, store
from .test_rag_pipeline_public_characterization import offline_pipeline


def capability(store, *, graph_pins=(), scope_ids=('a', 'b'), profile=PROFILE):
    from mech_chatbot.governance.query_v2_activation import QueryV2Capability
    expiry = (datetime.now(timezone.utc) + timedelta(hours=1)).isoformat()
    with bind_repository_runtime(db_engine=store):
        scopes = tuple(load_approved_scope(key, profile) for key in scope_ids)
    pins = tuple((scope.scope_id, scope.snapshot_id, reviewed_scope_fingerprint(scope)) for scope in scopes)
    comparison = MathV2Capability(COMPARISON_CONTRACT, 'compare_two_scopes', pins,
        'candidate', 'c'*64, 'd'*64, expiry)
    total = ScopeTotalCapability(TOTAL_CONTRACT, 'sum_reviewed_scope', pins,
        'candidate', 'c'*64, 'e'*64, expiry)
    math = MathTemplatesCapability(comparison, total, 'candidate', 'c'*64, 'f'*64, expiry)
    graph = GraphV2Capability(GRAPH_CONTRACT, graph_pins, 'candidate', 'c'*64, 'a'*64, expiry)
    return QueryV2Capability(math, graph, 'candidate', 'c'*64, 'b'*64, expiry)


def selected_plan():
    return replace(request(), scope_comparison=None, query_plan=QueryPlan((
        QueryBranch('total_a', 'Total A', scope_total='a'),
        QueryBranch('comparison', 'Compare', scope_comparison=('a', 'b')),
        QueryBranch('total_b', 'Total B', scope_total='b'))))


def test_production_query_selects_each_child_and_shares_six_calculations(store):
    approved_pair(store)
    grant = capability(store)
    with bind_repository_runtime(db_engine=store):
        result = collect_rag_events(DefaultRagExecutor(math_v2_authorizer=lambda: grant).run(
            selected_plan(), RagInvocation('production-query')))
    query = result.diagnostics['query_v2']
    assert query['contract'] == 'query-reviewed-actions-controlled-demo-v1'
    assert query['offline_only'] is False and query['accepted'] is False
    assert query['activation_scope'] == 'controlled_demo' and query['status'] == 'complete'
    assert [branch['math']['steps'][0]['display_value'] for branch in query['branches']] == ['4.00', '4.00', '8.00']
    assert [{card['scope_id'] for card in branch['sources']} for branch in query['branches']] == [{'a'}, {'a', 'b'}, {'b'}]
    assert result.diagnostics.budget.calculations == 6 and result.diagnostics.budget.subqueries == 3
    assert result.diagnostics.budget.planners == result.diagnostics.budget.final_generations == 0


def test_math_parent_cannot_authorize_a_query_plan(store):
    approved_pair(store)
    math = capability(store).math
    with pytest.raises(PermissionError):
        collect_rag_events(DefaultRagExecutor(math_v2_authorizer=lambda: math).run(
            selected_plan(), RagInvocation('math-is-not-query')))


def test_query_grant_rejects_scope_outside_its_total_child_before_read(store):
    approved_pair(store)
    grant = capability(store)
    chosen = replace(request(), scope_comparison=None, query_plan=QueryPlan((
        QueryBranch('missing', 'Outside authority', scope_total='unapproved'),)))
    with pytest.raises(PermissionError):
        collect_rag_events(DefaultRagExecutor(math_v2_authorizer=lambda: grant).run(
            chosen, RagInvocation('unapproved-plan')))


def test_query_parent_revoked_after_prepare_emits_no_answer_token(store):
    approved_pair(store)
    grant = capability(store)
    active = [grant]
    with bind_repository_runtime(db_engine=store):
        events = DefaultRagExecutor(math_v2_authorizer=lambda: active[0]).run(
            selected_plan(), RagInvocation('revoked-after-prepare'))
        next(events)
        active[0] = None
        remainder = list(events)
    assert not any(isinstance(event, RagToken) for event in remainder)
    assert isinstance(remainder[-1].cause, PermissionError)


def test_production_totals_classify_original_request_once_then_reuse_binding(store):
    from .test_query_scope_total_routing import Planner, payload, VERSION
    approved_pair(store)
    grant = capability(store)
    chosen = replace(request(), question='Please total A; please total B', scope_comparison=None,
        query_plan=QueryPlan((QueryBranch('a', 'Please total A', scope_total='a'),
                              QueryBranch('b', 'please total B', scope_total='b'))))
    planner = Planner(payload(chosen))
    with bind_repository_runtime(db_engine=store):
        result = collect_rag_events(DefaultRagExecutor(provider_adapter=planner,
            math_v2_authorizer=lambda: grant).run(chosen,
                RagInvocation('live-classification', task_routing_contract=VERSION)))
    assert result.diagnostics['query_v2']['status'] == 'complete'
    assert result.diagnostics.budget.planners == len(planner.calls) == 1
    assert result.diagnostics.budget.calculations == 2
    assert planner.calls[0][1]['retry_counter'].limits.provider_retries == 0


from .test_rag_bom_chat import bom_store, connection, request as bom_request, PROFILE as BOM_PROFILE


def bom_capability(engine, chosen):
    from mech_chatbot.db.repositories.graph_v2 import retrieve_graph_v2
    from mech_chatbot.domain.graph_query import graph_query_context, graph_fingerprint
    query = chosen.bom_rollup.graph
    with bind_repository_runtime(db_engine=engine):
        tree = retrieve_graph_v2(query.root_key, BOM_PROFILE, product_scope=query.product_scope,
            site=query.site, direction=query.direction, max_relations=query.max_relations,
            max_generation_edges=query.max_generation_edges)
    pins = ((json.dumps(graph_query_context(query), sort_keys=True, separators=(',', ':')),
             graph_fingerprint(tree)),)
    return capability(engine, graph_pins=pins, scope_ids=('quantity-scope', 'policy'), profile=BOM_PROFILE)


@pytest.mark.parametrize('partial', [False, True])
def test_production_bom_requires_exact_reviewed_graph_and_complete_tree(bom_store, partial):
    from sqlalchemy import text
    store, engine = bom_store
    chosen = bom_request()
    if partial:
        with store.begin() as conn:
            conn.execute(text('UPDATE ReviewedGraphDeclarations SET ExpectedRows=2 WHERE DocID=1'))
    grant = bom_capability(engine, chosen)
    with bind_repository_runtime(db_engine=engine):
        result = collect_rag_events(DefaultRagExecutor(math_v2_authorizer=lambda: grant).run(
            chosen, RagInvocation('live-bom')))
    metadata = result.diagnostics['math_v2']
    assert metadata['contract'] == 'query-reviewed-actions-controlled-demo-v1'
    assert metadata['offline_only'] is False
    if partial:
        assert result.outcome == 'refused' and not result.citations and metadata['steps'] == []
    else:
        assert result.outcome == 'answered' and metadata['steps'][0]['display_value'] == '6.00'
        assert result.diagnostics.budget.calculations == 1


def test_production_candidate_classifier_rechecks_resolved_graph_plan(bom_store):
    from .test_candidate_task_routing import candidate_request, candidate_payload
    from .test_semantic_graph_task_routing import Planner
    from mech_chatbot.domain.task_routing import CANDIDATE_TASK_ROUTING
    _, engine = bom_store
    grant = bom_capability(engine, bom_request())
    chosen = candidate_request()
    planner = Planner(candidate_payload(chosen.question))
    with bind_repository_runtime(db_engine=engine):
        result = collect_rag_events(DefaultRagExecutor(provider_adapter=planner,
            math_v2_authorizer=lambda: grant).run(chosen,
                RagInvocation('live-candidate', task_routing_contract=CANDIDATE_TASK_ROUTING)))
    assert result.diagnostics['query_v2']['status'] == 'complete'
    assert result.diagnostics['query_v2']['offline_only'] is False
    assert result.diagnostics.budget.planners == len(planner.calls) == 1
    assert result.diagnostics.budget.calculations == 0


def test_math_parent_cannot_authorize_text_or_bom(store):
    approved_pair(store)
    math = capability(store).math
    chosen = replace(request(), scope_comparison=None, text_query=True)
    with pytest.raises(PermissionError):
        collect_rag_events(DefaultRagExecutor(math_v2_authorizer=lambda: math).run(
            chosen, RagInvocation('math-is-not-text')))
    with pytest.raises(PermissionError):
        collect_rag_events(DefaultRagExecutor(math_v2_authorizer=lambda: math).run(
            bom_request(), RagInvocation('math-is-not-bom')))


def test_full_query_authorizes_production_text_without_inventing_missing_sources(store, offline_pipeline):
    approved_pair(store)
    grant = capability(store)
    _, _, _, runtime = offline_pipeline
    chosen = replace(request(), question='Quy trình đào tạo là gì?', scope_comparison=None, text_query=True)
    result = collect_rag_events(DefaultRagExecutor(retrieval_adapter=runtime,
        math_v2_authorizer=lambda: grant).run(chosen, RagInvocation('live-text')))
    assert result.outcome == 'refused' and result.citations == ()
    assert result.diagnostics['text_query_v2'] == {'schema': 'query-text-v2', 'accepted': False,
        'offline_only': False, 'contract': 'query-reviewed-actions-controlled-demo-v1',
        'activation_scope': 'controlled_demo'}
    assert result.diagnostics.budget.calculations == result.diagnostics.budget.final_generations == 0
