from types import SimpleNamespace
import pytest

from mech_chatbot.rag.comprehensive_execution import examine_batch


def state():
    return SimpleNamespace(checkpoint=lambda stage: None)


def test_comprehensive_generation_exposes_canonical_metadata_and_full_passages(monkeypatch):
    from langchain_core.documents import Document
    from mech_chatbot.rag import pipeline_steps
    from mech_chatbot.rag.comprehensive_execution import generate_batch
    label = 'Body label version 0.1\n'
    documents = [Document(page_content=label + 'x' * (8000 - len(label)),
        metadata={'doc_id':42, 'trang_so':3, 'version_no':1,
                  'file_goc':'approved-policy.md', 'is_current':True})]
    captured = []
    def generate(plan, **kwargs):
        captured.append(plan)
        return iter(['verified answer'])
    monkeypatch.setattr(pipeline_steps, 'generate_answer', generate)
    runtime = SimpleNamespace(request=SimpleNamespace(question='What is required?', response_language='vi',
        access=SimpleNamespace(department='HR', roles=('viewer',))), trace_id='contract',
        budget=SimpleNamespace(deadline_monotonic=None), bind_generation=lambda outcome: None, cancellation=None)
    answer, metrics = generate_batch(runtime, documents)
    context = captured[0].evidence.context_text
    assert 'approved-policy.md (Trang 3) | Version: 1 | SOURCE_ID: D42P3' in context
    assert documents[0].page_content in context
    assert captured[0].evidence.retrieved_docs is documents
    assert answer == 'verified answer'


@pytest.mark.parametrize('domain', ['generic', 'mechanical', 'tabular'])
@pytest.mark.parametrize(('language', 'line_rule', 'footer_rule'), [
    ('vi', 'Mỗi dòng chứa dữ kiện', 'Không dùng một trích dẫn chung cuối câu trả lời'),
    ('en', 'Every factual line', 'Do not use a shared citation footer'),
])
def test_generation_prompt_requires_verifiable_inline_sources(domain, language, line_rule, footer_rule):
    from mech_chatbot.rag.prompt import _build_prompt_template
    messages = _build_prompt_template(domain, language).format_messages(
        question='What is required?', context='SOURCE_ID: D42P3', chat_history_str='')
    rules = messages[0].content
    assert line_rule in rules and footer_rule in rules
    assert '[SRC:DnPm]' in rules


@pytest.mark.parametrize(('language', 'card'), [
    ('vi', '[Nguồn: tên file, Trang X, Version Y, SourceID DnPm]'),
    ('en', '[Source: file name, Page X, Version Y, SourceID DnPm]'),
])
def test_explicit_source_labels_use_existing_canonical_source_cards(language, card):
    from mech_chatbot.rag.prompt import _build_prompt_template
    rules = _build_prompt_template('generic', language).format_messages(
        question='What is required?', context='SOURCE_ID: D42P3', chat_history_str='')[0].content
    assert card in rules and '[SRC:DnPm]' in rules


@pytest.mark.parametrize(('language', 'atomic', 'scope', 'handling'), [
    ('vi', 'một khẳng định hoặc một hành động', 'không chứng minh hai nguồn không bao giờ mâu thuẫn',
     'giữ đủ các bước xử lý thiếu căn cứ'),
    ('en', 'one assertion or one action', 'does not prove that two sources never conflict',
     'retain all source-supported missing-evidence handling steps'),
])
def test_generation_keeps_independently_verifiable_actions_and_bounded_comparisons(language, atomic, scope, handling):
    from mech_chatbot.rag.prompt import _build_prompt_template
    rules = _build_prompt_template('generic', language).format_messages(
        question='Compare the processes and explain how to handle missing evidence.',
        context='SOURCE_ID: D42P3', chat_history_str='')[0].content
    assert atomic in rules and scope in rules and handling in rules


@pytest.mark.parametrize(('language', 'rule'), [
    ('vi', 'Tách nhận định khác phạm vi thành một dòng khẳng định riêng'),
    ('en', 'Put the difference in scope on its own affirmative line'),
])
def test_scope_comparison_is_separate_from_unconfirmed_conflict_or_replacement(language, rule):
    from mech_chatbot.rag.prompt import _build_prompt_template
    rules = _build_prompt_template('generic', language).format_messages(
        question='Do the two processes conflict?', context='SOURCE_ID: D42P3', chat_history_str='')[0].content
    assert rule in rules


@pytest.mark.parametrize(('language', 'rule'), [
    ('vi', 'giữ rõ tính đề xuất khi mô tả từng bước'),
    ('en', 'preserve the proposed status when describing each step'),
])
def test_generation_preserves_procedure_modality_instead_of_claiming_execution(language, rule):
    from mech_chatbot.rag.prompt import _build_prompt_template
    rules = _build_prompt_template('generic', language).format_messages(
        question='What do these procedures say?', context='SOURCE_ID: D42P3', chat_history_str='')[0].content
    assert rule in rules


