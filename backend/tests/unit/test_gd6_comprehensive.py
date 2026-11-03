import pytest
from sqlalchemy import create_engine, text

from mech_chatbot.rag.comprehensive import (
    CursorBinding,
    CursorMismatch,
    SnapshotStale,
    access_scope_fingerprint,
    assert_cursor_binding,
    decode_cursor,
    encode_cursor,
    query_fingerprint,
    snapshot_fingerprint,
    ComprehensiveScanStore,
)


@pytest.fixture
def store():
    engine = create_engine('sqlite://')
    with engine.begin() as conn:
        conn.execute(text("ATTACH DATABASE ':memory:' AS dbo"))
        conn.execute(text('''CREATE TABLE dbo.RagComprehensiveScan (
            ScanID TEXT PRIMARY KEY, UserID INTEGER, Username TEXT, QueryFingerprint TEXT,
            AccessScopeFingerprint TEXT, SearchMode TEXT, SnapshotFingerprint TEXT, Status TEXT,
            NextOffset INTEGER, TotalSources INTEGER, CheckedCount INTEGER, ErrorCount INTEGER,
            CreatedAt TEXT, UpdatedAt TEXT)'''))
        conn.execute(text('''CREATE TABLE dbo.RagComprehensiveSource (
            ScanID TEXT, SourceOrdinal INTEGER, DocID INTEGER, VersionNo TEXT, SourceIdentityJson TEXT,
            Status TEXT, ErrorCode TEXT, CheckedAt TEXT, PageOffset INTEGER DEFAULT 0,
            TextOffset INTEGER DEFAULT 0, PRIMARY KEY(ScanID,SourceOrdinal))'''))
    with engine.begin() as conn:
        conn.execute(text('CREATE TABLE dbo.RagComprehensiveBatch (ScanID TEXT, Revision INTEGER, ResultsJson TEXT, PRIMARY KEY(ScanID,Revision))'))
    catalog = [{'DocID': 1, 'VersionNo': 1, 'SourceHash': 'a' * 64},
               {'DocID': 2, 'VersionNo': 1, 'SourceHash': 'b' * 64}]
    repository = ComprehensiveScanStore(engine, cursor_secret='test-secret', catalog_loader=lambda **_: catalog)
    auth = dict(user_id=7, question='all procedures', access_scope={'user_id':7,'username':'u','allowed_sites':['HN']})
    _, binding = repository.start(username='u', **auth)
    return repository, binding, auth, catalog


def test_saved_progress_is_atomic_replayable_and_keeps_final_offset(store):
    repository, binding, auth, _ = store
    result = {'page_content': 'evidence', 'metadata': {'doc_id': 1}}
    advanced = repository.save_batch(binding, [{'SourceOrdinal':0,'status':'pending','page_offset':1,'results':[result]}], **auth)
    assert advanced.next_offset == 1
    replay = repository.load_batch(binding, **auth)
    assert replay.binding == advanced
    assert replay.rows[0]['SourceOrdinal'] == 1
    assert replay.rows[1]['PageOffset'] == 1
    assert replay.results == ()
    assert repository.get_results(advanced, **auth).results == (result,)
    assert repository.save_batch(binding, [{'SourceOrdinal':0,'status':'checked','page_offset':99,'results':[]}], **auth) == advanced
    final = repository.save_batch(advanced, [{'SourceOrdinal':0,'status':'checked','page_offset':2,'results':[]},
                                               {'SourceOrdinal':1,'status':'checked','page_offset':1,'results':[]}], **auth)
    batch = repository.load_batch(final, **auth)
    assert batch.complete and not batch.rows and batch.next_offset == 2
    assert repository.get_results(final, result_revision=1, **auth).results == (result,)


def test_loading_and_saving_validate_owner_query_scope(store):
    repository, binding, auth, _ = store
    for changed in ({'user_id':8}, {'question':'different'}, {'access_scope':{'allowed_sites':['HCM']}}):
        invalid = {**auth, **changed}
        with pytest.raises(CursorMismatch):
            repository.load_batch(binding, **invalid)
        with pytest.raises(CursorMismatch):
            repository.save_batch(binding, [], **invalid)


