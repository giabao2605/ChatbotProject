"""Offline certification before ranking; only synthetic source content is used."""
from dataclasses import replace
from time import perf_counter
from types import SimpleNamespace

import pytest
from langchain_core.documents import Document
from sqlalchemy import event, text

from mech_chatbot.config.repository_runtime import bind_repository_runtime
from mech_chatbot.rag.execution import AccessScope
from mech_chatbot.rag.phases import retrieval_rerank
from mech_chatbot.rag.phases.contracts import PhaseTerminal
from mech_chatbot.services.feedback_service import pin_generation_evidence, EvidenceCertificationError
from mech_chatbot.db.repositories.feedback_access import pin_generation_sources
from .test_feedback_scoped_flow import scoped_db
from .test_rag_phase_policy_branches import _decision, _enrichment, _state

pytestmark = pytest.mark.unit


def access():
    return AccessScope(user_id=9, username='reviewer', roles=frozenset({'reviewer'}),
        allowed_departments=frozenset({'ENG'}), allowed_sites=frozenset({'HN'}), max_security_level='internal')


def document(value='approved content', *, index=0, doc_id=1, page=1):
    return Document(page_content=value, metadata={'doc_id':doc_id, 'version_no':2, 'trang_so':page,
        'loai_du_lieu':'text', 'chunk_index':index})


def rerank_state():
    state = _state()
    state.request = SimpleNamespace(access=access())
    state.retrieval_adapter.rerank_candidate_cap = 2
    return state


def isolate_rerank(monkeypatch, observed):
    monkeypatch.setattr(retrieval_rerank, 'refresh_applicable_sources', lambda docs, question: list(docs))
    monkeypatch.setattr(retrieval_rerank, '_late_interaction_rerank', lambda docs, *args: (docs, False))
    monkeypatch.setattr(retrieval_rerank, '_apply_rerank_backend', lambda docs, *args: observed.extend(docs) or docs)
    monkeypatch.setattr(retrieval_rerank, '_hydrate_reranked_context', lambda docs, *args: (docs, []))
    monkeypatch.setattr(retrieval_rerank, 'long_context_reorder', lambda docs: docs)


def test_uncertified_candidates_do_not_consume_cap_and_fusion_order_is_preserved(scoped_db, monkeypatch):
    engine, _ = scoped_db
    docs = [document('fabricated first', index=0), document(index=1),
            document('fabricated second', index=2), document(index=3, doc_id=2)]
    observed = []
    isolate_rerank(monkeypatch, observed)
    with bind_repository_runtime(db_engine=engine):
        result = retrieval_rerank.rerank_retrieval(_decision(), _enrichment(docs), rerank_state())
    assert [doc.metadata['chunk_index'] for doc in observed] == [1, 3]
    assert [doc.metadata['chunk_index'] for doc in result.documents] == [1, 3]
    assert all('evidence_pin' in doc.metadata for doc in result.documents)
    assert all('evidence_pin' not in doc.metadata for doc in docs)


def test_all_uncertified_candidates_use_existing_empty_context_terminal(scoped_db, monkeypatch):
    engine, _ = scoped_db
    observed = []
    isolate_rerank(monkeypatch, observed)
    with bind_repository_runtime(db_engine=engine):
        result = retrieval_rerank.rerank_retrieval(_decision(), _enrichment([document('fabricated')]), rerank_state())
    assert isinstance(result, PhaseTerminal) and result.reason_code == 'empty_context'
    assert observed == []


@pytest.mark.parametrize('policy,roles,expected', [
    ('compare_versions', ('document_history_reader',), True),
    ('specific_version', ('document_history_reader',), True),
    ('include_archived', ('document_history_reader',), True),
    ('version_history', ('document_history_reader',), True),
    ('current_only', ('document_history_reader',), False),
    ('include_archived', ('viewer',), False),
])
def test_prerank_passes_same_history_policy_as_final_pin(monkeypatch, policy, roles, expected):
    from mech_chatbot.services import feedback_service
    observed = []
    isolate_rerank(monkeypatch, observed)
    checks = []
    def pin(docs, actor, **kwargs):
        checks.append(kwargs)
        return docs
    monkeypatch.setattr(feedback_service, 'pin_generation_evidence', pin)
    decision = _decision(intent_data={'version_policy':policy})
    decision = replace(decision, request=replace(decision.request, user_roles=roles))
    state = rerank_state()
    retrieval_rerank.rerank_retrieval(decision, _enrichment([document()]), state)
    assert len(checks) == 1 and checks[0]['allow_history'] is expected
    assert checks[0]['checkpoint'] is state.checkpoint
    assert checks[0]['deadline'] == state.budget.deadline_monotonic


def test_final_pin_rechecks_changed_page_after_prerank_snapshot(scoped_db, monkeypatch):
    engine, _ = scoped_db
    observed = []
    isolate_rerank(monkeypatch, observed)
    with bind_repository_runtime(db_engine=engine):
        result = retrieval_rerank.rerank_retrieval(_decision(), _enrichment([document()]), rerank_state())
        with engine.begin() as conn:
            conn.execute(text("UPDATE DocumentPages SET TextExtract='changed content' WHERE DocID=1"))
        with pytest.raises(EvidenceCertificationError):
            pin_generation_evidence(result.documents, access())


