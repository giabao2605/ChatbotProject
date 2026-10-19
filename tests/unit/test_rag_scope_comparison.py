"""Fixed v2 comparison through the same public RAG event lifecycle as chat."""
from dataclasses import replace
from decimal import Decimal

import pytest
from mech_chatbot.config.repository_runtime import bind_repository_runtime
from mech_chatbot.domain.structured_evidence import NumericFact, RoundingRule, ScopeManifest, SourceLocator
from mech_chatbot.db.repositories.structured_evidence import save_extracted_scope, review_numeric_fact, review_scope_completeness
from mech_chatbot.rag.execution import AccessScope, DefaultRagExecutor, RagRequest, RagInvocation, collect_rag_events, RagFailed
from .test_structured_evidence_repository import PROFILE, store


def approved_pair(store, *, values=('4','8'), row_only=False,source_hash='a'*64,source_page=None):
    for number, (scope_id,value) in enumerate(zip(('a','b'),values),2):
        fact = NumericFact('fact-'+scope_id,'Value',value,Decimal(value),'VND',
                           SourceLocator('1','2',page=source_page,sheet=None if row_only else 'CSV',cell=None if row_only else f'B{number}',row_key='row-'+scope_id),
                           source_hash=source_hash,currency='VND',extraction_method='csv')
        manifest = ScopeManifest(scope_id,'batch-1',(fact,),source_revisions=(('1','2'),),unit='VND',currency='VND')
        save_extracted_scope(manifest,db_engine=store)
        review_numeric_fact(fact.fact_id,PROFILE,action='approve',note='Offline fixture cell review',db_engine=store)
        review_scope_completeness(scope_id,PROFILE,expected_row_ids=(fact.locator.row_key,),rounding_rule=RoundingRule(2),
                                  definition='Reviewed amount',note='Offline fixture scope review',db_engine=store)


def request():
    return RagRequest('So sánh hai phạm vi đã chọn',AccessScope(roles=frozenset({'reviewer'}),
                      allowed_departments=frozenset({'ENG'}),allowed_sites=frozenset({'factory'}),max_security_level='internal'),
                      scope_comparison=('a','b'))


def approved_empty_pair(store, tmp_path, *, identity='strings'):
    from sqlalchemy import text
    from mech_chatbot.ingestion.pdf.structured import extract_structured_table_file
    from mech_chatbot.db.repositories.structured_evidence import load_approved_scope

    with store.begin() as conn:
        conn.execute(text('INSERT INTO TaiLieu SELECT 2,VersionNo,TenFile,FilePath,ThuMuc,Site,SecurityLevel,LifecycleStatus,ReviewStatus,PublicationState,Servable,IsCurrent,EffectiveStatus,EffectiveDate,ExpiryDate,KnowledgeApproverUserID FROM TaiLieu WHERE DocID=1'))
        conn.execute(text("INSERT INTO PhongBanChiaSe VALUES(2,'ENG')"))
    ids = []
    for doc, data in (('1', 'amount [VND]\n'), ('2', 'amount [VND]\n1.235\n')):
        path = tmp_path / f'export-{doc}.csv'
        path.write_bytes(data.encode('utf-8'))
        candidate = extract_structured_table_file(path, doc_id=doc if identity=='strings' else int(doc),
            version=2 if identity=='integers' else '2', snapshot_id='batch-1').tables[0].scopes[0]
        save_extracted_scope(candidate, db_engine=store)
        for fact in candidate.facts:
            review_numeric_fact(fact.fact_id, PROFILE, action='approve', value_text='1.235',
                                note='Synthetic reviewer confirmed decimal point and source cell', db_engine=store)
        review_scope_completeness(candidate.scope_id, PROFILE, expected_row_ids=tuple(f.locator.row_key for f in candidate.facts),
                                  rounding_rule=RoundingRule(2), definition='Reviewed amount', unit='VND', currency='VND',
                                  note='Synthetic reviewer checked the whole export, including zero rows', db_engine=store)
        ids.append(candidate.scope_id)
    return replace(request(), scope_comparison=tuple(ids)), tuple(load_approved_scope(i, PROFILE, db_engine=store) for i in ids)


