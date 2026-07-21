"""Explicit composition root for the browser-chat application surface.

The composition module only assembles application-owned contracts. Concrete
HTTP, SQL, audit, and pilot implementations are supplied by the caller so
importing this module never creates external clients or process-wide state.
"""

from __future__ import annotations

from dataclasses import dataclass
from collections.abc import Callable, Mapping
from typing import Any

from mech_chatbot.adapters.chat_runtime import (
    HttpRagStreamAdapter,
    PilotExperimentAdapter,
    RepositoryAuditSink,
    RepositoryChatStore,
)
from mech_chatbot.adapters.document_runtime import (
    LocalUploadStorage,
    RepositoryPublicationPort,
    RepositoryReviewStore,
    RepositoryUploadJobStore,
    SqlAppSupportQueries,
    SqlDocumentLookup,
)
from mech_chatbot.adapters.protected_files import (
    FilesystemProtectedFileStorage,
    RepositoryProtectedFileAuditSink,
    SqlProtectedFileStore,
)
from mech_chatbot.application.chat_turn import (
    AuditSink,
    ChatStore,
    ChatTurnRunner,
    CitationResolver,
    PilotExperimentPort,
    RagStreamPort,
)
from mech_chatbot.application.document_review import (
    PublicationCoordinator,
    PublicationPort,
    ReviewDocuments,
    ReviewStore,
)
from mech_chatbot.application.document_upload import (
    DocumentUpload,
    StoredUpload,
    UploadJobStore,
    UploadStorage,
)
from mech_chatbot.application.protected_files import ProtectedFileResolver


class _NullUploadStorage:
    def store(self, *, file_name: str, content: bytes, owner_department: str) -> StoredUpload:
        raise RuntimeError("upload storage is not configured")

    def delete(self, stored_path: str) -> bool:
        return False


class _NullUploadJobStore:
    def create_job(self, **_kwargs: Any) -> int | None:
        return None


class _NullReviewStore:
    def reject_job(self, job_id: int, reason: str | None) -> bool:
        return False

    def mark_job_rejected(self, job_id: int) -> Any:
        return None

    def reject_document(self, doc_id: int, reviewer: str) -> Any:
        return None

    def delete_document(self, doc_id: int, reviewer: str) -> Any:
        return None

    def delete_job(self, job_id: int) -> Any:
        return None


class _NullPublicationPort:
    def publish(self, command, actor):
        raise RuntimeError("publication port is not configured")


class _NullAppSupportQueries:
    def database_ready(self) -> bool:
        return False

    def chat_image_visible_to_user(self, *, image_id: str, username: str | None) -> bool:
        return False

    def feedback_review_context(self, feedback_id: int):
        return None

    def answer_sources_for_chat_ids(self, chat_ids: tuple[int, ...]) -> list[Any]:
        return []


@dataclass(frozen=True, slots=True)
class AppRuntime:
    """Immutable dependency bundle owned by one app-server process."""

    settings: Any
    rag_stream: RagStreamPort
    chat_store: ChatStore
    audit_sink: AuditSink
    pilot_experiments: PilotExperimentPort
    citation_resolver: CitationResolver
    chat_turn_runner: ChatTurnRunner
    upload_storage: UploadStorage
    upload_job_store: UploadJobStore
    document_upload: DocumentUpload
    review_store: ReviewStore
    publication_port: PublicationPort
    publication_coordinator: PublicationCoordinator
    review_documents: ReviewDocuments
    app_support_queries: Any
    protected_file_resolver: ProtectedFileResolver


def build_app_runtime(
    existing_settings: Any,
    *,
    rag_stream: RagStreamPort,
    chat_store: ChatStore,
    audit_sink: AuditSink,
    pilot_experiments: PilotExperimentPort,
    citation_resolver: CitationResolver,
    upload_storage: UploadStorage | None = None,
    upload_job_store: UploadJobStore | None = None,
    review_store: ReviewStore | None = None,
    publication_port: PublicationPort | None = None,
    app_support_queries: Any | None = None,
    protected_file_resolver: ProtectedFileResolver | None = None,
) -> AppRuntime:
    """Build an immutable app runtime from explicitly supplied adapters.

    ``existing_settings`` is retained by identity. This factory deliberately
    does not read environment variables, resolve globals, or instantiate
    infrastructure: the process bootstrap owns those decisions and passes
    already-configured ports here.
    """

    runner = ChatTurnRunner(
        rag_stream=rag_stream,
        chat_store=chat_store,
        audit_sink=audit_sink,
        pilot_experiments=pilot_experiments,
        citation_resolver=citation_resolver,
    )
    resolved_upload_storage = upload_storage or _NullUploadStorage()
    resolved_upload_job_store = upload_job_store or _NullUploadJobStore()
    resolved_review_store = review_store or _NullReviewStore()
    resolved_publication_port = publication_port or _NullPublicationPort()
    resolved_app_support_queries = app_support_queries or _NullAppSupportQueries()
    document_upload = DocumentUpload(
        storage=resolved_upload_storage,
        job_store=resolved_upload_job_store,
    )
    publication_coordinator = PublicationCoordinator(publication=resolved_publication_port)
    review_documents = ReviewDocuments(
        review_store=resolved_review_store,
        publication=publication_coordinator,
    )
    return AppRuntime(
        settings=existing_settings,
        rag_stream=rag_stream,
        chat_store=chat_store,
        audit_sink=audit_sink,
        pilot_experiments=pilot_experiments,
        citation_resolver=citation_resolver,
        chat_turn_runner=runner,
        upload_storage=resolved_upload_storage,
        upload_job_store=resolved_upload_job_store,
        document_upload=document_upload,
        review_store=resolved_review_store,
        publication_port=resolved_publication_port,
        publication_coordinator=publication_coordinator,
        review_documents=review_documents,
        app_support_queries=resolved_app_support_queries,
        protected_file_resolver=protected_file_resolver,
    )


