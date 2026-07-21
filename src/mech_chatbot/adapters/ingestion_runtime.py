"""Infrastructure adapters for the application ingestion lifecycle."""

from __future__ import annotations

import json
from collections.abc import Callable, Mapping
from pathlib import Path
from typing import Any

from sqlalchemy import text

from mech_chatbot.application.ingestion_runner import (
    IngestionJob,
    IngestionProcessResult,
)
from mech_chatbot.ingestion.progress import IngestionProgressEvent
from mech_chatbot.config.logging import logger


def _normalise_departments(value: object) -> tuple[str, ...]:
    if isinstance(value, str):
        values = value.split(",")
    elif isinstance(value, (list, tuple, set, frozenset)):
        values = value
    elif value is None:
        values = ()
    else:
        values = (value,)
    result: list[str] = []
    for item in values:
        department = str(item).strip()
        if department and department not in result:
            result.append(department)
    return tuple(result)


class RepositoryIngestionJobStore:
    """Map application lifecycle transitions to the existing SQL repository."""

    _STATUS_BY_PHASE = {
        "classifying": ("classifying", 5),
        "extracting": ("extracting", 20),
        "embedding": ("embedding", 70),
        # These are internal runner phases; SQL keeps its existing vocabulary.
        "quality_check": ("embedding", 90),
        "completed": ("embedding", 100),
    }

    def __init__(
        self,
        *,
        get_pending_job: Callable[[str], Mapping[str, Any] | None],
        update_ingestion_job: Callable[..., Any],
        update_ingestion_report: Callable[..., bool],
        mark_job_failed: Callable[..., Any],
        mark_job_waiting_quota: Callable[..., Any],
        write_audit_log: Callable[..., Any],
        engine: Any | None = None,
    ) -> None:
        self._get_pending_job = get_pending_job
        self._update_ingestion_job = update_ingestion_job
        self._update_ingestion_report = update_ingestion_report
        self._mark_job_failed = mark_job_failed
        self._mark_job_waiting_quota = mark_job_waiting_quota
        self._write_audit_log = write_audit_log
        self._engine = engine

    def claim_next(self, worker_id: str) -> IngestionJob | None:
        raw_job = self._get_pending_job(worker_id)
        if not raw_job:
            return None
        return IngestionJob(
            job_id=int(raw_job["job_id"]),
            file_path=Path(str(raw_job.get("file_path") or "")),
            file_name=str(raw_job.get("ten_file") or ""),
            owner_department=str(raw_job.get("thu_muc") or ""),
            shared_departments=_normalise_departments(raw_job.get("phong_ban")),
            domain=raw_job.get("domain"),
            security_level=raw_job.get("security_level"),
            process_stage=raw_job.get("cong_doan"),
            site=raw_job.get("site"),
        )

    def record_classification(
        self,
        job: IngestionJob,
        result: Mapping[str, Any],
    ) -> None:
        payload = dict(result)
        if self._engine is None:
            self._update_ingestion_job(
                job.job_id,
                status="extracting",
                error_message="Đang bóc tách nội dung...",
            )
        else:
            with self._engine.begin() as conn:
                conn.execute(
                    text(
                        """
                        UPDATE dbo.IngestionJobs
                        SET ClassificationJson = :j,
                            ClassificationConfidence = :c,
                            RequestedAction = :a,
                            Status = 'extracting',
                            ProgressPercent = 20,
                            ErrorMessage = NULL,
                            UpdatedAt = GETDATE()
                        WHERE JobID = :id
                        """
                    ),
                    {
                        "j": json.dumps(payload, ensure_ascii=False),
                        "c": payload.get("confidence", 0.0),
                        "a": payload.get("detected_action", "new_document"),
                        "id": job.job_id,
                    },
                )
        self._write_audit_log(
            "System Worker",
            "classify_done",
            "IngestionJobs",
            job.job_id,
            {"cls_res": payload},
        )

    def record_progress(self, job: IngestionJob, event: IngestionProgressEvent) -> None:
        status, progress = self._STATUS_BY_PHASE[event.phase]
        if self._engine is None:
            self._update_ingestion_job(
                job.job_id,
                status=status,
                error_message=event.message,
            )
            return
        with self._engine.begin() as conn:
            conn.execute(
                text(
                    """
                    UPDATE dbo.IngestionJobs
                    SET Status = :s,
                        ProgressPercent = :p,
                        ErrorMessage = :e,
                        UpdatedAt = GETDATE()
                    WHERE JobID = :id
                    """
                ),
                {"s": status, "p": progress, "e": event.message, "id": job.job_id},
            )

    def save_report(self, job: IngestionJob, report: Mapping[str, Any]) -> bool:
        return bool(self._update_ingestion_report(job.job_id, dict(report)))

    def mark_pending_review(self, job: IngestionJob) -> None:
        self._update_ingestion_job(job.job_id, status="pending_review", error_message="")

    def mark_failed(self, job: IngestionJob, message: str) -> None:
        self._mark_job_failed(job.job_id, error_message=message)

    def mark_waiting_quota(self, job: IngestionJob, message: str) -> None:
        self._mark_job_waiting_quota(job.job_id, error_message=message)

    def reconcile_unexpected_failure(
        self,
        job: IngestionJob,
        error: Exception,
    ) -> None:
        message = str(error) or error.__class__.__name__
        lowered = message.lower()
        if any(
            marker in lowered
            for marker in (
                "quota_exceeded",
                "quota exceeded",
                "resource_exhausted",
                "free_tier_requests",
            )
        ):
            self.mark_waiting_quota(job, message)
        else:
            self.mark_failed(job, message)


