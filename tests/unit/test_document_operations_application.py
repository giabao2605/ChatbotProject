"""Application contracts for Phase 2 document operations."""

from __future__ import annotations

from dataclasses import dataclass

import pytest

from mech_chatbot.application import document_upload as upload_module
from mech_chatbot.application.document_review import (
    PublicationCoordinator,
    PublicationOutcome,
    ReviewDocuments,
    ReviewDocumentsCommand,
    ReviewItem,
)
from mech_chatbot.application.document_upload import (
    DocumentActor,
    DocumentUpload,
    StoredUpload,
    UploadDocumentCommand,
    UploadRejected,
)


pytestmark = pytest.mark.unit


def _actor(**overrides) -> DocumentActor:
    values = {
        "user_id": 7,
        "username": "alice",
        "roles": ("uploader",),
        "allowed_departments": ("CoKhi",),
    }
    values.update(overrides)
    return DocumentActor(**values)


def _command(**overrides) -> UploadDocumentCommand:
    values = {
        "file_name": "bom.pdf",
        "content": b"pdf",
        "owner_department": "CoKhi",
        "shared_departments": ("Shared", "CoKhi"),
        "domain": "Mechanical",
        "security_level": "internal",
        "process_stage": None,
        "site": "HN",
        "upload_metadata": {"owner": "qa"},
    }
    values.update(overrides)
    return UploadDocumentCommand(**values)


class Storage:
    def __init__(
        self,
        *,
        cleanup=True,
        cleanup_error: Exception | None = None,
        error: Exception | None = None,
    ):
        self.cleanup = cleanup
        self.cleanup_error = cleanup_error
        self.error = error
        self.stored = []
        self.deleted = []

    def store(self, *, file_name, content, owner_department):
        if self.error:
            raise self.error
        self.stored.append((file_name, content, owner_department))
        return StoredUpload(file_name, f"C:/staged/{file_name}")

    def delete(self, stored_path):
        self.deleted.append(stored_path)
        if self.cleanup_error:
            raise self.cleanup_error
        return self.cleanup


class Jobs:
    def __init__(self, results=(91,)):
        self.results = iter(results)
        self.calls = []

    def create_job(self, **kwargs):
        self.calls.append(kwargs)
        return next(self.results)


def test_single_upload_validates_then_enqueues_normalized_departments() -> None:
    storage = Storage()
    jobs = Jobs()
    upload = DocumentUpload(storage=storage, job_store=jobs)

    receipt = upload.enqueue(_command(), _actor())

    assert receipt.job_id == 91
    assert receipt.file_name == "bom.pdf"
    assert jobs.calls[0]["shared_departments"] == ("CoKhi", "Shared")
    assert jobs.calls[0]["uploaded_by"] == "alice"


@pytest.mark.parametrize(
    ("command", "code"),
    [
        (_command(file_name="bom.exe"), "invalid_extension"),
        (_command(content=b""), "empty_file"),
        (_command(owner_department="Finance"), "unauthorized"),
    ],
)
def test_single_upload_rejects_boundary_input_without_writing(command, code) -> None:
    storage = Storage()
    upload = DocumentUpload(storage=storage, job_store=Jobs())

    with pytest.raises(UploadRejected) as raised:
        upload.enqueue(command, _actor())

    assert raised.value.failure.code == code
    assert storage.stored == []


def test_upload_preflight_rejects_department_and_extension_before_reading_bytes() -> None:
    upload = DocumentUpload(storage=Storage(), job_store=Jobs())

    unauthorized = upload.preflight(
        file_name="bom.pdf",
        owner_department="Finance",
        actor=_actor(),
    )
    invalid = upload.preflight(
        file_name="bom.exe",
        owner_department="CoKhi",
        actor=_actor(),
    )

    assert unauthorized is not None and unauthorized.code == "unauthorized"
    assert invalid is not None and invalid.code == "invalid_extension"


def test_single_upload_rejects_oversized_content(monkeypatch) -> None:
    monkeypatch.setattr(upload_module, "MAX_UPLOAD_BYTES", 2)

    with pytest.raises(UploadRejected) as raised:
        DocumentUpload(storage=Storage(), job_store=Jobs()).enqueue(
            _command(content=b"123"),
            _actor(),
        )

    assert raised.value.failure.code == "file_too_large"


@pytest.mark.parametrize(
    ("cleanup", "code"),
    [(True, "enqueue_failed"), (False, "cleanup_failed")],
)
def test_failed_enqueue_cleans_staged_file_and_reports_cleanup_outcome(cleanup, code) -> None:
    storage = Storage(cleanup=cleanup)
    upload = DocumentUpload(storage=storage, job_store=Jobs(results=(None,)))

    with pytest.raises(UploadRejected) as raised:
        upload.enqueue(_command(), _actor())

    assert raised.value.failure.code == code
    assert storage.deleted == ["C:/staged/bom.pdf"]


def test_cleanup_exception_is_reported_as_cleanup_failed() -> None:
    storage = Storage(cleanup_error=OSError("locked"))
    upload = DocumentUpload(storage=storage, job_store=Jobs(results=(None,)))

    with pytest.raises(UploadRejected) as raised:
        upload.enqueue(_command(), _actor())

    assert raised.value.failure.code == "cleanup_failed"
    assert raised.value.failure.detail == {"stored_path": "C:/staged/bom.pdf"}