def test_changed_content_commits_stale_before_raising_and_denies_saved_results(store):
    repository, binding, auth, catalog = store
    catalog[0] = {**catalog[0], 'SourceHash':'c' * 64}
    with pytest.raises(SnapshotStale):
        repository.get_results(binding, **auth)
    with repository.engine.connect() as conn:
        assert conn.execute(text('SELECT Status FROM dbo.RagComprehensiveScan')).scalar() == 'stale'


def test_partial_error_and_denied_sources_never_complete(store):
    repository, binding, auth, _ = store
    advanced = repository.save_batch(binding, [{'SourceOrdinal':0,'status':'checked','page_offset':1,'results':[]},
        {'SourceOrdinal':1,'status':'error','page_offset':0,'error_code':'extraction','results':[]}], **auth)
    batch = repository.load_batch(advanced, **auth)
    assert not batch.complete and batch.counts['error'] == 1


def test_scope_and_snapshot_include_site_and_content():
    assert access_scope_fingerprint({'site':'HN'}) != access_scope_fingerprint({'site':'HCM'})
    assert snapshot_fingerprint([{'DocID':1,'PagesFingerprint':'a'}]) != snapshot_fingerprint([{'DocID':1,'PagesFingerprint':'b'}])


def test_extraction_completeness_requires_authoritative_total_and_every_page():
    from mech_chatbot.db.repositories.comprehensive import _page_identity
    pages = [{'PageNo':1,'TextExtract':'source','ExtractionStatus':'success'}]
    assert not _page_identity(pages, None)['ExtractionComplete']
    assert not _page_identity(pages, {'status':'success','total_pages':2})['ExtractionComplete']
    assert _page_identity(pages, {'status':'success','total_pages':1})['ExtractionComplete']
    assert not _page_identity([{**pages[0], 'ExtractionStatus':'failed'}],
                              {'status':'success','total_pages':1})['ExtractionComplete']


def test_invalid_progress_rolls_back_batch_revision_and_all_source_writes(store):
    repository, binding, auth, _ = store
    with pytest.raises(ValueError):
        repository.save_batch(binding, [{'SourceOrdinal':0,'status':'checked','page_offset':1,'results':[]},
                                       {'SourceOrdinal':99,'status':'checked','page_offset':1,'results':[]}], **auth)
    batch = repository.load_batch(binding, **auth)
    assert batch.binding == binding and batch.counts['pending'] == 2


def test_reauthorization_denial_blocks_saved_results(store):
    repository, binding, auth, _ = store
    with pytest.raises(CursorMismatch):
        repository.get_results(binding, reauthorize=lambda:False, **auth)


def test_progress_read_never_materializes_saved_results_and_result_read_is_one_revision(store):
    from sqlalchemy import event
    repository, binding, auth, _ = store
    first = repository.save_batch(binding, [{'SourceOrdinal':0,'status':'pending','page_offset':1,
        'results':[{'answer':'first','batch_revision':1}]}], **auth)
    second = repository.save_batch(first, [{'SourceOrdinal':0,'status':'checked','page_offset':2,
        'results':[{'answer':'second','batch_revision':2}]}], **auth)
    statements = []
    event.listen(repository.engine, 'before_cursor_execute',
        lambda conn,cursor,statement,parameters,context,many: statements.append(statement))
    assert repository.load_batch(second, batch_size=1, **auth).results == ()
    assert all('ResultsJson' not in sql and 'SELECT * FROM dbo.RagComprehensiveSource' not in sql
               for sql in statements)
    assert repository.get_results(second, result_revision=1, **auth).results == (
        {'answer':'first','batch_revision':1},)


