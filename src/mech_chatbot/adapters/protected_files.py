"""SQL, filesystem, and audit adapters for protected file delivery."""

from __future__ import annotations

import os
from collections.abc import Callable, Iterable
from pathlib import Path
from typing import Any

from sqlalchemy import text

from mech_chatbot.application.protected_files import (
    DocumentAccessRecord,
    ProtectedFileAuditRecord,
    normalize_security_level,
)


class SqlProtectedFileStore:
    """Read only the metadata needed by the protected-file application seam."""

    def __init__(self, *, engine_provider: Callable[[], Any]) -> None:
        self._engine_provider = engine_provider

    def load_document(self, doc_id: int) -> DocumentAccessRecord | None:
        engine = self._engine_provider()
        if engine is None:
            return None
        with engine.connect() as connection:
            row = connection.execute(
                text(
                    """
                    SELECT DocID, TenFile, FilePath, ThuMuc, SecurityLevel, Site,
                           LifecycleStatus, ReviewStatus, Servable, PublicationState,
                           IsCurrent, EffectiveStatus, EffectiveDate, ExpiryDate
                    FROM dbo.TaiLieu
                    WHERE DocID = :doc_id
                    """
                ),
                {"doc_id": int(doc_id)},
            ).fetchone()
            if not row:
                return None
            department_rows = connection.execute(
                text(
                    "SELECT DeptCode FROM dbo.PhongBanChiaSe "
                    "WHERE DocID = :doc_id"
                ),
                {"doc_id": int(doc_id)},
            ).fetchall()
        departments = tuple(
            str(department_row[0]).strip()
            for department_row in department_rows
            if department_row and department_row[0]
        )
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
            is_current=bool(row[10]),
            effective_status=row[11],
            effective_date=row[12],
            expiry_date=row[13],
        )

    def load_page_image(self, doc_id: int, page_no: int) -> str | None:
        engine = self._engine_provider()
        if engine is None:
            return None
        with engine.connect() as connection:
            row = connection.execute(
                text(
                    """
                    SELECT TOP 1 ImagePath
                    FROM dbo.DocumentPages
                    WHERE DocID = :doc_id AND PageNo = :page_no
                      AND ImagePath IS NOT NULL
                    """
                ),
                {"doc_id": int(doc_id), "page_no": int(page_no)},
            ).fetchone()
        return str(row[0]) if row and row[0] else None

    def load_page_vision(self, doc_id: int, page_no: int) -> tuple[str, str] | None:
        engine = self._engine_provider()
        if engine is None:
            return None
        with engine.connect() as connection:
            row = connection.execute(
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
            return None
        return str(row[0]), str(row[1])

    def chat_image_owned_by(self, image_id: str, username: str) -> bool:
        engine = self._engine_provider()
        if engine is None:
            raise RuntimeError("database unavailable")
        with engine.connect() as connection:
            row = connection.execute(
                text(
                    """
                    SELECT TOP 1 ChatID
                    FROM dbo.LichSuChat
                    WHERE HinhAnhUpload LIKE :suffix AND Username = :username
                    """
                ),
                {"suffix": f"%{image_id}", "username": username},
            ).fetchone()
        return bool(row)


class FilesystemProtectedFileStorage:
    """Resolve database paths only within explicitly approved local roots."""

    def __init__(
        self,
        *,
        project_root: Path,
        raw_root: Path,
        processed_root: Path,
    ) -> None:
        self._project_root = Path(project_root)
        self._raw_root = Path(raw_root)
        self._processed_root = Path(processed_root)

    @property
    def project_root(self) -> Path:
        return self._project_root

    @property
    def raw_root(self) -> Path:
        return self._raw_root

    @property
    def processed_root(self) -> Path:
        return self._processed_root

    def resolve_under_root(
        self,
        raw_path: str | os.PathLike[str] | None,
        allowed_roots: Iterable[Path],
    ) -> Path | None:
        if not raw_path:
            return None
        candidate = Path(str(raw_path))
        if not candidate.is_absolute():
            candidate = self._project_root / candidate
        try:
            resolved = candidate.resolve(strict=False)
            roots = tuple(Path(root).resolve(strict=False) for root in allowed_roots)
        except OSError:
            return None
        if not any(resolved == root or root in resolved.parents for root in roots):
            return None
        return resolved

    def resolve_original(self, stored_path: str | None) -> Path | None:
        return self.resolve_under_root(stored_path, (self._raw_root,))

    def resolve_page_image(self, stored_path: str | None) -> Path | None:
        resolved = self.resolve_under_root(stored_path, (self._processed_root,))
        if resolved is not None and resolved.exists() and resolved.is_file():
            return resolved
        try:
            basename = Path(str(stored_path or "").replace("\\", "/")).name
        except (OSError, ValueError):
            basename = ""
        if not basename:
            return None
        fallback = (self._processed_root / basename).resolve(strict=False)
        root = self._processed_root.resolve(strict=False)
        if root in fallback.parents and fallback.exists() and fallback.is_file():
            return fallback
        return None

    def resolve_chat_image(self, image_id: str) -> Path | None:
        name = str(image_id or "")
        if not name or name.startswith(".") or os.path.basename(name) != name:
            return None
        return self.resolve_under_root(
            self._raw_root / "Chat_Images" / name,
            (self._raw_root,),
        )


class RepositoryProtectedFileAuditSink:
    def __init__(self, *, write_audit_log: Callable[..., Any]) -> None:
        self._write_audit_log = write_audit_log

    def record(self, record: ProtectedFileAuditRecord) -> None:
        self._write_audit_log(
            username=record.actor_username,
            action=record.action,
            entity_type="TaiLieu",
            entity_id=record.doc_id,
            details=dict(record.details),
        )


__all__ = [
    "FilesystemProtectedFileStorage",
    "RepositoryProtectedFileAuditSink",
    "SqlProtectedFileStore",
]