def test_storage_failure_is_typed_and_batch_keeps_earlier_success() -> None:
    class PerFileStorage(Storage):
        def store(self, *, file_name, content, owner_department):
            if file_name == "broken.pdf":
                raise OSError("disk unavailable")
            return super().store(
                file_name=file_name,
                content=content,
                owner_department=owner_department,
            )

    upload = DocumentUpload(storage=PerFileStorage(), job_store=Jobs(results=(91,)))

    result = upload.enqueue_batch(
        (_command(), _command(file_name="broken.pdf")),
        _actor(),
    )

    assert [receipt.job_id for receipt in result.jobs] == [91]
    assert result.errors[0].code == "storage_failed"
    assert result.created == 1
    assert result.failed == 1


def test_single_storage_failure_is_typed() -> None:
    upload = DocumentUpload(
        storage=Storage(error=OSError("disk unavailable")),
        job_store=Jobs(),
    )

    with pytest.raises(UploadRejected) as raised:
        upload.enqueue(_command(), _actor())

    assert raised.value.failure.code == "storage_failed"


def test_batch_unexpected_failure_does_not_expose_internal_exception() -> None:
    class UnexpectedUpload(DocumentUpload):
        def enqueue(self, command, actor):
            raise RuntimeError("secret internal path")

    upload = UnexpectedUpload(storage=Storage(), job_store=Jobs())

    result = upload.enqueue_batch((_command(),), _actor())

    assert result.errors[0].code == "storage_failed"
    assert result.errors[0].message == "Không xử lý được tệp: bom.pdf"
    assert "secret internal path" not in result.errors[0].message


class Publication:
    def __init__(self, outcomes):
        self.outcomes = iter(outcomes)

    def publish(self, command, actor):
        return next(self.outcomes)


class ReviewStore:
    def __init__(self):
        self.calls = []

    def reject_job(self, job_id, reason):
        self.calls.append(("reject_job", job_id, reason))
        return False

    def mark_job_rejected(self, job_id):
        self.calls.append(("mark_rejected", job_id))
        return False

    def reject_document(self, doc_id, reviewer):
        self.calls.append(("reject_doc", doc_id, reviewer))

    def delete_document(self, doc_id, reviewer):
        self.calls.append(("delete_doc", doc_id, reviewer))

    def delete_job(self, job_id):
        self.calls.append(("delete_job", job_id))


def _review(publication=()):
    store = ReviewStore()
    use_case = ReviewDocuments(
        review_store=store,
        publication=PublicationCoordinator(publication=Publication(publication)),
    )
    return use_case, store


def test_bulk_publish_preserves_published_pending_and_failed_accounting() -> None:
    use_case, _store = _review(
        (
            PublicationOutcome(True, "published", {"ok": True}),
            PublicationOutcome(True, "processing", {"ok": True, "state": "processing"}),
            PublicationOutcome(False, "failed", {"ok": False, "error": "failed"}, "failed"),
        )
    )

    result = use_case.execute(
        ReviewDocumentsCommand(
            action="publish",
            publish_mode="standalone",
            reason="",
            items=(ReviewItem(1, 11), ReviewItem(2, 12), ReviewItem(3, 13)),
        ),
        _actor(),
    )

    assert (result.updated, result.pending, result.failed) == (1, 1, 1)
    assert result.failures == (
        {"ok": True, "state": "processing"},
        {"ok": False, "error": "failed"},
    )


def test_bulk_reject_preserves_fallback_and_delete_without_ids_is_updated() -> None:
    reject, store = _review()
    rejected = reject.execute(
        ReviewDocumentsCommand("reject", "standalone", "duplicate", (ReviewItem(9, 42),)),
        _actor(),
    )
    delete, _ = _review()
    deleted = delete.execute(
        ReviewDocumentsCommand("delete", "standalone", "", (ReviewItem(None, None),)),
        _actor(),
    )

    assert rejected.updated == 1
    assert store.calls == [
        ("reject_job", 9, "duplicate"),
        ("mark_rejected", 9),
        ("reject_doc", 42, "alice"),
    ]
    assert deleted.updated == 1


@pytest.mark.parametrize(
    ("action", "expected_code"),
    [
        ("reject", "job_reject_failed"),
        ("delete", "delete_failed"),
    ],
)
def test_bulk_review_uses_locked_error_codes_for_store_failures(action, expected_code) -> None:
    class BrokenReviewStore(ReviewStore):
        def reject_job(self, job_id, reason):
            raise RuntimeError("repository unavailable")

        def delete_document(self, doc_id, reviewer):
            raise RuntimeError("repository unavailable")

    use_case = ReviewDocuments(
        review_store=BrokenReviewStore(),
        publication=PublicationCoordinator(publication=Publication(())),
    )

    result = use_case.execute(
        ReviewDocumentsCommand(
            action=action,
            publish_mode="standalone",
            reason="",
            items=(ReviewItem(9, 42),),
        ),
        _actor(),
    )

    assert result.failed == 1
    assert result.outcomes[0].code == expected_code
