"""Application-owned ingestion lifecycle.

The runner owns the decisions that happen after a worker has claimed a job.
Infrastructure concerns (SQL state, audit, and the legacy PDF pipeline) are
deliberately expressed as ports so the lifecycle can be replayed in tests
without a database or an external model provider.
"""

from __future__ import annotations

import logging
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal, Protocol

from mech_chatbot.domain.ingestion_progress import (
    IngestionPhase,
    IngestionProgressEvent,
)


logger = logging.getLogger(__name__)

IngestionOutcome = Literal["pending_review", "blocked", "waiting_quota", "failed"]


def _normalize_shared_departments(value: object) -> tuple[str, ...]:
    """Normalize SQL ``PhongBan`` values without changing their order."""

    if value is None:
        return ()
    if isinstance(value, str):
        values: Sequence[object] = value.split(",")
    elif isinstance(value, Sequence):
        values = value
    else:
        values = (value,)

    result: list[str] = []
    seen: set[str] = set()
    for item in values:
        text = str(item).strip()
        if text and text not in seen:
            seen.add(text)
            result.append(text)
    return tuple(result)


@dataclass(frozen=True, slots=True)
class IngestionJob:
    job_id: int
    file_path: Path
    file_name: str
    owner_department: str
    shared_departments: tuple[str, ...]
    domain: str | None
    security_level: str | None
    process_stage: str | None
    site: str | None

    def __post_init__(self) -> None:
        if self.job_id <= 0:
            raise ValueError("job_id must be positive")
        if not str(self.file_path).strip():
            raise ValueError("file_path is required")
        if not str(self.file_name).strip():
            raise ValueError("file_name is required")
        if not str(self.owner_department).strip():
            raise ValueError("owner_department is required")
        # The SQL adapter historically returned strings. Normalizing at this
        # boundary keeps the application contract typed while preserving that
        # compatibility for callers that still construct jobs from dictionaries.
        object.__setattr__(self, "file_path", Path(self.file_path))
        object.__setattr__(
            self,
            "shared_departments",
            _normalize_shared_departments(self.shared_departments),
        )


@dataclass(frozen=True, slots=True)
class IngestionProcessResult:
    success: bool
    message: str
    report: Mapping[str, Any]


@dataclass(frozen=True, slots=True)
class IngestionResult:
    outcome: IngestionOutcome
    report: Mapping[str, Any]
    reason_code: str | None
    message: str


class IngestionJobStore(Protocol):
    def record_classification(self, job: IngestionJob, result: Mapping[str, Any]) -> None: ...

    def record_progress(self, job: IngestionJob, event: IngestionProgressEvent) -> None: ...

    def save_report(self, job: IngestionJob, report: Mapping[str, Any]) -> bool: ...

    def mark_pending_review(self, job: IngestionJob) -> None: ...

    def mark_failed(self, job: IngestionJob, message: str) -> None: ...

    def mark_waiting_quota(self, job: IngestionJob, message: str) -> None: ...


class IngestionClassifier(Protocol):
    def classify(self, job: IngestionJob) -> Mapping[str, Any]: ...


class IngestionProcessor(Protocol):
    def process(
        self,
        job: IngestionJob,
        progress: "ProgressCallback",
    ) -> IngestionProcessResult: ...


ProgressCallback = Callable[[IngestionProgressEvent], None]


def _mapping(value: Mapping[str, Any] | None) -> dict[str, Any]:
    return dict(value or {})


def _quota_failure(message: str) -> bool:
    lowered = str(message).lower()
    return any(
        marker in lowered
        for marker in (
            "quota_exceeded",
            "quota exceeded",
            "resource_exhausted",
            "free_tier_requests",
        )
    )


def _quality_block_message(report: Mapping[str, Any]) -> str:
    reasons = report.get("quality_reason_codes") or report.get("reason_codes") or []
    if isinstance(reasons, str):
        reasons = [reasons]
    values = [str(reason) for reason in reasons if str(reason).strip()]
    if not values:
        return "Không đạt quality gate ingest"
    return f"Không đạt quality gate ingest ({', '.join(values)})"


