"""FastAPI composition root for the browser-facing Vue application.

Feature HTTP handlers live in :mod:`mech_chatbot.api.routers`.  This module is
limited to process lifecycle, application-runtime construction, router
composition, health reporting, and SPA fallback wiring.  Compatibility aliases
at the bottom keep older scripts able to import handlers from ``app_server``
without registering those handlers twice.
"""
from __future__ import annotations

from contextlib import asynccontextmanager
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Callable

import requests
from fastapi import FastAPI, Request
from starlette.exceptions import HTTPException as StarletteHTTPException
from fastapi.staticfiles import StaticFiles

from mech_chatbot.api import app_security
from mech_chatbot.api.dependencies import csrf_profile, current_profile, require_any_role
from mech_chatbot.api.file_access import (
    chat_image_path,
    data_raw_root,
    page_has_vision,
)
from mech_chatbot.api.routers import (
    auth_router,
    chat_router,
    documents_router,
    files_router,
    operations_router,
)
from mech_chatbot.api.routers import chat as _chat_routes
from mech_chatbot.api.routers import documents as _document_routes
from mech_chatbot.api.routers import operations as _operation_routes
from mech_chatbot.adapters.pilot_replay import (
    PilotReplayExecutor,
    iter_sse_events as _iter_sse_events,
    pilot_outcome as _pilot_outcome,
    pilot_route as _pilot_route,
)
from mech_chatbot.adapters.qdrant_runtime import (
    QdrantAdminRuntime,
    build_qdrant_admin_runtime,
)
from mech_chatbot.composition.app_runtime import (
    AppDatabaseRuntime,
    build_app_database_runtime,
    build_default_app_runtime,
    production_create_ingestion_job,
)
from mech_chatbot.config.logging import LoggingConfig, configure_logging, logger
from mech_chatbot.config.repository_runtime import bind_repository_runtime
from mech_chatbot.config.settings import (
    AppProcessSettings,
    QdrantSettings,
    RepositoryPolicySettings,
    Settings,
    SqlSettings,
    load_settings,
)
import mech_chatbot.services.audit_service as audit_service
import mech_chatbot.services.chat_service as chat_service
import mech_chatbot.services.document_service as document_service
import mech_chatbot.services.lifecycle_service as lifecycle_service
import mech_chatbot.services.ui_query_service as ui_query_service


# These service aliases are dependencies of the composition-time runtime
# factory.  Keeping the names local also preserves the historical monkeypatch
# seams used by baseline-capture scripts without moving domain work back here.
refresh_expired_status = lifecycle_service.refresh_expired_status
save_chat_history = chat_service.save_chat_history
save_answer_evidence = chat_service.save_answer_evidence
save_answer_sources = chat_service.save_answer_sources
write_audit_log = audit_service.write_audit_log
reject_ingestion_job = ui_query_service.reject_ingestion_job
mark_job_rejected = ui_query_service.mark_job_rejected
reject_document = document_service.reject_document
delete_document_completely = document_service.delete_document_completely
delete_ingestion_job = ui_query_service.delete_ingestion_job
publish_document = document_service.publish_document
mark_job_published = ui_query_service.mark_job_published


