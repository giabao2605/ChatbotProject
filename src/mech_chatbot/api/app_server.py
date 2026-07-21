"""FastAPI composition root for the browser-facing Vue application.

Feature HTTP handlers live in :mod:`mech_chatbot.api.routers`.  This module is
limited to process lifecycle, application-runtime construction, router
composition, health reporting, and SPA fallback wiring.  Compatibility aliases
at the bottom keep older scripts able to import handlers from ``app_server``
without registering those handlers twice.
"""
from __future__ import annotations

import os
from contextlib import asynccontextmanager
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import requests
from dotenv import load_dotenv
from fastapi import FastAPI
from starlette.exceptions import HTTPException as StarletteHTTPException
from fastapi.staticfiles import StaticFiles

from mech_chatbot.api import app_security
from mech_chatbot.api.dependencies import csrf_profile, current_profile, require_any_role
from mech_chatbot.api.file_access import (
    chat_image_path,
    data_raw_root,
    page_has_vision,
    strict_site_filter_enabled,
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
from mech_chatbot.composition.app_runtime import (
    build_default_app_runtime,
    production_create_ingestion_job,
    production_engine,
)
from mech_chatbot.config.logging import logger
from mech_chatbot.config.settings import settings as application_settings
import mech_chatbot.services.audit_service as audit_service
import mech_chatbot.services.chat_service as chat_service
import mech_chatbot.services.document_service as document_service
import mech_chatbot.services.lifecycle_service as lifecycle_service
import mech_chatbot.services.ui_query_service as ui_query_service


load_dotenv()

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


_APP_THREAD_LIMIT = int(os.getenv("APP_THREAD_LIMIT", "60"))
_PILOT_REPLAYS = PilotReplayExecutor(
    post=lambda *args, **kwargs: requests.post(*args, **kwargs),
    headers=lambda: _rag_headers(),
    workers=lambda: int(os.getenv("CRAG_PILOT_REPLAY_WORKERS", "2")),
    queue_size=lambda: int(os.getenv("CRAG_PILOT_REPLAY_QUEUE_SIZE", "8")),
    timeout_seconds=lambda: int(
        os.getenv("CRAG_PILOT_REPLAY_TIMEOUT_SECONDS", "300")
    ),
)


def _start_pilot_replay_executor() -> None:
    _PILOT_REPLAYS.start()


def _stop_pilot_replay_executor() -> None:
    _PILOT_REPLAYS.stop()

@asynccontextmanager
async def _lifespan(app: FastAPI):
    import asyncio

    import anyio

    anyio.to_thread.current_default_thread_limiter().total_tokens = _APP_THREAD_LIMIT
    logger.info("App server anyio thread limiter raised to %d", _APP_THREAD_LIMIT)
    _start_pilot_replay_executor()

    async def _reconcile_lifecycle_periodically() -> None:
        interval = max(60, int(os.getenv("LIFECYCLE_RECONCILE_SECONDS", "300")))
        while True:
            try:
                await anyio.to_thread.run_sync(refresh_expired_status)
            except Exception:
                logger.exception("Lifecycle reconciliation failed")
            await asyncio.sleep(interval)

    try:
        await anyio.to_thread.run_sync(refresh_expired_status)
    except Exception:
        logger.exception("Initial lifecycle reconciliation failed")
    lifecycle_task = asyncio.create_task(_reconcile_lifecycle_periodically())
    try:
        yield
    finally:
        _stop_pilot_replay_executor()
        lifecycle_task.cancel()
        try:
            await lifecycle_task
        except asyncio.CancelledError:
            pass


app = FastAPI(title="Mech Chatbot App API", version="0.1.0", lifespan=_lifespan)


@app.get("/api/health", tags=["system"])
def app_health():
    db_status = "unavailable"
    try:
        if app.state.runtime.app_support_queries.database_ready():
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


def _rag_base_url() -> str:
    return os.getenv("RAG_SERVER_URL", "http://127.0.0.1:8100").rstrip("/")


def _rag_headers() -> dict[str, str]:
    headers = {"Content-Type": "application/json"}
    token = os.getenv("RAG_SERVICE_TOKEN", "").strip()
    if token:
        headers["X-RAG-Service-Token"] = token
    return headers


def _execute_pilot_replay(replay) -> None:
    _PILOT_REPLAYS.execute(replay)


def _schedule_pilot_replay(route, payload, outcome, trace_id, profile) -> bool:
    return _PILOT_REPLAYS.schedule(route, payload, outcome, trace_id, profile)


# The chat router owns these interfaces.  The explicit aliases keep the runtime
# builder independent of HTTP module internals and preserve historical imports.
_resolve_chat_citations = _chat_routes._resolve_chat_citations
_chat_actor_profile = _chat_routes._chat_actor_profile


def _build_default_app_runtime():
    from mech_chatbot.evaluation.crag_pilot import assign_pilot_route, load_pilot_config

    return build_default_app_runtime(
        application_settings,
        post=lambda *args, **kwargs: requests.post(*args, **kwargs),
        base_url=_rag_base_url,
        headers=_rag_headers,
        timeout=lambda: (
            10,
            int(os.getenv("APP_RAG_CHAT_TIMEOUT_SECONDS", "300")),
        ),
        save_chat_history=lambda **kwargs: save_chat_history(**kwargs),
        save_answer_evidence=lambda *args: save_answer_evidence(*args),
        save_answer_sources=lambda *args: save_answer_sources(*args),
        write_audit_log=lambda **kwargs: write_audit_log(**kwargs),
        load_pilot_config=load_pilot_config,
        assign_pilot_route=assign_pilot_route,
        pilot_outcome=lambda answer, debug, **kwargs: _pilot_outcome(
            answer,
            debug,
            **kwargs,
        ),
        schedule_pilot_replay=lambda route, payload, outcome, trace_id, actor: (
            _schedule_pilot_replay(
                route,
                payload,
                outcome,
                trace_id,
                _chat_actor_profile(actor),
            )
        ),
        citation_resolver=_resolve_chat_citations,
        raw_root=data_raw_root,
        create_ingestion_job=lambda **kwargs: production_create_ingestion_job(**kwargs),
        reject_ingestion_job=lambda *args, **kwargs: reject_ingestion_job(*args, **kwargs),
        mark_job_rejected=lambda *args, **kwargs: mark_job_rejected(*args, **kwargs),
        reject_document=lambda *args, **kwargs: reject_document(*args, **kwargs),
        delete_document_completely=lambda *args, **kwargs: delete_document_completely(
            *args,
            **kwargs,
        ),
        delete_ingestion_job=lambda *args, **kwargs: delete_ingestion_job(*args, **kwargs),
        publish_document=lambda *args, **kwargs: publish_document(*args, **kwargs),
        mark_job_published=lambda *args, **kwargs: mark_job_published(*args, **kwargs),
        engine=production_engine,
        strict_site_filter=strict_site_filter_enabled(),
    )


app.state.runtime = _build_default_app_runtime()

app.include_router(auth_router)
app.include_router(chat_router)
app.include_router(files_router)
app.include_router(documents_router)
app.include_router(operations_router)


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


static_dir = _project_root() / "web-ui" / "dist"
if static_dir.exists():
    app.mount("/", SPAStaticFiles(directory=str(static_dir), html=True), name="web")


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

    uvicorn.run(
        "mech_chatbot.api.app_server:app",
        host=os.getenv("APP_SERVER_HOST", "0.0.0.0"),
        port=int(os.getenv("APP_SERVER_PORT", "8080")),
        reload=False,
    )
