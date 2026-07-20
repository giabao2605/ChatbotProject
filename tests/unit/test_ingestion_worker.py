from __future__ import annotations

from contextlib import nullcontext
import sys
from types import ModuleType
from unittest.mock import MagicMock, call

import pytest

from mech_chatbot.db import repository
from mech_chatbot.workers import ingestion_worker


class _StopWorker(BaseException):
    """Stop the intentional infinite worker loop without entering its error path."""


class _RecordingConnection:
    def __init__(self) -> None:
        self.calls: list[tuple[object, dict[str, object]]] = []

    def execute(self, statement: object, parameters: dict[str, object]) -> None:
        self.calls.append((statement, parameters))


class _FakeEngine:
    def __init__(self, connection: _RecordingConnection) -> None:
        self._connection = connection

    def begin(self):
        connection = self._connection

        class _Transaction:
            def __enter__(self):
                return connection

            def __exit__(self, exc_type, exc, traceback):
                return False

        return _Transaction()


@pytest.fixture
def worker_boundaries(monkeypatch: pytest.MonkeyPatch) -> dict[str, MagicMock]:
    monkeypatch.setenv("PUBLICATION_RECONCILE_INTERVAL_SECONDS", "15")
    monkeypatch.setenv("SERVING_RECONCILE_INTERVAL_SECONDS", "600")
    monkeypatch.setenv("SERVING_RECONCILE_BATCH_SIZE", "500")
    boundaries = {
        "get_pending_job": MagicMock(),
        "update_ingestion_job": MagicMock(),
        "mark_job_failed": MagicMock(),
        "mark_job_waiting_quota": MagicMock(),
        "write_audit_log": MagicMock(),
        "update_ingestion_report": MagicMock(return_value=True),
        "learn_new_file": MagicMock(),
        "classify_document": MagicMock(
            return_value={
                "confidence": 0.91,
                "detected_action": "new_document",
            }
        ),
    }
    for name, boundary in boundaries.items():
        if name != "classify_document":
            monkeypatch.setattr(ingestion_worker, name, boundary)

    classifier_module = ModuleType("mech_chatbot.ingestion.document_classifier")
    classifier_module.classify_document = boundaries["classify_document"]
    monkeypatch.setitem(
        sys.modules,
        "mech_chatbot.ingestion.document_classifier",
        classifier_module,
    )

    connection = _RecordingConnection()
    monkeypatch.setattr("mech_chatbot.db.repository.engine", _FakeEngine(connection))
    monkeypatch.setattr(ingestion_worker.time, "monotonic", lambda: 0.0)
    monkeypatch.setattr(
        ingestion_worker,
        "external_processing_context",
        lambda *args, **kwargs: nullcontext(),
    )
    boundaries["classification_connection"] = connection  # type: ignore[assignment]
    return boundaries


def _job(**overrides: object) -> dict[str, object]:
    return {
        "job_id": 17,
        "ten_file": "manual.pdf",
        "file_path": "C:/incoming/manual.pdf",
        "thu_muc": "quality",
        "domain": "mechanical",
        "security_level": "internal",
        "cong_doan": "assembly",
        "site": "HCM",
        "phong_ban": "QA",
        **overrides,
    }


def test_run_worker_waits_when_no_job_exists(
    monkeypatch: pytest.MonkeyPatch,
    worker_boundaries: dict[str, MagicMock],
) -> None:
    worker_boundaries["get_pending_job"].return_value = None
    sleep_calls: list[int] = []

    def stop_on_sleep(seconds: int) -> None:
        sleep_calls.append(seconds)
        raise _StopWorker

    monkeypatch.setattr(ingestion_worker.time, "sleep", stop_on_sleep)

    with pytest.raises(_StopWorker):
        ingestion_worker.run_worker()

    assert sleep_calls == [5]
    worker_boundaries["learn_new_file"].assert_not_called()