def test_reviewed_empty_export_keeps_zero_total_and_source_citation(store, tmp_path):
    from mech_chatbot.evaluation.grounded_math_v2 import evaluate_math_v2
    selected, scopes = approved_empty_pair(store, tmp_path)
    with bind_repository_runtime(db_engine=store):
        result = collect_rag_events(DefaultRagExecutor().run(selected,
            RagInvocation('empty-comparison', mode='evaluation', capture_math_observations=True)))
    assert result.outcome == 'answered' and result.diagnostics['answer_outcome'] == 'partial_answer'
    assert result.diagnostics['math_v2']['status'] == 'partial'
    steps = result.diagnostics['math_v2']['steps']
    assert [s['value'] for s in steps] == ['0', '1.235', '1.235', None]
    assert [s['display_value'] for s in steps] == ['0.00', '1.24', '1.24', '']
    assert len(result.citations) == 2
    empty_card = next(c for c in result.citations if c['doc_id'] == 1)
    assert empty_card['fact_count'] == 0 and empty_card['locator']['cell'] == 'A1'
    assert result.diagnostics.budget.calculations == 4
    assert result.diagnostics.budget.planners == 0 and result.diagnostics.budget.final_generations == 0
    assert evaluate_math_v2('compare_two_scopes', result.observed_math_results[0], scopes=scopes)['passed']


@pytest.mark.parametrize('language, label, before', [
    ('vi', 'Tổng B', 'giá trị trước làm tròn: 1.235'),
    ('en', 'Total B', 'value before rounding: 1.235'),
])
def test_comparison_answer_preserves_the_reviewed_value_before_material_rounding(store, tmp_path, language, label, before):
    selected, _ = approved_empty_pair(store, tmp_path)
    selected = replace(selected, response_language=language)
    with bind_repository_runtime(db_engine=store):
        result = collect_rag_events(DefaultRagExecutor().run(selected,
            RagInvocation('rounding-renderer', mode='evaluation', capture_math_observations=True)))
    line = next(line for line in result.answer.splitlines() if line.startswith(f'- {label}:'))
    assert '1.24 VND' in line and before in line
    card = next(card for card in result.citations if card['doc_id'] == 2)
    assert f"[SRC:{card['source_id']}]" in line
    assert result.diagnostics['math_v2']['status'] == 'partial'
    assert [step['value'] for step in result.observed_math_results[0]['steps']] == ['0', '1.235', '1.235', None]
    assert [step['display_value'] for step in result.observed_math_results[0]['steps']] == ['0.00', '1.24', '1.24', '']
    assert len(result.observed_math_results[0]['steps']) == result.diagnostics.budget.calculations == 4
    assert result.diagnostics.budget.planners == result.diagnostics.budget.final_generations == 0


@pytest.mark.parametrize('language, empty_notice, labels', [
    ('vi', 'phạm vi rỗng đã duyệt: 0 dòng dữ liệu', ('Tổng A', 'Tổng B')),
    ('en', 'reviewed empty scope: 0 data rows', ('Total A', 'Total B')),
])
@pytest.mark.parametrize('empty_side', ['a', 'b'])
def test_comparison_answer_identifies_reviewed_empty_scope_at_its_total_and_header(store, tmp_path, language, empty_notice, labels, empty_side):
    selected, _ = approved_empty_pair(store, tmp_path)
    selected = replace(selected, response_language=language,
        scope_comparison=selected.scope_comparison if empty_side == 'a' else tuple(reversed(selected.scope_comparison)))
    with bind_repository_runtime(db_engine=store):
        result = collect_rag_events(DefaultRagExecutor().run(selected,
            RagInvocation('empty-renderer', mode='evaluation', capture_math_observations=True)))
    label = labels[empty_side == 'b']
    line = next(line for line in result.answer.splitlines() if line.startswith(f'- {label}:'))
    assert '0.00 VND' in line and empty_notice in line
    assert result.answer.count(empty_notice) == 1
    card = next(card for card in result.citations if card['doc_id'] == 1)
    assert card['fact_count'] == 0 and card['locator']['cell'] == 'A1'
    assert f"[SRC:{card['source_id']}]" in line
    assert len(result.observed_math_results[0]['steps']) == result.diagnostics.budget.calculations == 4
    assert result.diagnostics.budget.planners == result.diagnostics.budget.final_generations == 0


