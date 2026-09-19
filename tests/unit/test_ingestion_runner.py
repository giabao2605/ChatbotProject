from __future__ import annotations

from dataclasses import dataclass

import pytest

from mech_chatbot.application.ingestion_runner import (
    IngestionJob,
    IngestionProcessResult,
    IngestionProgressEvent,
    IngestionResult,
    IngestionRunner,
)


pytestmark = pytest.mark.unit


def _job() -> IngestionJob:
    return IngestionJob(
        job_id=17,
        file_path="C:/incoming/manual.pdf",
        file_name="manual.pdf",
        owner_department="quality",
        shared_departments=("QA", "quality"),
        domain="mechanical",
        security_level="internal",
        process_stage="assembly",
        site="HCM",
    )


class FakeStore:
    def __init__(self, *, report_saved: bool = True) -> None:
        self.report_saved = report_saved
        self.classifications: list[dict[str, object]] = []
        self.progress: list[IngestionProgressEvent] = []
        self.reports: list[dict[str, object]] = []
        self.transitions: list[tuple[str, str]] = []

    def record_classification(self, job, result):
        self.classifications.append(dict(result))

    def record_progress(self, job, event):
        self.progress.append(event)

    def save_report(self, job, report):
        self.reports.append(dict(report))
        return self.report_saved

    def mark_pending_review(self, job):
        self.transitions.append(("pending_review", ""))

    def mark_failed(self, job, message):
        self.transitions.append(("failed", message))

    def mark_waiting_quota(self, job, message):
        self.transitions.append(("waiting_quota", message))


@dataclass
class FakeClassifier:
    result: dict[str, object] | None = None
    error: Exception | None = None

    def classify(self, job):
        if self.error is not None:
            raise self.error
        return self.result or {"confidence": 0.91, "detected_action": "new_document"}


class FakeProcessor:
    def __init__(self, result=None, error: Exception | None = None) -> None:
        self.result = result
        self.error = error
        self.calls = 0

    def process(self, job, progress):
        self.calls += 1
        if self.error is not None:
            raise self.error
        progress(IngestionProgressEvent("embedding", "Đang tạo embedding..."))
        return self.result


def _runner(*, store=None, classifier=None, processor=None):
    return IngestionRunner(
        job_store=store or FakeStore(),
        classifier=classifier or FakeClassifier(),
        processor=processor
        or FakeProcessor(
            IngestionProcessResult(
                success=True,
                message="ingested",
                report={"quality_status": "ready_for_review", "quality_score": 0.9},
            )
        ),
    )


def test_runner_persists_classification_progress_report_and_pending_review() -> None:
    store = FakeStore()
    runner = _runner(store=store)

    result = runner.run(_job())

    assert isinstance(result, IngestionResult)
    assert result.outcome == "pending_review"
    assert result.reason_code is None
    assert store.classifications == [
        {"confidence": 0.91, "detected_action": "new_document"}
    ]
    assert [event.phase for event in store.progress] == [
        "classifying",
        "extracting",
        "embedding",
        "quality_check",
        "completed",
    ]
    assert store.reports[0]["quality_status"] == "ready_for_review"
    assert store.transitions == [("pending_review", "")]


def test_runner_blocks_quality_failure_and_keeps_reason_code() -> None:
    store = FakeStore()
    runner = _runner(
        store=store,
        processor=FakeProcessor(
            IngestionProcessResult(
                success=True,
                message="ingested",
                report={
                    "quality_status": "blocked",
                    "quality_reason_codes": ["low_ocr", "missing_metadata"],
                },
            )
        ),
    )

    result = runner.run(_job())

    assert result.outcome == "blocked"
    assert result.reason_code == "quality_blocked"
    assert store.transitions == [
        ("failed", "Không đạt quality gate ingest (low_ocr, missing_metadata)")
    ]
    assert [event.phase for event in store.progress] == [
        "classifying",
        "extracting",
        "embedding",
        "quality_check",
    ]


def test_runner_fails_closed_when_report_persistence_fails() -> None:
    store = FakeStore(report_saved=False)
    runner = _runner(store=store)

    result = runner.run(_job())

    assert result.outcome == "failed"
    assert result.reason_code == "report_persistence_failed"
    assert store.transitions == [
        (
            "failed",
            "Không lưu được ExtractionReport/QualityScore/QualityStatus. Không cho qua quality gate.",
        )
    ]


def test_runner_routes_quota_failures_to_waiting_quota() -> None:
    store = FakeStore()
    message = "[quota_exceeded] try later"
    runner = _runner(
        store=store,
        processor=FakeProcessor(
            IngestionProcessResult(
                success=False,
                message=message,
                report={"status": "error"},
            )
        ),
    )

    result = runner.run(_job())

    assert result.outcome == "waiting_quota"
    assert result.reason_code == "quota_exceeded"
    assert store.transitions == [("waiting_quota", message)]


