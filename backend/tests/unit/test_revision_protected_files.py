"""Revision-qualified browser requests use current ACL and source metadata."""
from types import SimpleNamespace
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, event, text
from sqlalchemy.pool import StaticPool
from mech_chatbot.adapters.protected_files import SqlProtectedFileStore, FilesystemProtectedFileStorage
from mech_chatbot.application.protected_files import ProtectedFileResolver
from mech_chatbot.api.dependencies import current_profile
from mech_chatbot.api.routers import documents
from .test_chat_history_snapshot import HistoryEngine


@pytest.fixture
def revision_files(tmp_path):
    raw = tmp_path/'raw'; raw.mkdir()
    processed = tmp_path/'processed'; processed.mkdir()
    original = raw/'manual.pdf'; original.write_bytes(b'approved original')
    page = processed/'page.png'; page.write_bytes(b'approved page')
    database = create_engine('sqlite://',connect_args={'check_same_thread':False},poolclass=StaticPool)
    with database.begin() as conn:
        conn.execute(text('CREATE TABLE TaiLieu(DocID INT,TenFile TEXT,FilePath TEXT,ThuMuc TEXT,SecurityLevel TEXT,Site TEXT,LifecycleStatus TEXT,ReviewStatus TEXT,Servable INT,PublicationState TEXT,IsCurrent INT,EffectiveStatus TEXT,EffectiveDate TEXT,ExpiryDate TEXT,VersionNo INT,OwnerDepartment TEXT,KnowledgeOwnerUserID INT,KnowledgeApproverUserID INT)'))
        conn.execute(text("INSERT INTO TaiLieu VALUES(42,'manual.pdf',:path,'ENG','internal','factory','published','approved',1,'published',1,'effective',NULL,NULL,2,'ENG',NULL,NULL)"), {'path':str(original)})
        conn.execute(text('CREATE TABLE DepartmentKnowledgeGovernance(DeptCode TEXT,KnowledgeApproverUserID INT,IsActive INT)'))
        conn.execute(text('CREATE TABLE PhongBanChiaSe(DocID INT,DeptCode TEXT)'))
        conn.execute(text("INSERT INTO PhongBanChiaSe VALUES(42,'ENG')"))
        conn.execute(text('CREATE TABLE DocumentPages(DocID INT,PageNo INT,ImagePath TEXT)'))
        conn.execute(text('INSERT INTO DocumentPages VALUES(42,1,:path)'), {'path':str(page)})
    engine = HistoryEngine(database)
    profile = {'username':'alice','roles':['viewer'],'allowed_departments':['ENG'],
               'allowed_sites':['factory'],'max_security_level':'internal'}
    audit = SimpleNamespace(record=lambda record:None)
    app = FastAPI(); app.include_router(documents.files_router)
    app.dependency_overrides[current_profile] = lambda:profile
    app.state.database_runtime = SimpleNamespace(engine=engine)
    app.state.runtime = SimpleNamespace(protected_file_resolver=ProtectedFileResolver(
        store=SqlProtectedFileStore(engine_provider=lambda:engine),
        storage=FilesystemProtectedFileStorage(project_root=tmp_path,raw_root=raw,processed_root=processed),
        audit=audit,strict_site_filter=True))
    yield database, TestClient(app), engine, original, page
    database.dispose()


@pytest.mark.parametrize('route',['original','pages/1'])
@pytest.mark.parametrize('change',['version','unknown','acl','unpublished'])
def test_qualified_file_request_hides_stale_revision_and_current_denials(revision_files,route,change):
    database,client,engine,original,page = revision_files
    sql = {'version':'VersionNo=3','unknown':'VersionNo=NULL','acl':"SecurityLevel='confidential'",
           'unpublished':'Servable=0'}[change]
    with database.begin() as conn:conn.execute(text('UPDATE TaiLieu SET '+sql))
    response = client.get('/api/files/documents/42/'+route,params={'citation_version':2})
    assert response.status_code == 404
    assert response.json() == {'detail':'File not found'}
    assert b'approved' not in response.content


@pytest.mark.parametrize('route,body',[('original',b'approved original'),('pages/1',b'approved page')])
def test_qualified_file_request_serves_current_revision_with_no_cache(revision_files,route,body):
    database,client,engine,original,page = revision_files
    response = client.get('/api/files/documents/42/'+route,params={'citation_version':2})
    assert response.status_code == 200 and response.content == body
    assert response.headers['cache-control'] == 'private, no-store'
    assert engine.opens == 1


def test_unqualified_file_request_preserves_legacy_behavior(revision_files):
    database,client,engine,original,page = revision_files
    with database.begin() as conn:conn.execute(text('UPDATE TaiLieu SET VersionNo=NULL'))
    assert client.get('/api/files/documents/42/original').content == b'approved original'


def test_qualified_page_does_not_substitute_an_image_with_the_same_basename(revision_files):
    database,client,engine,original,page = revision_files
    with database.begin() as conn:
        conn.execute(text("UPDATE DocumentPages SET ImagePath='/unapproved/worker/page.png'"))
    response=client.get('/api/files/documents/42/pages/1',params={'citation_version':2})
    assert response.status_code==404 and b'approved page' not in response.content
    assert client.get('/api/files/documents/42/pages/1').content==b'approved page'


