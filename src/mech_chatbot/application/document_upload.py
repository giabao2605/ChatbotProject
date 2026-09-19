"""Application owner for document upload and ingestion-job enqueueing."""

from __future__ import annotations

import logging
import re
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol

from mech_chatbot.auth.authorization import role_allows


MAX_UPLOAD_BYTES = 100 * 1024 * 1024
MAX_BATCH_FILES = 50
ALLOWED_UPLOAD_EXTENSIONS = frozenset(
    {
        ".pdf",
        ".docx",
        ".doc",
        ".xlsx",
        ".xls",
        ".txt",
        ".md",
        ".csv",
        ".pptx",
        ".png",
        ".jpg",
        ".jpeg",
        ".bmp",
        ".gif",
        ".webp",
        ".tif",
        ".tiff",
    }
)

logger = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class DocumentActor:
    user_id: int | None
    username: str | None
    roles: tuple[str, ...]
    allowed_departments: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class UploadDocumentCommand:
    file_name: str
    content: bytes
    owner_department: str
    shared_departments: tuple[str, ...]
    domain: str | None
    security_level: str | None
    process_stage: str | None
    site: str | None
    upload_metadata: Mapping[str, Any]


@dataclass(frozen=True, slots=True)
class StoredUpload:
    original_name: str
    stored_path: str


@dataclass(frozen=True, slots=True)
class UploadReceipt:
    job_id: int
    file_name: str
    owner_department: str


@dataclass(frozen=True, slots=True)
class UploadFailure:
    code: str
    file_name: str
    message: str
    detail: Mapping[str, Any] | None = None


@dataclass(frozen=True, slots=True)
class UploadBatchResult:
    jobs: tuple[UploadReceipt, ...]
    errors: tuple[UploadFailure, ...]
    created: int
    failed: int

    @property
    def ok(self) -> bool:
        return self.failed == 0


class UploadRejected(Exception):
    """Raised when a single upload cannot be accepted."""

    def __init__(self, failure: UploadFailure) -> None:
        super().__init__(failure.message)
        self.failure = failure


class UploadStorage(Protocol):
    def store(self, *, file_name: str, content: bytes, owner_department: str) -> StoredUpload:
        """Persist bytes and return the original/stored file identity."""

    def delete(self, stored_path: str) -> bool:
        """Best-effort cleanup for a staged upload."""


class UploadJobStore(Protocol):
    def create_job(
        self,
        *,
        file_name: str,
        file_path: str,
        owner_department: str,
        uploaded_by: str | None,
        domain: str | None,
        security_level: str | None,
        process_stage: str | None,
        site: str | None,
        shared_departments: tuple[str, ...],
        upload_metadata: Mapping[str, Any],
    ) -> int | None:
        """Create the ingestion job row for an already-staged file."""


def sanitize_department_folder(department: str | None) -> str:
    return re.sub(r"[^A-Za-z0-9_\-]", "_", (department or "").strip()) or "CHUNG"


def _failure(code: str, file_name: str, message: str, detail: Mapping[str, Any] | None = None) -> UploadFailure:
    return UploadFailure(code=code, file_name=file_name, message=message, detail=detail)


