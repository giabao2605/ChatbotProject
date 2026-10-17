"""Offline SQL policy checks; no application database is contacted."""

import json
import re
from contextlib import nullcontext
from types import SimpleNamespace

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.exc import SQLAlchemyError

from mech_chatbot.db.repositories import graph_v2


pytestmark = pytest.mark.unit


def sqlite_sql(sql):
    sql = str(sql).replace("dbo.", "").replace("ISNULL(", "IFNULL(")
    sql = sql.replace("CAST(GETDATE() AS DATE)", "date('now')")
    sql = sql.replace("OPENJSON(", "json_each(")
    sql = sql.replace("'document:'+CAST", "'document:'||CAST")
    sql = sql.replace("'part:'+LOWER", "'part:'||LOWER")
    sql = sql.replace("'page-'+CAST", "'page-'||CAST").replace("+'\u003a'+o.RowKey", "||'\u003a'||o.RowKey")
    for alias in ('bom', 'edge_bom'):
        sql = sql.replace(f"LEFT(NULLIF(LTRIM(RTRIM({alias}.RawRowJson)),N''),2000)",
                          f"substr(NULLIF(LTRIM(RTRIM({alias}.RawRowJson)),N''),1,2000)")
    sql = sql.replace("OUTER APPLY (SELECT TOP (:remaining) * FROM SafeOccurrences rows\n"
                      "             WHERE rows.ParentDocID=d.DocID AND rows.ParentVersion=d.VersionNo\n"
                      "             ORDER BY rows.OccurrenceID) o",
                      "LEFT JOIN SafeOccurrences o ON o.ParentDocID=d.DocID AND o.ParentVersion=d.VersionNo")
    sql = re.sub(r"SELECT DISTINCT TOP \(:remaining\)", "SELECT DISTINCT", sql)
    if "SELECT TOP (:limit)" in sql:
        sql = sql.replace("SELECT TOP (:limit)", "SELECT") + " LIMIT :limit"
    sql = sql.replace('OFFSET :offset ROWS FETCH NEXT :limit ROWS ONLY', 'LIMIT :limit OFFSET :offset')
    return sql.replace("N''", "''")


class SqliteConnection:
    def __init__(self, conn):
        self.conn = conn
        self.calls = []

    def __getattr__(self, name):
        return getattr(self.conn, name)

    def execution_options(self, **options):
        # Publish staged fixture changes before opening an isolated reader.
        self.conn.commit()
        self.conn.execution_options(**options)
        return self

    def execute(self, statement, params):
        self.calls.append((str(statement), params))
        return self.conn.execute(text(sqlite_sql(statement)), params)