@pytest.mark.parametrize(('language', 'procedure', 'identity'), [
    ('vi', 'So sánh quy trình dùng các dòng hành động có nguồn', 'Nhãn file/phiên bản được hiển thị từ metadata'),
    ('en', 'Compare procedures using cited action lines', 'File/version labels are rendered from metadata'),
])
def test_procedure_comparison_does_not_require_generated_source_identity_tables(language, procedure, identity):
    from mech_chatbot.rag.prompt import _build_prompt_template
    rules = _build_prompt_template('generic', language).format_messages(
        question='Compare the procedures.', context='SOURCE_ID: D42P3', chat_history_str='')[0].content
    assert procedure in rules and identity in rules


def row(doc=1, count=2, complete=True):
    import json
    return {'SourceOrdinal': doc - 1, 'DocID': doc, 'VersionNo': 1, 'PageOffset': 0,
            'SourceIdentityJson': json.dumps({'DocID': doc, 'VersionNo': 1, 'PageCount': count, 'ExtractionComplete': complete})}


def test_examines_all_batch_sources_without_top_k_and_leaves_long_source_pending():
    seen = []
    def pages(source, offset, limit):
        seen.append(source['DocID'])
        return [SimpleNamespace(page_content='full page', metadata={'doc_id': source['DocID'], 'trang_so': 1})]
    updates, answer, docs, metrics = examine_batch(state(), [row(), row(2, 1)], pages,
        lambda docs: ('verified answer', {'verification_status': 'verified'}))
    assert seen == [1, 2]
    assert [u['status'] for u in updates] == ['pending', 'checked']
    assert all(u['page_offset'] == 1 for u in updates)
    assert answer == 'verified answer'


def test_extraction_failure_and_missing_page_are_never_checked():
    updates, answer, docs, metrics = examine_batch(state(), [row(complete=False), row(2)],
        lambda *args: [], lambda docs: pytest.fail('no generation without pages'))
    assert [u['status'] for u in updates] == ['error', 'error']


def test_failed_verification_never_advances_offsets():
    pages = lambda *args: [SimpleNamespace(page_content='full', metadata={})]
    updates, answer, docs, metrics = examine_batch(state(), [row()], pages,
        lambda docs: ('unsafe', {'verification_status': 'unavailable'}))
    assert updates[0]['status'] == 'error'
    assert updates[0]['page_offset'] == 0
    assert not answer


def test_long_page_resumes_every_character_without_marking_page_checked_early():
    from langchain_core.documents import Document
    from mech_chatbot.rag.comprehensive_execution import MAX_PAGE_CHARACTERS
    content = 'a' * MAX_PAGE_CHARACTERS + 'tail'
    pages = lambda *args: [Document(page_content=content, metadata={'doc_id':1, 'trang_so':1})]
    examined = []
    def generate(docs):
        examined.append(docs[0].page_content)
        return 'answer', {'verification_status':'verified'}
    source = row(count=1)
    updates, *_ = examine_batch(state(), [source], pages, generate)
    assert updates[0]['status'] == 'pending' and updates[0]['page_offset'] == 0
    assert updates[0]['text_offset'] == MAX_PAGE_CHARACTERS
    updates, *_ = examine_batch(state(), [{**source, 'TextOffset': updates[0]['text_offset']}], pages, generate)
    assert updates[0]['status'] == 'checked' and updates[0]['page_offset'] == 1
    assert updates[0]['text_offset'] == 0
    assert ''.join(examined) == content