@asynccontextmanager
async def _lifespan(application: FastAPI):
    import asyncio

    import anyio

    settings_snapshot = application.state.settings_snapshot
    process = application.state.process_settings
    pilot_replays = application.state.pilot_replays
    configure_logging(LoggingConfig.from_settings(settings_snapshot))
    database_runtime = application.state.database_builder(
        SqlSettings.from_settings(settings_snapshot)
    )
    qdrant_runtime = None
    lifecycle_task = None
    pilot_started = False
    try:
        qdrant_runtime = application.state.qdrant_builder(
            QdrantSettings.from_settings(settings_snapshot)
        )
        application.state.database_runtime = database_runtime
        application.state.qdrant_runtime = qdrant_runtime
        application.state.runtime = _build_default_app_runtime(
            process,
            pilot_replays,
            post=application.state.post,
            engine=database_runtime.engine,
            strict_site_filter=RepositoryPolicySettings.from_settings(
                settings_snapshot
            ).strict_site_filter,
            qdrant_client=qdrant_runtime.client,
            collection_name=qdrant_runtime.collection_name,
        )
        refresh_lifecycle = lambda: refresh_expired_status(
            db_engine=database_runtime.engine,
            qdrant_client=qdrant_runtime.client,
            collection_name=qdrant_runtime.collection_name,
        )
        anyio.to_thread.current_default_thread_limiter().total_tokens = (
            process.thread_limit
        )
        logger.info(
            "App server anyio thread limiter raised to %d",
            process.thread_limit,
        )
        pilot_replays.start()
        pilot_started = True

        async def _reconcile_lifecycle_periodically() -> None:
            while True:
                try:
                    await anyio.to_thread.run_sync(refresh_lifecycle)
                except Exception:
                    logger.exception("Lifecycle reconciliation failed")
                await asyncio.sleep(process.lifecycle_reconcile_seconds)

        try:
            await anyio.to_thread.run_sync(refresh_lifecycle)
        except Exception:
            logger.exception("Initial lifecycle reconciliation failed")
        lifecycle_task = asyncio.create_task(
            _reconcile_lifecycle_periodically()
        )
        yield
    finally:
        if pilot_started:
            pilot_replays.stop()
        if lifecycle_task is not None:
            lifecycle_task.cancel()
            try:
                await lifecycle_task
            except asyncio.CancelledError:
                pass
        if qdrant_runtime is not None:
            qdrant_runtime.close()
        database_runtime.close()
        application.state.database_runtime = None
        application.state.qdrant_runtime = None


def app_health(request: Request):
    db_status = "unavailable"
    try:
        if request.app.state.runtime.app_support_queries.database_ready():
            db_status = "ok"
    except Exception as exc:
        logger.warning("App health DB probe failed: %s", exc)
    return {
        "status": "ok" if db_status == "ok" else "degraded",
        "app": "mech-chatbot-app-api",
        "db": db_status,
    }


def _project_root() -> Path:
    return Path(__file__).resolve().parents[3]


def _rag_headers(process: AppProcessSettings) -> dict[str, str]:
    headers = {"Content-Type": "application/json"}
    token = process.rag_service_token.strip()
    if token:
        headers["X-RAG-Service-Token"] = token
    return headers


# The chat router owns these interfaces.  The explicit aliases keep the runtime
# builder independent of HTTP module internals and preserve historical imports.
_resolve_chat_citations = _chat_routes._resolve_chat_citations
_chat_actor_profile = _chat_routes._chat_actor_profile


def _build_default_app_runtime(
    process: AppProcessSettings,
    pilot_replays: PilotReplayExecutor,
    *,
    post: Callable[..., Any],
    engine: Any = None,
    strict_site_filter: bool = True,
    qdrant_client: Any = None,
    collection_name: str | None = None,
):
    from mech_chatbot.evaluation.crag_pilot import assign_pilot_route, load_pilot_config

    return build_default_app_runtime(
        process,
        post=post,
        base_url=process.rag_base_url,
        headers=_rag_headers(process),
        timeout=(10, process.rag_chat_timeout_seconds),
        save_chat_history=lambda **kwargs: save_chat_history(
            **kwargs,
            db_engine=engine,
        ),
        save_answer_evidence=lambda *args: save_answer_evidence(
            *args,
            db_engine=engine,
        ),
        save_answer_sources=lambda *args: save_answer_sources(
            *args,
            db_engine=engine,
        ),
        write_audit_log=lambda *args, **kwargs: write_audit_log(
            *args,
            **kwargs,
            db_engine=engine,
        ),
        load_pilot_config=load_pilot_config,
        assign_pilot_route=assign_pilot_route,
        pilot_outcome=lambda answer, debug, **kwargs: _pilot_outcome(
            answer,
            debug,
            **kwargs,
        ),
        schedule_pilot_replay=lambda route, payload, outcome, trace_id, actor: (
            pilot_replays.schedule(
                route,
                payload,
                outcome,
                trace_id,
                _chat_actor_profile(actor),
            )
        ),
        citation_resolver=_resolve_chat_citations,
        raw_root=data_raw_root,
        create_ingestion_job=lambda **kwargs: production_create_ingestion_job(
            **kwargs,
            db_engine=engine,
        ),
        reject_ingestion_job=lambda *args, **kwargs: reject_ingestion_job(
            *args,
            **kwargs,
            db_engine=engine,
        ),
        mark_job_rejected=lambda *args, **kwargs: mark_job_rejected(
            *args,
            **kwargs,
            db_engine=engine,
        ),
        reject_document=lambda *args, **kwargs: reject_document(
            *args,
            **kwargs,
            db_engine=engine,
            qdrant_client=qdrant_client,
            collection_name=collection_name,
        ),
        delete_document_completely=lambda *args, **kwargs: delete_document_completely(
            *args,
            **kwargs,
            db_engine=engine,
            qdrant_client=qdrant_client,
            collection_name=collection_name,
        ),
        delete_ingestion_job=lambda *args, **kwargs: delete_ingestion_job(
            *args,
            **kwargs,
            db_engine=engine,
        ),
        publish_document=lambda *args, **kwargs: publish_document(
            *args,
            **kwargs,
            db_engine=engine,
            qdrant_client=qdrant_client,
            collection_name=collection_name,
        ),
        mark_job_published=lambda *args, **kwargs: mark_job_published(
            *args,
            **kwargs,
            db_engine=engine,
        ),
        engine=engine,
        strict_site_filter=strict_site_filter,
    )