class LegacyDocumentClassifier:
    """Adapter around the existing classifier until it is ported to a port."""

    def __init__(
        self,
        classify: Callable[..., Mapping[str, Any]],
        *,
        processing_context: Callable[..., Any],
        load_governance: Callable[[str], Mapping[str, Any] | None] | None = None,
    ) -> None:
        self._classify = classify
        self._processing_context = processing_context
        self._load_governance = load_governance

    def _external_allowed(self, job: IngestionJob) -> bool:
        if self._load_governance is None:
            return False
        try:
            governance = self._load_governance(job.owner_department)
        except Exception as error:  # noqa: BLE001 - policy lookup must fail closed
            logger.warning(
                "Cannot load ingestion classification governance for %s: %s",
                job.owner_department,
                error,
            )
            return False
        if not governance or not bool(governance.get("is_active")):
            return False
        policy = str(governance.get("external_processing_policy") or "").strip().lower()
        return policy == "all_external"

    def classify(self, job: IngestionJob) -> Mapping[str, Any]:
        with self._processing_context(
            "ingestion-worker",
            False,
            f"ingestion_{job.job_id}",
        ):
            return self._classify(
                str(job.file_path),
                job.file_name,
                thu_muc=job.owner_department,
                allow_external=self._external_allowed(job),
            )


class IngestionPipelineProcessor:
    """Run the PDF/file implementation behind the application processor port."""

    def __init__(
        self,
        learn_new_file: Callable[..., tuple[bool, str, Mapping[str, Any] | None]],
        *,
        processing_context: Callable[..., Any],
    ) -> None:
        self._learn_new_file = learn_new_file
        self._processing_context = processing_context

    def process(self, job: IngestionJob, progress: Callable[[IngestionProgressEvent], None]) -> IngestionProcessResult:
        with self._processing_context("ingestion-worker", False, f"ingestion_{job.job_id}"):
            success, message, report = self._learn_new_file(
                file_path=str(job.file_path),
                ten_file=job.file_name,
                thu_muc=job.owner_department,
                progress_callback=progress,
                domain_override=job.domain,
                security_override=job.security_level,
                cong_doan_override=job.process_stage,
                site_override=job.site,
                phong_ban_override=job.shared_departments,
                scan_sensitive=True,
            )
        return IngestionProcessResult(
            success=bool(success),
            message=str(message or ""),
            report=dict(report or {}),
        )


__all__ = [
    "IngestionPipelineProcessor",
    "LegacyDocumentClassifier",
    "RepositoryIngestionJobStore",
]