@pytest.mark.parametrize('language, raw_note, empty_notice', [
    ('vi', 'giá trị trước làm tròn', 'phạm vi rỗng đã duyệt'),
    ('en', 'value before rounding', 'reviewed empty scope'),
])
@pytest.mark.parametrize('values, expected', [
    (('4', '8'), ['4', '8', '4', '100']),
    (('0', '8'), ['0', '8', '8', None]),
])
def test_comparison_does_not_label_nonempty_zero_or_numeric_equal_formatting_as_extra_evidence(store, language, raw_note, empty_notice, values, expected):
    approved_pair(store, values=values)
    with bind_repository_runtime(db_engine=store):
        result = collect_rag_events(DefaultRagExecutor().run(replace(request(), response_language=language),
            RagInvocation('nonempty-renderer', mode='evaluation', capture_math_observations=True)))
    assert raw_note not in result.answer and empty_notice not in result.answer
    assert all(card['fact_count'] == 1 for card in result.citations)
    assert [step['value'] for step in result.observed_math_results[0]['steps']] == expected
    assert len(result.observed_math_results[0]['steps']) == result.diagnostics.budget.calculations == 4


def test_reviewed_comparison_emits_four_real_steps_and_structured_citations(store):
    approved_pair(store)
    with bind_repository_runtime(db_engine=store):
        result = collect_rag_events(DefaultRagExecutor().run(request(),RagInvocation('comparison',mode='evaluation')))
    assert result.outcome == 'answered'
    steps = result.diagnostics['math_v2']['steps']
    assert [s['step_id'] for s in steps] == ['sum_a','sum_b','delta','percent_change']
    assert [s['display_value'] for s in steps] == ['4.00','8.00','4.00','100.00']
    assert result.diagnostics.budget.calculations == 4
    assert result.diagnostics['math_v2']['offline_only'] and not result.diagnostics['math_v2']['accepted']
    assert len(result.citations) == 2
    assert {c['locator']['cell'] for c in result.citations} == {'B2','B3'}
    assert all(c['page_no'] is None for c in result.citations)
    assert result.diagnostics.budget.final_generations == 0


@pytest.mark.parametrize('empty', [False, True])
@pytest.mark.parametrize('template', ['comparison', 'total'])
def test_executor_citations_keep_authorized_sql_filename_for_exact_revision(store, tmp_path, monkeypatch, empty, template):
    from sqlalchemy import event, text
    from mech_chatbot.rag import scope_comparison
    if empty:
        selected, _ = approved_empty_pair(store, tmp_path)
    else:
        approved_pair(store)
        selected = request()
    with store.begin() as conn:
        conn.execute(text("UPDATE TaiLieu SET TenFile='reviewed-v2.csv',FilePath='private/path/not-the-filename.csv' WHERE DocID=1"))
        if empty:
            conn.execute(text("UPDATE TaiLieu SET TenFile='other-v2.csv' WHERE DocID=2"))
    if template == 'total':
        selected = replace(selected, question='Total selected scope', scope_total=selected.scope_comparison[0], scope_comparison=None)
    filename_reads = []
    name_pins = []
    original_source = scope_comparison._source
    def source(conn, doc, version, profile):
        name_pins.append((doc, version))
        return original_source(conn, doc, version, profile)
    monkeypatch.setattr(scope_comparison, '_source', source)
    def capture(conn, cursor, sql, params, context, many):
        if 'SELECT * FROM TaiLieu WHERE DocID=' in sql:
            filename_reads.append(id(conn))
    event.listen(store, 'before_cursor_execute', capture)
    try:
        with bind_repository_runtime(db_engine=store):
            result = collect_rag_events(DefaultRagExecutor().run(selected,
                RagInvocation('filename-comparison', mode='evaluation')))
    finally:
        event.remove(store, 'before_cursor_execute', capture)
    assert result.outcome == 'answered'
    assert all(card['version_no'] == 2 for card in result.citations)
    assert {card['doc_id']: card['file_name'] for card in result.citations} == (
        {1: 'reviewed-v2.csv', 2: 'other-v2.csv'} if empty and template == 'comparison' else {1: 'reviewed-v2.csv'})
    assert len(name_pins) == len(set(name_pins)) == len({card['doc_id'] for card in result.citations})
    assert len(set(filename_reads)) == 1
    assert 'reviewed-v2.csv' in result.ref_text


@pytest.mark.parametrize('fault', ['rights', 'revision', 'not_current'])
def test_citation_filename_lookup_rechecks_source_access_and_revision(store, fault):
    from sqlalchemy import text
    from mech_chatbot.db.repositories.structured_evidence import load_approved_scope
    from mech_chatbot.rag.scope_comparison import _sources
    approved_pair(store)
    scope = load_approved_scope('a', PROFILE, db_engine=store)
    profile = PROFILE
    if fault == 'rights':
        profile = {**PROFILE, 'allowed_sites': ['other']}
    else:
        with store.begin() as conn:
            conn.execute(text('UPDATE TaiLieu SET VersionNo=3' if fault == 'revision' else 'UPDATE TaiLieu SET IsCurrent=0'))
    with bind_repository_runtime(db_engine=store), pytest.raises(PermissionError, match='Evidence source unavailable'):
        _sources((scope,), profile)


