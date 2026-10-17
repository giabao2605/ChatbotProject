from contextlib import nullcontext
import re
from types import SimpleNamespace

import pytest
from sqlalchemy import text

from mech_chatbot.db.repositories import graph_v2_review as review
from .test_graph_v2_repository import connection, SqliteConnection


pytestmark = pytest.mark.unit


def declaration(**changes):
    return {"doc_id": 1, "version_no": 1, "snapshot_id": "snapshot",
            "rows_complete": True, "tree_complete": True, "source_page": 1,
            "source_quote": "reviewed declaration", "expected_rows": 1,
            "source_hash": "a" * 64, **changes}


def occurrence(**changes):
    return {"occurrence_id": "1:row-1:1", "edge_id": 1, "parent_doc_id": 1,
            "parent_version": 1, "row_key": "row-1", "part_key": "part:p",
            "quantity": "2", "unit": "piece", "quantity_basis": "per_parent",
            "child_binding_id": None, "is_leaf": True, "source_page": 1,
            "source_quote": "quantity", **changes}


def context():
    return {"allowed_departments": ["tech"], "allowed_sites": ["factory"],
            "max_security_level": "internal"}


def binding(**changes):
    return {'doc_id':2, 'version_no':1, 'source_doc_id':2, 'source_version':1,
            'source_page':1, 'source_quote':'binding', 'part_key':'part:p', **changes}


class ReviewSqliteConnection(SqliteConnection):
    def execute(self, statement, params):
        sql = re.sub(r' WITH \([^)]*\)', '', str(statement))
        sql = sql.replace('SYSUTCDATETIME()', "datetime('now')")
        if sql.startswith('INSERT dbo.'):
            sql = sql.replace('INSERT dbo.', 'INSERT INTO dbo.')
            output = re.search(r' OUTPUT INSERTED\.(\w+) AS record_id ', sql)
            sql = sql.replace(output.group(0), ' ') + ' RETURNING ' + output[1] + ' AS record_id'
        return super().execute(sql, params)


@pytest.fixture
def review_database(connection, monkeypatch):
    conn = ReviewSqliteConnection(connection.conn)
    conn.conn.execute(text('ALTER TABLE TaiLieu ADD COLUMN KnowledgeApproverUserID INT'))
    conn.conn.execute(text('UPDATE TaiLieu SET KnowledgeApproverUserID=7'))
    conn.conn.execute(text('CREATE TABLE Users (UserID INT,Username TEXT,IsActive INT)'))
    conn.conn.execute(text('CREATE TABLE Roles (RoleID INT,RoleName TEXT)'))
    conn.conn.execute(text('CREATE TABLE UserRoles (UserID INT,RoleID INT)'))
    conn.conn.execute(text('CREATE TABLE AuditLog (UserID INT,Username TEXT,Action TEXT,EntityType TEXT,EntityID INT,Details TEXT)'))
    conn.conn.execute(text("INSERT INTO Users VALUES (7,'real reviewer',1)"))
    conn.conn.execute(text("INSERT INTO Roles VALUES (1,'reviewer')"))
    conn.conn.execute(text('INSERT INTO UserRoles VALUES (7,1)'))
    # SQLite identity equivalent for public proposal tests.
    conn.conn.execute(text('DROP TABLE ReviewedAssemblyBindings'))
    conn.conn.execute(text('CREATE TABLE ReviewedAssemblyBindings (BindingID INTEGER PRIMARY KEY, DocID INT,VersionNo INT,SourceDocID INT,SourceVersion INT,SourcePage INT,SourceQuote TEXT,ProductScope TEXT,Site TEXT,PartKey TEXT,ReviewStatus TEXT,ReviewedBy INT,ReviewedAt TEXT,SourceHash TEXT,SnapshotID TEXT)'))
    install(monkeypatch, conn)
    return conn


