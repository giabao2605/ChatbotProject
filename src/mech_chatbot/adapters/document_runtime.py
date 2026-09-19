"""Concrete adapters for document upload, review and publication use cases."""

from __future__ import annotations

import os
from collections.abc import Callable, Mapping
from pathlib import Path
from typing import Any
from uuid import uuid4

from sqlalchemy import text

from mech_chatbot.application.document_review import (
    PublicationCommand,
    PublicationOutcome,
)
from mech_chatbot.application.document_upload import (
    StoredUpload,
    sanitize_department_folder,
)


class LocalUploadStorage:
    """Store upload bytes under the configured raw-data root."""

    def __init__(self, *, raw_root: Callable[[], Path]) -> None:
        self._raw_root = raw_root

    def store(self, *, file_name: str, content: bytes, owner_department: str) -> StoredUpload:
        ext = Path(file_name or "").suffix.lower()
        safe_dept = sanitize_department_folder(owner_department)
        out_dir = self._raw_root() / "Uploads" / safe_dept
        out_dir.mkdir(parents=True, exist_ok=True)
        stored_path = out_dir / f"{uuid4().hex}{ext}"
        temporary_path = out_dir / f".{stored_path.name}.{uuid4().hex}.tmp"
        try:
            with temporary_path.open("xb") as handle:
                handle.write(content)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary_path, stored_path)
        except Exception:
            temporary_path.unlink(missing_ok=True)
            raise
        return StoredUpload(original_name=file_name, stored_path=str(stored_path))

    def delete(self, stored_path: str) -> bool:
        try:
            Path(stored_path).unlink(missing_ok=True)
            return True
        except Exception:
            return False


class RepositoryUploadJobStore:
    """Create ingestion jobs through the existing repository function."""

    def __init__(self, *, create_ingestion_job: Callable[..., int | None]) -> None:
        self._create_ingestion_job = create_ingestion_job

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
        return self._create_ingestion_job(
            file_name=file_name,
            file_path=file_path,
            thu_muc=owner_department,
            uploaded_by=uploaded_by,
            domain=domain,
            security_level=security_level,
            cong_doan=process_stage,
            site=site,
            phong_ban=list(shared_departments),
            upload_meta=dict(upload_metadata or {}),
        )


class RepositoryReviewStore:
    """Call existing document/job repository operations for review actions."""

    def __init__(
        self,
        *,
        reject_ingestion_job: Callable[..., bool],
        mark_job_rejected: Callable[[int], Any],
        reject_document: Callable[..., Any],
        delete_document_completely: Callable[..., Any],
        delete_ingestion_job: Callable[[int], Any],
    ) -> None:
        self._reject_ingestion_job = reject_ingestion_job
        self._mark_job_rejected = mark_job_rejected
        self._reject_document = reject_document
        self._delete_document_completely = delete_document_completely
        self._delete_ingestion_job = delete_ingestion_job

    def reject_job(self, job_id: int, reason: str | None) -> bool:
        return bool(self._reject_ingestion_job(job_id, reason or ""))

    def mark_job_rejected(self, job_id: int) -> Any:
        return self._mark_job_rejected(job_id)

    def reject_document(self, doc_id: int, reviewer: str) -> Any:
        return self._reject_document(doc_id, reviewer=reviewer)

    def delete_document(self, doc_id: int, reviewer: str) -> Any:
        return self._delete_document_completely(doc_id, reviewer=reviewer)

    def delete_job(self, job_id: int) -> Any:
        return self._delete_ingestion_job(job_id)