def test_public_pipeline_prepared_cursor_resume_and_verified_progress(monkeypatch):
    from dataclasses import replace
    from mech_chatbot.rag.comprehensive import CursorBinding, ScanBatch
    from mech_chatbot.rag.comprehensive_execution import execute_comprehensive
    from mech_chatbot.rag.execution import RagRequest, AccessScope
    binding = CursorBinding('scan', 1, 'q', 'a', 'comprehensive', 'snapshot', 0)
    source = row(1, 1)
    class Store:
        cursor_secret = 'test-signing-secret'
        def __init__(self):
            self.current = ScanBatch('scan', 'snapshot', (source,), 0, 1,
                {'pending': 1, 'checked': 0, 'error': 0, 'denied': 0}, False, binding, ())
            self.reads = 0
        def start(self, **kwargs):
            return 'scan', binding
        def load_batch(self, binding, **kwargs):
            self.reads += 1
            return self.current
        def save_batch(self, binding, updates, **kwargs):
            assert updates[0]['status'] == 'checked'
            self.current = replace(self.current, rows=(), next_offset=1,
                counts={'pending': 0, 'checked': 1, 'error': 0, 'denied': 0}, complete=True,
                binding=replace(binding, next_offset=1), results=tuple(updates[0]['results']))
            return self.current.binding
        def get_results(self, binding, **kwargs):
            self.reads += 1
            return self.current
    store = Store()
    def prepared(values):
        return SimpleNamespace(stream=values[0], diagnostics=dict(values[4]))
    runtime = SimpleNamespace(request=RagRequest('scan', AccessScope(user_id=1, username='user'),search_mode='comprehensive'),
        checkpoint=lambda stage: None, prepared=prepared)
    result = execute_comprehensive(runtime, store=store,
        page_loader=lambda *args: [SimpleNamespace(page_content='full page', metadata={'doc_id': 1, 'trang_so': 1})],
        generator=lambda docs: ('verified answer', {'verification_status': 'verified'}))
    assert result.diagnostics['comprehensive']['continuation_cursor']
    assert result.diagnostics['comprehensive']['pending_sources'] == 1
    assert 'verified answer' in ''.join(result.stream)
    assert result.diagnostics['comprehensive']['snapshot_complete']
    assert store.reads >= 2


def test_sql_pages_include_all_extracted_fields_without_structured_locator(monkeypatch):
    from sqlalchemy import create_engine, text
    from mech_chatbot.rag.comprehensive_execution import load_pages
    from mech_chatbot.db.repositories import structured_evidence
    from mech_chatbot.db.repositories import feedback_access
    engine = create_engine('sqlite://')
    with engine.begin() as conn:
        conn.execute(text('CREATE TABLE DocumentPages (DocID INTEGER, PageNo INTEGER, TextExtract TEXT, LocalOCRText TEXT, VisionSummary TEXT)'))
        conn.execute(text("INSERT INTO DocumentPages VALUES (1,1,'native','ocr detail','vision detail')"))
    monkeypatch.setattr(structured_evidence, '_source', lambda *args: {'TenFile': 'a.pdf', 'SecurityLevel': 'public'})
    monkeypatch.setattr(feedback_access, 'context_hash', lambda *args: 'test-context-hash')
    docs = load_pages(engine, {}, row(), 0, 1)
    assert docs[0].page_content == 'native\nocr detail\nvision detail'
    assert 'locator' not in docs[0].metadata
    assert docs[0].metadata['doc_id'] == 1


def test_public_pipeline_timeout_retains_cursor_and_marks_paused(monkeypatch):
    from dataclasses import replace
    from mech_chatbot.rag.comprehensive import CursorBinding, ScanBatch
    from mech_chatbot.rag.comprehensive_execution import execute_comprehensive
    from mech_chatbot.rag.execution import RagRequest, AccessScope, RequestDeadlineExceeded
    binding = CursorBinding('scan', 1, 'q', 'a', 'comprehensive', 'snapshot', 0)
    batch = ScanBatch('scan', 'snapshot', (row(),), 0, 1,
        {'pending': 1, 'checked': 0, 'error': 0, 'denied': 0}, False, binding, ())
    store = SimpleNamespace(cursor_secret='secret', start=lambda **kw: ('scan', binding), load_batch=lambda *a, **kw: batch)
    def fail(stage):
        raise RequestDeadlineExceeded('expired')
    runtime = SimpleNamespace(request=RagRequest('scan', AccessScope(user_id=1,username='u'),search_mode='comprehensive'),
        checkpoint=fail, prepared=lambda v: SimpleNamespace(stream=v[0],diagnostics=dict(v[4])))
    result = execute_comprehensive(runtime,store=store)
    with pytest.raises(RequestDeadlineExceeded):
        list(result.stream)
    progress = result.diagnostics['comprehensive']
    assert progress['scan_status'] == 'paused'
    assert progress['pending_sources'] == 1
    assert progress['continuation_cursor']


