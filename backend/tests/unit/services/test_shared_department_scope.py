import sqlite3
from dataclasses import replace

import pytest

from mech_chatbot.application.protected_files import (
    DocumentAccessRecord,
    ProtectedFileActor,
    evaluate_document_access,
)
from mech_chatbot.db.repositories.ui_queries import _document_access_filters


pytestmark = pytest.mark.unit


@pytest.mark.parametrize("departments", [["A"], [], None])
@pytest.mark.parametrize("sites", [["HCM"], []])
def test_sql_scope_matches_shared_department_policy(departments, sites):
    actor = ProtectedFileActor(
        username="viewer", roles=frozenset({"viewer"}), department="A",
        allowed_departments=frozenset(departments or ()),
        allowed_sites=frozenset(sites), max_security_level="internal",
    )
    shared = DocumentAccessRecord(
        doc_id=1, ten_file="shared.pdf", file_path=None, thu_muc="CHUNG",
        security_level="internal", site="HCM", lifecycle_status="published",
        review_status="approved", departments=("CHUNG",),
    )
    records = [
        shared,
        replace(shared, doc_id=2, departments=("A",)),
        replace(shared, doc_id=3, departments=("B",)),
        replace(shared, doc_id=4, site="HN"),
        replace(shared, doc_id=5, security_level="confidential"),
        replace(shared, doc_id=6, lifecycle_status="deleting"),
        replace(shared, doc_id=7, review_status="pending_review"),
        replace(shared, doc_id=8, servable=False),
        replace(shared, doc_id=9, publication_state="draft"),
        replace(shared, doc_id=10, is_current=False),
        replace(shared, doc_id=11, departments=()),
    ]
    expected = {record.doc_id for record in records if evaluate_document_access(
        actor, record, strict_site_filter=True,
    ).allowed}
    assert expected == (({1, 2} if departments else {1}) if sites else set())

    params = {}
    filters = _document_access_filters(
        allowed_departments=departments, allowed_sites=sites,
        max_security_level="internal", global_read_admin=False, params=params,
        strict_site_filter=True,
    )
    with sqlite3.connect(":memory:") as conn:
        conn.execute("ATTACH DATABASE ':memory:' AS dbo")
        conn.execute("""CREATE TABLE documents (
            DocID INTEGER, LifecycleStatus TEXT, ReviewStatus TEXT, IsCurrent INTEGER,
            Servable INTEGER, PublicationState TEXT, EffectiveStatus TEXT,
            SecurityLevel TEXT, Site TEXT)""")
        conn.execute("CREATE TABLE dbo.PhongBanChiaSe (DocID INTEGER, DeptCode TEXT)")
        conn.executemany("INSERT INTO documents VALUES (?,?,?,?,?,?,?,?,?)", [
            (r.doc_id, r.lifecycle_status, r.review_status, r.is_current, r.servable,
             r.publication_state, r.effective_status, r.security_level, r.site)
            for r in records
        ])
        conn.executemany("INSERT INTO dbo.PhongBanChiaSe VALUES (?,?)", [
            (r.doc_id, department) for r in records for department in r.departments
        ])
        # SQLite uses IFNULL for SQL Server's two-argument ISNULL function.
        predicate = " AND ".join(filters).replace("ISNULL(", "IFNULL(")
        actual = {row[0] for row in conn.execute(
            f"SELECT d.DocID FROM documents d WHERE {predicate}", params,
        )}
    assert actual == expected
