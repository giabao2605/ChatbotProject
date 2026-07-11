"""Shared file-access policy for app-api file endpoints.

Retrieval RBAC is enforced in Qdrant filters. Browser file access has to repeat
the same security semantics against SQL metadata before serving any bytes.
"""
from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable

from sqlalchemy import text

from mech_chatbot.config.constants import SHARE_ALL_DEPARTMENT
from mech_chatbot.db.engine import engine

LEVEL_ORDER = {"public": 0, "internal": 1, "confidential": 2}


@dataclass(frozen=True)
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
    # Defaults preserve compatibility for isolated callers that construct an
    # in-memory record, while database-backed access always reads the serving
    # state below and therefore fails closed for staged documents.
    servable: bool = True
    publication_state: str = "published"


@dataclass(frozen=True)
class AccessDecision:
    allowed: bool
    reason: str
    security_level: str = "confidential"


def project_root() -> Path:
    return Path(__file__).resolve().parents[3]


def data_raw_root() -> Path:
    return project_root() / "data" / "raw"


def data_processed_root() -> Path:
    return project_root() / "data" / "processed"


def normalize_security_level(value: Any) -> str:
    normalized = str(value or "").strip().lower()
    return normalized if normalized in LEVEL_ORDER else "confidential"


def _normalize_site(value: Any) -> str:
    return str(value or "").strip()


def _profile_roles(profile: dict[str, Any]) -> set[str]:
    return {str(role).strip().lower() for role in (profile.get("roles") or []) if role}


def _profile_allowed_departments(profile: dict[str, Any]) -> set[str]:
    allowed = {str(d).strip() for d in (profile.get("allowed_departments") or []) if d}
    department = profile.get("department")
    if department:
        allowed.add(str(department).strip())
    allowed.add(SHARE_ALL_DEPARTMENT)
    return {d for d in allowed if d}


def _strict_site_enabled() -> bool:
    raw = os.getenv("RBAC_STRICT_SITE_FILTER", "true")
    return str(raw).strip().lower() in {"true", "1", "yes", "on"}


def load_document_access_record(doc_id: int) -> DocumentAccessRecord | None:
    if engine is None:
        return None
    with engine.connect() as conn:
        row = conn.execute(
            text(
                """
                SELECT DocID, TenFile, FilePath, ThuMuc, SecurityLevel, Site,
                       LifecycleStatus, ReviewStatus, Servable, PublicationState
                FROM dbo.TaiLieu
                WHERE DocID = :doc_id
                """
            ),
            {"doc_id": doc_id},
        ).fetchone()
        if not row:
            return None
        dept_rows = conn.execute(
            text("SELECT DeptCode FROM dbo.PhongBanChiaSe WHERE DocID = :doc_id"),
            {"doc_id": doc_id},
        ).fetchall()
    departments = tuple(str(r[0]).strip() for r in dept_rows if r and r[0])
    return DocumentAccessRecord(
        doc_id=int(row[0]),
        ten_file=row[1],
        file_path=row[2],
        thu_muc=row[3],
        security_level=normalize_security_level(row[4]),
        site=row[5],
        lifecycle_status=row[6],
        review_status=row[7],
        departments=departments,
        servable=bool(row[8]),
        publication_state=str(row[9] or "").strip().lower(),
    )