class SPAStaticFiles(StaticFiles):
    async def get_response(self, path: str, scope: dict[str, Any]):
        try:
            return await super().get_response(path, scope)
        except StarletteHTTPException as exc:
            if exc.status_code == 404 and self._should_fallback_to_index(path, scope):
                return await super().get_response("index.html", scope)
            raise

    @staticmethod
    def _should_fallback_to_index(path: str, scope: dict[str, Any]) -> bool:
        request_path = str(scope.get("path") or "")
        raw_path = scope.get("raw_path") or b""
        raw_path_text = raw_path.decode("latin-1", errors="ignore").lower()
        if request_path == "/api" or request_path.startswith("/api/"):
            return False
        if (
            ".." in request_path
            or ".." in path
            or "%2f" in raw_path_text
            or "%5c" in raw_path_text
        ):
            return False
        leaf = path.rsplit("/", 1)[-1]
        return "." not in leaf


def _build_pilot_replays(
    process: AppProcessSettings,
    *,
    post: Callable[..., Any],
) -> PilotReplayExecutor:
    return PilotReplayExecutor(
        post=post,
        headers=lambda: _rag_headers(process),
        workers=lambda: process.pilot_replay_workers,
        queue_size=lambda: process.pilot_replay_queue_size,
        timeout_seconds=lambda: int(process.pilot_replay_timeout_seconds),
    )


def _install_http_surface(application: FastAPI) -> None:
    application.add_api_route(
        "/api/health",
        app_health,
        methods=["GET"],
        tags=["system"],
    )
    application.include_router(auth_router)
    application.include_router(chat_router)
    application.include_router(files_router)
    application.include_router(documents_router)
    application.include_router(operations_router)

    static_dir = _project_root() / "web-ui" / "dist"
    if static_dir.exists():
        application.mount(
            "/",
            SPAStaticFiles(directory=str(static_dir), html=True),
            name="web",
        )


def create_app(
    existing_settings: Settings | None = None,
    *,
    post: Callable[..., Any] = requests.post,
    database_builder: Callable[[SqlSettings], AppDatabaseRuntime] = (
        build_app_database_runtime
    ),
    qdrant_builder: Callable[[QdrantSettings], QdrantAdminRuntime] = (
        build_qdrant_admin_runtime
    ),
) -> FastAPI:
    """Build one browser API process from one immutable environment snapshot."""

    settings_snapshot = existing_settings or load_settings()
    process = AppProcessSettings.from_settings(settings_snapshot)
    pilot_replays = _build_pilot_replays(process, post=post)
    application = FastAPI(
        title="Mech Chatbot App API",
        version="0.1.0",
        lifespan=_lifespan,
    )
    application.state.process_settings = process
    application.state.settings_snapshot = settings_snapshot
    application.state.pilot_replays = pilot_replays
    application.state.post = post
    application.state.database_builder = database_builder
    application.state.qdrant_builder = qdrant_builder
    application.state.database_runtime = None
    application.state.qdrant_runtime = None
    application.state.runtime = _build_default_app_runtime(
        process,
        pilot_replays,
        post=post,
        strict_site_filter=RepositoryPolicySettings.from_settings(
            settings_snapshot
        ).strict_site_filter,
    )

    @application.middleware("http")
    async def _bind_security_settings(request: Request, call_next):
        database_runtime = request.app.state.database_runtime
        qdrant_runtime = request.app.state.qdrant_runtime
        with (
            app_security.bind_security_settings(
                request.app.state.process_settings
            ),
            bind_repository_runtime(
                policy=RepositoryPolicySettings.from_settings(
                    request.app.state.settings_snapshot
                ),
                db_engine=getattr(database_runtime, "engine", None),
                qdrant_client=getattr(qdrant_runtime, "client", None),
                qdrant_collection=getattr(
                    qdrant_runtime,
                    "collection_name",
                    None,
                ),
            ),
        ):
            return await call_next(request)

    _install_http_surface(application)
    return application


