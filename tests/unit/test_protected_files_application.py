"""Application contracts for protected browser-file delivery."""

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


pytestmark = pytest.mark.unit


def _actor(**overrides) -> ProtectedFileActor:
    values = {
        "username": "alice",
        "roles": frozenset({"viewer"}),
        "department": "Technical",
        "allowed_departments": frozenset({"Technical"}),
        "allowed_sites": frozenset({"HQ"}),
        "max_security_level": "internal",
    }
    values.update(overrides)
    return ProtectedFileActor(**values)


def _record(**overrides) -> DocumentAccessRecord:
    values = {
        "doc_id": 42,
        "ten_file": "manual.pdf",
        "file_path": "data/raw/Technical/manual.pdf",
        "thu_muc": "Technical",
        "security_level": "internal",
        "site": "HQ",
        "lifecycle_status": "published",
        "review_status": "approved",
        "departments": ("Technical",),
    }
    values.update(overrides)
    return DocumentAccessRecord(**values)


@dataclass
class Store:
    record: DocumentAccessRecord | None = None
    page_path: str | None = None
    chat_owned: bool = False
    available: bool = True

    def load_document(self, doc_id: int) -> DocumentAccessRecord | None:
        return self.record

    def load_page_image(self, doc_id: int, page_no: int) -> str | None:
        return self.page_path

    def chat_image_owned_by(self, image_id: str, username: str) -> bool:
        if not self.available:
            raise RuntimeError("database unavailable")
        return self.chat_owned


@dataclass
class Storage:
    original: Path | None = None
    page: Path | None = None
    chat: Path | None = None

    def resolve_original(self, stored_path: str | None) -> Path | None:
        return self.original

    def resolve_page_image(self, stored_path: str | None) -> Path | None:
        return self.page

    def resolve_chat_image(self, image_id: str) -> Path | None:
        return self.chat


class Audit:
    def __init__(self) -> None:
        self.records = []

    def record(self, record) -> None:
        self.records.append(record)


def _resolver(store: Store, storage: Storage, audit: Audit | None = None):
    return ProtectedFileResolver(
        store=store,
        storage=storage,
        audit=audit or Audit(),
        strict_site_filter=True,
    )


def test_resolve_original_authorizes_then_returns_named_file_and_audits(tmp_path) -> None:
    source = tmp_path / "manual.pdf"
    audit = Audit()
    resolver = _resolver(Store(record=_record()), Storage(original=source), audit)

    result = resolver.resolve(ProtectedFileReference.original(42), _actor())

    assert result.path == source
    assert result.filename == "manual.pdf"
    assert result.placeholder is False
    assert audit.records[0].action == "download_original"
    assert audit.records[0].actor_username == "alice"
    assert audit.records[0].doc_id == 42


def test_denied_document_never_resolves_storage() -> None:
    class ForbiddenStorage(Storage):
        def resolve_original(self, stored_path: str | None) -> Path | None:
            pytest.fail("denied document must not touch storage")

    resolver = _resolver(
        Store(record=_record(security_level="confidential")),
        ForbiddenStorage(),
    )

    with pytest.raises(ProtectedFileError) as raised:
        resolver.resolve(ProtectedFileReference.original(42), _actor())

    assert raised.value.code == "unauthorized"
    assert raised.value.detail == "security_denied"


def test_missing_page_image_returns_placeholder_after_confidential_audit() -> None:
    audit = Audit()
    resolver = _resolver(
        Store(record=_record(security_level="confidential"), page_path=None),
        Storage(page=None),
        audit,
    )

    result = resolver.resolve(
        ProtectedFileReference.page(42, 3),
        _actor(max_security_level="confidential"),
    )

    assert result.path is None
    assert result.placeholder is True
    assert audit.records[0].action == "view_citation_page"
    assert audit.records[0].details["page_no"] == 3


@pytest.mark.parametrize("image_id", ["../secret.png", "nested/secret.png", ".hidden"])
def test_chat_image_identifier_fails_closed_before_ownership_lookup(image_id) -> None:
    class UntouchedStore(Store):
        def chat_image_owned_by(self, image_id: str, username: str) -> bool:
            pytest.fail("invalid identifiers must not query ownership")

    resolver = _resolver(UntouchedStore(), Storage(chat=None))

    with pytest.raises(ProtectedFileError) as raised:
        resolver.resolve(ProtectedFileReference.chat_image(image_id), _actor())

    assert raised.value.code == "not_found"
    assert raised.value.detail == "Image not found"


def test_chat_image_requires_owner_and_preserves_database_unavailable() -> None:
    path = Path("owned.png")

    forbidden = _resolver(Store(chat_owned=False), Storage(chat=path))
    with pytest.raises(ProtectedFileError) as denied:
        forbidden.resolve(ProtectedFileReference.chat_image("owned.png"), _actor())
    assert denied.value.code == "unauthorized"
    assert denied.value.detail == "Image is not visible to this user"

    unavailable = _resolver(
        Store(chat_owned=False, available=False),
        Storage(chat=path),
    )
    with pytest.raises(ProtectedFileError) as failed:
        unavailable.resolve(ProtectedFileReference.chat_image("owned.png"), _actor())
    assert failed.value.code == "storage_failed"
    assert failed.value.detail == "Database is not ready"