@pytest.fixture
def connection():
    db = create_engine("sqlite://")
    raw = db.raw_connection()
    raw.create_function("CHARINDEX", 2, lambda needle, haystack: haystack.find(needle) + 1)
    def json_value(value, path):
        result = json.loads(value) if value else {}
        for key in path[2:].split('.'):
            result = result.get(key) if isinstance(result, dict) else None
        return result
    raw.create_function("JSON_VALUE", 2, json_value)
    raw.close()
    with db.connect() as conn:
        schemas = {
            "TaiLieu": "DocID INT,VersionNo INT,Servable INT,IsCurrent INT,PublicationState TEXT,LifecycleStatus TEXT,ReviewStatus TEXT,EffectiveStatus TEXT,EffectiveDate TEXT,ExpiryDate TEXT,Site TEXT,ThuMuc TEXT,SecurityLevel TEXT",
            "PhongBanChiaSe": "DocID INT,DeptCode TEXT",
            "KnowledgeGraphNode": "NodeID INT,CanonicalKey TEXT",
            "KnowledgeGraphEdge": "EdgeID INT,SourceNodeID INT,TargetNodeID INT,ServingStatus TEXT,RelationType TEXT,SourceDocID INT,SourceVersion INT,SourcePage INT,SourceQuote TEXT",
            "ReviewedGraphOccurrences": "OccurrenceID TEXT,EdgeID INT,ParentDocID INT,ParentVersion INT,ProductScope TEXT,Site TEXT,PartKey TEXT,ChildBindingID INT,ReviewStatus TEXT,ReviewedBy TEXT,ReviewedAt TEXT,SourcePage INT,SourceQuote TEXT,RowKey TEXT,Quantity TEXT,Unit TEXT,QuantityBasis TEXT,IsLeaf INT",
            "ReviewedAssemblyBindings": "BindingID INT,DocID INT,VersionNo INT,SourceDocID INT,SourceVersion INT,SourcePage INT,SourceQuote TEXT,ProductScope TEXT,Site TEXT,PartKey TEXT,ReviewStatus TEXT,ReviewedBy TEXT,ReviewedAt TEXT,SourceHash TEXT,SnapshotID TEXT",
            "ReviewedGraphDeclarations": "DocID INT,VersionNo INT,ProductScope TEXT,ReviewStatus TEXT,ReviewedBy TEXT,ReviewedAt TEXT,SourceHash TEXT,SnapshotID TEXT,SourcePage INT,SourceQuote TEXT,RowsComplete INT,TreeComplete INT,ExpectedRows INT",
            "BangKeVatTu": "DocID INT,TrangSo INT,MaHang TEXT,RawRowJson TEXT",
            "DocumentPages": "DocID INT,PageNo INT,TextExtract TEXT,LocalOCRText TEXT,VisionSummary TEXT",
            "StructuredSourceSnapshot": "DocID INT,VersionNo INT,SnapshotID TEXT,SourceHash TEXT,CurrentSnapshot INT",
            "StructuredNumericFact": "SourceDocID INT,SourceVersion INT,SourceHash TEXT,ReviewStatus TEXT,ReviewedBy INT,ReviewedPayloadJson TEXT,ScopeID TEXT",
            "StructuredEvidenceScope": "ScopeID TEXT,SnapshotID TEXT",
        }
        for table, fields in schemas.items():
            conn.execute(text(f"CREATE TABLE {table} ({fields})"))
        for doc_id, department in ((1, "tech"), (2, "tech"), (3, "finance")):
            conn.execute(text("INSERT INTO TaiLieu VALUES (:id,1,1,1,'published','published','approved','effective',NULL,NULL,'factory',:dept,'internal')"), {"id": doc_id, "dept": department})
            conn.execute(text("INSERT INTO DocumentPages VALUES (:id,1,'reviewed declaration binding',NULL,NULL)"), {"id": doc_id})
            conn.execute(text("INSERT INTO ReviewedGraphDeclarations VALUES (:id,1,'product','approved','reviewer','now',:hash,'snapshot',1,'reviewed declaration',1,1,1)"), {"id": doc_id, "hash": "a" * 64})
            conn.execute(text("INSERT INTO StructuredSourceSnapshot VALUES (:id,1,'snapshot',:hash,1)"), {"id": doc_id, "hash": "a" * 64})
            conn.execute(text('INSERT INTO PhongBanChiaSe VALUES(:id,:dept)'),{'id':doc_id,'dept':department})
        payload = json.dumps({"source_row_id": "row-1", "part": "p", "quantity": 2})
        conn.execute(text("INSERT INTO KnowledgeGraphNode VALUES (1,'document:1'),(2,'part:p')"))
        conn.execute(text("INSERT INTO KnowledgeGraphEdge VALUES (1,1,2,'approved','CONTAINS_PART',1,1,1,:quote)"), {"quote": payload})
        conn.execute(text("INSERT INTO BangKeVatTu VALUES (1,1,'p',:quote)"), {"quote": payload})
        conn.execute(text("INSERT INTO ReviewedGraphOccurrences VALUES ('1:row-1:1',1,1,1,'product','factory','part:p',NULL,'approved','reviewer','now',1,'quantity','row-1','2','piece','per_parent',1)"))
        conn.execute(text("INSERT INTO StructuredEvidenceScope VALUES ('quantity-scope','snapshot')"))
        conn.execute(text("INSERT INTO StructuredNumericFact VALUES (1,1,:hash,'approved',7,:payload,'quantity-scope')"), {
            'hash': 'a'*64, 'payload': json.dumps({'value':'2','unit':'piece','locator':{'page':1,'row_key':'page-1:row-1'}})})
        yield SqliteConnection(conn)
    db.dispose()


def reader(connection, **context):
    return graph_v2.SqlGraphV2Reader(connection, product_scope="product", site="factory",
        access_context={"allowed_departments": ["tech"], "allowed_sites": ["factory"],
                        "max_security_level": "internal", **context})


