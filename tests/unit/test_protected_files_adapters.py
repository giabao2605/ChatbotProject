"""Infrastructure adapter contracts for protected files."""

from __future__ import annotations

from pathlib import Path

import pytest

from mech_chatbot.adapters.protected_files import (
    FilesystemProtectedFileStorage,
    RepositoryProtectedFileAuditSink,
    SqlProtectedFileStore,
)
from mech_chatbot.application.protected_files import ProtectedFileAuditRecord


pytestmark = pytest.mark.unit


class Rows:
    def __init__(self, one=None, many=()):
        self._one = one
        self._many = tuple(many)

    def fetchone(self):
        return self._one

    def fetchall(self):
        return list(self._many)


class Connection:
    def __init__(self, *, document=None, departments=(), page=None, vision=None, owner=None):
        self.document = document
        self.departments = departments
        self.page = page
        self.vision = vision
        self.owner = owner

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def execute(self, statement, _parameters):
        query = str(statement)
        if "FROM dbo.TaiLieu" in query:
            return Rows(one=self.document)
        if "PhongBanChiaSe" in query:
            return Rows(many=self.departments)
        if "VisionSummary" in query:
            return Rows(one=self.vision)
        if "FROM dbo.DocumentPages" in query:
            return Rows(one=self.page)
        return Rows(one=self.owner)


class Engine:
    def __init__(self, connection: Connection):
        self.connection = connection

    def connect(self):
        return self.connection


def test_sql_store_loads_normalized_document_and_page_metadata() -> None:
    connection = Connection(
        document=(
            9,
            "drawing.pdf",
            "data/raw/drawing.pdf",
            "Technical",
            "INTERNAL",
            "HQ",
            "published",
            "approved",
            1,
            "published",
            1,
            "effective",
            None,
            None,
        ),
        departments=[("Technical",), (None,)],
        page=("/worker/page-9.png",),
    )
    store = SqlProtectedFileStore(engine_provider=lambda: Engine(connection))

    record = store.load_document(9)

    assert record is not None
    assert record.doc_id == 9
    assert record.security_level == "internal"
    assert record.departments == ("Technical",)
    assert store.load_page_image(9, 1) == "/worker/page-9.png"


def test_sql_store_chat_ownership_fails_when_database_is_unavailable() -> None:
    store = SqlProtectedFileStore(engine_provider=lambda: None)

    with pytest.raises(RuntimeError, match="database unavailable"):
        store.chat_image_owned_by("owned.png", "alice")


def test_filesystem_storage_allows_only_approved_roots_and_safe_image_names(tmp_path) -> None:
    raw = tmp_path / "data" / "raw"
    processed = tmp_path / "data" / "processed"
    raw.mkdir(parents=True)
    processed.mkdir(parents=True)
    original = raw / "manual.pdf"
    original.write_bytes(b"pdf")
    page = processed / "page.png"
    page.write_bytes(b"png")
    chat = raw / "Chat_Images" / "owned.png"
    chat.parent.mkdir()
    chat.write_bytes(b"png")
    storage = FilesystemProtectedFileStorage(
        project_root=tmp_path,
        raw_root=raw,
        processed_root=processed,
    )

    assert storage.resolve_original("data/raw/manual.pdf") == original.resolve()
    assert storage.resolve_original("data/processed/page.png") is None
    assert storage.resolve_page_image("/untrusted/worker/page.png") == page.resolve()
    assert storage.resolve_chat_image("owned.png") == chat.resolve()
    assert storage.resolve_chat_image("../owned.png") is None
    assert storage.resolve_chat_image(".hidden") is None


def test_audit_adapter_preserves_repository_call_shape() -> None:
    calls = []
    sink = RepositoryProtectedFileAuditSink(
        write_audit_log=lambda **kwargs: calls.append(kwargs)
    )

    sink.record(
        ProtectedFileAuditRecord(
            actor_username="alice",
            action="download_original",
            doc_id=42,
            details={"source": "app-api"},
        )
    )

    assert calls == [
        {
            "username": "alice",
            "action": "download_original",
            "entity_type": "TaiLieu",
            "entity_id": 42,
            "details": {"source": "app-api"},
        }
    ]