app = create_app()


# Compatibility export surface.  These are direct aliases to the feature
# modules, not duplicate endpoint implementations, and therefore register no
# additional routes.
_CHAT_COMPAT_EXPORTS = (
    "ChatMessageRequest",
    "_citation_list",
    "_filter_citations_by_answer",
    "_answer_body_without_reference_appendix",
    "_citation_ref_text",
    "_decorate_history_messages",
    "_sign_image_upload",
    "_verify_image_upload",
    "_chat_actor",
    "_chat_event_sse",
    "sessions",
    "history",
    "delete_session",
    "upload_chat_image",
    "chat_message",
    "feedback",
)
_DOCUMENT_COMPAT_EXPORTS = (
    "_document_actor",
    "_read_upload_command",
    "_raise_upload_failure",
    "_upload_error_payload",
    "_file_response",
    "_resolve_protected_file",
    "citation_page",
    "original_document",
    "chat_image",
    "documents",
    "document_lifecycle_counts",
    "documents_upload",
    "documents_upload_batch",
    "ingestion_jobs",
    "documents_pending_review",
    "documents_reconcile_serving",
    "documents_expiring",
    "documents_bulk_meta",
    "documents_bulk_metadata",
    "_documents_review_bulk_impl",
    "document_set_current",
    "document_mark_expired",
    "document_update_metadata",
    "document_publish_contract",
    "document_update_governance",
    "document_backfill_site",
    "document_publish_new_version",
    "document_publish_new_variant",
    "document_publish_standalone",
    "document_reject",
    "document_archive",
    "document_delete",
    "ingestion_eta",
    "ingestion_bulk_jobs",
    "ingestion_bulk_delete",
    "ingestion_set_priority",
    "ingestion_cancel",
    "ingestion_requeue",
    "ingestion_pending_review",
    "ingestion_publish",
    "ingestion_reject",
    "ingestion_delete",
    "lifecycle_overview",
    "lifecycle_refresh",
    "lifecycle_set_document",
    "lifecycle_mark_reviewed",
)
_OPERATION_COMPAT_EXPORTS = (
    "LoginRequest",
    "LanguageRequest",
    "login",
    "me",
    "logout",
    "update_preferences",
    "refresh_session",
    "dashboard",
    "usage",
    "observability",
    "audit",
    "access_request",
    "access_requests",
    "my_access_requests",
    "access_request_resolve",
    "access_users",
    "access_grants",
    "access_revoke_clearance",
    "access_revoke_department",
    "users",
    "user_detail",
    "user_create",
    "user_active",
    "user_roles",
    "user_departments",
    "user_sites",
    "user_clearance",
    "user_password",
    "user_delete",
    "catalog_departments",
    "catalog_missing_site_documents",
    "catalog_knowledge_governance",
    "catalog_domain_profiles",
    "catalog_rollout_readiness",
    "catalog_rollout_plans",
    "catalog_rollout_plan_set",
    "catalog_evaluation_gate_record",
    "catalog_department_governance",
    "catalog_department_governance_set",
    "catalog_department_domain_profile",
    "catalog_department_domain_profile_set",
    "catalog_department",
    "catalog_department_upsert",
    "catalog_department_status",
    "catalog_department_archive",
    "catalog_department_reassign",
    "catalog_sites",
    "catalog_site_upsert",
    "glossary",
    "glossary_upsert",
    "glossary_active",
    "glossary_delete",
    "materials",
    "material_upsert",
    "material_synonym_add",
    "material_delete",
    "material_synonym_delete",
    "feedbacks",
    "feedback_classify",
    "feedback_delete",
    "regression_questions",
    "regression_question_add",
    "regression_question_active",
    "regression_runs",
    "regression_run",
    "quality_documents",
    "quality_recompute",
    "quality_cleanup",
    "analytics_departments",
    "analytics_cache",
    "settings",
    "external_ai_policy",
    "external_ai_policy_set",
    "setting_set",
    "graph_proposals",
    "_review_graph_proposal_endpoint",
    "graph_proposal_approve",
    "graph_proposal_reject",
    "community_summaries",
    "_review_community_summary_endpoint",
    "community_summary_approve",
    "community_summary_reject",
)