def test_two_occurrences_keep_own_rows_while_sharing_one_approved_edge(connection):
    from mech_chatbot.db.repositories import graph_v2_review
    row = {'source_row_id':'row-2','part':'p','quantity':3}
    connection.conn.execute(text('INSERT INTO BangKeVatTu VALUES(1,1,:part,:raw)'), {'part':'p','raw':json.dumps(row)})
    connection.conn.execute(text("INSERT INTO ReviewedGraphOccurrences VALUES('1:row-2:1',1,1,1,'product','factory','part:p',NULL,'approved','reviewer','now',1,'quantity','row-2','3','piece','per_parent',1)"))
    connection.conn.execute(text("INSERT INTO StructuredNumericFact VALUES(1,1,:hash,'approved',7,:raw,'quantity-scope')"),
        {'hash':'a'*64,'raw':json.dumps({'value':'3','unit':'piece','locator':{'page':1,'row_key':'page-1:row-2'}})})
    connection.conn.execute(text('UPDATE ReviewedGraphDeclarations SET ExpectedRows=2 WHERE DocID=1'))
    data = reader(connection).assembly(1,500)
    assert len(data['rows']) == 2
    assert {str(r['quantity']) for r in data['rows']} == {'2','3'}
    payload = {'edge_id':1,'parent_doc_id':1,'parent_version':1,'source_page':1,'source_quote':'quantity',
               'part_key':'part:p','row_key':'row-2','quantity':'3','unit':'piece','child_binding_id':None}
    params = graph_v2_review._params({'allowed_departments':['tech'],'allowed_sites':['factory'],'max_security_level':'internal'},'product','factory')
    assert graph_v2_review._proof(connection,'occurrence',payload,params)
    # Sharing the edge must not allow another part's quote to become its evidence.
    connection.conn.execute(text("INSERT INTO BangKeVatTu VALUES(1,1,'other',:raw)"), {'raw':json.dumps({'source_row_id':'row-other','part':'other','quantity':9})})
    connection.conn.execute(text("UPDATE KnowledgeGraphEdge SET SourceQuote=:raw WHERE EdgeID=1"), {'raw':json.dumps({'source_row_id':'row-other','part':'other','quantity':9})})
    assert reader(connection).assembly(1,500)['rows'] == ()
    assert not graph_v2_review._proof(connection,'occurrence',payload,params)


def test_scope_choices_require_current_reviewed_source_and_page_proof(connection, monkeypatch):
    monkeypatch.setattr(graph_v2, '_ensure_engine', lambda: None)
    monkeypatch.setattr(graph_v2, 'engine', SimpleNamespace(connect=lambda: nullcontext(connection)))
    profile = {'allowed_departments':['tech'], 'allowed_sites':['factory'], 'max_security_level':'internal'}
    page = graph_v2.list_approved_graph_scopes(1, profile, site='factory', limit=1)
    assert page == {'items':[{'product_scope':'product','site':'factory'}], 'offset':0, 'limit':1, 'has_more':False}
    connection.conn.execute(text("INSERT INTO ReviewedGraphDeclarations SELECT DocID,VersionNo,'another',ReviewStatus,ReviewedBy,ReviewedAt,SourceHash,SnapshotID,SourcePage,SourceQuote,RowsComplete,TreeComplete,ExpectedRows FROM ReviewedGraphDeclarations WHERE DocID=1"))
    first = graph_v2.list_approved_graph_scopes(1, profile, site='factory', limit=1)
    assert first['items'] == [{'product_scope':'another','site':'factory'}] and first['has_more']
    assert graph_v2.list_approved_graph_scopes(1, profile, site='factory', offset=1, limit=1)['items'] == page['items']
    for changes in ({'allowed_departments':['finance']}, {'allowed_sites':['other']}, {'max_security_level':'public'}):
        hidden = graph_v2.list_approved_graph_scopes(1, {**profile, **changes}, site='factory', limit=1)
        assert hidden == {'items':[], 'offset':0, 'limit':1, 'has_more':False}
    connection.conn.execute(text("UPDATE DocumentPages SET TextExtract='unrelated' WHERE DocID=1"))
    assert graph_v2.list_approved_graph_scopes(1, profile, site='factory')['items'] == []


