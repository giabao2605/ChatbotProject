"""Regression contracts for the public-review queue and source preview fixes."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import pytest

from mech_chatbot.application.protected_files import (
    DocumentAccessRecord,
    ProtectedFileActor,
    ProtectedFileError,
    ProtectedFileReference,
    ProtectedFileResolver,
)
from mech_chatbot.db.repositories import ui_queries


pytestmark = pytest.mark.unit


class _Result:
    def __init__(self, *, rows=(), one=None):
        self._rows = list(rows)
        self._one = one

    def fetchall(self):
        return list(self._rows)

    def fetchone(self):
        return self._one


class _Connection:
    def __init__(self, *, rows=()):
        self.rows = list(rows)
        self.calls: list[tuple[str, dict]] = []

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def execute(self, statement, params=None):
        self.calls.append((str(statement), dict(params or {})))
        return _Result(rows=self.rows)


class _Engine:
    def __init__(self, connection):
        self.connection = connection

    def connect(self):
        return self.connection


@pytest.mark.parametrize("query_fn", [ui_queries.list_pending_review_docs, ui_queries.list_bulk_action_jobs])
def test_review_queue_hides_published_servable_match_but_keeps_newer_pending_version(
    monkeypatch, query_fn
):
    connection = _Connection()
    monkeypatch.setattr(ui_queries, "engine", _Engine(connection))

    query_fn()

    sql = connection.calls[-1][0]
    normalized = " ".join(sql.split())
    assert "NOT EXISTS" in normalized
    assert "published.ReviewStatus = 'approved'" in normalized
    assert "published.PublicationState = 'published'" in normalized
    assert "published.Servable = 1" in normalized
    assert "published.DocID >= pending.DocID" in normalized


class _Store:
    def __init__(self, record):
        self.record = record

    def load_document(self, _doc_id):
        return self.record

    def load_page_image(self, _doc_id, _page_no):
        return None

    def chat_image_owned_by(self, _image_id, _username):
        return False


@dataclass
class _Storage:
    original: Path | None = None

    def resolve_original(self, _stored_path):
        return self.original

    def resolve_page_image(self, _stored_path):
        return None

    def resolve_chat_image(self, _image_id):
        return None


class _Audit:
    def __init__(self):
        self.records = []

    def record(self, record):
        self.records.append(record)


def _pending_record(**overrides):
    values = {
        "doc_id": 135,
        "ten_file": "qa-text.pdf",
        "file_path": "data/raw/IT/qa-text.pdf",
        "thu_muc": "IT",
        "security_level": "internal",
        "site": "HQ",
        "lifecycle_status": "draft",
        "review_status": "pending_review",
        "departments": ("IT",),
        "servable": False,
        "publication_state": "draft",
        "knowledge_approver_user_id": 81,
    }
    values.update(overrides)
    return DocumentAccessRecord(**values)


def _actor(**overrides):
    values = {
        "user_id": 81,
        "username": "it-approver",
        "roles": frozenset({"knowledge_approver"}),
        "department": "IT",
        "allowed_departments": frozenset({"IT"}),
        "allowed_sites": frozenset({"HQ"}),
        "max_security_level": "internal",
    }
    values.update(overrides)
    return ProtectedFileActor(**values)


def _resolver(record, source, audit):
    return ProtectedFileResolver(
        store=_Store(record),
        storage=_Storage(original=source),
        audit=audit,
        strict_site_filter=True,
    )


def test_assigned_knowledge_approver_can_preview_pending_original_with_audit(tmp_path):
    source = tmp_path / "qa-text.pdf"
    audit = _Audit()
    resolver = _resolver(_pending_record(), source, audit)

    result = resolver.resolve(
        ProtectedFileReference.review_original(135),
        _actor(),
    )

    assert result.path == source
    assert result.filename == "qa-text.pdf"
    assert audit.records[0].action == "review_preview_original"
    assert audit.records[0].details["policy_decision"] == "assigned_knowledge_approver"


def test_pending_original_route_remains_denied_and_preview_rejects_viewer_or_wrong_department(
    tmp_path,
):
    source = tmp_path / "qa-text.pdf"
    resolver = _resolver(_pending_record(), source, _Audit())

    with pytest.raises(ProtectedFileError) as original_error:
        resolver.resolve(ProtectedFileReference.original(135), _actor())
    assert original_error.value.detail == "document_not_servable"

    with pytest.raises(ProtectedFileError) as viewer_error:
        resolver.resolve(
            ProtectedFileReference.review_original(135),
            _actor(roles=frozenset({"viewer"})),
        )
    assert viewer_error.value.detail == "review_preview_role_denied"

    with pytest.raises(ProtectedFileError) as department_error:
        resolver.resolve(
            ProtectedFileReference.review_original(135),
            _actor(
                department="Finance",
                allowed_departments=frozenset({"Finance"}),
            ),
        )
    assert department_error.value.detail == "department_denied"


def test_review_preview_fails_closed_without_effective_approver(tmp_path):
    resolver = _resolver(
        _pending_record(knowledge_approver_user_id=None),
        tmp_path / "qa-text.pdf",
        _Audit(),
    )

    with pytest.raises(ProtectedFileError) as raised:
        resolver.resolve(ProtectedFileReference.review_original(135), _actor())

    assert raised.value.detail == "review_preview_assignment_missing"