def test_qualified_download_checks_size_before_reading_the_whole_file(revision_files,monkeypatch):
    import io
    from pathlib import Path
    from mech_chatbot.api import source_file_pins
    database,client,engine,original,page = revision_files
    read_sizes = []
    class TrackedSource(io.BytesIO):
        def getbuffer(self):
            buffer=super().getbuffer(); read_sizes.append(len(buffer)); return buffer
        def read(self,size=-1):
            chunk=super().read(size); read_sizes.append(len(chunk)); return chunk
        def readinto(self,buffer):
            count=super().readinto(buffer); read_sizes.append(count); return count
    real_open=Path.open
    monkeypatch.setattr(Path,'open',lambda path,*args,**kwargs:
                        TrackedSource(b'x'*(3*1024*1024)) if path==original else real_open(path,*args,**kwargs))
    monkeypatch.setattr(source_file_pins,'MAX_UPLOAD_BYTES',1024*1024)
    response=client.get('/api/files/documents/42/original',params={'citation_version':2})
    assert response.status_code==413
    assert sum(read_sizes)<=2*1024*1024


@pytest.mark.parametrize('version',['0','-1','unknown'])
def test_qualified_file_revision_requires_positive_integer(revision_files,version):
    database,client,engine,original,page = revision_files
    assert client.get('/api/files/documents/42/original',params={'citation_version':version}).status_code == 422
    assert engine.opens == 0


@pytest.mark.parametrize('failure,status',[('sql',503),('no_database',503),('deleted',404)])
def test_qualified_file_storage_failures_do_not_expose_paths_or_sql(revision_files,monkeypatch,failure,status):
    database,client,engine,original,page = revision_files
    if failure=='sql':
        with database.begin() as conn:conn.execute(text('DROP TABLE TaiLieu'))
    elif failure=='no_database':
        client.app.state.database_runtime=None
        monkeypatch.setattr(documents,'current_repository_engine',lambda:None)
    else:
        original.unlink()  # Test-owned temporary file only.
    response=client.get('/api/files/documents/42/original',params={'citation_version':2})
    assert response.status_code==status
    assert str(original) not in response.text and 'SELECT' not in response.text


@pytest.mark.parametrize('phase',['expired','copy','after_copy'])
def test_qualified_download_deadline_stops_io_and_closes_the_temporary_file(revision_files,monkeypatch,phase):
    import io
    from pathlib import Path
    from mech_chatbot.api import source_file_pins
    database,client,engine,original,page = revision_files
    clock=[0.0]; reads=[]; spools=[]
    client.app.state.settings_snapshot=SimpleNamespace(RAG_REQUEST_DEADLINE_SECONDS=0 if phase=='expired' else 1)
    monkeypatch.setattr(documents,'monotonic',lambda:clock[0])
    class SlowSource(io.BytesIO):
        def read(self,size=-1):
            chunk=super().read(size); reads.append(len(chunk))
            if phase=='copy' or (phase=='after_copy' and not chunk):clock[0]=2.0
            return chunk
    real_open=Path.open
    monkeypatch.setattr(Path,'open',lambda path,*args,**kwargs:
                        SlowSource(b'x'*(4*1024*1024)) if path==original else real_open(path,*args,**kwargs))
    real_spool=source_file_pins.SpooledTemporaryFile
    def capture_spool(*args,**kwargs):
        spool=real_spool(*args,**kwargs); spools.append(spool); return spool
    monkeypatch.setattr(source_file_pins,'SpooledTemporaryFile',capture_spool)
    response=client.get('/api/files/documents/42/original',params={'citation_version':2})
    assert response.status_code==504 and response.json()=={'detail':'Source request timed out'}
    assert all(spool.closed for spool in spools)
    if phase=='copy':assert sum(reads)<=1024*1024
    if phase=='expired':assert reads==[] and engine.opens==0


@pytest.mark.parametrize('route',['original','pages/1'])
def test_qualified_files_keep_revision_acl_and_path_in_one_snapshot(revision_files,tmp_path,route):
    database,client,engine,original,page = revision_files
    database_path = tmp_path/'race.sqlite'
    reader = create_engine('sqlite:///'+database_path.as_posix())
    writer = create_engine('sqlite:///'+database_path.as_posix())
    with database.connect() as source, reader.connect() as target:
        source.connection.driver_connection.backup(target.connection.driver_connection)
        target.exec_driver_sql('PRAGMA journal_mode=WAL')
    changed = []
    replacement = original.parent/'changed.pdf'; replacement.write_bytes(b'changed private original')
    new_page = page.parent/'changed.png'; new_page.write_bytes(b'changed private page')
    @event.listens_for(reader,'before_cursor_execute')
    def change_source(conn,cursor,statement,params,context,many):
        if 'PhongBanChiaSe' in statement and not changed:
            with writer.begin() as other:
                other.execute(text('UPDATE TaiLieu SET VersionNo=3,FilePath=:path'),{'path':str(replacement)})
                other.execute(text('UPDATE DocumentPages SET ImagePath=:path'),{'path':str(new_page)})
            changed.append(True)
    engine.database = reader
    try:
        response = client.get('/api/files/documents/42/'+route,params={'citation_version':2})
        assert changed and response.status_code==200
        assert response.content == (b'approved original' if route=='original' else b'approved page')
        assert engine.opens==1
        assert client.get('/api/files/documents/42/'+route,params={'citation_version':2}).status_code==404
    finally:
        reader.dispose(); writer.dispose()