class IngestionRunner:
    """Run the post-claim ingestion lifecycle through explicit ports."""

    def __init__(
        self,
        *,
        job_store: IngestionJobStore,
        classifier: IngestionClassifier | None,
        processor: IngestionProcessor,
    ) -> None:
        self._job_store = job_store
        self._classifier = classifier
        self._processor = processor

    def run(self, job: IngestionJob) -> IngestionResult:
        self._emit(job, "classifying", "Đang phân loại tài liệu...")
        self._classify_optional(job)
        self._emit(job, "extracting", "Đang trích xuất nội dung...")

        try:
            process_result = self._processor.process(job, lambda event: self._on_progress(job, event))
        except Exception as exc:  # noqa: BLE001 - lifecycle must reconcile every job
            message = str(exc) or exc.__class__.__name__
            if _quota_failure(message):
                self._job_store.mark_waiting_quota(job, message)
                return IngestionResult(
                    "waiting_quota",
                    {},
                    "quota_exceeded",
                    message,
                )
            self._job_store.mark_failed(job, message)
            return IngestionResult("failed", {}, "unexpected_error", message)

        report = _mapping(process_result.report)
        if not process_result.success:
            return self._finish_failure(job, report, process_result.message)

        self._emit(job, "quality_check", "Đang kiểm tra chất lượng...")
        if report and not self._job_store.save_report(job, report):
            message = (
                "Không lưu được ExtractionReport/QualityScore/QualityStatus. "
                "Không cho qua quality gate."
            )
            self._job_store.mark_failed(job, message)
            return IngestionResult("failed", report, "report_persistence_failed", message)

        quality_status = str(report.get("quality_status") or "").lower()
        if quality_status in {"ready_for_review", "needs_review"}:
            self._emit(job, "completed", "Hoàn tất ingest, chờ review.")
            self._job_store.mark_pending_review(job)
            return IngestionResult("pending_review", report, None, process_result.message)
        if quality_status == "blocked":
            message = _quality_block_message(report)
            self._job_store.mark_failed(job, message)
            return IngestionResult("blocked", report, "quality_blocked", message)

        message = (
            f"{process_result.message} (Failed quality gate: khong xac dinh duoc noi dung)"
        )
        self._job_store.mark_failed(job, message)
        return IngestionResult("failed", report, "invalid_quality_status", message)

    def _classify_optional(self, job: IngestionJob) -> None:
        if self._classifier is None:
            return
        try:
            result = self._classifier.classify(job)
            self._job_store.record_classification(job, result)
        except Exception:  # noqa: BLE001 - classification is best-effort by contract
            logger.warning("Ingestion classification failed for job %s", job.job_id, exc_info=True)

    def _on_progress(self, job: IngestionJob, event: IngestionProgressEvent) -> None:
        if event.phase not in {"classifying", "extracting", "embedding", "quality_check", "completed"}:
            raise ValueError(f"unsupported ingestion phase: {event.phase}")
        self._job_store.record_progress(job, event)

    def _emit(self, job: IngestionJob, phase: IngestionPhase, message: str) -> None:
        self._on_progress(job, IngestionProgressEvent(phase, message))

    def _finish_failure(
        self,
        job: IngestionJob,
        report: Mapping[str, Any],
        message: str,
    ) -> IngestionResult:
        if report and not self._job_store.save_report(job, report):
            persistence_message = (
                "Không lưu được ExtractionReport/QualityScore/QualityStatus. "
                "Không cho qua quality gate."
            )
            self._job_store.mark_failed(job, persistence_message)
            return IngestionResult(
                "failed",
                report,
                "report_persistence_failed",
                persistence_message,
            )
        if _quota_failure(message):
            self._job_store.mark_waiting_quota(job, message)
            return IngestionResult("waiting_quota", report, "quota_exceeded", message)
        if str(report.get("quality_status") or "").lower() == "blocked":
            block_message = _quality_block_message(report)
            self._job_store.mark_failed(job, block_message)
            return IngestionResult(
                "blocked",
                report,
                "quality_blocked",
                block_message,
            )
        self._job_store.mark_failed(job, message)
        return IngestionResult("failed", report, "ingestion_failed", message)


__all__ = [
    "IngestionClassifier",
    "IngestionJob",
    "IngestionJobStore",
    "IngestionProcessResult",
    "IngestionProcessor",
    "IngestionProgressEvent",
    "IngestionResult",
    "IngestionRunner",
]
