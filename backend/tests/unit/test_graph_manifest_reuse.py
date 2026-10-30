"""A dependent BOM uses the full graph already read in this SQL snapshot."""
from dataclasses import replace
import pytest
from sqlalchemy import event, text

from mech_chatbot.domain.query_plan import QueryPlan
from .test_rag_bom_chat import bom_store, store, connection, run, expand_bom, history_store
from .test_rag_query_plan import combined_request


def dependent_request():
    request = combined_request()
    graph, total = request.query_plan.branches
    return replace(request,query_plan=QueryPlan((graph,replace(total,depends_on=('relations',)))))


def test_dependent_bom_uses_300_occurrences_without_spending_600_relations(bom_store):
    database, engine = bom_store
    expand_bom(database,count=300)
    reads = []
    event.listen(database,'before_cursor_execute',lambda conn,cursor,statement,parameters,context,many:
        reads.append(statement) if 'SafeOccurrences' in statement else None)
    result = run(engine,dependent_request())
    branches = result.diagnostics['query_v2']['branches']
    assert branches[0]['data_complete'] is True and branches[0]['status'] == 'partial'
    assert len(branches[0]['graph']['edges']) == 50
    assert len(branches[0]['graph']['manifest']['occurrences']) == 300
    assert branches[1]['status'] == 'complete'
    assert branches[1]['math']['steps'][0]['display_value'] == '1800.00'
    assert result.diagnostics.budget.graph_examined_relations == 300
    assert result.diagnostics.budget.graph_edges == 50
    assert result.diagnostics.budget.calculations == 1
    assert len(branches[1]['sources']) == 301
    assert branches[1]['math']['graph_reused_from'] == 'relations'
    assert len(reads) == 1


def test_display_limit_does_not_shrink_the_full_manifest_or_force_a_second_read(bom_store):
    database, engine = bom_store
    expand_bom(database,count=51)
    query = dependent_request()
    graph, total = query.query_plan.branches
    graph = replace(graph,graph_query=replace(graph.graph_query,max_generation_edges=25))
    result = run(engine,replace(query,query_plan=QueryPlan((graph,total))))
    branches = result.diagnostics['query_v2']['branches']
    assert branches[1]['math']['steps'][0]['display_value'] == '306.00'
    assert branches[1]['math']['graph_reused_from'] == 'relations'
    assert len(branches[0]['graph']['edges']) == result.diagnostics.budget.graph_edges == 25
    assert result.diagnostics.budget.graph_examined_relations == 51


@pytest.mark.parametrize('field,value',[('root_key','document:2'),('product_scope','other-product'),
    ('site','other-site'),('max_relations',25)])
def test_a_different_bom_scope_or_lower_data_limit_never_reuses_the_other_tree(bom_store,field,value):
    database, engine = bom_store
    expand_bom(database,count=51)
    query = dependent_request()
    graph, total = query.query_plan.branches
    selection = replace(total.bom_rollup,graph=replace(total.bom_rollup.graph,**{field:value}))
    result = run(engine,replace(query,query_plan=QueryPlan((graph,replace(total,bom_rollup=selection)))))
    branches = result.diagnostics['query_v2']['branches']
    assert branches[0]['data_complete'] and branches[1]['status'] == 'unknown'
    assert 'graph_reused_from' not in branches[1]['math']
    assert branches[1]['sources'] == [] and branches[1]['math']['steps'] == []
    assert result.diagnostics.budget.calculations == 0


def test_independent_actions_still_spend_their_own_retrieval_budget(bom_store):
    database, engine = bom_store
    expand_bom(database,count=51)
    result = run(engine,combined_request())
    branch = result.diagnostics['query_v2']['branches'][1]
    assert branch['status'] == 'complete' and 'graph_reused_from' not in branch['math']
    assert branch['math']['steps'][0]['display_value'] == '306.00'
    assert result.diagnostics.budget.graph_examined_relations == 102


def test_a_reused_tree_still_requires_a_current_reviewed_rounding_policy(bom_store):
    database, engine = bom_store
    with database.begin() as conn:
        conn.execute(text("UPDATE StructuredEvidenceScope SET CompletenessReviewed=0 WHERE ScopeID='policy'"))
    result = run(engine,dependent_request())
    branches = result.diagnostics['query_v2']['branches']
    assert branches[0]['status'] == 'complete' and branches[1]['status'] == 'unknown'
    assert branches[1]['sources'] == [] and branches[1]['math']['steps'] == []
    assert result.diagnostics.budget.calculations == 0


@pytest.mark.parametrize('change',['quantity','policy','site'])
def test_reused_source_pins_revalidate_history_then_hide_a_changed_basis(bom_store,history_store,change):
    from mech_chatbot.db.repositories import chat

    database, engine = bom_store
    expand_bom(database,count=51)
    query = dependent_request()
    graph, total = query.query_plan.branches
    graph = replace(graph,graph_query=replace(graph.graph_query,max_generation_edges=25))
    result = run(engine,replace(query,query_plan=QueryPlan((graph,total))))
    with database.begin() as conn:
        conn.execute(text("INSERT INTO LichSuChat VALUES(61,'reused','Original',:answer,NULL,NULL,NULL,'approver','2026-10-15')"),
            {'answer':result.answer})
    assert chat.save_answer_evidence(61,list(result.diagnostics['retrieved_docs']),db_engine=engine)
    assert chat.save_answer_sources(61,list(result.citations),db_engine=engine)
    def read():
        return chat.get_chat_history('reused',username='approver',allowed_departments=['tech','ENG'],
            allowed_sites=['factory'],user_clearance='internal',db_engine=engine)[-1]
    assert read()['content'] == result.answer and '306.00' in result.answer
    with database.begin() as conn:
        updates = {'quantity':"UPDATE ReviewedGraphOccurrences SET Quantity='999' WHERE EdgeID=51",
            'policy':"UPDATE StructuredEvidenceScope SET CompletenessReviewed=0 WHERE ScopeID='policy'",
            'site':"UPDATE TaiLieu SET Site='other-site' WHERE DocID=1"}
        conn.execute(text(updates[change]))
    assert '306.00' not in read()['content']
