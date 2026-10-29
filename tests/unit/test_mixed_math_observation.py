"""Mixed evidence cannot inherit the fixed-only branch gate label."""
from dataclasses import replace
from .test_rag_bom_chat import bom_store, request, store, connection
from .test_rag_pipeline_public_characterization import offline_pipeline


def test_text_postcheck_accepts_only_bound_locator_sources():
    from mech_chatbot.application.chat_citations import structured_citation
    from mech_chatbot.rag.answer_checks import has_valid_source_citation, extract_source_ids
    record={'doc_id':1,'version_no':2,'locator':{'doc_id':'1','version':'2','sheet':'CSV','cell':'B2','row_key':'row-a'},
            'snapshot_id':'batch-1','source_hash':'a'*64,'scope_id':'a','scope_hash':'b'*64}
    card=structured_citation(record,page_has_vision=lambda *_:False)
    answer=f"The difference is 200 VND. [SRC:{card['source_id']}]"
    assert extract_source_ids(answer)=={card['source_id']}
    assert has_valid_source_citation(answer,[],dependency_sources=(card,))
    assert not has_valid_source_citation(answer,[])
    assert not has_valid_source_citation('[SRC:D1LFFFFFFFFFFFFFFFF]',[],dependency_sources=(card,))
    assert not has_valid_source_citation(answer,[],dependency_sources=({**card,'scope_hash':'invalid'},))
    assert not has_valid_source_citation(answer+' [SRC:invalid]',[],dependency_sources=(card,))
    assert not has_valid_source_citation(answer[:-1],[],dependency_sources=(card,))


def test_mixed_observation_has_separate_branch_contract(bom_store, offline_pipeline):
    from mech_chatbot.config.repository_runtime import bind_repository_runtime
    from mech_chatbot.domain.query_plan import QueryBranch, QueryPlan
    from mech_chatbot.evaluation.grounded_math_v2 import export_math_observation
    from mech_chatbot.rag.execution import RagInvocation, collect_rag_events
    _, engine = bom_store
    _, _, executor, _ = offline_pipeline
    original = request()
    selected = replace(original, bom_rollup=None, question='Tính BOM; tìm hướng dẫn',
        query_plan=QueryPlan((QueryBranch('total', 'Tính BOM', bom_rollup=original.bom_rollup),
                             QueryBranch('guide', 'Tìm hướng dẫn', text_query=True))))
    with bind_repository_runtime(db_engine=engine):
        result = collect_rag_events(executor.run(selected, RagInvocation(
            'mixed-observation', mode='test', capture_math_observations=True)))
    observation = export_math_observation(result)
    assert observation['query_branch_schema'] == 'query-branch-mixed-v1-draft'
    assert observation['calculation_count'] == 1
    assert [branch['template'] for branch in observation['query_branches']] == ['bom_rollup', 'text_query']
    assert observation['accepted'] is False