@pytest.mark.parametrize('change', ['missing', 'new_snapshot', 'hash', 'legacy'])
def test_public_binding_review_rejects_missing_or_changed_pin(review_database, change):
    conn = review_database
    options = {'access_context':context(), 'product_scope':'product', 'site':'factory', 'note':'checked source'}
    if change == 'missing':
        conn.conn.execute(text('UPDATE StructuredSourceSnapshot SET CurrentSnapshot=0 WHERE DocID=2'))
    proposed = review.propose_graph_v2_record('binding', binding(), actor_id=7, **options)
    if change == 'missing':
        assert proposed == {'ok':False, 'reason':'invalid_provenance'}
        return
    assert proposed['status'] == 'pending'
    listing = review.list_graph_v2_records('binding', **{key:value for key,value in options.items() if key != 'note'})
    assert listing[0]['SourceHash'] == 'a'*64 and listing[0]['SnapshotID'] == 'snapshot'
    if change == 'new_snapshot':
        conn.conn.execute(text('UPDATE StructuredSourceSnapshot SET CurrentSnapshot=0 WHERE DocID=2'))
        conn.conn.execute(text("INSERT INTO StructuredSourceSnapshot VALUES (2,1,'next',:hash,1)"), {'hash':'b'*64})
    elif change == 'hash':
        conn.conn.execute(text('UPDATE StructuredSourceSnapshot SET SourceHash=:hash WHERE DocID=2'), {'hash':'b'*64})
    else:
        conn.conn.execute(text('UPDATE ReviewedAssemblyBindings SET SnapshotID=NULL,SourceHash=NULL'))
    result = review.review_graph_v2_record('binding', proposed['record_id'], 'approve', reviewer_id=7, **options)
    assert result == {'ok':False, 'reason':'invalid_provenance'}
    assert review.list_graph_v2_records('binding', **{key:value for key,value in options.items() if key != 'note'})[0]['ReviewStatus'] == 'pending'


def test_public_binding_proposal_captures_server_pin_and_preserves_audit(review_database):
    options = {'access_context':context(), 'product_scope':'product', 'site':'factory', 'note':'checked source'}
    for extra in ({'source_hash':'b'*64}, {'snapshot_id':'invented'}):
        with pytest.raises(ValueError):
            review.propose_graph_v2_record('binding', binding(**extra), actor_id=7, **options)
    proposed = review.propose_graph_v2_record('binding', binding(), actor_id=7, **options)
    assert review.review_graph_v2_record('binding', proposed['record_id'], 'approve', reviewer_id=7, **options)['status'] == 'approved'
    records = review.list_graph_v2_records('binding', status='approved', **{key:value for key,value in options.items() if key != 'note'})
    assert records[0]['SourceHash'] == 'a'*64 and records[0]['SnapshotID'] == 'snapshot'
    assert records[0]['ReviewedBy'] == 7


def test_review_source_proof_actual_sql_all_three_record_kinds(connection):
    params = review._params(context(), "product", "factory")
    assert review._proof(connection, "declaration", declaration(), params)
    assert review._proof(connection, "occurrence", occurrence(), params)
    binding = {"doc_id": 2, "version_no": 1, "source_doc_id": 1,
               "source_version": 1, "source_page": 1, "source_quote": "binding", "part_key": "part:p",
               "source_hash":"a"*64, "snapshot_id":"snapshot"}
    assert review._proof(connection, "binding", binding, params)
    assert not review._proof(connection, "binding", {**binding, "doc_id": 3}, params)
    assert not review._proof(connection, "declaration", declaration(source_hash="b" * 64), params)
    assert not review._proof(connection, "occurrence", occurrence(row_key="missing"), params)
    connection.conn.execute(text("UPDATE TaiLieu SET IsCurrent=0 WHERE DocID=1"))
    assert not review._proof(connection, "declaration", declaration(), params)


@pytest.mark.parametrize("kind,payload", [
    ("declaration", declaration()), ("occurrence", occurrence()),
    ("binding", {"doc_id": 2, "version_no": 1, "source_doc_id": 1,
                 "source_version": 1, "source_page": 1, "source_quote": "binding", "part_key": "part:p"}),
])
def test_versioned_payload_accepts_only_exact_fields(kind, payload):
    assert review._payload(kind, payload) == payload
    with pytest.raises(ValueError):
        review._payload(kind, {**payload, "review_status": "approved"})


@pytest.mark.parametrize("payload", [
    declaration(doc_id=True), declaration(expected_rows=-1),
    declaration(rows_complete="true"), declaration(source_quote=""),
    declaration(source_quote="x" * 2001), declaration(source_hash="z" * 64),
    declaration(rows_complete=False, tree_complete=True),
    occurrence(quantity="NaN"), occurrence(quantity="-1"), occurrence(quantity="oops"),
    occurrence(quantity_basis="per_process"), occurrence(child_binding_id=1),
    occurrence(part_key="p"), occurrence(unit=""),
])
def test_payload_rejects_untrusted_values(payload):
    with pytest.raises(ValueError):
        review._payload("occurrence" if "occurrence_id" in payload else "declaration", payload)


