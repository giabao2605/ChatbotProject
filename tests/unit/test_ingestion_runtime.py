from __future__ import annotations

from contextlib import nullcontext

from mech_chatbot.adapters.ingestion_runtime import (
    IngestionPipelineProcessor,
    LegacyDocumentClassifier,
    RepositoryIngestionJobStore,
)
from mech_chatbot.application.ingestion_runner import IngestionProgressEvent


def _store(*, update_job=None):
    calls: list[tuple] = []
    update = update_job or (lambda *args, **kwargs: calls.append((args, kwargs)))
    store = RepositoryIngestionJobStore(
        get_pending_job=lambda worker_id: {
            "job_id": 9,
            "file_path": "C:/raw/manual.pdf",
            "ten_file": "manual.pdf",
            "thu_muc": "quality",
            "phong_ban": " QA, quality, QA ",
            "domain": "mechanical",
            "security_level": "internal",
            "cong_doan": "assembly",
            "site": "HCM",
        },
        update_ingestion_job=update,
        update_ingestion_report=lambda *args, **kwargs: True,
        mark_job_failed=lambda *args, **kwargs: calls.append(("failed", args, kwargs)),
        mark_job_waiting_quota=lambda *args, **kwargs: calls.append(("quota", args, kwargs)),
        write_audit_log=lambda *args, **kwargs: calls.append(("audit", args, kwargs)),
    )
    return store, calls


def test_job_store_claim_normalizes_department_collection() -> None:
    store, _ = _store()

    job = store.claim_next("worker-9")

    assert job is not None
    assert job.job_id == 9
    assert job.shared_departments == ("QA", "quality")
    assert job.file_path.as_posix().endswith("/raw/manual.pdf")


def test_job_store_maps_typed_progress_to_legacy_status() -> None:
    store, calls = _store()
    job = store.claim_next("worker-9")

    assert job is not None
    store.record_progress(job, IngestionProgressEvent("quality_check", "Đang kiểm tra chất lượng..."))

    assert calls[0][0] == (9,)
    assert calls[0][1] == {
        "status": "embedding",
        "error_message": "Đang kiểm tra chất lượng...",
    }


def test_pipeline_processor_forwards_typed_embedding_event() -> None:
    events: list[IngestionProgressEvent] = []

    def learn(**kwargs):
        kwargs["progress_callback"](IngestionProgressEvent("embedding", "Đang tạo embedding..."))
        return True, "done", {"quality_status": "ready_for_review"}

    processor = IngestionPipelineProcessor(learn, processing_context=lambda *args: nullcontext())
    result = processor.process(
        _store()[0].claim_next("worker-9"),  # type: ignore[arg-type]
        events.append,
    )

    assert result.success is True
    assert events == [IngestionProgressEvent("embedding", "Đang tạo embedding...")]


class RecordingConnection:
    def __init__(self) -> None:
        self.calls = []

    def execute(self, statement, parameters) -> None:
        self.calls.append((str(statement), parameters))


class RecordingEngine:
    def __init__(self, connection: RecordingConnection) -> None:
        self.connection = connection

    def begin(self):
        connection = self.connection

        class Transaction:
            def __enter__(self):
                return connection

            def __exit__(self, exc_type, exc, traceback):
                return False

        return Transaction()


def test_job_store_persists_classification_and_progress_with_parameterized_sql() -> None:
    connection = RecordingConnection()
    audit_calls = []
    store = RepositoryIngestionJobStore(
        get_pending_job=lambda _: {
            "job_id": 9,
            "file_path": "manual.pdf",
            "ten_file": "manual.pdf",
            "thu_muc": "quality",
            "phong_ban": ["QA", "quality", "QA"],
        },
        update_ingestion_job=lambda *args, **kwargs: None,
        update_ingestion_report=lambda *args, **kwargs: True,
        mark_job_failed=lambda *args, **kwargs: None,
        mark_job_waiting_quota=lambda *args, **kwargs: None,
        write_audit_log=lambda *args, **kwargs: audit_calls.append((args, kwargs)),
        engine=RecordingEngine(connection),
    )
    job = store.claim_next("worker-9")

    assert job is not None
    assert job.shared_departments == ("QA", "quality")
    store.record_classification(
        job,
        {"confidence": 0.87, "detected_action": "new_version"},
    )
    store.record_progress(job, IngestionProgressEvent("embedding", "Embedding"))

    assert connection.calls[0][1]["id"] == 9
    assert connection.calls[0][1]["c"] == 0.87
    assert connection.calls[0][1]["a"] == "new_version"
    assert connection.calls[1][1] == {
        "s": "embedding",
        "p": 70,
        "e": "Embedding",
        "id": 9,
    }
    assert len(audit_calls) == 1