def build_default_app_runtime(
    existing_settings: Any,
    *,
    post: Callable[..., Any],
    base_url: str | Callable[[], str],
    headers: Mapping[str, str] | Callable[[], Mapping[str, str]],
    timeout: Any | Callable[[], Any],
    save_chat_history: Callable[..., int | None],
    save_answer_evidence: Callable[..., Any],
    save_answer_sources: Callable[..., Any],
    write_audit_log: Callable[..., Any],
    load_pilot_config: Callable[[], object | None],
    assign_pilot_route: Callable[..., object],
    pilot_outcome: Callable[..., Mapping[str, Any]],
    schedule_pilot_replay: Callable[..., bool],
    citation_resolver: CitationResolver,
    raw_root: Callable[[], Any] | None = None,
    create_ingestion_job: Callable[..., int | None] | None = None,
    reject_ingestion_job: Callable[..., bool] | None = None,
    mark_job_rejected: Callable[[int], Any] | None = None,
    reject_document: Callable[..., Any] | None = None,
    delete_document_completely: Callable[..., Any] | None = None,
    delete_ingestion_job: Callable[[int], Any] | None = None,
    publish_document: Callable[..., Any] | None = None,
    mark_job_published: Callable[[int], Any] | None = None,
    engine: Any = None,
    strict_site_filter: bool = True,
) -> AppRuntime:
    """Compose production adapters without exposing them to the API router."""

    document_lookup = SqlDocumentLookup(engine=engine)
    protected_file_resolver = None
    if raw_root is not None:
        resolved_raw_root = raw_root()
        protected_file_resolver = ProtectedFileResolver(
            store=SqlProtectedFileStore(engine_provider=lambda: engine() if callable(engine) else engine),
            storage=FilesystemProtectedFileStorage(
                project_root=resolved_raw_root.parent.parent,
                raw_root=resolved_raw_root,
                processed_root=resolved_raw_root.parent / "processed",
            ),
            audit=RepositoryProtectedFileAuditSink(write_audit_log=write_audit_log),
            strict_site_filter=strict_site_filter,
        )
    return build_app_runtime(
        existing_settings,
        rag_stream=HttpRagStreamAdapter(
            post=post,
            base_url=base_url,
            headers=headers,
            timeout=timeout,
        ),
        chat_store=RepositoryChatStore(
            save_chat_history=save_chat_history,
            save_answer_evidence=save_answer_evidence,
            save_answer_sources=save_answer_sources,
        ),
        audit_sink=RepositoryAuditSink(write_audit_log=write_audit_log),
        pilot_experiments=PilotExperimentAdapter(
            load_config=load_pilot_config,
            assign_route=assign_pilot_route,
            outcome=pilot_outcome,
            schedule_replay=schedule_pilot_replay,
        ),
        citation_resolver=citation_resolver,
        upload_storage=LocalUploadStorage(raw_root=raw_root) if raw_root else None,
        upload_job_store=RepositoryUploadJobStore(
            create_ingestion_job=create_ingestion_job,
        ) if create_ingestion_job else None,
        review_store=RepositoryReviewStore(
            reject_ingestion_job=reject_ingestion_job,
            mark_job_rejected=mark_job_rejected,
            reject_document=reject_document,
            delete_document_completely=delete_document_completely,
            delete_ingestion_job=delete_ingestion_job,
        ) if all(
            (
                reject_ingestion_job,
                mark_job_rejected,
                reject_document,
                delete_document_completely,
                delete_ingestion_job,
            )
        ) else None,
        publication_port=RepositoryPublicationPort(
            publish_document=publish_document,
            mark_job_published=mark_job_published,
            resolve_latest_doc_id_for_job=document_lookup.latest_doc_id_for_job,
        ) if publish_document and mark_job_published else None,
        app_support_queries=SqlAppSupportQueries(engine=engine),
        protected_file_resolver=protected_file_resolver,
    )


def production_engine() -> Any:
    from mech_chatbot.db.engine import engine

    return engine


def production_create_ingestion_job(**kwargs: Any) -> int | None:
    from mech_chatbot.db.repositories import jobs

    return jobs.create_ingestion_job(**kwargs)


__all__ = [
    "AppRuntime",
    "build_app_runtime",
    "build_default_app_runtime",
    "production_create_ingestion_job",
    "production_engine",
]