class Result:
    def __init__(self, row=None, scalar=1):
        self.row, self.scalar = row, scalar
    def mappings(self):
        return self
    def first(self):
        return self.row
    def all(self):
        return (self.row,) if self.row else ()
    def scalar_one(self):
        return self.scalar
    def scalars(self):
        return ("reviewer",)


class RecordingConnection:
    def __init__(self, row=None, proof=True, scalar=1):
        self.row, self.proof, self.scalar = row, proof, scalar
        self.calls = []
        self.dialect = SimpleNamespace(name="mssql")
    def execute(self, statement, params):
        sql = str(statement)
        self.calls.append((sql, params))
        if "SELECT Username,IsActive" in sql:
            return Result({"Username": "reviewer", "IsActive": True})
        if "SELECT KnowledgeApproverUserID" in sql:
            return Result({"KnowledgeApproverUserID": 7})
        if sql.startswith('SELECT SourceHash,SnapshotID'):
            return Result({'SourceHash':'a'*64, 'SnapshotID':'snapshot'})
        if "SELECT r.RoleName" in sql:
            return Result()
        if sql.startswith("SELECT *"):
            return Result(self.row)
        if sql.startswith("INSERT"):
            return Result({"record_id": 5})
        if "SELECT COUNT(*)" in sql:
            return Result(scalar=self.scalar)
        if "SELECT t.DocID" in sql:
            return Result((1,) if self.proof else None)
        if "SELECT r.* FROM dbo." in sql:
            return Result(self.row)
        return Result()


def install(monkeypatch, conn):
    monkeypatch.setattr(review, "_ensure_engine", lambda: None)
    monkeypatch.setattr(review, "engine", SimpleNamespace(begin=lambda: nullcontext(conn),
                                                          connect=lambda: nullcontext(conn)))


def record(status="pending"):
    columns = review._spec("declaration")[2]
    return {**{column: declaration()[key] for key, column in columns.items()},
            "ReviewStatus": status}


def test_proposal_is_pending_only_and_proof_required(monkeypatch):
    conn = RecordingConnection()
    install(monkeypatch, conn)
    result = review.propose_graph_v2_record("declaration", declaration(), actor_id=7,
                access_context=context(), product_scope="product", site="factory", note="reviewed against source")
    assert result["status"] == "pending"
    insert = next(sql for sql, params in conn.calls if sql.startswith("INSERT dbo.ReviewedGraph"))
    assert "'pending'" in insert
    assert "ReviewedBy" not in insert
    assert conn.calls[-1][1]["action"] == "graph_v2_declaration_propose"
    conn.proof = False
    denied = review.propose_graph_v2_record("declaration", declaration(), actor_id=7,
                access_context=context(), product_scope="product", site="factory", note="reviewed against source")
    assert denied["reason"] == "invalid_provenance"


def test_sqlserver_binding_pin_is_locked_through_proposal_and_review(monkeypatch):
    conn = RecordingConnection()
    install(monkeypatch, conn)
    options = {'access_context':context(), 'product_scope':'product', 'site':'factory', 'note':'checked source'}
    assert review.propose_graph_v2_record('binding', binding(), actor_id=7, **options)['status'] == 'pending'
    pin_sql = next(sql for sql, _ in conn.calls if sql.startswith('SELECT SourceHash,SnapshotID'))
    assert 'UPDLOCK,HOLDLOCK' in pin_sql
    insert_sql, insert_params = next((sql, params) for sql, params in conn.calls if sql.startswith('INSERT dbo.ReviewedAssemblyBindings'))
    assert 'SourceHash,SnapshotID' in insert_sql
    assert insert_params['source_hash'] == 'a'*64 and insert_params['snapshot_id'] == 'snapshot'
    conn.row = {**{column:binding()[key] for key,column in review._spec('binding')[2].items()},
                'SourceHash':'a'*64, 'SnapshotID':'snapshot', 'ReviewStatus':'pending'}
    conn.calls = []
    assert review.review_graph_v2_record('binding', 5, 'approve', reviewer_id=7, **options)['status'] == 'approved'
    proof_sql, proof_params = next((sql, params) for sql,params in conn.calls if 'SELECT t.DocID' in sql)
    assert 'StructuredSourceSnapshot pinned WITH (UPDLOCK,HOLDLOCK)' in proof_sql
    assert proof_params['source_hash'] == 'a'*64 and proof_params['snapshot_id'] == 'snapshot'