def test_job_store_owns_report_and_final_transitions() -> None:
    calls = []
    store = RepositoryIngestionJobStore(
        get_pending_job=lambda _: {
            "job_id": 9,
            "file_path": "manual.pdf",
            "ten_file": "manual.pdf",
            "thu_muc": "quality",
            "phong_ban": None,
        },
        update_ingestion_job=lambda *args, **kwargs: calls.append(("update", args, kwargs)),
        update_ingestion_report=lambda *args, **kwargs: calls.append(("report", args, kwargs)) or True,
        mark_job_failed=lambda *args, **kwargs: calls.append(("failed", args, kwargs)),
        mark_job_waiting_quota=lambda *args, **kwargs: calls.append(("quota", args, kwargs)),
        write_audit_log=lambda *args, **kwargs: calls.append(("audit", args, kwargs)),
    )
    job = store.claim_next("worker-9")

    assert job is not None
    assert job.shared_departments == ()
    store.record_classification(job, {"confidence": 0.5})
    assert store.save_report(job, {"quality_status": "ready_for_review"}) is True
    store.mark_pending_review(job)
    store.mark_failed(job, "failed")
    store.mark_waiting_quota(job, "quota")

    assert [item[0] for item in calls] == [
        "update",
        "audit",
        "report",
        "update",
        "failed",
        "quota",
    ]


def test_job_store_reconciles_unexpected_failures_by_failure_family() -> None:
    store, calls = _store()
    job = store.claim_next("worker-9")

    assert job is not None
    store.reconcile_unexpected_failure(
        job,
        RuntimeError("resource_exhausted: quota exceeded"),
    )
    store.reconcile_unexpected_failure(job, RuntimeError("parser failed"))

    assert calls[0][0] == "quota"
    assert calls[1][0] == "failed"


def test_document_classifier_forwards_job_source_fields() -> None:
    calls = []
    classifier = LegacyDocumentClassifier(
        lambda *args, **kwargs: calls.append((args, kwargs)) or {"confidence": 0.7},
        processing_context=lambda *args: nullcontext(),
    )
    job = _store()[0].claim_next("worker-9")

    result = classifier.classify(job)  # type: ignore[arg-type]

    assert result == {"confidence": 0.7}
    assert calls[0][0] == (str(job.file_path), "manual.pdf")  # type: ignore[union-attr]
    assert calls[0][1] == {"thu_muc": "quality", "allow_external": False}


def test_document_classifier_allows_external_only_for_active_explicit_policy() -> None:
    calls = []
    classifier = LegacyDocumentClassifier(
        lambda *args, **kwargs: calls.append((args, kwargs)) or {"confidence": 0.7},
        processing_context=lambda *args: nullcontext(),
        load_governance=lambda _department: {
            "external_processing_policy": "all_external",
            "is_active": True,
        },
    )
    job = _store()[0].claim_next("worker-9")

    classifier.classify(job)  # type: ignore[arg-type]

    assert calls[0][1]["allow_external"] is True


def test_document_classifier_governance_lookup_failure_is_fail_closed() -> None:
    calls = []

    def unavailable(_department):
        raise RuntimeError("governance database unavailable")

    classifier = LegacyDocumentClassifier(
        lambda *args, **kwargs: calls.append((args, kwargs)) or {"confidence": 0.5},
        processing_context=lambda *args: nullcontext(),
        load_governance=unavailable,
    )
    job = _store()[0].claim_next("worker-9")

    classifier.classify(job)  # type: ignore[arg-type]

    assert calls[0][1]["allow_external"] is False
