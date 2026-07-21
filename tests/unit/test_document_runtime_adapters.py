"""Production adapter contracts for Phase 2 document operations."""

from __future__ import annotations

from contextlib import nullcontext

import pytest

from mech_chatbot.adapters.document_runtime import (
    RepositoryPublicationPort,
    SqlAppSupportQueries,
    SqlDocumentLookup,
)
from mech_chatbot.application.document_review import PublicationCommand
from mech_chatbot.application.document_upload import DocumentActor


pytestmark = pytest.mark.unit


class Result:
    def __init__(self, row=None, rows=()):
        self.row = row
        self.rows = rows

    def fetchone(self):
        return self.row

    def fetchall(self):
        return self.rows


class Connection:
    def __init__(self, result):
        self.result = result

    def execute(self, _statement, _params=None):
        return self.result


class Engine:
    def __init__(self, result):
        self.result = result

    def connect(self):
        return nullcontext(Connection(self.result))


class Published:
    ok = True
    state = "published"
    error = None

    def __bool__(self):
        return True

    def to_dict(self):
        return {"ok": True, "state": "published", "doc_id": 42}


def _actor():
    return DocumentActor(7, "alice", ("reviewer",), ("CoKhi",))


def test_sql_adapters_resolve_engine_provider_at_call_time() -> None:
    holder = {"engine": Engine(Result(row=(42,)))}
    lookup = SqlDocumentLookup(engine=lambda: holder["engine"])
    support = SqlAppSupportQueries(engine=lambda: holder["engine"])

    assert lookup.latest_doc_id_for_job(8) == 42
    assert support.database_ready() is True

    holder["engine"] = None
    assert lookup.latest_doc_id_for_job(8) is None
    assert support.database_ready() is False


def test_publication_adapter_resolves_job_document_and_marks_only_published() -> None:
    calls = []
    port = RepositoryPublicationPort(
        publish_document=lambda doc_id, **kwargs: calls.append(
            ("publish", doc_id, kwargs)
        )
        or Published(),
        mark_job_published=lambda job_id: calls.append(("mark", job_id)),
        resolve_latest_doc_id_for_job=lambda job_id: 42 if job_id == 8 else None,
    )

    outcome = port.publish(PublicationCommand(8, None, "standalone"), _actor())

    assert outcome.state == "published"
    assert calls[0][0:2] == ("publish", 42)
    assert calls[0][2]["reviewer"] == "alice"
    assert calls[1] == ("mark", 8)
