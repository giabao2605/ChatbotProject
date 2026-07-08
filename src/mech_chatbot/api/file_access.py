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
    try:
        from mech_chatbot.db.repository import get_app_setting

        raw = get_app_setting("rbac_strict_site_filter", "false")
        return str(raw).strip().lower() in {"true", "1", "yes", "on"}
    except Exception:
        return False


def load_document_access_record(doc_id: int) -> DocumentAccessRecord | None:
    if engine is None:
        return None
    with engine.connect() as conn:
        row = conn.execute(
            text(
                """
                SELECT DocID, TenFile, FilePath, ThuMuc, SecurityLevel, Site,
                       LifecycleStatus, ReviewStatus
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
    )


def evaluate_document_access(profile: dict[str, Any], record: DocumentAccessRecord | None) -> AccessDecision:
    if record is None:
        return AccessDecision(False, "document_not_found")
    if "admin" in _profile_roles(profile):
        return AccessDecision(True, "admin", security_level=record.security_level)

    doc_departments = {d for d in record.departments if d}
    if not doc_departments:
        return AccessDecision(False, "document_has_no_department_grants", security_level=record.security_level)
    if SHARE_ALL_DEPARTMENT not in doc_departments and not (doc_departments & _profile_allowed_departments(profile)):
        return AccessDecision(False, "department_denied", security_level=record.security_level)

    user_level = normalize_security_level(profile.get("max_security_level") or "public")
    if LEVEL_ORDER[record.security_level] > LEVEL_ORDER[user_level]:
        return AccessDecision(False, "security_denied", security_level=record.security_level)

    allowed_sites = {_normalize_site(s) for s in (profile.get("allowed_sites") or []) if _normalize_site(s)}
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
    return resolve_under_root(row[0], [data_processed_root()])


def original_file_path(record: DocumentAccessRecord) -> Path | None:
    return resolve_under_root(record.file_path, [data_raw_root()])


def chat_image_path(image_id: str) -> Path | None:
    name = os.path.basename(str(image_id or ""))
    if not name or name != image_id:
        return None
    return resolve_under_root(data_raw_root() / "Chat_Images" / name, [data_raw_root()])