@pytest.mark.parametrize('file_name', [None, '', '   '])
def test_missing_source_filename_never_returns_numbers_or_invented_path_name(store, file_name):
    from sqlalchemy import text
    from mech_chatbot.rag.execution import consume_rag_events
    approved_pair(store)
    with store.begin() as conn:
        conn.execute(text('UPDATE TaiLieu SET TenFile=:name,FilePath=:path'),
            {'name': file_name, 'path': 'private/should-not-be-cited.csv'})
    with bind_repository_runtime(db_engine=store):
        result = consume_rag_events(DefaultRagExecutor().run(request(), RagInvocation('missing-filename', mode='evaluation')))
    assert isinstance(result, RagFailed) and result.code == 'PermissionError'
    assert result.message == 'Evidence source unavailable'
    assert not result.diagnostics.get('citation_docs') and not result.diagnostics.get('retrieved_docs')


def test_opt_in_capture_retains_actual_operands_only_on_the_completed_result(store):
    approved_pair(store)
    with bind_repository_runtime(db_engine=store):
        result = collect_rag_events(DefaultRagExecutor().run(request(),
            RagInvocation('captured-comparison', mode='evaluation', capture_math_observations=True)))
    assert len(result.observed_math_results) == 1
    record = result.observed_math_results[0]
    assert record['schema'] == 'grounded-calculation-v2' and record['domain_step_count'] == 4
    assert [source['facts'][0]['value'] for source in record['sources']] == ['4', '8']
    assert record['sources'][0]['facts'][0]['locator']['row_key'] == 'row-a'
    assert record['sources'][0]['facts'][0]['source_hash'] == 'a' * 64
    assert 'observed_math_results' not in dict(result.diagnostics)
    assert 'sources' not in result.diagnostics['math_v2']
    result.diagnostics['math_v2']['steps'][0]['value'] = '999'
    assert record['steps'][0]['value'] == '4'


def test_ordinary_math_execution_does_not_capture_full_operands(store):
    approved_pair(store)
    with bind_repository_runtime(db_engine=store):
        result = collect_rag_events(DefaultRagExecutor().run(request(), RagInvocation('normal', mode='evaluation')))
    assert result.observed_math_results is None


def test_completed_capture_can_be_exported_without_reconstructing_from_reference_inputs(store):
    from mech_chatbot.evaluation.grounded_math_v2 import export_math_observation
    approved_pair(store)
    with bind_repository_runtime(db_engine=store):
        result = collect_rag_events(DefaultRagExecutor().run(request(),
            RagInvocation('observed-export', mode='evaluation', capture_math_observations=True)))
    observation = export_math_observation(result)
    assert observation['trace_id'] == 'observed-export'
    assert observation['math_results'][0]['steps'][1]['value'] == '8'
    assert observation['calculation_count'] == 4
    assert observation['answer'] == result.answer
    assert observation['accepted'] is False and observation['offline_only'] is True
    observation['math_results'][0]['steps'][0]['value'] = '999'
    assert result.observed_math_results[0]['steps'][0]['value'] == '4'


@pytest.mark.parametrize('mode', ['production', 'pilot_replay'])
def test_capture_cannot_be_requested_outside_evaluation_or_test(mode):
    with pytest.raises(ValueError, match='Math observations'):
        RagInvocation('invalid-capture', mode=mode, capture_math_observations=True)


def test_capture_requires_reviewer_before_any_pipeline_work():
    from mech_chatbot.rag.execution import consume_rag_events
    def unexpected_pipeline(state):
        raise AssertionError('Pipeline must not execute')
    query = replace(request(), access=AccessScope(roles=frozenset({'viewer'})), scope_comparison=None)
    result = consume_rag_events(DefaultRagExecutor(execute_pipeline=unexpected_pipeline).run(query,
        RagInvocation('viewer', mode='evaluation', capture_math_observations=True)))
    assert isinstance(result, RagFailed) and result.code == 'PermissionError'


