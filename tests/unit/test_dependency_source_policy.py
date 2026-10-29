"""Computed evidence retains SQL policy and pins before entering a text prompt."""
from dataclasses import replace
import pytest
from sqlalchemy import text
from tests.unit.test_rag_scope_comparison import approved_pair, request, store
from mech_chatbot.config.repository_runtime import bind_repository_runtime
from mech_chatbot.rag.execution import DefaultRagExecutor,RagInvocation,collect_rag_events


def cards(store):
    approved_pair(store,values=('1000','1200'))
    with bind_repository_runtime(db_engine=store):
        return collect_rag_events(DefaultRagExecutor().run(request(),RagInvocation('dependency-pins',mode='test'))).citations


def test_dependency_reads_real_sql_policy_and_refuses_changed_basis(store):
    from mech_chatbot.services.rag_evidence_service import dependency_source_attributes
    sources=cards(store)
    with store.begin() as conn:
        conn.execute(text('ALTER TABLE TaiLieu ADD COLUMN ExternalProcessingPolicy TEXT'))
        conn.execute(text("UPDATE TaiLieu SET ExternalProcessingPolicy='all_external'"))
    with bind_repository_runtime(db_engine=store):
        attrs=dependency_source_attributes(sources,request().access)
        assert attrs==({'doc_id':1,'security_level':'internal','external_processing_policy':'all_external'},)
        with pytest.raises(PermissionError):
            dependency_source_attributes(sources,replace(request().access,allowed_sites=frozenset({'other'})))
    with store.begin() as conn:
        conn.execute(text('UPDATE StructuredEvidenceScope SET CompletenessReviewed=0'))
    with bind_repository_runtime(db_engine=store), pytest.raises(PermissionError):
        dependency_source_attributes(sources,request().access)


def test_dependency_without_explicit_external_policy_defaults_internal(store):
    from mech_chatbot.services.rag_evidence_service import dependency_source_attributes
    sources=cards(store)
    with bind_repository_runtime(db_engine=store):
        assert dependency_source_attributes(sources,request().access)[0]['external_processing_policy']=='internal_only'
        with pytest.raises(PermissionError):
            dependency_source_attributes(({**sources[0],'scope_hash':'f'*64},),request().access)


def test_sql_dependency_policy_reaches_real_external_boundary(store):
    from mech_chatbot.services.rag_evidence_service import dependency_source_attributes
    from mech_chatbot.llm import external_ai
    from tests.unit.test_external_ai_policy import _external_settings, _profile
    sources=cards(store)
    with bind_repository_runtime(db_engine=store):
        attrs=dependency_source_attributes(sources,request().access)
    with pytest.raises(external_ai.ExternalProcessingDenied):
        external_ai.make_external_call_spec(provider='proxyllm',model='model-test',surface='generation',
            policies=[entry['external_processing_policy'] for entry in attrs],
            profile=_profile(),settings=_external_settings())
    with store.begin() as conn:
        conn.execute(text('ALTER TABLE TaiLieu ADD COLUMN ExternalProcessingPolicy TEXT'))
        conn.execute(text("UPDATE TaiLieu SET ExternalProcessingPolicy='all_external'"))
    with bind_repository_runtime(db_engine=store):
        attrs=dependency_source_attributes(sources,request().access)
    spec=external_ai.make_external_call_spec(provider='proxyllm',model='model-test',surface='generation',
        policies=[entry['external_processing_policy'] for entry in attrs],
        profile=_profile(),settings=_external_settings())
    assert spec.policies==('all_external',)