@pytest.mark.parametrize("reconcile_fails", [False, True])
def test_run_worker_reconciles_publication_and_serving_before_polling(
    monkeypatch: pytest.MonkeyPatch,
    worker_boundaries: dict[str, MagicMock],
    reconcile_fails: bool,
) -> None:
    publication = MagicMock(return_value={"processed": 2})
    serving = MagicMock(return_value={"failed_doc_ids": ["doc-9"]})
    if reconcile_fails:
        publication.side_effect = RuntimeError("publication unavailable")
        serving.side_effect = RuntimeError("serving unavailable")
    monkeypatch.setattr(repository, "reconcile_publications", publication)
    monkeypatch.setattr(repository, "reconcile_serving_state", serving)
    monkeypatch.setattr(ingestion_worker.time, "monotonic", lambda: 1_000.0)
    worker_boundaries["get_pending_job"].side_effect = _StopWorker

    with pytest.raises(_StopWorker):
        ingestion_worker.run_worker()

    publication.assert_called_once_with(limit=10)
    serving.assert_called_once_with(
        limit=500,
        worker_id="ingestion-worker-serving-reconciler",
    )
    worker_boundaries["learn_new_file"].assert_not_called()


def test_run_worker_completes_ready_for_review_job(
    worker_boundaries: dict[str, MagicMock],
) -> None:
    report = {"quality_status": "ready_for_review"}
    worker_boundaries["get_pending_job"].side_effect = [_job(), _StopWorker]

    def successful_ingestion(**kwargs):
        kwargs["progress_callback"]("__STATUS__:embedding")
        return True, "ingested", report

    worker_boundaries["learn_new_file"].side_effect = successful_ingestion

    with pytest.raises(_StopWorker):
        ingestion_worker.run_worker()

    worker_boundaries["learn_new_file"].assert_called_once()
    ingestion_call = worker_boundaries["learn_new_file"].call_args.kwargs
    assert ingestion_call == {
        "file_path": "C:/incoming/manual.pdf",
        "ten_file": "manual.pdf",
        "thu_muc": "quality",
        "progress_callback": ingestion_call["progress_callback"],
        "domain_override": "mechanical",
        "security_override": "internal",
        "cong_doan_override": "assembly",
        "site_override": "HCM",
        "phong_ban_override": "QA",
        "scan_sensitive": True,
    }
    assert worker_boundaries["update_ingestion_job"].call_args_list == [
        call(17, status="extracting", error_message="Đang bóc tách nội dung..."),
        call(17, status="embedding", error_message="Đang tạo embedding..."),
        call(17, status="pending_review", error_message=""),
    ]
    worker_boundaries["update_ingestion_report"].assert_called_once_with(17, report)
    worker_boundaries["mark_job_failed"].assert_not_called()
    worker_boundaries["write_audit_log"].assert_called_once()

    connection = worker_boundaries["classification_connection"]
    assert connection.calls[0][1] == {
        "j": '{"confidence": 0.91, "detected_action": "new_document"}',
        "c": 0.91,
        "a": "new_document",
        "id": 17,
    }


@pytest.mark.parametrize(
    ("message", "expected_boundary"),
    [
        ("[quota_exceeded] try later", "mark_job_waiting_quota"),
        ("malformed document", "mark_job_failed"),
    ],
)
def test_run_worker_finalizes_failed_ingestion(
    worker_boundaries: dict[str, MagicMock],
    message: str,
    expected_boundary: str,
) -> None:
    report = {"quality_status": "blocked"}
    worker_boundaries["get_pending_job"].side_effect = [_job(), _StopWorker]
    worker_boundaries["learn_new_file"].return_value = False, message, report

    with pytest.raises(_StopWorker):
        ingestion_worker.run_worker()

    worker_boundaries["update_ingestion_report"].assert_called_once_with(17, report)
    worker_boundaries[expected_boundary].assert_called_once_with(
        17,
        error_message=message,
    )
    other_boundary = (
        "mark_job_failed"
        if expected_boundary == "mark_job_waiting_quota"
        else "mark_job_waiting_quota"
    )
    worker_boundaries[other_boundary].assert_not_called()