class RepositoryPublicationPort:
    """Publish documents and update their ingestion job state when needed."""

    def __init__(
        self,
        *,
        publish_document: Callable[..., Any],
        mark_job_published: Callable[[int], Any],
        resolve_latest_doc_id_for_job: Callable[[int], int | None],
    ) -> None:
        self._publish_document = publish_document
        self._mark_job_published = mark_job_published
        self._resolve_latest_doc_id_for_job = resolve_latest_doc_id_for_job

    def resolve_latest_doc_id(self, job_id: int) -> int | None:
        return self._resolve_latest_doc_id_for_job(job_id)

    def publish_document(self, command: PublicationCommand, actor) -> PublicationOutcome:
        if command.doc_id is None:
            raise ValueError("Publication command must contain doc_id")
        result = self._publish_document(
            int(command.doc_id),
            action=command.publish_mode,
            reviewer=actor.username or "System",
            reviewer_id=getattr(actor, "user_id", None),
            reviewer_roles=list(actor.roles),
        )
        payload = result.to_dict() if hasattr(result, "to_dict") else {"ok": bool(result)}
        return PublicationOutcome(
            ok=bool(result),
            state=str(getattr(result, "state", payload.get("state", "")) or ""),
            error=getattr(result, "error", None) or payload.get("error"),
            payload=payload,
        )

    def mark_job_published(self, job_id: int) -> Any:
        return self._mark_job_published(job_id)


class SqlDocumentLookup:
    """Small SQL adapter for document ids resolved from ingestion jobs."""

    def __init__(self, *, engine: Any) -> None:
        self._engine = engine

    def _resolved_engine(self) -> Any:
        return self._engine() if callable(self._engine) else self._engine

    def latest_doc_id_for_job(self, job_id: int) -> int | None:
        engine = self._resolved_engine()
        if engine is None:
            return None
        with engine.connect() as conn:
            row = conn.execute(
                text(
                    """
                    SELECT TOP 1 d.DocID
                    FROM dbo.IngestionJobs j
                    JOIN dbo.TaiLieu d ON d.TenFile = j.TenFile AND d.ThuMuc = j.ThuMuc
                    WHERE j.JobID = :job_id
                    ORDER BY d.DocID DESC
                    """
                ),
                {"job_id": job_id},
            ).fetchone()
        return int(row[0]) if row else None


class SqlAppSupportQueries:
    """Remaining SQL reads used by the browser API transport layer."""

    def __init__(self, *, engine: Any) -> None:
        self._engine = engine

    def _resolved_engine(self) -> Any:
        return self._engine() if callable(self._engine) else self._engine

    def database_ready(self) -> bool:
        engine = self._resolved_engine()
        if engine is None:
            return False
        with engine.connect() as conn:
            conn.execute(text("SELECT 1"))
        return True

    def chat_image_visible_to_user(self, *, image_id: str, username: str | None) -> bool:
        engine = self._resolved_engine()
        if engine is None:
            return False
        with engine.connect() as conn:
            row = conn.execute(
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

    def feedback_review_context(self, feedback_id: int) -> tuple[Any, Any, Any, Any] | None:
        engine = self._resolved_engine()
        if engine is None:
            return None
        with engine.connect() as conn:
            row = conn.execute(
                text(
                    "SELECT Question, SourceDocID, Department, Site "
                    "FROM FeedbackReview WHERE FeedbackID = :fid"
                ),
                {"fid": feedback_id},
            ).fetchone()
        return tuple(row) if row else None

    def answer_sources_for_chat_ids(self, chat_ids: tuple[int, ...]) -> list[Any]:
        engine = self._resolved_engine()
        if not chat_ids or engine is None:
            return []
        keys: list[str] = []
        params: dict[str, Any] = {}
        for index, chat_id in enumerate(chat_ids):
            key = f"cid_{index}"
            keys.append(f":{key}")
            params[key] = chat_id
        with engine.connect() as conn:
            rows = conn.execute(
                text(
                    """
                    SELECT a.ChatID, a.DocID, a.FileName, a.VersionNo, a.ChunkRef, a.Score,
                           CASE WHEN NULLIF(LTRIM(RTRIM(p.VisionSummary)), '') IS NOT NULL
                                      AND p.ImagePath IS NOT NULL
                                THEN 1 ELSE 0 END AS HasVision
                    FROM dbo.AnswerSource a
                    LEFT JOIN dbo.DocumentPages p
                      ON p.DocID = a.DocID
                     AND p.PageNo = TRY_CONVERT(INT, a.ChunkRef)
                    WHERE a.ChatID IN (
                    """
                    + ", ".join(keys)
                    + ") ORDER BY a.ChatID, a.RankNo"
                ),
                params,
            ).fetchall()
        return list(rows)