@pytest.mark.parametrize("record_value,proof,scalar,reason", [
    (None, True, 1, "not_found"), (record("approved"), True, 1, "already_reviewed"),
    (record(), False, 1, "invalid_provenance"), (record(), True, 2, "coverage_unverified"),
])
def test_review_cannot_bypass_proof_or_completeness(monkeypatch, record_value, proof, scalar, reason):
    conn = RecordingConnection(record_value, proof, scalar)
    install(monkeypatch, conn)
    result = review.review_graph_v2_record("declaration", 1, "approve", reviewer_id=7,
                access_context=context(), product_scope="product", site="factory", note="reviewed against source")
    assert result["reason"] == reason
    assert not any(sql.startswith("UPDATE") for sql, params in conn.calls)


@pytest.mark.parametrize("action,status", [("approve", "approved"), ("reject", "rejected")])
def test_explicit_reviewer_recorded_and_locked(monkeypatch, action, status):
    conn = RecordingConnection(record())
    install(monkeypatch, conn)
    result = review.review_graph_v2_record("declaration", 1, action, reviewer_id=7,
                access_context=context(), product_scope="product", site="factory", note="reviewed against source")
    assert result["status"] == status
    assert "UPDLOCK" in conn.calls[0][0]
    update = next(params for sql, params in conn.calls if sql.startswith("UPDATE"))
    assert update["reviewer"] == 7
    assert conn.calls[-1][1]["action"] == "graph_v2_declaration_" + action


def test_listing_is_scoped_and_validates_kind_status_actor(monkeypatch):
    conn = RecordingConnection(record())
    install(monkeypatch, conn)
    assert review.list_graph_v2_records("declaration", access_context=context(), product_scope="product", site="factory")
    assert conn.calls[-1][1]["product_scope"] == "product"
    for args in (("invalid", "pending"), ("declaration", "all")):
        with pytest.raises(ValueError):
            review.list_graph_v2_records(*args, access_context=context(), product_scope="product", site="factory")
    with pytest.raises(ValueError):
        review.propose_graph_v2_record("declaration", declaration(), actor_id=0,
            access_context=context(), product_scope="product", site="factory", note="reviewed against source")
    with pytest.raises(ValueError):
        review.review_graph_v2_record("declaration", 1, "set", reviewer_id=7,
            access_context=context(), product_scope="product", site="factory", note="reviewed against source")


def test_real_database_actor_roles_activity_and_assignment(connection):
    connection.dialect = connection.conn.dialect
    connection.conn.execute(text("ALTER TABLE TaiLieu ADD COLUMN KnowledgeApproverUserID INT"))
    connection.conn.execute(text("UPDATE TaiLieu SET KnowledgeApproverUserID=7"))
    connection.conn.execute(text("CREATE TABLE Users (UserID INT,Username TEXT,IsActive INT)"))
    connection.conn.execute(text("CREATE TABLE Roles (RoleID INT,RoleName TEXT)"))
    connection.conn.execute(text("CREATE TABLE UserRoles (UserID INT,RoleID INT)"))
    connection.conn.execute(text("INSERT INTO Users VALUES (7,'real reviewer',1),(8,'other',1)"))
    connection.conn.execute(text("INSERT INTO Roles VALUES (1,'reviewer')"))
    connection.conn.execute(text("INSERT INTO UserRoles VALUES (7,1),(8,1)"))
    assert review._actor(connection, 7, "declaration", declaration())["username"] == "real reviewer"
    with pytest.raises(PermissionError, match="Assigned"):
        review._actor(connection, 8, "declaration", declaration())
    connection.conn.execute(text("DELETE FROM UserRoles WHERE UserID=7"))
    with pytest.raises(PermissionError, match="Verified"):
        review._actor(connection, 7, "declaration", declaration())
    connection.conn.execute(text("UPDATE Users SET IsActive=0 WHERE UserID=7"))
    with pytest.raises(PermissionError, match="Verified"):
        review._actor(connection, 7, "declaration", declaration())


def test_occurrence_listing_does_not_cross_record_site(connection, monkeypatch):
    install(monkeypatch, connection)
    connection.conn.execute(text("UPDATE ReviewedGraphOccurrences SET Site='other-factory'"))
    records = review.list_graph_v2_records('occurrence', status='approved',
        access_context=context(), product_scope='product', site='factory')
    assert records == ()


@pytest.mark.parametrize("note", [None, "", " ", "x" * 2001])
def test_human_review_note_required(note):
    with pytest.raises(ValueError, match="note"):
        review.review_graph_v2_record("declaration", 1, "approve", reviewer_id=7,
            access_context=context(), product_scope="product", site="factory", note=note)