def test_snapshot_commit_failure_cannot_be_exported_as_a_math_observation(store):
    from sqlalchemy import event
    from mech_chatbot.evaluation.grounded_math_v2 import export_math_observation
    from mech_chatbot.rag.execution import consume_rag_events
    approved_pair(store)
    def fail_commit(conn):
        raise RuntimeError('Snapshot commit failed')
    event.listen(store, 'commit', fail_commit)
    try:
        with bind_repository_runtime(db_engine=store):
            result = consume_rag_events(DefaultRagExecutor().run(request(),
                RagInvocation('lost-snapshot', mode='evaluation', capture_math_observations=True)))
    finally:
        event.remove(store, 'commit', fail_commit)
    assert isinstance(result, RagFailed)
    with pytest.raises(ValueError, match='completed opt-in'):
        export_math_observation(result)


def test_denied_capture_is_canonical_and_contains_no_source_data(store):
    from mech_chatbot.evaluation.grounded_math_v2 import export_math_observation
    approved_pair(store)
    query = replace(request(), access=replace(request().access, allowed_sites=frozenset({'other'})))
    with bind_repository_runtime(db_engine=store):
        result = collect_rag_events(DefaultRagExecutor().run(query,
            RagInvocation('denied-observation', mode='evaluation', capture_math_observations=True)))
    observation = export_math_observation(result)
    record = observation['math_results'][0]
    assert record['schema'] == 'grounded-calculation-v2' and record['status'] == 'blocked'
    assert record['steps'] == record['sources'] == [] and record['rounding_rule'] is None
    assert observation['citations'] == [] and observation['calculation_count'] == 0


def test_unusable_citation_provenance_cannot_export_a_successful_calculation(store, monkeypatch):
    from mech_chatbot.evaluation.grounded_math_v2 import export_math_observation
    from mech_chatbot.rag import scope_comparison
    approved_pair(store)
    monkeypatch.setattr(scope_comparison, 'build_citation_list', lambda *args, **kwargs: [])
    with bind_repository_runtime(db_engine=store):
        result = collect_rag_events(DefaultRagExecutor().run(request(),
            RagInvocation('missing-citations', mode='evaluation', capture_math_observations=True)))
    observation = export_math_observation(result)
    record = observation['math_results'][0]
    assert result.outcome == 'refused' and record['reason'] == 'invalid_source_provenance'
    assert record['sources'] == record['steps'] == observation['citations'] == []
    assert observation['calculation_count'] == 0


@pytest.mark.parametrize('failure', ['not_opted_in', 'cancelled', 'deadline'])
def test_observation_export_rejects_uncaptured_cancelled_or_timed_out_execution(store, failure):
    import threading
    from mech_chatbot.evaluation.grounded_math_v2 import export_math_observation
    from mech_chatbot.rag.execution import consume_rag_events, RequestBudgetLimits
    approved_pair(store)
    cancellation = threading.Event()
    if failure == 'cancelled':
        cancellation.set()
    executor = DefaultRagExecutor(budget_limits=RequestBudgetLimits(deadline_seconds=0 if failure == 'deadline' else 120))
    with bind_repository_runtime(db_engine=store):
        result = consume_rag_events(executor.run(request(), RagInvocation('no-export', mode='evaluation',
            capture_math_observations=failure != 'not_opted_in'), cancellation=cancellation))
    with pytest.raises(ValueError, match='completed opt-in'):
        export_math_observation(result)


def test_observation_export_requires_the_full_recorded_shared_calculation_count(store):
    from mech_chatbot.evaluation.grounded_math_v2 import export_math_observation, evaluate_math_v2
    from mech_chatbot.rag.execution import RagDiagnostics
    from mech_chatbot.db.repositories.structured_evidence import load_approved_scope
    approved_pair(store)
    with bind_repository_runtime(db_engine=store):
        references = tuple(load_approved_scope(s, PROFILE) for s in ('a', 'b'))
        result = collect_rag_events(DefaultRagExecutor().run(request(),
            RagInvocation('step-count', mode='evaluation', capture_math_observations=True)))
    assert evaluate_math_v2('compare_two_scopes', result.observed_math_results[0], scopes=references)['passed']
    bad = replace(result, diagnostics=RagDiagnostics.from_mapping({
        **dict(result.diagnostics), 'generation_metrics': {'calculation_count': 3}}))
    with pytest.raises(ValueError, match='recorded Math steps'):
        export_math_observation(bad)


