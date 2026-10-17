"""File links must revalidate the exact reviewed source at click time."""
from types import SimpleNamespace
from hashlib import sha256
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine,text
from sqlalchemy.pool import StaticPool

from mech_chatbot.api.dependencies import current_profile
from mech_chatbot.api.routers import documents
from mech_chatbot.application.protected_files import AuthorizedFile
from mech_chatbot.config.repository_runtime import bind_repository_runtime
from mech_chatbot.rag.execution import DefaultRagExecutor,RagInvocation,collect_rag_events
from .test_rag_scope_comparison import approved_pair,approved_empty_pair,request
from .test_structured_evidence_repository import PROFILE,store


@pytest.fixture
def files(store,tmp_path):
    path=tmp_path/'export.csv';path.write_bytes(b'private file')
    approved_pair(store,source_hash=sha256(path.read_bytes()).hexdigest())
    with bind_repository_runtime(db_engine=store):
        result=collect_rag_events(DefaultRagExecutor().run(request(),RagInvocation('file',mode='test')))
    engine=create_engine('sqlite://',connect_args={'check_same_thread':False},poolclass=StaticPool)
    with store.connect() as a,engine.connect() as b:
        a.connection.driver_connection.backup(b.connection.driver_connection)
    app=FastAPI();app.include_router(documents.files_router)
    app.dependency_overrides[current_profile]=lambda:PROFILE
    opened=[]
    app.state.runtime=SimpleNamespace(protected_file_resolver=SimpleNamespace(resolve=lambda ref,actor:
        opened.append(ref) or AuthorizedFile(path,filename='export.csv')))
    yield engine,TestClient(app),result.citations[0],opened,path
    engine.dispose()


def pin(card):
    return {key:card[key] for key in ('version_no','snapshot_id','source_hash','scope_id','scope_hash')}


@pytest.mark.parametrize('identity', ['strings', 'integers', 'mixed'])
def test_empty_scope_file_link_requires_current_declaration_and_exact_bytes(store, tmp_path, identity):
    selected, _ = approved_empty_pair(store, tmp_path, identity=identity)
    with bind_repository_runtime(db_engine=store):
        result = collect_rag_events(DefaultRagExecutor().run(selected, RagInvocation('empty-file', mode='test')))
    card = next(c for c in result.citations if c['doc_id']==1)
    path = tmp_path/'export-1.csv'
    engine = create_engine('sqlite://', connect_args={'check_same_thread':False}, poolclass=StaticPool)
    with store.connect() as source, engine.connect() as target:
        source.connection.driver_connection.backup(target.connection.driver_connection)
    app = FastAPI()
    app.include_router(documents.files_router)
    app.dependency_overrides[current_profile] = lambda: PROFILE
    opened = []
    app.state.runtime = SimpleNamespace(protected_file_resolver=SimpleNamespace(resolve=lambda ref, actor:
        opened.append(ref) or AuthorizedFile(path, filename='export-1.csv')))
    try:
        with TestClient(app) as client, bind_repository_runtime(db_engine=engine):
            response = client.get('/api/files/documents/1/original', params=pin(card))
            assert response.status_code==200 and response.content==b'amount [VND]\n'
            assert response.headers['cache-control']=='private, no-store'
            opened.clear()
            with engine.begin() as conn:
                conn.execute(text('UPDATE StructuredSourceSnapshot SET CurrentSnapshot=0 WHERE DocID=1'))
            response = client.get('/api/files/documents/1/original', params=pin(card))
            assert response.status_code==404 and opened==[]
            assert 'amount' not in response.text
    finally:
        engine.dispose()


@pytest.mark.parametrize('route',['original','pages/2'])
def test_changed_revision_is_rejected_before_file_resolution(files,route):
    engine,client,card,opened,path=files
    with engine.begin() as connection:
        connection.execute(text('UPDATE TaiLieu SET VersionNo=3'))
    with bind_repository_runtime(db_engine=engine):
        response=client.get('/api/files/documents/1/'+route,params=pin(card))
    assert response.status_code==404 and opened==[]
    assert 'private file' not in response.text and 'batch-1' not in response.text


def test_original_rejects_changed_bytes_without_returning_wrong_revision(files):
    engine,client,card,opened,path=files
    path.write_bytes(b'changed secret file')
    with bind_repository_runtime(db_engine=engine):
        response=client.get('/api/files/documents/1/original',params=pin(card))
    assert response.status_code==404 and b'changed secret file' not in response.content


def test_original_returns_exact_pinned_bytes_without_cache_reuse(files):
    engine,client,card,opened,path=files
    with bind_repository_runtime(db_engine=engine):
        response=client.get('/api/files/documents/1/original',params=pin(card))
    assert response.status_code==200 and response.content==b'private file'
    assert response.headers['cache-control']=='private, no-store'


def test_generated_citation_url_carries_the_full_source_pin(files):
    from urllib.parse import urlsplit,parse_qs
    engine,client,card,opened,path=files
    query=parse_qs(urlsplit(card['original_url']).query)
    assert query=={key:[str(value)] for key,value in pin(card).items()}


@pytest.mark.parametrize('params',[{'snapshot_id':'batch-1'},{'version_no':0},{'scope_hash':'not-a-hash'}])
def test_malformed_or_partial_pin_never_opens_a_file(files,params):
    engine,client,card,opened,path=files
    response=client.get('/api/files/documents/1/original',params=params)
    assert response.status_code==422 and opened==[]


def test_legacy_unpinned_file_request_keeps_the_existing_route(files):
    engine,client,card,opened,path=files
    response=client.get('/api/files/documents/1/original')
    assert response.status_code==200 and response.content==b'private file'


