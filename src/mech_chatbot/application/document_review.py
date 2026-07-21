"""Application owner for document review and publication coordination."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any, Literal, Protocol

from mech_chatbot.application.document_upload import DocumentActor

ReviewAction = Literal["publish", "reject", "delete"]
PublishMode = Literal["standalone", "new_version", "new_variant"]
OutcomeStatus = Literal["updated", "pending", "failed"]


@dataclass(frozen=True, slots=True)
class ReviewItem:
    job_id: int | None
    doc_id: int | None


@dataclass(frozen=True, slots=True)
class ReviewDocumentsCommand:
    action: ReviewAction
    publish_mode: PublishMode
    reason: str | None
    items: tuple[ReviewItem, ...]


@dataclass(frozen=True, slots=True)
class ReviewItemOutcome:
    status: OutcomeStatus
    job_id: int | None
    doc_id: int | None
    code: str | None
    message: str | None
    detail: Mapping[str, Any] | None = None


@dataclass(frozen=True, slots=True)
class BatchReviewResult:
    outcomes: tuple[ReviewItemOutcome, ...]
    updated: int
    pending: int
    failed: int

    @property
    def ok(self) -> bool:
        return self.failed == 0

    @property
    def failures(self) -> tuple[Mapping[str, Any], ...]:
        return tuple(outcome.detail for outcome in self.outcomes if outcome.detail)


@dataclass(frozen=True, slots=True)
class PublicationCommand:
    job_id: int
    doc_id: int | None
    publish_mode: PublishMode


@dataclass(frozen=True, slots=True)
class PublicationOutcome:
    ok: bool
    state: str
    payload: Mapping[str, Any]
    error: str | None = None


class PublicationPort(Protocol):
    def publish(self, command: PublicationCommand, actor: DocumentActor) -> PublicationOutcome:
        """Publish a document or publish the latest document for an ingestion job."""


class ReviewStore(Protocol):
    def reject_job(self, job_id: int, reason: str | None) -> bool:
        """Reject an ingestion job through the primary repository path."""

    def mark_job_rejected(self, job_id: int) -> Any:
        """Compatibility fallback for a rejected ingestion job."""

    def reject_document(self, doc_id: int, reviewer: str) -> Any:
        """Reject a document when a bulk reject item includes a document id."""

    def delete_document(self, doc_id: int, reviewer: str) -> Any:
        """Delete a document completely."""

    def delete_job(self, job_id: int) -> Any:
        """Delete an ingestion job."""


def normalize_publish_mode(value: str | None) -> PublishMode:
    return value if value in {"new_version", "new_variant"} else "standalone"


class PublicationCoordinator:
    """Own publication precondition handling while preserving repository contract."""

    def __init__(self, *, publication: PublicationPort) -> None:
        self._publication = publication

    def publish_job(self, command: PublicationCommand, actor: DocumentActor) -> PublicationOutcome:
        return self._publication.publish(command, actor)


class ReviewDocuments:
    """Own bulk review item orchestration and partial-result accounting."""

    def __init__(self, *, review_store: ReviewStore, publication: PublicationCoordinator) -> None:
        self._review_store = review_store
        self._publication = publication

    def execute(self, command: ReviewDocumentsCommand, actor: DocumentActor) -> BatchReviewResult:
        outcomes = tuple(self._execute_item(command, actor, item) for item in command.items)
        return BatchReviewResult(
            outcomes=outcomes,
            updated=sum(1 for outcome in outcomes if outcome.status == "updated"),
            pending=sum(1 for outcome in outcomes if outcome.status == "pending"),
            failed=sum(1 for outcome in outcomes if outcome.status == "failed"),
        )

    def _execute_item(
        self,
        command: ReviewDocumentsCommand,
        actor: DocumentActor,
        item: ReviewItem,
    ) -> ReviewItemOutcome:
        try:
            if command.action == "publish":
                return self._publish_item(command, actor, item)
            if command.action == "reject":
                return self._reject_item(command, actor, item)
            return self._delete_item(item, actor)
        except Exception as exc:
            return ReviewItemOutcome(
                status="failed",
                job_id=item.job_id,
                doc_id=item.doc_id,
                code=self._failure_code(command.action),
                message=str(exc),
            )

    @staticmethod
    def _failure_code(action: ReviewAction) -> str:
        if action == "reject":
            return "job_reject_failed"
        if action == "delete":
            return "delete_failed"
        return "publish_contract_failed"

    def _publish_item(
        self,
        command: ReviewDocumentsCommand,
        actor: DocumentActor,
        item: ReviewItem,
    ) -> ReviewItemOutcome:
        if not item.doc_id or not item.job_id:
            return ReviewItemOutcome(
                status="failed",
                job_id=item.job_id,
                doc_id=item.doc_id,
                code="missing_doc_id" if item.job_id else "missing_job_id",
                message="Thiếu DocID hoặc JobID",
            )
        result = self._publication.publish_job(
            PublicationCommand(
                job_id=item.job_id,
                doc_id=item.doc_id,
                publish_mode=command.publish_mode,
            ),
            actor,
        )
        if result.ok and result.state == "published":
            return ReviewItemOutcome("updated", item.job_id, item.doc_id, None, None)
        if result.ok:
            return ReviewItemOutcome(
                "pending",
                item.job_id,
                item.doc_id,
                "publication_pending",
                result.error,
                result.payload,
            )
        return ReviewItemOutcome(
            "failed",
            item.job_id,
            item.doc_id,
            "publish_contract_failed",
            result.error or "Publish thất bại",
            result.payload,
        )

    def _reject_item(self, command: ReviewDocumentsCommand, actor: DocumentActor, item: ReviewItem) -> ReviewItemOutcome:
        if not item.job_id:
            return ReviewItemOutcome("failed", item.job_id, item.doc_id, "missing_job_id", "Thiếu JobID")
        self._review_store.reject_job(item.job_id, command.reason) or self._review_store.mark_job_rejected(item.job_id)
        if item.doc_id:
            self._review_store.reject_document(item.doc_id, reviewer=actor.username or "System")
        return ReviewItemOutcome("updated", item.job_id, item.doc_id, None, None)

    def _delete_item(self, item: ReviewItem, actor: DocumentActor) -> ReviewItemOutcome:
        # Preserve the current odd contract: delete with neither id is a no-op
        # counted as an updated item.
        if item.doc_id:
            self._review_store.delete_document(item.doc_id, reviewer=actor.username or "System")
        if item.job_id:
            self._review_store.delete_job(item.job_id)
        return ReviewItemOutcome("updated", item.job_id, item.doc_id, None, None)