@pytest.mark.parametrize('statement', [
    'UPDATE TaiLieu SET VersionNo=2', 'UPDATE TaiLieu SET Servable=0',
    "UPDATE TaiLieu SET EffectiveStatus='superseded'", "UPDATE TaiLieu SET ExpiryDate='2000-01-01'",
    'UPDATE StructuredSourceSnapshot SET CurrentSnapshot=0',
    "UPDATE StructuredSourceSnapshot SET SourceHash='changed'",
    "UPDATE ReviewedGraphDeclarations SET ReviewStatus='pending'",
    'UPDATE ReviewedGraphDeclarations SET ReviewedBy=NULL', 'UPDATE ReviewedGraphDeclarations SET ReviewedAt=NULL',
    'UPDATE ReviewedGraphDeclarations SET SourcePage=0',
])
def test_scope_choices_hide_invalidated_sources(connection, monkeypatch, statement):
    monkeypatch.setattr(graph_v2, '_ensure_engine', lambda: None)
    monkeypatch.setattr(graph_v2, 'engine', SimpleNamespace(connect=lambda: nullcontext(connection)))
    connection.conn.execute(text(statement))
    profile = {'allowed_departments':['tech'], 'allowed_sites':['factory'], 'max_security_level':'internal'}
    assert graph_v2.list_approved_graph_scopes(1, profile, site='factory')['items'] == []


@pytest.mark.parametrize('values', [{'doc_id':True}, {'doc_id':0}, {'doc_id':2147483648},
    {'offset':-1}, {'offset':100001}, {'limit':True}, {'limit':0}, {'limit':51},
    {'site':''}, {'site':'   '}, {'site':'s'*101}])
def test_graph_scope_choices_validate_bounds_before_storage(monkeypatch, values):
    monkeypatch.setattr(graph_v2, '_ensure_engine', lambda: pytest.fail('invalid request must not open storage'))
    with pytest.raises(ValueError):
        graph_v2.list_approved_graph_scopes(profile={}, **{'doc_id':1, 'site':'factory', **values})


def test_actual_sql_filters_acl_currentness_and_preserves_provenance(connection):
    value = reader(connection).assembly(1, 500)
    assert value["available"] is True
    assert value["rows"][0]["sources"][0]["source_hash"] == "a" * 64
    assert value["rows"][0]["quantity"] == "2"
    assert reader(connection, allowed_departments=["finance"]).assembly(1, 500) == {"available": False}
    connection.conn.execute(text("UPDATE TaiLieu SET VersionNo=2 WHERE DocID=1"))
    assert reader(connection).assembly(1, 500) == {"available": False}


def test_folder_name_is_not_a_substitute_for_a_document_grant(connection):
    connection.conn.execute(text('DELETE FROM PhongBanChiaSe WHERE DocID=1'))
    assert reader(connection).assembly(1,500)=={'available':False}


def test_shared_department_grant_keeps_site_and_clearance_checks(connection):
    connection.conn.execute(text("UPDATE PhongBanChiaSe SET DeptCode='CHUNG' WHERE DocID=1"))
    assert reader(connection,allowed_departments=['sales']).assembly(1,500)['available']
    assert reader(connection,allowed_departments=['sales'],allowed_sites=['other']).assembly(1,500)=={'available':False}


def test_occurrence_cannot_change_or_use_unreviewed_source_quantity(connection):
    connection.conn.execute(text("UPDATE ReviewedGraphOccurrences SET Quantity='999'"))
    assert reader(connection).assembly(1,500)['rows'] == ()
    connection.conn.execute(text("UPDATE ReviewedGraphOccurrences SET Quantity='2'"))
    connection.conn.execute(text("UPDATE StructuredNumericFact SET ReviewStatus='pending'"))
    assert reader(connection).assembly(1,500)['rows'] == ()


def test_part_endpoint_restricted_binding_hides_even_leaf_existence(connection):
    connection.conn.execute(text("INSERT INTO ReviewedAssemblyBindings VALUES (1,3,1,3,1,1,'binding','product','factory','part:p','approved','reviewer','now',:hash,'snapshot')"), {'hash':'a'*64})
    batch = reader(connection).assembly(1, 500)
    assert batch["available"] is True
    assert batch["rows"] == ()
    assert reader(connection).parents("part:p") == ()
    assert reader(connection, roles=["admin"]).assembly(1, 500)["rows"]