class DocumentUpload:
    """Own upload validation, staged-file cleanup and job enqueue coordination."""

    def __init__(self, *, storage: UploadStorage, job_store: UploadJobStore) -> None:
        self._storage = storage
        self._job_store = job_store

    def preflight(
        self,
        *,
        file_name: str,
        owner_department: str,
        actor: DocumentActor,
    ) -> UploadFailure | None:
        """Validate authorization and extension before transport reads bytes."""
        if not role_allows(actor.roles, "uploader", "reviewer", "admin"):
            return _failure("unauthorized", file_name, "Forbidden")
        if owner_department not in set(actor.allowed_departments):
            return _failure(
                "unauthorized",
                file_name,
                f"Không có quyền upload vào phòng ban {owner_department}",
            )
        extension = Path(file_name or "").suffix.lower()
        if extension not in ALLOWED_UPLOAD_EXTENSIONS:
            return _failure(
                "invalid_extension",
                file_name,
                f"Định dạng tệp không được hỗ trợ: {file_name}",
            )
        return None

    def enqueue(self, command: UploadDocumentCommand, actor: DocumentActor) -> UploadReceipt:
        failure = self._validate(command, actor)
        if failure is not None:
            raise UploadRejected(failure)

        try:
            stored = self._storage.store(
                file_name=command.file_name,
                content=command.content,
                owner_department=command.owner_department,
            )
        except Exception as exc:
            raise UploadRejected(
                _failure(
                    "storage_failed",
                    command.file_name,
                    f"Không lưu được tệp: {command.file_name}",
                )
            ) from exc
        shared_departments = self._shared_departments(command)
        try:
            job_id = self._job_store.create_job(
                file_name=stored.original_name,
                file_path=stored.stored_path,
                owner_department=command.owner_department,
                uploaded_by=actor.username,
                domain=command.domain,
                security_level=command.security_level,
                process_stage=command.process_stage,
                site=command.site,
                shared_departments=shared_departments,
                upload_metadata=command.upload_metadata,
            )
        except Exception as exc:
            self._raise_enqueue_failure(stored, cause=exc)
        if job_id:
            return UploadReceipt(
                job_id=int(job_id),
                file_name=stored.original_name,
                owner_department=command.owner_department,
            )

        self._raise_enqueue_failure(stored)

    def _raise_enqueue_failure(
        self,
        stored: StoredUpload,
        *,
        cause: Exception | None = None,
    ) -> None:
        try:
            cleanup_ok = self._storage.delete(stored.stored_path)
        except Exception:
            cleanup_ok = False
        detail = None if cleanup_ok else {"stored_path": stored.stored_path}
        code = "enqueue_failed" if cleanup_ok else "cleanup_failed"
        message = "Không tạo được job (phòng ban có thể bị vô hiệu)"
        if not cleanup_ok:
            message = "Không tạo được job và không xóa được file đã lưu"
            logger.error(
                "Upload cleanup failed after enqueue failure",
                extra={"stored_path": stored.stored_path},
            )
        error = UploadRejected(_failure(code, stored.original_name, message, detail))
        if cause is not None:
            raise error from cause
        raise error

    def enqueue_batch(
        self,
        commands: tuple[UploadDocumentCommand, ...],
        actor: DocumentActor,
    ) -> UploadBatchResult:
        if len(commands) > MAX_BATCH_FILES:
            return UploadBatchResult(
                jobs=(),
                errors=(
                    _failure(
                        "invalid_batch",
                        "",
                        "Một lần upload tối đa 50 tệp",
                        {"limit": MAX_BATCH_FILES, "count": len(commands)},
                    ),
                ),
                created=0,
                failed=1,
            )

        receipts: list[UploadReceipt] = []
        errors: list[UploadFailure] = []
        for command in commands:
            try:
                receipts.append(self.enqueue(command, actor))
            except UploadRejected as exc:
                errors.append(exc.failure)
            except Exception:
                errors.append(
                    _failure(
                        "storage_failed",
                        command.file_name,
                        f"Không xử lý được tệp: {command.file_name}",
                    )
                )
        return UploadBatchResult(
            jobs=tuple(receipts),
            errors=tuple(errors),
            created=len(receipts),
            failed=len(errors),
        )

    def _validate(self, command: UploadDocumentCommand, actor: DocumentActor) -> UploadFailure | None:
        preflight_failure = self.preflight(
            file_name=command.file_name,
            owner_department=command.owner_department,
            actor=actor,
        )
        if preflight_failure is not None:
            return preflight_failure
        if not command.content:
            return _failure("empty_file", command.file_name, f"Tệp rỗng: {command.file_name}")
        if len(command.content) > MAX_UPLOAD_BYTES:
            return _failure(
                "file_too_large",
                command.file_name,
                f"Tệp quá lớn (giới hạn 100MB): {command.file_name}",
                {"limit_bytes": MAX_UPLOAD_BYTES, "actual_bytes": len(command.content)},
            )
        return None

    @staticmethod
    def _shared_departments(command: UploadDocumentCommand) -> tuple[str, ...]:
        seen = {command.owner_department}
        ordered = [command.owner_department]
        for department in command.shared_departments:
            if department and department not in seen:
                ordered.append(department)
                seen.add(department)
        return tuple(ordered)