def test_run_worker_fails_closed_when_report_cannot_be_saved(
    worker_boundaries: dict[str, MagicMock],
) -> None:
    report = {"quality_status": "ready_for_review"}
    worker_boundaries["get_pending_job"].side_effect = [_job(), _StopWorker]
    worker_boundaries["learn_new_file"].return_value = True, "ingested", report
    worker_boundaries["update_ingestion_report"].return_value = False

    with pytest.raises(_StopWorker):
        ingestion_worker.run_worker()

    worker_boundaries["mark_job_failed"].assert_called_once_with(
        17,
        "Không lưu được ExtractionReport/QualityScore/QualityStatus. Không cho qua quality gate.",
    )
    worker_boundaries["update_ingestion_job"].assert_called_once_with(
        17,
        status="extracting",
        error_message="Đang bóc tách nội dung...",
    )


def test_run_worker_continues_when_optional_classification_fails(
    worker_boundaries: dict[str, MagicMock],
) -> None:
    worker_boundaries["get_pending_job"].side_effect = [_job(), _StopWorker]
    worker_boundaries["classify_document"].side_effect = RuntimeError(
        "classifier unavailable"
    )
    worker_boundaries["learn_new_file"].return_value = (
        False,
        "malformed document",
        None,
    )

    with pytest.raises(_StopWorker):
        ingestion_worker.run_worker()

    worker_boundaries["write_audit_log"].assert_not_called()
    worker_boundaries["learn_new_file"].assert_called_once()
    worker_boundaries["mark_job_failed"].assert_called_once_with(
        17,
        error_message="malformed document",
    )


@pytest.mark.parametrize(
    ("report", "expected_message"),
    [
        (
            {"quality_status": "blocked", "quality_reason_codes": ["low_ocr"]},
            "Không đạt quality gate ingest (low_ocr)",
        ),
        (None, "ingested (Failed quality gate: khong xac dinh duoc noi dung)"),
    ],
)
def test_run_worker_fails_closed_when_success_has_no_publishable_quality(
    worker_boundaries: dict[str, MagicMock],
    report: dict[str, object] | None,
    expected_message: str,
) -> None:
    worker_boundaries["get_pending_job"].side_effect = [_job(), _StopWorker]
    worker_boundaries["learn_new_file"].return_value = True, "ingested", report

    with pytest.raises(_StopWorker):
        ingestion_worker.run_worker()

    if report:
        worker_boundaries["mark_job_failed"].assert_called_once_with(
            17,
            expected_message,
        )
    else:
        worker_boundaries["mark_job_failed"].assert_called_once_with(
            17,
            error_message=expected_message,
        )


@pytest.mark.parametrize(
    ("error", "expected_boundary"),
    [
        ("resource_exhausted: quota exceeded", "mark_job_waiting_quota"),
        ("unexpected parser failure", "mark_job_failed"),
    ],
)
def test_run_worker_finalizes_unexpected_exception(
    monkeypatch: pytest.MonkeyPatch,
    worker_boundaries: dict[str, MagicMock],
    error: str,
    expected_boundary: str,
) -> None:
    worker_boundaries["get_pending_job"].return_value = _job()
    worker_boundaries["learn_new_file"].side_effect = RuntimeError(error)
    sleep_calls: list[int] = []

    def stop_after_error(seconds: int) -> None:
        sleep_calls.append(seconds)
        raise _StopWorker

    monkeypatch.setattr(ingestion_worker.time, "sleep", stop_after_error)

    with pytest.raises(_StopWorker):
        ingestion_worker.run_worker()

    worker_boundaries[expected_boundary].assert_called_once_with(
        17,
        error_message=error,
    )
    assert sleep_calls == [10]
