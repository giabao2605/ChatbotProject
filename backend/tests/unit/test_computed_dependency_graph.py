"""Dependent text uses the full BOM result without re-traversing the graph."""
from contextlib import nullcontext
from dataclasses import replace
from types import SimpleNamespace
import re

from langchain_core.messages import AIMessage
from langchain_core.runnables import RunnableLambda
from sqlalchemy import text
from .test_rag_bom_chat import bom_store, store, connection, request, expand_bom
from .test_rag_pipeline_public_characterization import offline_pipeline, _document
from mech_chatbot.config.repository_runtime import bind_repository_runtime
from mech_chatbot.domain.query_plan import QueryBranch, QueryPlan
from mech_chatbot.rag.execution import DefaultRagExecutor, RagInvocation, collect_rag_events


def test_bom_dependency_keeps_manifest_total_beyond_context_edge_limit(bom_store, offline_pipeline, monkeypatch):
    from mech_chatbot.rag import pipeline_steps
    source_store, engine = bom_store
    expand_bom(source_store, 51)
    with source_store.begin() as conn:
        conn.execute(text('ALTER TABLE TaiLieu ADD COLUMN ExternalProcessingPolicy TEXT'))
        conn.execute(text("UPDATE TaiLieu SET ExternalProcessingPolicy='all_external'"))
    docs, _, _, runtime = offline_pipeline
    docs.append(_document('Assembly quantities follow the reviewed occurrence paths, multiplied by order quantity.'))
    prompts = []
    def generate(prompt):
        prompts.append(str(prompt))
        locators = sorted(set(re.findall(r'D\d+L[0-9A-Fa-f]{16}', prompts[-1])))
        assert '306.00' in prompts[-1]
        return AIMessage(content='The part total follows the reviewed occurrence paths and order quantity. '
            + ' '.join('[SRC:'+source+']' for source in locators) + ' [SRC:D17P2]')
    monkeypatch.setattr(pipeline_steps, 'audited_external_call', lambda **kwargs: nullcontext())
    provider = SimpleNamespace(client=RunnableLambda(generate),
        settings=SimpleNamespace(model_name='fixture', base_url='http://127.0.0.1/fixture'),
        invoke=lambda *_a, **_kw: SimpleNamespace(content=''))
    original = request()
    selected = replace(original, bom_rollup=None, question='Tính BOM; giải thích cơ sở số lượng',
        access=replace(original.access, allowed_departments=frozenset({'tech','ENG','HR'}),
                       allowed_sites=frozenset({'factory','HCM'})),
        query_plan=QueryPlan((QueryBranch('bom','Tính BOM',bom_rollup=original.bom_rollup),
            QueryBranch('explain','Giải thích cơ sở số lượng',text_query=True,depends_on=('bom',)))))
    with bind_repository_runtime(db_engine=engine):
        result = collect_rag_events(DefaultRagExecutor(retrieval_adapter=runtime, provider_adapter=provider).run(
            selected, RagInvocation('bom-dependent-explanation',mode='test')))
    branches = result.diagnostics['query_v2']['branches']
    assert [branch['status'] for branch in branches] == ['complete','complete']
    assert branches[0]['math']['steps'][0]['display_value'] == '306.00'
    assert len(branches[0]['math']['steps'][0]['source_ids']) == 51
    assert len([source for source in branches[1]['sources'] if source.get('graph_context')]) == 51
    assert len(prompts) == result.diagnostics.budget.final_generations == 1
    assert result.diagnostics.budget.graph_examined_relations == 51
    assert result.diagnostics.budget.graph_edges == 50
    assert result.diagnostics.budget.calculations == 1 and result.diagnostics.budget.subqueries == 2
