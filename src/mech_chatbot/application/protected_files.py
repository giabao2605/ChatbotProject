"""Application-owned protected file authorization and resolution.

The resolver is the single public seam used by browser file delivery.  It
coordinates serving-state RBAC, approved storage roots, chat-image ownership,
and audit intent without depending on FastAPI, SQLAlchemy, or global engines.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from pathlib import Path
from types import MappingProxyType
from typing import Any, Literal, Mapping, Protocol

from mech_chatbot.config.constants import SHARE_ALL_DEPARTMENT
from mech_chatbot.domain.serving_state import is_currently_servable


LEVEL_ORDER = {"public": 0, "internal": 1, "confidential": 2}
ProtectedFileKind = Literal["document_page", "document_original", "chat_image"]


@dataclass(frozen=True, slots=True)
class DocumentAccessRecord:
    doc_id: int
    ten_file: str | None
    file_path: str | None
    thu_muc: str | None
    security_level: str
    site: str | None
    lifecycle_status: str | None
    review_status: str | None
    departments: tuple[str, ...]
    servable: bool = True
    publication_state: str = "published"
    is_current: bool = True
    effective_status: str | None = "effective"
    effective_date: date | str | None = None
    expiry_date: date | str | None = None


@dataclass(frozen=True, slots=True)
class AccessDecision:
    allowed: bool
    reason: str
    security_level: str = "confidential"


@dataclass(frozen=True, slots=True)
class ProtectedFileActor:
    username: str
    roles: frozenset[str]
    department: str | None
    allowed_departments: frozenset[str]
    allowed_sites: frozenset[str]
    max_security_level: str

    @classmethod
    def from_profile(cls, profile: Mapping[str, Any]) -> "ProtectedFileActor":
        return cls(
            username=str(profile.get("username") or ""),
            roles=frozenset(
                str(role).strip().lower()
                for role in (profile.get("roles") or ())
                if str(role).strip()
            ),
            department=(
                str(profile.get("department")).strip()
                if profile.get("department")
                else None
            ),
            allowed_departments=frozenset(
                str(department).strip()
                for department in (profile.get("allowed_departments") or ())
                if str(department).strip()
            ),
            allowed_sites=frozenset(
                str(site).strip()
                for site in (profile.get("allowed_sites") or ())
                if str(site).strip()
            ),
            max_security_level=normalize_security_level(
                profile.get("max_security_level") or "public"
            ),
        )


@dataclass(frozen=True, slots=True)
class ProtectedFileReference:
    kind: ProtectedFileKind
    doc_id: int | None = None
    page_no: int | None = None
    image_id: str | None = None

    @classmethod
    def original(cls, doc_id: int) -> "ProtectedFileReference":
        return cls(kind="document_original", doc_id=int(doc_id))

    @classmethod
    def page(cls, doc_id: int, page_no: int) -> "ProtectedFileReference":
        return cls(kind="document_page", doc_id=int(doc_id), page_no=int(page_no))

    @classmethod
    def chat_image(cls, image_id: str) -> "ProtectedFileReference":
        return cls(kind="chat_image", image_id=str(image_id or ""))


@dataclass(frozen=True, slots=True)
class AuthorizedFile:
    path: Path | None
    filename: str | None = None
    placeholder: bool = False


@dataclass(frozen=True, slots=True)
class ProtectedFileAuditRecord:
    actor_username: str
    action: str
    doc_id: int
    details: Mapping[str, Any]


class ProtectedFileError(Exception):
    """Typed application failure translated by the HTTP boundary."""

    def __init__(self, code: str, detail: str) -> None:
        super().__init__(detail)
        self.code = code
        self.detail = detail


class ProtectedFileStore(Protocol):
    def load_document(self, doc_id: int) -> DocumentAccessRecord | None: ...

    def load_page_image(self, doc_id: int, page_no: int) -> str | None: ...

    def chat_image_owned_by(self, image_id: str, username: str) -> bool: ...


class ProtectedFileStorage(Protocol):
    def resolve_original(self, stored_path: str | None) -> Path | None: ...

    def resolve_page_image(self, stored_path: str | None) -> Path | None: ...

    def resolve_chat_image(self, image_id: str) -> Path | None: ...


class ProtectedFileAuditSink(Protocol):
    def record(self, record: ProtectedFileAuditRecord) -> None: ...


def normalize_security_level(value: Any) -> str:
    normalized = str(value or "").strip().lower()
    return normalized if normalized in LEVEL_ORDER else "confidential"


def evaluate_document_access(
    actor: ProtectedFileActor,
    record: DocumentAccessRecord | None,
    *,
    strict_site_filter: bool,
) -> AccessDecision:
    if record is None:
        return AccessDecision(False, "document_not_found")

    security_level = normalize_security_level(record.security_level)
    if not is_currently_servable(
        {
            "servable": record.servable,
            "publication_state": record.publication_state,
            "lifecycle_status": record.lifecycle_status,
            "review_status": record.review_status,
            "is_current": record.is_current,
            "effective_status": record.effective_status,
            "effective_date": record.effective_date,
            "expiry_date": record.expiry_date,
        },
        require_current=True,
    ):
        return AccessDecision(False, "document_not_servable", security_level)

    if "admin" in actor.roles:
        return AccessDecision(True, "global_admin", security_level)

    document_departments = frozenset(
        str(department).strip()
        for department in record.departments
        if str(department).strip()
    )
    if not document_departments:
        return AccessDecision(False, "document_has_no_department_grants", security_level)

    allowed_departments = actor.allowed_departments | frozenset(
        value for value in (actor.department, SHARE_ALL_DEPARTMENT) if value
    )
    if (
        SHARE_ALL_DEPARTMENT not in document_departments
        and document_departments.isdisjoint(allowed_departments)
    ):
        return AccessDecision(False, "department_denied", security_level)

    actor_level = normalize_security_level(actor.max_security_level)
    if LEVEL_ORDER[security_level] > LEVEL_ORDER[actor_level]:
        return AccessDecision(False, "security_denied", security_level)

    allowed_sites = frozenset(site.strip() for site in actor.allowed_sites if site.strip())
    if not allowed_sites:
        return AccessDecision(False, "site_assignment_missing", security_level)
    document_site = str(record.site or "").strip()
    if strict_site_filter:
        if document_site not in allowed_sites:
            return AccessDecision(False, "site_denied", security_level)
    elif document_site and document_site not in allowed_sites:
        return AccessDecision(False, "site_denied", security_level)

    return AccessDecision(True, "allowed", security_level)


class ProtectedFileResolver:
    """Resolve protected files through explicit policy and infrastructure ports."""

    def __init__(
        self,
        *,
        store: ProtectedFileStore,
        storage: ProtectedFileStorage,
        audit: ProtectedFileAuditSink,
        strict_site_filter: bool,
    ) -> None:
        self._store = store
        self._storage = storage
        self._audit = audit
        self._strict_site_filter = bool(strict_site_filter)

    def resolve(
        self,
        reference: ProtectedFileReference,
        actor: ProtectedFileActor,
    ) -> AuthorizedFile:
        if reference.kind == "chat_image":
            return self._resolve_chat_image(reference, actor)
        if reference.kind in {"document_page", "document_original"}:
            return self._resolve_document(reference, actor)
        raise ProtectedFileError("not_found", "File not found")

    def _resolve_document(
        self,
        reference: ProtectedFileReference,
        actor: ProtectedFileActor,
    ) -> AuthorizedFile:
        doc_id = int(reference.doc_id or 0)
        record = self._store.load_document(doc_id)
        decision = evaluate_document_access(
            actor,
            record,
            strict_site_filter=self._strict_site_filter,
        )
        if not decision.allowed or record is None:
            raise ProtectedFileError("unauthorized", decision.reason)

        if reference.kind == "document_page":
            page_no = int(reference.page_no or 0)
            stored_path = self._store.load_page_image(doc_id, page_no)
            path = self._storage.resolve_page_image(stored_path)
            if "admin" in actor.roles or decision.security_level == "confidential":
                self._record_audit(
                    actor,
                    "admin_global_read_citation"
                    if "admin" in actor.roles
                    else "view_citation_page",
                    doc_id,
                    {
                        "page_no": page_no,
                        "security_level": decision.security_level,
                        "access_scope": decision.reason,
                        "source": "app-api",
                    },
                )
            return AuthorizedFile(path=path, placeholder=path is None)

        path = self._storage.resolve_original(record.file_path)
        if path is None:
            raise ProtectedFileError("not_found", "Original file not found")
        self._record_audit(
            actor,
            "admin_global_read_original"
            if "admin" in actor.roles
            else "download_original",
            doc_id,
            {
                "file": record.ten_file,
                "security_level": decision.security_level,
                "access_scope": decision.reason,
                "source": "app-api",
            },
        )
        return AuthorizedFile(path=path, filename=record.ten_file or path.name)

    def _resolve_chat_image(
        self,
        reference: ProtectedFileReference,
        actor: ProtectedFileActor,
    ) -> AuthorizedFile:
        image_id = str(reference.image_id or "")
        path = self._storage.resolve_chat_image(image_id)
        if path is None:
            raise ProtectedFileError("not_found", "Image not found")
        try:
            owned = self._store.chat_image_owned_by(image_id, actor.username)
        except Exception as exc:
            raise ProtectedFileError("storage_failed", "Database is not ready") from exc
        if not owned:
            raise ProtectedFileError(
                "unauthorized",
                "Image is not visible to this user",
            )
        return AuthorizedFile(path=path)

    def _record_audit(
        self,
        actor: ProtectedFileActor,
        action: str,
        doc_id: int,
        details: Mapping[str, Any],
    ) -> None:
        self._audit.record(
            ProtectedFileAuditRecord(
                actor_username=actor.username,
                action=action,
                doc_id=doc_id,
                details=MappingProxyType(dict(details)),
            )
        )


__all__ = [
    "AccessDecision",
    "AuthorizedFile",
    "DocumentAccessRecord",
    "LEVEL_ORDER",
    "ProtectedFileActor",
    "ProtectedFileAuditRecord",
    "ProtectedFileError",
    "ProtectedFileReference",
    "ProtectedFileResolver",
    "ProtectedFileStorage",
    "ProtectedFileStore",
    "evaluate_document_access",
    "normalize_security_level",
]
