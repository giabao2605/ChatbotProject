import pytest


pytestmark = pytest.mark.unit


ui_queries = pytest.importorskip("mech_chatbot.db.repositories.ui_queries")


class _Rows:
    def __init__(self, rows):
        self._rows = rows

    def fetchall(self):
        return self._rows


class _Connection:
    def __init__(self, rows):
        self.rows = rows
        self.sql = ""
        self.params = {}

    def execute(self, statement, params):
        self.sql = str(statement)
        self.params = params
        return _Rows(self.rows)

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False


class _Engine:
    def __init__(self, connection):
        self.connection = connection

    def connect(self):
        return self.connection


def test_bulk_metadata_candidates_include_documents_pending_review(monkeypatch):
    pending_document = (19, "technical_demo_process_v2.md", "Technical", "mechanical")
    connection = _Connection([pending_document])
    monkeypatch.setattr(ui_queries, "engine", _Engine(connection))

    rows = ui_queries.list_docs_for_bulk_meta(
        dept="Technical", domain="mechanical", global_read_admin=True
    )

    assert rows == [pending_document]
    assert "IsCurrent = 1" in connection.sql
    assert "IngestionJobs" in connection.sql
    assert "pending_review" in connection.sql
    assert connection.params == {"dept": "Technical", "domain": "mechanical"}


def test_bulk_metadata_candidates_fail_closed_without_scope(monkeypatch):
    connection = _Connection([])
    monkeypatch.setattr(ui_queries, "engine", _Engine(connection))

    ui_queries.list_docs_for_bulk_meta()

    assert "AND 1 = 0" in connection.sql


def test_bulk_metadata_candidates_apply_department_site_and_clearance(monkeypatch):
    connection = _Connection([])
    monkeypatch.setattr(ui_queries, "engine", _Engine(connection))

    ui_queries.list_docs_for_bulk_meta(
        allowed_departments=["QA_A"], allowed_sites=["A"],
        max_security_level="internal",
    )

    assert "PhongBanChiaSe" in connection.sql
    assert "t.Site IN" in connection.sql
    assert "t.SecurityLevel" in connection.sql
    assert connection.params == {
        "allowed_dept_0": "QA_A", "allowed_site_0": "A",
        "allowed_level_0": "public", "allowed_level_1": "internal",
    }