@pytest.mark.parametrize('change',['snapshot','review','wrong_doc','rights'])
def test_pin_denial_hides_source_metadata_before_file_lookup(files,change):
    engine,client,card,opened,path=files
    if change=='snapshot':
        with engine.begin() as conn: conn.execute(text('UPDATE StructuredSourceSnapshot SET CurrentSnapshot=0'))
    if change=='review':
        from mech_chatbot.db.repositories.structured_evidence import review_numeric_fact,review_scope_completeness
        from mech_chatbot.domain.structured_evidence import RoundingRule
        review_numeric_fact('fact-a',PROFILE,action='approve',value_text='6',note='Offline fixture correction',db_engine=engine)
        review_scope_completeness('a',PROFILE,expected_row_ids=('row-a',),rounding_rule=RoundingRule(2),
                                  definition='Reviewed amount',note='Offline fixture recheck',db_engine=engine)
    if change=='rights':
        client.app.dependency_overrides[current_profile]=lambda:{**PROFILE,'allowed_sites':['elsewhere']}
    with bind_repository_runtime(db_engine=engine):
        response=client.get(f'/api/files/documents/{2 if change=="wrong_doc" else 1}/original',params=pin(card))
    assert response.status_code==404 and opened==[] and 'batch-1' not in response.text


def test_pin_storage_failure_has_a_safe_service_error(files):
    engine,client,card,opened,path=files
    with engine.begin() as conn: conn.execute(text('DROP TABLE StructuredEvidenceScope'))
    with bind_repository_runtime(db_engine=engine):
        response=client.get('/api/files/documents/1/original',params=pin(card))
    assert response.status_code==503 and 'SELECT' not in response.text and opened==[]


def test_pinned_file_size_limit_and_missing_file_are_explicit(files,monkeypatch):
    from mech_chatbot.api import source_file_pins
    engine,client,card,opened,path=files
    monkeypatch.setattr(source_file_pins,'MAX_UPLOAD_BYTES',4)
    with bind_repository_runtime(db_engine=engine):
        response=client.get('/api/files/documents/1/original',params=pin(card))
    assert response.status_code==413
    path.unlink()  # Test-owned temporary fixture only.
    with bind_repository_runtime(db_engine=engine):
        missing=client.get('/api/files/documents/1/original',params=pin(card))
    assert missing.status_code==404 and str(path) not in missing.text


def test_v2_image_cannot_be_used_until_its_rendered_snapshot_is_proven(store):
    from mech_chatbot.application.chat_citations import build_citation_list
    approved_pair(store,source_page=2)
    with bind_repository_runtime(db_engine=store):
        result=collect_rag_events(DefaultRagExecutor().run(request(),RagInvocation('page',mode='test')))
    cards=build_citation_list(result.diagnostics['citation_docs'],page_has_vision=lambda *a:True)
    assert all(card['page_url'] is None and not card['has_vision'] for card in cards)
    shared=create_engine('sqlite://',connect_args={'check_same_thread':False},poolclass=StaticPool)
    with store.connect() as a,shared.connect() as b:
        a.connection.driver_connection.backup(b.connection.driver_connection)
    app=FastAPI();app.include_router(documents.files_router)
    app.dependency_overrides[current_profile]=lambda:PROFILE
    app.state.runtime=SimpleNamespace(protected_file_resolver=SimpleNamespace(
        resolve=lambda *a:pytest.fail('Unbound rendered image must not be opened')))
    try:
        with bind_repository_runtime(db_engine=shared):
            response=TestClient(app).get('/api/files/documents/1/pages/2',params=pin(cards[0]))
        assert response.status_code==404
    finally:
        shared.dispose()


def test_verified_page_snapshot_serves_only_the_bound_image_bytes(store,tmp_path):
    from mech_chatbot.db.repositories.source_page_images import save_source_page_images
    with store.begin() as conn:conn.execute(text('ALTER TABLE StructuredSourceSnapshot ADD PageImagesJson TEXT'))
    approved_pair(store,source_page=2)
    image=tmp_path/'page.png';image.write_bytes(b'approved rendered PNG')
    digest=sha256(image.read_bytes()).hexdigest()
    save_source_page_images(1,2,'batch-1','a'*64,{2:digest},db_engine=store)
    with bind_repository_runtime(db_engine=store):
        result=collect_rag_events(DefaultRagExecutor().run(request(),RagInvocation('image',mode='test')))
    from mech_chatbot.application.chat_citations import build_citation_list
    cards=build_citation_list(result.diagnostics['citation_docs'],page_has_vision=lambda *a:True)
    assert cards[0]['has_vision'] and 'image_hash='+digest in cards[0]['page_url']
    shared=create_engine('sqlite://',connect_args={'check_same_thread':False},poolclass=StaticPool)
    with store.connect() as a,shared.connect() as b:a.connection.driver_connection.backup(b.connection.driver_connection)
    app=FastAPI();app.include_router(documents.files_router)
    app.dependency_overrides[current_profile]=lambda:PROFILE
    app.state.runtime=SimpleNamespace(protected_file_resolver=SimpleNamespace(resolve=lambda *a:AuthorizedFile(image)))
    try:
        with bind_repository_runtime(db_engine=shared):
            response=TestClient(app).get(cards[0]['page_url'])
        assert response.status_code==200 and response.content==b'approved rendered PNG'
        assert response.headers['cache-control']=='private, no-store'
        image.write_bytes(b'wrong rendered image')
        with bind_repository_runtime(db_engine=shared):
            wrong=TestClient(app).get(cards[0]['page_url'])
        assert wrong.status_code==404 and b'wrong rendered image' not in wrong.content
    finally:shared.dispose()
