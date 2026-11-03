"""Opt-in SQL Server checks on a separately provisioned GD8 test database.

The catalog is synthetic; these checks do not certify provider or business quality.
Keep all test rows for inspection. Never bootstrap or clean an existing database here.
"""
import os
import secrets
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier
from time import monotonic
from types import SimpleNamespace
from urllib.parse import quote_plus

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.exc import DBAPIError

from mech_chatbot.rag.comprehensive import (
    ComprehensiveScanStore, CursorMismatch, SnapshotStale,
)

pytestmark = pytest.mark.integration


@pytest.fixture(scope="module", autouse=True)
def configured_integration_repository_runtime():
    # Override the shared fixture: this test must never load the production .env.
    yield


@pytest.fixture(scope="module")
def sql_engine():
    database = os.environ.get("GD8_SQL_DATABASE", "")
    if not database:
        pytest.skip("Set GD8_SQL_DATABASE to a provisioned isolated GD8 database")
    assert database.startswith("Mech_Chatbot_Test_GD8_") and database.replace("_", "").isalnum()
    server = os.environ.get("GD8_SQL_SERVER", r"localhost\SQLEXPRESS")
    connection = ("DRIVER={ODBC Driver 17 for SQL Server};SERVER=" + server
                  + ";DATABASE=" + database + ";Trusted_Connection=yes;")
    engine = create_engine("mssql+pyodbc:///?odbc_connect=" + quote_plus(connection))
    with engine.connect() as conn:
        assert conn.execute(text("SELECT DB_NAME()")).scalar_one() == database
        versions = set(conn.execute(text("SELECT Version FROM dbo._SchemaVersions")).scalars())
        assert {"V0049", "V0050"} <= versions
    yield engine
    engine.dispose()


@pytest.fixture
def scan(sql_engine):
    catalog = [{"DocID": 1, "VersionNo": 1, "SourceHash": "a" * 64},
               {"DocID": 2, "VersionNo": 1, "SourceHash": "b" * 64}]
    store = ComprehensiveScanStore(sql_engine, cursor_secret=secrets.token_hex(32),
                                   catalog_loader=lambda **_: catalog)
    auth = dict(user_id=7, question="GD8 isolated SQL check",
                access_scope={"user_id": 7, "username": "gd8", "allowed_sites": ["HN"]})
    _, binding = store.start(username="gd8", **auth)
    return store, binding, auth, catalog


def update(ordinal=0, *, status="checked", answer="one"):
    return {"SourceOrdinal": ordinal, "status": status, "page_offset": 1,
            "results": [{"answer": answer}]}


def test_sql_revisions_resume_replay_and_complete(scan):
    store, initial, auth, _ = scan
    first = store.save_batch(initial, [update()], **auth)
    second = store.save_batch(first, [update(1, answer="two")], **auth)
    assert (first.next_offset, second.next_offset) == (1, 2)
    assert store.save_batch(initial, [update(answer="duplicate")], **auth) == second
    assert store.load_batch(second, **auth).complete
    assert store.get_results(second, result_revision=1, **auth).results == ({"answer": "one"},)
    assert store.get_results(second, result_revision=2, **auth).results == ({"answer": "two"},)
    with store.engine.connect() as conn:
        assert conn.execute(text("SELECT COUNT(*) FROM dbo.RagComprehensiveBatch WHERE ScanID=:id"),
                            {"id": initial.scan_id}).scalar_one() == 2


def test_sql_concurrent_same_revision_commits_once(scan):
    store, initial, auth, _ = scan
    barrier = Barrier(2)
    def save(answer):
        barrier.wait(timeout=5)
        return store.save_batch(initial, [update(answer=answer)], **auth)
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(save, ["first", "second"]))
    assert results[0] == results[1] and results[0].next_offset == 1
    assert len(store.get_results(results[0], **auth).results) == 1


def test_sql_changed_source_persists_stale(scan):
    store, initial, auth, catalog = scan
    saved = store.save_batch(initial, [update()], **auth)
    catalog[0] = {**catalog[0], "SourceHash": "c" * 64}
    with pytest.raises(SnapshotStale):
        store.get_results(saved, **auth)
    with store.engine.connect() as conn:
        assert conn.execute(text("SELECT Status FROM dbo.RagComprehensiveScan WHERE ScanID=:id"),
                            {"id": initial.scan_id}).scalar_one() == "stale"


def test_sql_revocation_blocks_saved_results(scan):
    store, initial, auth, _ = scan
    saved = store.save_batch(initial, [update()], **auth)
    with pytest.raises(CursorMismatch):
        store.get_results(saved, reauthorize=lambda: False, **auth)


def test_sql_invalid_batch_rolls_back_revision_and_results(scan):
    store, initial, auth, _ = scan
    with pytest.raises(ValueError):
        store.save_batch(initial, [update(), update(99)], **auth)
    batch = store.load_batch(initial, **auth)
    assert batch.binding == initial and batch.counts["pending"] == 2
    assert store.get_results(initial, **auth).results == ()


def test_sql_locked_row_respects_request_deadline(scan, monkeypatch):
    from mech_chatbot.db.scan_connection import scan_connection
    from mech_chatbot.rag import execution
    store, initial, _, _ = scan
    statement = text("UPDATE dbo.RagComprehensiveScan SET NextOffset=NextOffset WHERE ScanID=:id")
    with store.engine.connect() as blocker:
        transaction = blocker.begin()
        try:
            blocker.execute(statement, {"id": initial.scan_id})
            deadline = monotonic() + 2
            monkeypatch.setattr(execution, "current_request_budget",
                                lambda: SimpleNamespace(deadline_monotonic=deadline))
            with pytest.raises(DBAPIError) as error:
                with scan_connection(store.engine) as conn:
                    conn.execute(statement, {"id": initial.scan_id})
            assert any(code in str(error.value.orig) for code in ("HYT00", "1222"))
        finally:
            transaction.rollback()