def test_stream_close_failure_never_returns_a_collectable_math_observation():
    from mech_chatbot.evaluation.grounded_math_v2 import export_math_observation
    from mech_chatbot.rag.execution import consume_rag_events, RagPrepared, RagCompleted
    terminal = RagCompleted('refused', 'close-failure', {}, observed_math_results=())
    class FailedClose:
        def __iter__(self):
            return iter((RagPrepared('', (), (), {}), terminal))
        def close(self):
            raise RuntimeError('Stream close failed')
    with pytest.raises(RuntimeError, match='Stream close failed'):
        consume_rag_events(FailedClose())
    with pytest.raises(ValueError, match='completed opt-in'):
        export_math_observation(terminal)


@pytest.mark.parametrize('failure', ['cancelled', 'deadline'])
def test_late_cancel_or_timeout_during_citations_cannot_produce_an_export(store, monkeypatch, failure):
    import threading
    from mech_chatbot.evaluation.grounded_math_v2 import export_math_observation
    from mech_chatbot.rag import execution
    approved_pair(store)
    cancellation = threading.Event()
    original = execution.attributed_citations
    def interrupt(diagnostics, answer):
        if failure == 'cancelled':
            cancellation.set()
        else:
            execution.current_request_budget().started_monotonic -= 1000
        yield from original(diagnostics, answer)
    monkeypatch.setattr(execution, 'attributed_citations', interrupt)
    with bind_repository_runtime(db_engine=store):
        result = execution.consume_rag_events(DefaultRagExecutor().run(request(),
            RagInvocation('late-stop', mode='evaluation', capture_math_observations=True), cancellation=cancellation))
    with pytest.raises(ValueError, match='completed opt-in'):
        export_math_observation(result)


def test_row_only_sources_survive_comparison_without_fabricated_sheet_or_page(store):
    approved_pair(store, row_only=True)
    # Both values in A share a source; their physical row locators remain distinct.
    first = NumericFact('fact-extra', 'Value', '2', Decimal('2'), 'VND',
                        SourceLocator('1', '2', row_key='row-extra'), source_hash='a'*64,
                        currency='VND', extraction_method='export')
    original = NumericFact('fact-c', 'Value', '4', Decimal('4'), 'VND',
                           SourceLocator('1', '2', row_key='row-a'), source_hash='a'*64,
                           currency='VND', extraction_method='export')
    save_extracted_scope(ScopeManifest('c', 'batch-1', (original, first),
                         source_revisions=(('1','2'),), unit='VND', currency='VND'), db_engine=store)
    for fact in (original, first):
        review_numeric_fact(fact.fact_id, PROFILE, action='approve', note='Offline row review', db_engine=store)
    review_scope_completeness('c', PROFILE, expected_row_ids=('row-a','row-extra'),
                             rounding_rule=RoundingRule(2), definition='Reviewed amount',
                             note='Offline whole scope review', db_engine=store)
    with bind_repository_runtime(db_engine=store):
        result = collect_rag_events(DefaultRagExecutor().run(replace(request(), scope_comparison=('c','b')), RagInvocation('rows', mode='test')))
    assert result.outcome == 'answered'
    assert result.diagnostics['math_v2']['steps'][0]['display_value'] == '6.00'
    assert {card['locator']['row_key'] for card in result.citations} == {'row-a','row-extra','row-b'}
    assert all(card['page_no'] is None and card['locator']['sheet'] is None for card in result.citations)


def test_comparison_does_not_silently_discard_four_question_intents(store):
    from mech_chatbot.rag.query_decomposition import QUERY_INTENT_OVERFLOW_NOTICE
    query = replace(request(), question='Tổng A; tổng B; chênh lệch; xem vật liệu')
    with bind_repository_runtime(db_engine=store):
        result = collect_rag_events(DefaultRagExecutor().run(query, RagInvocation('overflow', mode='evaluation')))
    assert result.outcome == 'refused' and result.refusal_reason == 'intent_overflow'
    assert result.answer == QUERY_INTENT_OVERFLOW_NOTICE
    assert result.citations == () and result.diagnostics.budget.calculations == 0


@pytest.mark.parametrize('reason', ['private arbitrary reason', None])
def test_observation_unknown_refusal_is_generic_without_exposing_reason(store, reason):
    from mech_chatbot.evaluation.grounded_math_v2 import export_math_observation
    query=replace(request(),question='Tổng A; tổng B; chênh lệch; xem vật liệu')
    with bind_repository_runtime(db_engine=store):
        result=collect_rag_events(DefaultRagExecutor().run(query,
            RagInvocation('unknown-refusal',mode='evaluation',capture_math_observations=True)))
    observed=export_math_observation(replace(result,refusal_reason=reason))
    assert observed['actual_outcome']=='insufficient_evidence'
    assert 'refusal_reason' not in observed and 'private arbitrary reason' not in str(observed)