def test_binding_both_source_and_target_permissions_and_revision(connection):
    connection.conn.execute(text("INSERT INTO ReviewedAssemblyBindings VALUES (1,2,1,1,1,1,'binding','product','factory','part:p','approved','reviewer','now',:hash,'snapshot')"), {'hash':'a'*64})
    connection.conn.execute(text("UPDATE ReviewedGraphOccurrences SET ChildBindingID=1,IsLeaf=0"))
    row = reader(connection).assembly(1, 500)["rows"][0]
    assert row["child_doc_id"] == 2
    assert row["child_version"] == 1
    assert len(row["sources"]) == 2
    reverse = reader(connection).parents('document:2', remaining=17)
    assert reverse[0]['source_key'] == 'document:1'
    assert reverse[0]['target_key'] == 'part:p'
    assert len(reverse[0]['sources']) == 2
    assert connection.calls[-1][1]['remaining'] == 17
    connection.conn.execute(text("UPDATE TaiLieu SET ReviewStatus='pending' WHERE DocID=2"))
    assert reader(connection).assembly(1, 500)["rows"] == ()
    assert reader(connection).parents('document:2') == ()


@pytest.mark.parametrize('change', ['new_snapshot', 'changed_hash', 'legacy_unpinned'])
def test_binding_never_adopts_a_later_source_declaration(connection, monkeypatch, change):
    connection.conn.execute(text("INSERT INTO ReviewedAssemblyBindings VALUES (1,2,1,2,1,1,'binding','product','factory','part:p','approved','reviewer','now',:hash,'snapshot')"), {'hash':'a'*64})
    connection.conn.execute(text('UPDATE ReviewedGraphOccurrences SET ChildBindingID=1,IsLeaf=0'))
    monkeypatch.setattr(graph_v2, '_ensure_engine', lambda: None)
    monkeypatch.setattr(graph_v2, 'engine', SimpleNamespace(connect=lambda: nullcontext(connection)))
    profile = {'allowed_departments':['tech'], 'allowed_sites':['factory'], 'max_security_level':'internal'}
    def retrieve(root, direction):
        return graph_v2.retrieve_graph_v2(root, profile, product_scope='product', site='factory', direction=direction)
    assert retrieve('document:1', 'contains')['edges']
    assert retrieve('part:p', 'where_used')['edges']
    if change == 'new_snapshot':
        connection.conn.execute(text('UPDATE StructuredSourceSnapshot SET CurrentSnapshot=0 WHERE DocID=2'))
        connection.conn.execute(text("INSERT INTO StructuredSourceSnapshot VALUES (2,1,'snapshot-b',:hash,1)"), {'hash':'b'*64})
        connection.conn.execute(text("INSERT INTO ReviewedGraphDeclarations SELECT DocID,VersionNo,ProductScope,ReviewStatus,ReviewedBy,ReviewedAt,:hash,'snapshot-b',SourcePage,SourceQuote,RowsComplete,TreeComplete,ExpectedRows FROM ReviewedGraphDeclarations WHERE DocID=2"), {'hash':'b'*64})
    elif change == 'changed_hash':
        connection.conn.execute(text('UPDATE StructuredSourceSnapshot SET SourceHash=:hash WHERE DocID=2'), {'hash':'b'*64})
        connection.conn.execute(text('UPDATE ReviewedGraphDeclarations SET SourceHash=:hash WHERE DocID=2'), {'hash':'b'*64})
    else:
        connection.conn.execute(text('UPDATE ReviewedAssemblyBindings SET SourceHash=NULL,SnapshotID=NULL'))
    forward = retrieve('document:1', 'contains')
    assert forward['edges'] == () and forward['status'] == 'partial'
    assert forward['manifest']['occurrences'] == ()
    assert retrieve('part:p', 'where_used')['edges'] == ()


