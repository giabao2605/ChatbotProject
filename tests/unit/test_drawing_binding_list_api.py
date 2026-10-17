"""Document-scoped binding review through the repository and HTTP seams."""
from fastapi import FastAPI
from fastapi.testclient import TestClient
import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.exc import OperationalError

from mech_chatbot.api.dependencies import current_profile
from mech_chatbot.api.routers import rag_evidence
from mech_chatbot.db.repositories import graph_v2_review as review
from . import test_graph_v2_repository as graph_fixture
from .test_graph_v2_review import context, install

pytestmark = pytest.mark.unit


@pytest.fixture
def connection(monkeypatch):
    # HTTP sync handlers run in a worker thread; retain the shared isolated fixture connection.
    monkeypatch.setattr(graph_fixture, 'create_engine',
        lambda url: create_engine(url, connect_args={'check_same_thread':False}))
    yield from graph_fixture.connection.__wrapped__()


@pytest.fixture
def bindings(connection, monkeypatch):
    install(monkeypatch, connection)
    for index in range(1, 62):
        connection.conn.execute(text("""INSERT INTO ReviewedAssemblyBindings
            VALUES (:id,2,1,1,1,1,'binding','product','factory','part:p',
                    'pending',NULL,NULL,:hash,'snapshot')"""), {'id':index, 'hash':'a'*64})
    connection.conn.execute(text("""INSERT INTO ReviewedAssemblyBindings
        VALUES (100,1,1,2,1,1,'binding','product','factory','part:p',
                'pending',NULL,NULL,:hash,'snapshot')"""), {'hash':'a'*64})
    return connection


def api(roles=('reviewer',)):
    app = FastAPI()
    app.include_router(rag_evidence.router)
    app.dependency_overrides[current_profile] = lambda: {
        **context(), 'roles':roles, 'user_id':7, 'username':'reviewer'}
    return TestClient(app)


def listing(**changes):
    return review.list_graph_v2_records('binding', access_context=context(),
        product_scope='product', site='factory', **{'source_doc_id':1, **changes})


def test_repository_paginates_selected_source_without_changing_tuple_contract(bindings):
    first = listing(limit=50)
    second = listing(limit=50, offset=50)
    assert isinstance(first, tuple) and [row['BindingID'] for row in first] == list(range(1,51))
    assert [row['BindingID'] for row in second] == list(range(51,62))
    assert [row['BindingID'] for row in listing(source_doc_id=2)] == [100]
    assert len(review.list_graph_v2_records('binding', access_context=context(),
        product_scope='product', site='factory')) == 62


@pytest.mark.parametrize('doc_id', [1,2])
@pytest.mark.parametrize('change', ["ThuMuc='finance'", "Site='other'", 'VersionNo=2', 'IsCurrent=0'])
def test_repository_hides_binding_when_either_endpoint_is_ineligible(bindings, doc_id, change):
    bindings.conn.execute(text(f'UPDATE TaiLieu SET {change} WHERE DocID=:id'), {'id':doc_id})
    if change == "ThuMuc='finance'":
        bindings.conn.execute(text('DELETE FROM PhongBanChiaSe WHERE DocID=:id'), {'id':doc_id})
    assert listing() == ()


@pytest.mark.parametrize('values', [{'source_doc_id':True}, {'source_doc_id':0},
    {'source_doc_id':2147483648}, {'source_doc_id':'1'}, {'offset':True}, {'offset':-1},
    {'offset':100001}, {'offset':'1'}])
def test_repository_rejects_bounds_before_opening_storage(monkeypatch, values):
    monkeypatch.setattr(review, '_ensure_engine', lambda: pytest.fail('must not open storage'))
    with pytest.raises(ValueError):
        listing(**values)


def test_repository_source_filter_is_only_valid_for_bindings(monkeypatch):
    monkeypatch.setattr(review, '_ensure_engine', lambda: pytest.fail('must not open storage'))
    with pytest.raises(ValueError):
        review.list_graph_v2_records('occurrence', source_doc_id=1, access_context=context(),
            product_scope='product', site='factory')


def test_http_paginates_using_real_repository_and_hides_restricted_source(bindings):
    path = '/api/rag-evidence/documents/1/drawing-bindings?product_scope=product&site=factory'
    first = api().get(path).json()
    assert len(first['items']) == 50 and first['has_more'] and first['offset'] == 0 and first['limit'] == 50
    second = api().get(path+'&offset=50').json()
    assert [row['BindingID'] for row in second['items']] == list(range(51,62))
    assert second['has_more'] is False
    bindings.conn.execute(text("UPDATE TaiLieu SET Site='other' WHERE DocID=1"))
    assert api().get(path).json() == {'items':[], 'offset':0, 'limit':50, 'has_more':False}


@pytest.mark.parametrize('suffix', ['', '?product_scope=product', '?product_scope=+++&site=factory',
    '?product_scope=product&site=+++', '?product_scope=product&site=factory&limit=101',
    '?product_scope=product&site=factory&offset=100001', '?product_scope=product&site=factory&offset=-1',
    '?product_scope=product&site=factory&status=unknown'])
def test_http_rejects_invalid_query_before_storage(monkeypatch, suffix):
    monkeypatch.setattr(review, '_ensure_engine', lambda: pytest.fail('must not open storage'))
    assert api().get('/api/rag-evidence/documents/1/drawing-bindings'+suffix).status_code == 422


def test_http_requires_reviewer_and_redacts_storage_errors(monkeypatch):
    path = '/api/rag-evidence/documents/1/drawing-bindings?product_scope=product&site=factory'
    assert api(('viewer',)).get(path).status_code == 403
    assert api().get(path.replace('/1/', '/0/')).status_code == 422
    assert api().get(path.replace('/1/', '/2147483648/')).status_code == 422
    def failed(*args, **kwargs):
        raise OperationalError('restricted table', {}, Exception('secret'))
    monkeypatch.setattr(rag_evidence.graph_service, 'list_graph_v2_records', failed)
    response = api().get(path)
    assert response.status_code == 503
    assert 'secret' not in response.text and 'restricted table' not in response.text