def _thirty_sources(engine, distinct):
    if distinct:
        with engine.begin() as conn:
            columns = [row[1] for row in conn.execute(text('PRAGMA table_info(TaiLieu)')) if row[1] != 'DocID']
            fields = ','.join(columns)
            for doc_id in range(3, 31):
                conn.execute(text(f'INSERT INTO TaiLieu(DocID,{fields}) SELECT :doc,{fields} FROM TaiLieu WHERE DocID=1'), {'doc':doc_id})
                conn.execute(text("INSERT INTO PhongBanChiaSe VALUES(:doc,'ENG')"), {'doc':doc_id})
                conn.execute(text("INSERT INTO DocumentPages VALUES(:doc,1,'approved content',NULL,NULL)"), {'doc':doc_id})
    return [{'doc_id':index+1 if distinct else 1, 'version_no':2, 'trang':1, 'text':'approved content'} for index in range(30)]


@pytest.mark.parametrize('distinct', [False, True], ids=['same-page-30', 'distinct-doc-pages-30'])
def test_thirty_candidate_sql_count(scoped_db, distinct, record_property):
    engine, actor = scoped_db
    sources = _thirty_sources(engine, distinct)
    statements = []
    def count(conn, cursor, statement, parameters, context, executemany):
        if statement.lstrip().upper().startswith('SELECT'):
            statements.append(statement)
    event.listen(engine, 'before_cursor_execute', count)
    try:
        with engine.connect() as conn:
            started = perf_counter()
            pinned = pin_generation_sources(conn, sources, actor)
            elapsed = (perf_counter()-started)*1000
    finally:
        event.remove(engine, 'before_cursor_execute', count)
    record_property('candidate_count',30)
    record_property('sql_select_count',len(statements))
    record_property('elapsed_ms',round(elapsed,3))
    print({'distinct':distinct, 'candidates':30, 'selects':len(statements), 'elapsed_ms':round(elapsed,3)})
    assert len(pinned) == 30
    assert len(statements) == (120 if distinct else 4)

@pytest.mark.parametrize('distinct', [False, True], ids=['same-page-service-30', 'distinct-doc-pages-service-30'])
def test_thirty_candidate_service_includes_actor_and_snapshot_cost(scoped_db, distinct, record_property):
    engine, _ = scoped_db
    sources = _thirty_sources(engine, distinct)
    docs = [Document(page_content=source['text'], metadata={k:v for k,v in source.items() if k!='text'}) for source in sources]
    statements = []
    def count(conn, cursor, statement, parameters, context, executemany):
        statements.append(statement)
    event.listen(engine, 'before_cursor_execute', count)
    try:
        with bind_repository_runtime(db_engine=engine):
            started = perf_counter()
            pinned = pin_generation_evidence(docs, access())
            elapsed = (perf_counter()-started)*1000
    finally:
        event.remove(engine, 'before_cursor_execute', count)
    selects = [sql for sql in statements if sql.lstrip().upper().startswith('SELECT')]
    sources_sql = [sql for sql in selects if any(name in sql for name in ('TaiLieu','PhongBanChiaSe','DocumentPages','TechnicalAttributes'))]
    record_property('sql_select_count',len(selects))
    record_property('source_select_count',len(sources_sql))
    record_property('actor_select_count',len(selects)-len(sources_sql))
    record_property('non_select_commands',len(statements)-len(selects))
    record_property('elapsed_ms',round(elapsed,3))
    print({'service_distinct':distinct, 'candidates':30, 'source_selects':len(sources_sql),
           'actor_selects':len(selects)-len(sources_sql), 'non_select_commands':len(statements)-len(selects),
           'elapsed_ms':round(elapsed,3)})
    assert len(pinned) == 30 and len(sources_sql) == (120 if distinct else 4)
    assert len(selects) > len(sources_sql)


def test_cached_page_still_checks_each_candidate_text(scoped_db):
    engine, _ = scoped_db
    docs = [document(index=0), document('fabricated',index=1), document(index=2)]
    with bind_repository_runtime(db_engine=engine):
        result = pin_generation_evidence(docs, access())
    assert [doc.metadata['chunk_index'] for doc in result] == [0,2]


def test_cached_certification_keeps_per_candidate_cancellation_checkpoint(scoped_db):
    from mech_chatbot.llm.external_ai import ExternalAICallCancelled
    engine, _ = scoped_db
    observed = []
    def checkpoint(stage):
        if stage == 'evidence_certification':
            observed.append(stage)
            if len(observed) == 2:
                raise ExternalAICallCancelled('cancelled')
    with bind_repository_runtime(db_engine=engine), pytest.raises(ExternalAICallCancelled):
        pin_generation_evidence([document(),document()], access(), checkpoint=checkpoint)
    assert len(observed) == 2


def test_cached_source_does_not_cross_actor_or_version(scoped_db):
    engine, actor = scoped_db
    good = {'doc_id':1,'version_no':2,'trang':1,'text':'approved content'}
    with engine.connect() as conn:
        assert pin_generation_sources(conn,[good],actor)
        with pytest.raises(PermissionError):
            pin_generation_sources(conn,[good],{**actor,'allowed_departments':['HR']})
        with pytest.raises(PermissionError):
            pin_generation_sources(conn,[{**good,'version_no':3}],actor)
