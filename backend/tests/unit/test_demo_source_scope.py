from langchain_core.documents import Document

from mech_chatbot.rag import demo_sources


def test_demo_classification_uses_metadata_not_filename():
    assert demo_sources.is_demo_source({'tags': 'demo,IT'})
    assert demo_sources.is_demo_source({'source_system': 'demo-wave-v1'})
    assert not demo_sources.is_demo_source({'file_goc': 'demo_process.md', 'tags': 'approved'})
    assert demo_sources.is_demo_source({'tags': None, 'source_system': 'upload',
        'doc_number': 'DEMO-IT-001', 'title': 'Quy trình cấp quyền hệ thống giả lập'})
    assert not demo_sources.is_demo_source({'tags': None, 'doc_number': 'BUSINESS-IT-001',
        'title': 'Quy trình hỗ trợ CNTT'})


def test_general_and_followup_questions_cannot_use_demo_but_named_source_can():
    demo = Document(page_content='two approvals', metadata={
        'doc_id': 1, 'tags': ['demo', 'IT'], 'file_goc': 'it_demo_process_v1.md',
        'title': 'Quy trình cấp quyền hệ thống giả lập'})
    official = Document(page_content='CHƯA XÁC NHẬN', metadata={'doc_id': 2})
    for question in ('So sánh trách nhiệm IT và HR', 'Cần mấy người duyệt?', 'demo'):
        assert demo_sources.filter_applicable_sources([demo, official], question) == [official]
    assert demo_sources.filter_applicable_sources([demo], 'Theo it_demo_process_v1.md, cần mấy người duyệt?') == [demo]
    assert demo_sources.filter_applicable_sources([demo], 'Trong Quy trình cấp quyền hệ thống giả lập, cần mấy người duyệt?') == [demo]


def test_current_metadata_overrides_old_vector_tags_and_missing_rows_fail_closed(monkeypatch):
    from mech_chatbot.config import repository_runtime
    from mech_chatbot.db.repositories import doc_metadata
    monkeypatch.setattr(repository_runtime, 'current_repository_engine', lambda: object())
    monkeypatch.setattr(doc_metadata, 'get_common_metadata_for_rag', lambda ids: {1: {'tags': 'demo'}})
    docs = [Document(page_content='x', metadata={'doc_id': i}) for i in (1, 2)]
    assert demo_sources.refresh_applicable_sources(docs, 'Quy định công ty?') == []
    assert docs[0].metadata == {'doc_id': 1}


def test_explicit_demo_notice_is_in_context_stream_and_citation():
    from mech_chatbot.rag.phases.citations import _render_citation
    doc = Document(page_content='2 approvals', metadata={'tags': 'demo', 'file_goc': 'sample.md'})
    assert 'không phải quy định' in demo_sources.demo_scope_context([doc])
    assert ''.join(demo_sources.mark_demo_stream(iter(['2 approvals']), [doc])).startswith(demo_sources.DEMO_NOTICE)
    assert 'DEMO' in _render_citation(doc.metadata, 'IT')


def test_mixed_community_summary_does_not_leak_demo_via_official_source():
    demo = Document(page_content='mixed summary', metadata={'doc_id': 1, 'tags': 'demo', 'community_summary_id': 4})
    official = Document(page_content='mixed summary', metadata={'doc_id': 2, 'community_summary_id': 4})
    ordinary = Document(page_content='official only', metadata={'doc_id': 2})
    assert demo_sources.filter_applicable_sources([demo, official, ordinary], 'Quy định?') == [ordinary]


def test_rerank_filters_before_provider_and_after_enrichment(monkeypatch):
    from .test_rag_phase_policy_branches import _decision, _enrichment, _state
    from mech_chatbot.rag.phases import retrieval_rerank
    demo = Document(page_content='4 hours', metadata={'doc_id': 6, 'doc_number': 'DEMO-IT-001',
        'title': 'Quy trình cấp quyền hệ thống giả lập'})
    official = Document(page_content='CHƯA XÁC NHẬN', metadata={'doc_id': 1177})
    seen = []
    monkeypatch.setattr(retrieval_rerank, '_late_interaction_rerank', lambda docs, *args: (docs, False))
    monkeypatch.setattr(retrieval_rerank, '_apply_rerank_backend', lambda docs, *args: seen.extend(docs) or docs)
    monkeypatch.setattr(retrieval_rerank, '_hydrate_reranked_context', lambda docs, *args: ([*docs, demo], []))
    result = retrieval_rerank.rerank_retrieval(_decision(), _enrichment([demo, official]), _state())
    assert seen == [official]
    assert result.documents == (official,)


def test_enrichment_blocks_demo_before_disambiguation(monkeypatch):
    from types import SimpleNamespace
    from .test_rag_phase_policy_branches import _decision, _primary, _state
    from mech_chatbot.rag.phases import retrieval_enrichment as enrichment
    demo = Document(page_content='4 hours', metadata={'doc_id': 6, 'doc_number': 'DEMO-IT-001',
        'title': 'Quy trình cấp quyền hệ thống giả lập'})
    monkeypatch.setattr(enrichment, '_enrich_graph', lambda *args: None)
    monkeypatch.setattr(enrichment, '_enrich_community', lambda *args: SimpleNamespace(documents=(demo,)))
    monkeypatch.setattr(enrichment, '_handle_exact_code_miss', lambda *args: SimpleNamespace(
        terminal=None, documents=(demo,), part_ids=()))
    monkeypatch.setattr(enrichment, '_probe_exact_code_access', lambda *args: (False, None))
    monkeypatch.setattr(enrichment, '_disambiguate_documents', lambda *args: (_ for _ in ()).throw(AssertionError('demo reached selector')))
    result = enrichment.enrich_retrieval(_decision(), _primary(), _state())
    assert demo_sources.DEMO_MISSING_NOTICE in ''.join(result.prepared[0])


def test_sql_refresh_preserves_governed_document_type_and_valid_community(monkeypatch):
    from mech_chatbot.config import repository_runtime
    from mech_chatbot.db.repositories import doc_metadata
    from mech_chatbot.rag.phases.retrieval_enrichment_support import _GovernedSqlBomDocument
    monkeypatch.setattr(repository_runtime, 'current_repository_engine', lambda: object())
    monkeypatch.setattr(doc_metadata, 'get_common_metadata_for_rag', lambda ids: {1: {'tags': None, 'title': 'Official'}})
    bom = _GovernedSqlBomDocument(page_content='BOM', metadata={'doc_id': 1})
    community = Document(page_content='Approved summary', metadata={'doc_id': 1, 'community_summary_id': 7})
    result = demo_sources.refresh_applicable_sources([bom, community], 'Quy định?')
    assert isinstance(result[0], _GovernedSqlBomDocument)
    assert result[1].page_content == 'Approved summary'