def test_binding_bom_quote_proof_is_served_and_removal_hides_both_directions(connection):
    connection.conn.execute(text("INSERT INTO ReviewedAssemblyBindings VALUES (1,2,1,2,1,1,'binding in BOM','product','factory','part:p','approved','reviewer','now',:hash,'snapshot')"), {'hash':'a'*64})
    connection.conn.execute(text('UPDATE ReviewedGraphOccurrences SET ChildBindingID=1,IsLeaf=0'))
    connection.conn.execute(text("INSERT INTO BangKeVatTu VALUES (2,1,'p','binding in BOM')"))
    assert reader(connection).assembly(1, 500)['rows']
    assert reader(connection).parents('document:2')
    connection.conn.execute(text('DELETE FROM BangKeVatTu WHERE DocID=2'))
    assert reader(connection).assembly(1, 500)['rows'] == ()
    assert reader(connection).parents('document:2') == ()


def test_sql_proof_missing_or_mismatched_row_excluded(connection):
    connection.conn.execute(text("UPDATE KnowledgeGraphEdge SET SourceQuote='invented'"))
    assert reader(connection).assembly(1, 500)["rows"] == ()
    sql, params = connection.calls[-1]
    assert "TOP (:remaining)" in sql
    assert params["remaining"] == 500
    assert "'product'" not in sql


def test_incoming_direction_is_parameterized_and_returns_reviewed_parents(connection):
    incoming = reader(connection).parents("part:p")
    assert incoming[0]["source_key"] == "document:1"
    assert incoming[0]["relation_type"] == "CONTAINS_PART"
    assert reader(connection).parents("part:p'; DROP TABLE TaiLieu;--") == ()
    assert connection.calls[-1][1]["part_key"].startswith("part:p';")


def test_public_service_uses_real_reader_and_contains_manifest(connection, monkeypatch):
    monkeypatch.setattr(graph_v2, "_ensure_engine", lambda: None)
    monkeypatch.setattr(graph_v2, "engine", SimpleNamespace(connect=lambda: nullcontext(connection)))
    context = {"allowed_departments": ["tech"], "allowed_sites": ["factory"],
               "max_security_level": "internal"}
    result = graph_v2.retrieve_graph_v2("document:1", context, product_scope="product", site="factory")
    assert result["status"] == "complete"
    assert result["manifest"]["occurrences"][0]["quantity_path"] == ("2",)
    incoming = graph_v2.retrieve_graph_v2("part:p", context, product_scope="product",
                                        site="factory", direction="where_used")
    assert incoming["status"] == "partial"
    assert incoming["manifest"] is None
    missing = graph_v2.retrieve_graph_v2("part:missing", context, product_scope="product",
                                       site="factory", direction="where_used")
    assert missing["status"] == "unknown"


@pytest.mark.parametrize("root,kwargs", [
    ("document:1", {"direction": "other"}),
    ("document:1", {"product_scope": ""}),
    ("document:1", {"site": ""}),
    ("part:p", {}), ("document:0", {}),
    ("document:1", {"direction": "where_used"}),
    ("part:", {"direction": "where_used"}),
])
def test_public_input_validation_precedes_database(root, kwargs):
    with pytest.raises(ValueError):
        graph_v2.retrieve_graph_v2(root, {}, **{"product_scope": "product", "site": "factory", **kwargs})


def test_timeout_before_and_after_sql_and_database_failure(connection, monkeypatch):
    monkeypatch.setattr(graph_v2, "_ensure_engine", lambda: None)
    monkeypatch.setattr(graph_v2, "engine", SimpleNamespace(connect=lambda: nullcontext(connection)))
    expired = graph_v2.retrieve_graph_v2("document:1", {}, product_scope="product", site="factory", deadline=0)
    assert expired["reasons"] == ("deadline",)
    from mech_chatbot.rag import graph_retrieval_v2
    ticks = iter((0, 0, 2))
    clock = lambda: next(ticks)
    monkeypatch.setattr(graph_v2, "monotonic", clock)
    monkeypatch.setattr(graph_retrieval_v2, "monotonic", clock)
    expired = graph_v2.retrieve_graph_v2("part:p", {}, product_scope="product", site="factory",
                                       direction="where_used", deadline=1)
    assert "deadline" in expired["reasons"]
    assert expired['edges'] == ()
    assert connection.calls[-1][1]['part_key'] == 'part:p'
    def broken():
        raise SQLAlchemyError("private connection details")
    monkeypatch.setattr(graph_v2, "engine", SimpleNamespace(connect=broken))
    result = graph_v2.retrieve_graph_v2("document:1", {}, product_scope="product", site="factory")
    assert result["reasons"] == ("storage_unavailable",)
    assert "private" not in repr(result)