def evaluate_document_access(profile: dict[str, Any], record: DocumentAccessRecord | None) -> AccessDecision:
    if record is None:
        return AccessDecision(False, "document_not_found")

    # Citation/original-file endpoints are serving surfaces too.  A document
    # may already have been embedded for review, but it must never be opened
    # until the publication outbox has made it explicitly servable.
    lifecycle = str(record.lifecycle_status or "").strip().lower()
    review = str(record.review_status or "").strip().lower()
    if (
        not record.servable
        or record.publication_state != "published"
        or lifecycle != "published"
        or review != "approved"
    ):
        return AccessDecision(False, "document_not_servable", security_level=record.security_level)

    if "admin" in _profile_roles(profile):
        # Revised plan v3 explicitly retains legacy-admin global read.  The
        # endpoint performs an audit write for every successful access; new
        # platform/security control-plane roles do not inherit this bypass.
        return AccessDecision(True, "global_admin", security_level=record.security_level)

    # Platform/security administrators are not document-read roles.  File
    # bytes follow the same department, clearance, and site checks as RAG.
    doc_departments = {d for d in record.departments if d}
    if not doc_departments:
        return AccessDecision(False, "document_has_no_department_grants", security_level=record.security_level)
    if SHARE_ALL_DEPARTMENT not in doc_departments and not (doc_departments & _profile_allowed_departments(profile)):
        return AccessDecision(False, "department_denied", security_level=record.security_level)

    user_level = normalize_security_level(profile.get("max_security_level") or "public")
    if LEVEL_ORDER[record.security_level] > LEVEL_ORDER[user_level]:
        return AccessDecision(False, "security_denied", security_level=record.security_level)

    allowed_sites = {_normalize_site(s) for s in (profile.get("allowed_sites") or []) if _normalize_site(s)}
    if not allowed_sites:
        return AccessDecision(False, "site_assignment_missing", security_level=record.security_level)
    if allowed_sites:
        doc_site = _normalize_site(record.site)
        if _strict_site_enabled():
            if doc_site not in allowed_sites:
                return AccessDecision(False, "site_denied", security_level=record.security_level)
        elif doc_site and doc_site not in allowed_sites:
            return AccessDecision(False, "site_denied", security_level=record.security_level)

    return AccessDecision(True, "allowed", security_level=record.security_level)


def can_access_document(profile: dict[str, Any], doc_id: int) -> tuple[AccessDecision, DocumentAccessRecord | None]:
    record = load_document_access_record(doc_id)
    return evaluate_document_access(profile, record), record


def resolve_under_root(raw_path: str | os.PathLike[str] | None, allowed_roots: Iterable[Path]) -> Path | None:
    if not raw_path:
        return None
    candidate = Path(str(raw_path))
    if not candidate.is_absolute():
        candidate = project_root() / candidate
    try:
        resolved = candidate.resolve(strict=False)
    except OSError:
        return None
    roots = [root.resolve(strict=False) for root in allowed_roots]
    if not any(resolved == root or root in resolved.parents for root in roots):
        return None
    return resolved


def page_image_path(doc_id: int, page_no: int) -> Path | None:
    if engine is None:
        return None
    with engine.connect() as conn:
        row = conn.execute(
            text(
                """
                SELECT TOP 1 ImagePath
                FROM dbo.DocumentPages
                WHERE DocID = :doc_id AND PageNo = :page_no AND ImagePath IS NOT NULL
                """
            ),
            {"doc_id": doc_id, "page_no": page_no},
        ).fetchone()
    if not row:
        return None
    stored = row[0]
    resolved = resolve_under_root(stored, [data_processed_root()])
    if resolved is not None and resolved.exists() and resolved.is_file():
        return resolved

    # ImagePath may have been written by a worker running in another
    # environment (for example /app/... in Docker, or a Windows host path).
    # All rendered page images are stored flat under data/processed, so safely
    # retry by basename inside the local approved root. Never trust parent
    # directories from the database for this fallback.
    try:
        basename = Path(str(stored).replace("\\", "/")).name
    except Exception:
        basename = ""
    if basename:
        fallback = (data_processed_root() / basename).resolve(strict=False)
        root = data_processed_root().resolve(strict=False)
        if root in fallback.parents and fallback.exists() and fallback.is_file():
            return fallback
    return None


def page_has_vision(doc_id: int, page_no: int) -> bool:
    """True only when this page was successfully processed by Vision.

    A rendered PDF page alone is not enough: text-heavy PDF pages are rendered
    for internal processing too, but the chat UI must only display an image
    when VisionSummary proves that Vision/OCR actually ran successfully.
    """
    if engine is None:
        return False
    try:
        with engine.connect() as conn:
            row = conn.execute(
                text(
                    """
                    SELECT TOP 1 VisionSummary, ImagePath
                    FROM dbo.DocumentPages
                    WHERE DocID = :doc_id AND PageNo = :page_no
                    """
                ),
                {"doc_id": int(doc_id), "page_no": int(page_no)},
            ).fetchone()
        if not row or not str(row[0] or "").strip() or not row[1]:
            return False
        return page_image_path(int(doc_id), int(page_no)) is not None
    except Exception:
        return False


def original_file_path(record: DocumentAccessRecord) -> Path | None:
    return resolve_under_root(record.file_path, [data_raw_root()])


def chat_image_path(image_id: str) -> Path | None:
    name = os.path.basename(str(image_id or ""))
    if not name or name != image_id:
        return None
    return resolve_under_root(data_raw_root() / "Chat_Images" / name, [data_raw_root()])