@pytest.mark.parametrize('fault', ['reason_bool','reason_list','outcome','answer_outcome','subqueries_bool',
                                   'subqueries_float','subqueries_work','graph_work','metadata','query_type'])
def test_overflow_observation_rejects_invalid_or_executed_metadata(store,fault):
    from mech_chatbot.evaluation.grounded_math_v2 import export_math_observation
    from mech_chatbot.rag.execution import RagDiagnostics
    query=replace(request(),question='Tổng A; tổng B; chênh lệch; xem vật liệu')
    with bind_repository_runtime(db_engine=store):
        result=collect_rag_events(DefaultRagExecutor().run(query,
            RagInvocation('bad-overflow',mode='evaluation',capture_math_observations=True)))
    if fault.startswith('reason_'):
        result=replace(result,refusal_reason=True if fault=='reason_bool' else ['intent_overflow'])
    elif fault=='outcome':
        result=replace(result,outcome='answered')
    elif fault in {'answer_outcome','metadata','query_type'}:
        key,value={'answer_outcome':('answer_outcome',True),'metadata':('graph_v2',{'status':'unknown'}),
                   'query_type':('query_v2',[])}[fault]
        result=replace(result,diagnostics=RagDiagnostics.from_mapping({**dict(result.diagnostics),key:value}))
    else:
        field,value={'subqueries_bool':('subqueries',False),'subqueries_float':('subqueries',0.0),
                     'subqueries_work':('subqueries',1),'graph_work':('graph_examined_relations',1)}[fault]
        result=replace(result,diagnostics=replace(result.diagnostics,
            budget=replace(result.diagnostics.budget,**{field:value})))
    with pytest.raises(ValueError):
        export_math_observation(result)


def test_executed_math_cannot_be_relabelled_as_untouched_intent_overflow(store):
    from mech_chatbot.evaluation.grounded_math_v2 import export_math_observation
    from mech_chatbot.rag.execution import RagDiagnostics
    approved_pair(store)
    with bind_repository_runtime(db_engine=store):
        result=collect_rag_events(DefaultRagExecutor().run(request(),
            RagInvocation('executed-overflow',mode='evaluation',capture_math_observations=True)))
    result=replace(result,outcome='refused',refusal_reason='intent_overflow',
        diagnostics=RagDiagnostics.from_mapping({**dict(result.diagnostics),'answer_outcome':'refused'}))
    with pytest.raises(ValueError):
        export_math_observation(result)


def test_executed_math_rejects_a_forged_direct_clarification_label(store):
    from mech_chatbot.evaluation.grounded_math_v2 import export_math_observation
    from mech_chatbot.rag.execution import RagDiagnostics
    approved_pair(store)
    with bind_repository_runtime(db_engine=store):
        result=collect_rag_events(DefaultRagExecutor().run(request(),
            RagInvocation('forged-clarification',mode='evaluation',capture_math_observations=True)))
    result=replace(result,diagnostics=RagDiagnostics.from_mapping(
        {**dict(result.diagnostics),'answer_outcome':'clarification_required'}))
    with pytest.raises(ValueError):
        export_math_observation(result)


@pytest.mark.parametrize('values,status', [(('0','8'),'partial'), (('4','8'),'complete')])
def test_zero_denominator_returns_valid_totals_and_explicit_partial_state(store, values, status):
    approved_pair(store, values=values)
    with bind_repository_runtime(db_engine=store):
        result = collect_rag_events(DefaultRagExecutor().run(request(), RagInvocation('zero', mode='test')))
    assert result.diagnostics['math_v2']['status'] == status
    if status == 'partial':
        assert [s['display_value'] for s in result.diagnostics['math_v2']['steps'][:3]] == ['0.00','8.00','8.00']
        assert result.diagnostics['math_v2']['steps'][3]['value'] is None
        assert result.diagnostics['answer_outcome'] == 'partial_answer'
        assert 'Không xác định' in result.answer