for _name in _CHAT_COMPAT_EXPORTS:
    globals()[_name] = getattr(_chat_routes, _name)
for _name in _DOCUMENT_COMPAT_EXPORTS:
    globals()[_name] = getattr(_document_routes, _name)
for _name in _OPERATION_COMPAT_EXPORTS:
    globals().setdefault(_name, getattr(_operation_routes, _name))

# Historical spelling retained for external imports.
data_router = operations_router
bcrypt = _operation_routes.bcrypt
HTTPException = _operation_routes.HTTPException


def documents_review_bulk(body: dict[str, Any], profile: dict[str, Any]):
    """Compatibility call-through for the former non-route helper."""

    return _document_routes._documents_review_bulk_impl(body, profile, app.state.runtime)


def _compat_request() -> Any:
    return SimpleNamespace(app=app)


def _sources_for_chat_ids(chat_ids: list[int]):
    return _chat_routes._sources_for_chat_ids(chat_ids, app.state.runtime)


def _decorate_history_messages(messages: list[dict[str, Any]]):
    return _chat_routes._decorate_history_messages(messages, app.state.runtime)


def history(body: dict[str, Any], profile: dict[str, Any]):
    return _chat_routes.history(body, _compat_request(), profile)


def upload_chat_image(request: Any, file: Any, profile: dict[str, Any]):
    del request
    return _chat_routes.upload_chat_image(file, profile)


def citation_page(doc_id: int, page_no: int, profile: dict[str, Any]):
    return _document_routes.citation_page(doc_id, page_no, _compat_request(), profile)


def original_document(doc_id: int, profile: dict[str, Any]):
    return _document_routes.original_document(doc_id, _compat_request(), profile)


def chat_image(image_id: str, profile: dict[str, Any]):
    return _document_routes.chat_image(image_id, _compat_request(), profile)


def documents_upload_batch(
    files: list[Any],
    thu_muc: str | None,
    domain: str | None,
    security_level: str | None,
    cong_doan: str | None,
    site: str | None,
    meta_json: str | None,
    extra_departments_json: str | None,
    assignments_json: str | None,
    profile: dict[str, Any],
):
    return _document_routes.documents_upload_batch(
        _compat_request(),
        files,
        thu_muc,
        domain,
        security_level,
        cong_doan,
        site,
        meta_json,
        extra_departments_json,
        assignments_json,
        profile,
    )


def document_publish_new_version(doc_id: int, response: Any, profile: dict[str, Any]):
    return _document_routes.document_publish_new_version(
        doc_id, _compat_request(), response, profile
    )


def document_publish_new_variant(doc_id: int, response: Any, profile: dict[str, Any]):
    return _document_routes.document_publish_new_variant(
        doc_id, _compat_request(), response, profile
    )


def document_publish_standalone(doc_id: int, response: Any, profile: dict[str, Any]):
    return _document_routes.document_publish_standalone(
        doc_id, _compat_request(), response, profile
    )


def ingestion_publish(job_id: int, response: Any, profile: dict[str, Any]):
    return _document_routes.ingestion_publish(
        job_id, _compat_request(), response, profile
    )


def feedback_classify(
    feedback_id: int,
    body: dict[str, Any],
    profile: dict[str, Any],
):
    return _operation_routes.feedback_classify(
        feedback_id, body, _compat_request(), profile
    )


if __name__ == "__main__":
    import uvicorn

    process = AppProcessSettings.from_settings(load_settings())
    uvicorn.run(
        "mech_chatbot.api.app_server:app",
        host=process.server_host,
        port=process.server_port,
        reload=False,
    )