@pytest.mark.parametrize('concurrent_save', [False, True])
def test_old_cursor_replays_each_committed_revision_without_skipping_results(concurrent_save):
    from dataclasses import replace
    from mech_chatbot.rag.comprehensive import CursorBinding, ScanBatch, encode_cursor, decode_cursor
    from mech_chatbot.rag.comprehensive_execution import execute_comprehensive
    from mech_chatbot.rag.execution import RagRequest, AccessScope
    initial = CursorBinding('scan', 1, 'q', 'a', 'comprehensive', 'snapshot', 0)
    latest = replace(initial, next_offset=3)
    results = tuple({'batch_revision':n, 'answer':f'answer-{n}', 'page_content':f'page-{n}',
        'metadata':{'doc_id':1,'version_no':1,'trang_so':n}} for n in (1, 2, 3))
    batch = ScanBatch('scan', 'snapshot', (), 3, 1, {'checked':1}, True, latest, results)
    calls = []
    def load(*args, **kwargs):
        calls.append(True)
        if concurrent_save and len(calls) <= 2:
            return replace(batch, rows=(row(1, 1),), binding=initial, next_offset=0, complete=False)
        return batch
    store = SimpleNamespace(cursor_secret='secret', load_batch=load,
        get_results=lambda *a, **k:batch, save_batch=lambda *a, **k:latest)
    cursor = encode_cursor(initial, 'secret')
    for n in (1, 2, 3):
        runtime = SimpleNamespace(request=RagRequest('scan', AccessScope(user_id=1),
            search_mode='comprehensive', continuation_cursor=cursor),
            checkpoint=lambda stage:None,
            prepared=lambda v:SimpleNamespace(stream=v[0],diagnostics=dict(v[4])))
        prepared = execute_comprehensive(runtime, store=store,
            page_loader=lambda *a:[SimpleNamespace(page_content='page',metadata={'doc_id':1,'trang_so':1})],
            generator=lambda docs:('answer',{'verification_status':'verified'}))
        answer = ''.join(prepared.stream)
        assert f'answer-{n}' in answer
        assert all(f'answer-{other}' not in answer for other in (1,2,3) if other != n)
        progress = prepared.diagnostics['comprehensive']
        cursor = progress['continuation_cursor']
        assert decode_cursor(cursor, 'secret').next_offset == n
        assert progress['results_pending'] is (n < 3)


@pytest.mark.parametrize('exception,status', [('stale','stale'), ('revoked','denied')])
def test_snapshot_or_access_change_before_lazy_batch_hides_saved_counts(exception, status):
    from mech_chatbot.rag.comprehensive import CursorBinding, ScanBatch, SnapshotStale, CursorMismatch
    from mech_chatbot.rag.comprehensive_execution import execute_comprehensive
    from mech_chatbot.rag.execution import RagRequest, AccessScope
    binding = CursorBinding('scan', 1, 'q', 'a', 'comprehensive', 'snapshot', 0)
    batch = ScanBatch('scan', 'snapshot', (row(),), 0, 9, {'pending':9}, False, binding, ())
    calls = []
    def load(*args, **kwargs):
        calls.append(True)
        if len(calls) > 1:
            raise SnapshotStale() if exception == 'stale' else CursorMismatch()
        return batch
    store = SimpleNamespace(cursor_secret='secret', start=lambda **k:('scan',binding),load_batch=load)
    runtime = SimpleNamespace(request=RagRequest('scan', AccessScope(user_id=1),search_mode='comprehensive'),
        prepared=lambda v:SimpleNamespace(stream=v[0],diagnostics=dict(v[4])))
    prepared = execute_comprehensive(runtime,store=store)
    if exception == 'revoked':
        with pytest.raises(CursorMismatch):
            list(prepared.stream)
    else:
        list(prepared.stream)
    progress = prepared.diagnostics['comprehensive']
    assert progress['scan_status'] == status
    assert 'total_sources' not in progress and 'continuation_cursor' not in progress


@pytest.mark.parametrize('failure', ['scope_changed', 'permission_denied', 'invalid_cursor'])
def test_initial_resume_denial_returns_explicit_progress_without_source_details(failure):
    from mech_chatbot.rag.comprehensive import CursorBinding, CursorMismatch, encode_cursor
    from mech_chatbot.rag.comprehensive_execution import execute_comprehensive
    from mech_chatbot.rag.execution import RagRequest, AccessScope
    binding = CursorBinding('private-scan', 1, 'q', 'a', 'comprehensive', 'private-snapshot', 1)
    def load(*args, **kwargs):
        raise PermissionError('private-source') if failure == 'permission_denied' else CursorMismatch('private-source')
    cursor = 'invalid' if failure == 'invalid_cursor' else encode_cursor(binding, 'secret')
    store = SimpleNamespace(cursor_secret='secret', load_batch=load)
    runtime = SimpleNamespace(request=RagRequest('scan', AccessScope(user_id=1),
        search_mode='comprehensive', continuation_cursor=cursor),
        prepared=lambda v: SimpleNamespace(stream=v[0], diagnostics=dict(v[4])))
    prepared = execute_comprehensive(runtime, store=store)
    progress = prepared.diagnostics['comprehensive']
    assert progress == {'search_mode': 'comprehensive', 'scan_status': 'denied',
                        'snapshot_complete': False, 'turn_complete': True}
    assert 'private' not in ''.join(prepared.stream)