def test_runner_marks_unexpected_processor_error_failed() -> None:
    store = FakeStore()
    runner = _runner(store=store, processor=FakeProcessor(error=RuntimeError("parser failed")))

    result = runner.run(_job())

    assert result.outcome == "failed"
    assert result.reason_code == "unexpected_error"
    assert store.transitions == [("failed", "parser failed")]


def test_runner_routes_unexpected_quota_exception_to_waiting_quota() -> None:
    store = FakeStore()
    message = "resource_exhausted: quota exceeded"
    runner = _runner(
        store=store,
        processor=FakeProcessor(error=RuntimeError(message)),
    )

    result = runner.run(_job())

    assert result.outcome == "waiting_quota"
    assert result.reason_code == "quota_exceeded"
    assert store.transitions == [("waiting_quota", message)]


def test_runner_continues_when_optional_classification_fails(caplog) -> None:
    store = FakeStore()
    runner = _runner(
        store=store,
        classifier=FakeClassifier(error=RuntimeError("classifier unavailable")),
    )

    result = runner.run(_job())

    assert result.outcome == "pending_review"
    assert store.classifications == []
    assert store.transitions == [("pending_review", "")]
    assert "Ingestion classification failed" in caplog.text


def test_runner_persists_non_quota_failure_report_and_marks_failed() -> None:
    store = FakeStore()
    runner = _runner(
        store=store,
        processor=FakeProcessor(
            IngestionProcessResult(
                success=False,
                message="malformed document",
                report={"status": "error"},
            )
        ),
    )

    result = runner.run(_job())

    assert result.outcome == "failed"
    assert result.reason_code == "ingestion_failed"
    assert store.reports == [{"status": "error"}]
    assert store.transitions == [("failed", "malformed document")]


def test_runner_keeps_blocked_outcome_after_pipeline_rollback() -> None:
    store = FakeStore()
    runner = _runner(
        store=store,
        processor=FakeProcessor(
            IngestionProcessResult(
                success=False,
                message="rolled back",
                report={
                    "status": "error",
                    "quality_status": "blocked",
                    "quality_reason_codes": ["no_chunks"],
                },
            )
        ),
    )

    result = runner.run(_job())

    assert result.outcome == "blocked"
    assert result.reason_code == "quality_blocked"
    assert store.transitions == [
        ("failed", "Không đạt quality gate ingest (no_chunks)")
    ]


def test_runner_fails_closed_when_failure_report_cannot_be_saved() -> None:
    store = FakeStore(report_saved=False)
    runner = _runner(
        store=store,
        processor=FakeProcessor(
            IngestionProcessResult(
                success=False,
                message="malformed document",
                report={"status": "error"},
            )
        ),
    )

    result = runner.run(_job())

    assert result.reason_code == "report_persistence_failed"
    assert store.transitions[0][0] == "failed"


def test_runner_rejects_unknown_quality_status_with_legacy_message() -> None:
    store = FakeStore()
    runner = _runner(
        store=store,
        processor=FakeProcessor(
            IngestionProcessResult(success=True, message="ingested", report={})
        ),
    )

    result = runner.run(_job())

    assert result.reason_code == "invalid_quality_status"
    assert result.message == (
        "ingested (Failed quality gate: khong xac dinh duoc noi dung)"
    )


def test_runner_blocks_without_inventing_unknown_reason() -> None:
    store = FakeStore()
    runner = _runner(
        store=store,
        processor=FakeProcessor(
            IngestionProcessResult(
                success=True,
                message="ingested",
                report={"quality_status": "blocked"},
            )
        ),
    )

    result = runner.run(_job())

    assert result.message == "Không đạt quality gate ingest"


def test_ingestion_job_normalizes_legacy_string_departments() -> None:
    job = IngestionJob(
        job_id=17,
        file_path="manual.pdf",
        file_name="manual.pdf",
        owner_department="quality",
        shared_departments=" QA, quality, QA ",  # type: ignore[arg-type]
        domain=None,
        security_level=None,
        process_stage=None,
        site=None,
    )

    assert job.shared_departments == ("QA", "quality")


@pytest.mark.parametrize(
    "overrides",
    [
        {"job_id": 0},
        {"file_path": ""},
        {"file_name": ""},
        {"owner_department": ""},
    ],
)
def test_ingestion_job_rejects_missing_required_identity(overrides) -> None:
    values = {
        "job_id": 17,
        "file_path": "manual.pdf",
        "file_name": "manual.pdf",
        "owner_department": "quality",
        "shared_departments": (),
        "domain": None,
        "security_level": None,
        "process_stage": None,
        "site": None,
        **overrides,
    }

    with pytest.raises(ValueError):
        IngestionJob(**values)