def test_catalog_rejects_revoked_rights_even_with_unchanged_documents(monkeypatch):
    from mech_chatbot.db.repositories import comprehensive as catalog
    monkeypatch.setattr(catalog, 'load_user_profile', lambda *a, **k: {
        'user_id':7,'username':'u','roles':['viewer'],'allowed_departments':['A'],
        'allowed_sites':['HN'],'max_security_level':'public'})
    scope = {'user_id':7,'username':'u','roles':['viewer'],'allowed_departments':['A'],
             'allowed_sites':['HN'],'max_security_level':'public'}
    assert catalog._validate_actor(scope)['user_id'] == 7
    for changed in ({'roles':['admin']}, {'allowed_departments':['B']},
                    {'allowed_sites':['HCM']}, {'max_security_level':'confidential'}):
        with pytest.raises(CursorMismatch):
            catalog._validate_actor({**scope, **changed})


def test_catalog_sql_pins_publication_acl_and_exact_report_identity(monkeypatch):
    import json
    from mech_chatbot.db.repositories import comprehensive as catalog
    scope = {'user_id':7,'username':'u','roles':['viewer'],'allowed_departments':['A'],
             'allowed_sites':['HN'],'max_security_level':'public'}
    monkeypatch.setattr(catalog, 'load_user_profile', lambda *a, **k: scope)
    report = {'status':'success','total_pages':1,'structured_source_snapshot':{
        'doc_id':1,'version':'1','snapshot_id':'snap','source_hash':'a'*64,'status':'registered'}}
    row = {'DocID':1,'VersionNo':1,'SnapshotID':'snap','SourceHash':'a'*64,
           'ExtractionReport':json.dumps(report)}
    statements = []
    class Result:
        def __init__(self, rows): self.rows = rows
        def mappings(self): return self
        def all(self): return self.rows
        def __iter__(self): return iter(self.rows)
    class Connection:
        def __enter__(self): return self
        def __exit__(self, *args): pass
        def execution_options(self, **kwargs): return self
        def begin(self): return self
        def execute(self, statement, params):
            statements.append(str(statement))
            return Result([{'PageNo':1,'TextExtract':'source','ExtractionStatus':'success'}]
                          if 'FROM dbo.DocumentPages' in str(statement) else [row])
    class Engine:
        def connect(self): return Connection()
    monkeypatch.setattr(catalog, 'resolve_engine', lambda _:Engine())
    result = catalog.list_comprehensive_catalog(**scope)
    assert result[0]['ExtractionComplete']
    sql = statements[0]
    assert "d.PublicationState = 'published'" in sql and "d.ReviewStatus = 'approved'" in sql
    assert 'd.IsCurrent = 1' in sql and 'dbo.PhongBanChiaSe' in sql and 'd.Site IN' in sql
    assert 'j.FilePath=d.FilePath' in sql and 's.CurrentSnapshot=1' in sql
    row['ExtractionReport'] = json.dumps({**report, 'structured_source_snapshot':{}})
    assert not catalog.list_comprehensive_catalog(**scope)[0]['ExtractionComplete']


def _binding():
    return CursorBinding("scan-1", 7, query_fingerprint("Tất cả quy trình"),
                         access_scope_fingerprint({"user_id": 7, "allowed_sites": ["HN"]}),
                         "comprehensive", snapshot_fingerprint(({"DocID": 1, "VersionNo": "2"},)), 3)


def test_cursor_is_signed_and_round_trips():
    binding = _binding()
    assert decode_cursor(encode_cursor(binding, "secret"), "secret") == binding
    with pytest.raises(CursorMismatch):
        decode_cursor(encode_cursor(binding, "secret"), "wrong")


def test_cursor_binding_rejects_scope_and_marks_snapshot_stale():
    binding = _binding()
    with pytest.raises(CursorMismatch):
        assert_cursor_binding(binding, user_id=8, query="Tất cả quy trình",
                              access_scope={"user_id": 8, "allowed_sites": ["HN"]},
                              snapshot=binding.snapshot_fingerprint)
    with pytest.raises(SnapshotStale):
        assert_cursor_binding(binding, user_id=7, query="Tất cả quy trình",
                              access_scope={"user_id": 7, "allowed_sites": ["HN"]},
                              snapshot="0" * 64)