@pytest.mark.parametrize('invalidator', ['rights','revision','snapshot','pending','missing_rows'])
def test_comparison_refuses_unavailable_source_without_metadata_leaks(store, invalidator):
    from sqlalchemy import text
    approved_pair(store)
    query = request()
    if invalidator == 'rights':
        query = replace(query, access=replace(query.access, allowed_sites=frozenset({'other'})))
    else:
        statement = {'revision':'UPDATE TaiLieu SET VersionNo=3',
                     'snapshot':'UPDATE StructuredSourceSnapshot SET CurrentSnapshot=0',
                     'pending':"UPDATE StructuredNumericFact SET ReviewStatus='pending' WHERE FactID='fact-a'",
                     'missing_rows':"DELETE FROM StructuredNumericFact WHERE FactID='fact-a'"}[invalidator]
        with store.begin() as conn:
            conn.execute(text(statement))
    with bind_repository_runtime(db_engine=store):
        result = collect_rag_events(DefaultRagExecutor().run(query, RagInvocation('denied', mode='evaluation')))
    assert result.outcome == 'refused'
    assert result.diagnostics['math_v2']['status'] == 'blocked'
    assert result.diagnostics['math_v2']['scopes'] == ()
    assert result.citations == () and 'row-a' not in result.answer


def test_cancelled_comparison_does_not_emit_a_numeric_answer(store):
    import threading
    from mech_chatbot.rag.execution import RagCancelled, RagToken
    approved_pair(store)
    cancelled = threading.Event()
    cancelled.set()
    with bind_repository_runtime(db_engine=store):
        events = list(DefaultRagExecutor().run(request(), RagInvocation('cancel', mode='test'), cancellation=cancelled))
    assert any(isinstance(e, RagCancelled) for e in events)
    assert not any(isinstance(e, RagToken) for e in events)


@pytest.mark.parametrize('mode,roles,code', [('production',{'reviewer'},'ValueError'),
                                        ('pilot_replay',{'reviewer'},'ValueError'),
                                        ('test',{'viewer'},'PermissionError')])
def test_executor_also_rejects_unapproved_context_and_roles(store, mode, roles, code):
    query = replace(request(), access=replace(request().access, roles=frozenset(roles)))
    with bind_repository_runtime(db_engine=store):
        events = list(DefaultRagExecutor().run(query, RagInvocation('restricted', mode=mode)))
    terminal = next(e for e in events if isinstance(e, RagFailed))
    assert terminal.code == code and terminal.diagnostics.budget.calculations == 0


def test_comparison_respects_the_same_request_deadline(store):
    from mech_chatbot.rag.execution import RequestBudgetLimits, RagToken
    approved_pair(store)
    executor = DefaultRagExecutor(budget_limits=RequestBudgetLimits(deadline_seconds=0))
    with bind_repository_runtime(db_engine=store):
        events = list(executor.run(request(), RagInvocation('deadline', mode='test')))
    assert any(isinstance(e, RagFailed) and e.diagnostics.budget.deadline_exceeded for e in events)
    assert not any(isinstance(e, RagToken) for e in events)


def test_comparison_provides_english_labels_and_safe_missing_scope_response(store):
    approved_pair(store)
    with bind_repository_runtime(db_engine=store):
        query = replace(request(), response_language='en')
        result = collect_rag_events(DefaultRagExecutor().run(query, RagInvocation('english', mode='evaluation')))
        missing = collect_rag_events(DefaultRagExecutor().run(replace(query,scope_comparison=('a','unknown')),
                                     RagInvocation('missing', mode='test')))
    assert 'Total A: 4.00 VND' in result.answer and 'Change: 100.00 %' in result.answer
    assert missing.answer == 'Insufficient reviewed data for this comparison.'
    assert missing.outcome == 'refused' and missing.citations == ()


def test_reviewed_value_change_in_the_same_snapshot_changes_the_citation_basis(store):
    approved_pair(store)
    with bind_repository_runtime(db_engine=store):
        before = collect_rag_events(DefaultRagExecutor().run(request(),RagInvocation('before',mode='test')))
        review_numeric_fact('fact-a', PROFILE, action='approve', value_text='6', note='Offline correction')
        review_scope_completeness('a', PROFILE, expected_row_ids=('row-a',), rounding_rule=RoundingRule(2),
                                  definition='Reviewed amount',note='Offline recheck complete scope')
        after = collect_rag_events(DefaultRagExecutor().run(request(),RagInvocation('after',mode='test')))
    a_before = next(card for card in before.citations if card['scope_id']=='a')
    a_after = next(card for card in after.citations if card['scope_id']=='a')
    assert a_before['snapshot_id'] == a_after['snapshot_id'] == 'batch-1'
    assert a_before['source_id'] != a_after['source_id']
    assert a_before['scope_hash'] != a_after['scope_hash']
    assert after.diagnostics['math_v2']['steps'][0]['display_value'] == '6.00'
