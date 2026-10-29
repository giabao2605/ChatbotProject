"""Compatibility facade for protected-file access.

Phase 2 moved policy into ``application.protected_files`` and SQL/filesystem
details into ``adapters.protected_files``.  These functions remain for older
evaluation callers until the Phase 6 compatibility-removal gate.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any, Iterable
from mech_chatbot.db.evidence_snapshot import EvidenceSnapshotLost, evidence_snapshot_active
from sqlalchemy.exc import SQLAlchemyError

from mech_chatbot.adapters.protected_files import (
    FilesystemProtectedFileStorage,
    SqlProtectedFileStore,
)
from mech_chatbot.application.protected_files import (
    AccessDecision,
    DocumentAccessRecord,
    LEVEL_ORDER,
    ProtectedFileActor,
    evaluate_document_access as _evaluate_document_access,
    normalize_security_level,
)


# An explicit compatibility injection point for old isolated tests. Production
# supplies its process-owned engine directly to the page metadata helpers.
_ENGINE_UNSET = object()
engine: Any = _ENGINE_UNSET


def project_root() -> Path:
    return Path(__file__).resolve().parents[3]


def data_raw_root() -> Path:
    return project_root() / "data" / "raw"


def data_processed_root() -> Path:
    return project_root() / "data" / "processed"


def _engine_provider() -> Any:
    if engine is not _ENGINE_UNSET:
        return engine
    from mech_chatbot.composition.app_runtime import production_engine

    return production_engine()


def _store(db_engine: Any = _ENGINE_UNSET) -> SqlProtectedFileStore:
    return SqlProtectedFileStore(
        engine_provider=_engine_provider if db_engine is _ENGINE_UNSET else lambda: db_engine() if callable(db_engine) else db_engine
    )


def _storage() -> FilesystemProtectedFileStorage:
    raw_root = data_raw_root()
    return FilesystemProtectedFileStorage(
        project_root=project_root(),
        raw_root=raw_root,
        processed_root=data_processed_root(),
        additional_processed_roots=(
            raw_root.parent.parent / "src" / "data" / "processed",
            raw_root.resolve().parent.parent / "src" / "data" / "processed",
        ),
    )


def strict_site_filter_enabled(configured: bool = True) -> bool:
    """Return the composition-selected strict site policy.

    Environment parsing is deliberately excluded from this compatibility
    facade.  Process startup supplies the parsed value; the default preserves
    the historical fail-closed behavior.
    """

    return bool(configured)


def _strict_site_enabled() -> bool:
    return strict_site_filter_enabled()


def _actor_from_profile(profile: dict[str, Any]) -> ProtectedFileActor:
    return ProtectedFileActor.from_profile(profile)


def load_document_access_record(doc_id: int) -> DocumentAccessRecord | None:
    return _store().load_document(doc_id)


def evaluate_document_access(
    profile: dict[str, Any],
    record: DocumentAccessRecord | None,
) -> AccessDecision:
    return _evaluate_document_access(
        _actor_from_profile(profile),
        record,
        strict_site_filter=_strict_site_enabled(),
    )


def can_access_document(
    profile: dict[str, Any],
    doc_id: int,
) -> tuple[AccessDecision, DocumentAccessRecord | None]:
    record = load_document_access_record(doc_id)
    return evaluate_document_access(profile, record), record


def resolve_under_root(
    raw_path: str | os.PathLike[str] | None,
    allowed_roots: Iterable[Path],
) -> Path | None:
    return _storage().resolve_under_root(raw_path, allowed_roots)


def page_image_path(doc_id: int, page_no: int, *, db_engine: Any = _ENGINE_UNSET) -> Path | None:
    stored_path = _store(db_engine).load_page_image(doc_id, page_no)
    return _storage().resolve_page_image(stored_path)


def page_has_vision(doc_id: int, page_no: int, *, db_engine: Any = _ENGINE_UNSET) -> bool:
    try:
        metadata = _store(db_engine).load_page_vision(doc_id, page_no)
        return bool(metadata and _storage().resolve_page_image(metadata[1]))
    except (EvidenceSnapshotLost, TimeoutError):
        raise
    except Exception as error:
        if isinstance(error, SQLAlchemyError) and evidence_snapshot_active():
            raise
        return False


def page_has_preview(doc_id: int, page_no: int, *, db_engine: Any = _ENGINE_UNSET) -> bool:
    try:
        return page_image_path(doc_id, page_no, db_engine=db_engine) is not None
    except Exception:
        return False


def original_file_path(record: DocumentAccessRecord) -> Path | None:
    return _storage().resolve_original(record.file_path)


def chat_image_path(image_id: str) -> Path | None:
    return _storage().resolve_chat_image(image_id)


__all__ = [
    "AccessDecision",
    "DocumentAccessRecord",
    "LEVEL_ORDER",
    "can_access_document",
    "chat_image_path",
    "data_processed_root",
    "data_raw_root",
    "evaluate_document_access",
    "load_document_access_record",
    "normalize_security_level",
    "original_file_path",
    "page_has_preview",
    "page_has_vision",
    "page_image_path",
    "project_root",
    "resolve_under_root",
    "strict_site_filter_enabled",
]
