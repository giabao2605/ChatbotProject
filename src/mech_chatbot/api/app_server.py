"""Browser-facing FastAPI app for the Vue migration.

This service does not load RAG models. It authenticates browser users, proxies
chat questions to the internal rag-server, owns SQL persistence/audit for chat,
and serves protected files through SQL-backed RBAC checks.
"""
from __future__ import annotations

import json
import mimetypes
import os
import time
from concurrent.futures import ThreadPoolExecutor
from contextlib import asynccontextmanager
from pathlib import Path
from threading import BoundedSemaphore
from typing import Any, Mapping
from uuid import uuid4

import bcrypt
import requests
from dotenv import load_dotenv
from fastapi import APIRouter, Depends, FastAPI, File, Form, HTTPException, Request, Response, UploadFile, status
from fastapi.responses import FileResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field
from starlette.exceptions import HTTPException as StarletteHTTPException

from mech_chatbot.application.chat_turn import (
    ChatActor,
    ChatCitation,
    ChatDelta,
    ChatDone,
    ChatError,
    ChatThinking,
    ChatTurnCommand,
    ChatWarning,
)
from mech_chatbot.application.chat_citations import (
    build_citation_list,
    citation_ref_text,
    filter_citations_by_answer,
    resolve_chat_citations,
)
from mech_chatbot.application.document_review import (
    PublicationCommand,
    PublicationOutcome,
    ReviewDocumentsCommand,
    ReviewItem,
    normalize_publish_mode,
)
from mech_chatbot.application.document_upload import (
    DocumentActor,
    UploadDocumentCommand,
    UploadFailure,
    UploadRejected,
)
from mech_chatbot.application.protected_files import (
    ProtectedFileActor,
    ProtectedFileError,
    ProtectedFileReference,
)
from mech_chatbot.api import app_security
from mech_chatbot.api.dependencies import (
    csrf_profile,
    current_profile,
    public_profile as _public_profile,
    require_any_role,
    session_payload as _session_payload,
)
from mech_chatbot.api.routers import (
    chat_router,
    documents_router,
    files_router,
    operations_router as data_router,
)
from mech_chatbot.api.transport_utils import (
    assert_any_role as _assert_any_role,
    parse_json_list as _parse_json_list,
    parse_json_obj as _parse_json_obj,
    parse_json_or_csv_list as _parse_json_or_csv_list,
    row_to_json as _row_to_json,
    rows_to_json as _rows_to_json,
    safe_int as _safe_int,
    split_csv as _split_csv,
)
from mech_chatbot.api.file_access import (
    chat_image_path,
    data_raw_root,
    page_has_vision,
    strict_site_filter_enabled,
)
from mech_chatbot.auth.core import authenticate_user, load_user_profile, update_user_preferred_language
from mech_chatbot.auth.authorization import role_allows
from mech_chatbot.config.logging import logger, log_trace
from mech_chatbot.config.settings import settings as application_settings
from mech_chatbot.composition.app_runtime import (
    build_default_app_runtime,
    production_create_ingestion_job,
    production_engine,
)
from mech_chatbot.llm.external_ai import invalidate_external_ai_provider_profiles
from mech_chatbot.services import (
    add_material_synonym,
    add_regression_question,
    archive_department,
    archive_document,
    bulk_delete_ingestion_jobs,
    cancel_job,
    classify_feedback_and_get_source,
    clear_chat_history,
    cleanup_dangling_records,
    create_access_request,
    count_pending_access_requests,
    create_user_with_roles,
    dashboard_by_department,
    delete_feedback,
    delete_document_completely,
    delete_glossary_term,
    delete_ingestion_job,
    delete_material,
    delete_material_synonym,
    delete_user_account,
    get_all_app_settings,
    get_all_sessions,
    get_department_summary,
    get_department_rollout_readiness,
    get_doc_quality_ranking,
    get_grant_history,
    get_chat_history,
    get_dashboard_stats,
    get_role_dashboard,
    get_department_domain_profile,
    get_department_knowledge_governance,
    get_lifecycle_overview,
    get_document_lifecycle_counts,
    get_observability,
    get_regression_runs,
    get_user_access_requests,
    get_user_clearance,
    get_user_departments,
    get_user_roles,
    get_user_sites,
    get_usage_analytics,
    list_audit_logs,
    list_access_requests,
    list_bulk_action_jobs,
    list_bulk_meta_departments,
    list_docs_for_bulk_meta,
    list_domain_glossary,
    list_department_domain_profiles,
    list_department_knowledge_governance,
    list_external_ai_provider_profiles,
    list_documents,
    list_expiring_documents,
    list_ingestion_jobs,
    list_feedbacks,
    list_graph_proposals,
    list_community_summaries,
    list_known_departments,
    list_known_sites,
    list_materials,
    list_missing_site_documents,
    list_department_rollout_plans,
    list_pending_review_docs,
    list_recent_documents,
    list_recent_failed_jobs,
    list_regression_questions,
    list_users_basic,
    list_users_with_access,
    mark_document_expired,
    mark_document_reviewed,
    mark_job_pending_review,
    mark_job_published,
    mark_job_rejected,
    publish_document,
    queue_eta_seconds,
    reassign_department_data,
    reconcile_serving_state,
    record_department_evaluation_gate,
    requeue_job,
    recompute_doc_quality_scores,
    reject_document,
    reject_ingestion_job,
    resolve_access_request,
    review_graph_proposal,
    review_community_summary,
    refresh_expired_status,
    revoke_user_clearance,
    revoke_user_department,
    save_answer_evidence,
    save_answer_sources,
    save_chat_history,
    sc_stats,
    set_app_setting,
    set_department_status,
    set_document_lifecycle,
    set_glossary_active,
    set_job_priority,
    set_regression_question_active,
    set_user_active_status,
    set_user_clearance,
    set_user_departments,
    set_user_sites,
    update_chat_feedback,
    update_document_common_metadata,
    update_document_governance_metadata,
    update_user_active_and_roles,
    update_user_password,
    upsert_golden_answer,
    upsert_external_ai_provider_profile,
    upsert_department_domain_profile,
    upsert_department_knowledge_governance,
    upsert_department_rollout_plan,
    upsert_department,
    upsert_glossary_term,
    upsert_material,
    upsert_site,
    ensure_regression_question,
    write_audit_log,
    validate_publish_contract,
    validate_document_metadata_actor,
)

load_dotenv()

# ---------------------------------------------------------------------------
# Lifespan: raise anyio thread limiter so blocking def endpoints don't starve
# ---------------------------------------------------------------------------
_APP_THREAD_LIMIT = int(os.getenv("APP_THREAD_LIMIT", "60"))
_PILOT_REPLAY_EXECUTOR = None
_PILOT_REPLAY_CAPACITY = None


def _start_pilot_replay_executor():
    global _PILOT_REPLAY_EXECUTOR, _PILOT_REPLAY_CAPACITY
    workers = max(1, int(os.getenv("CRAG_PILOT_REPLAY_WORKERS", "2")))
    queue_size = max(0, int(os.getenv("CRAG_PILOT_REPLAY_QUEUE_SIZE", "8")))
    _PILOT_REPLAY_EXECUTOR = ThreadPoolExecutor(
        max_workers=workers, thread_name_prefix="crag-pilot-replay"
    )
    _PILOT_REPLAY_CAPACITY = BoundedSemaphore(workers + queue_size)


def _stop_pilot_replay_executor():
    global _PILOT_REPLAY_EXECUTOR, _PILOT_REPLAY_CAPACITY
    executor = _PILOT_REPLAY_EXECUTOR
    _PILOT_REPLAY_EXECUTOR = None
    _PILOT_REPLAY_CAPACITY = None
    if executor is not None:
        executor.shutdown(wait=False, cancel_futures=True)


@asynccontextmanager
async def _lifespan(app: FastAPI):
    import asyncio
    import anyio
    anyio.to_thread.current_default_thread_limiter().total_tokens = _APP_THREAD_LIMIT
    logger.info("App server anyio thread limiter raised to %d", _APP_THREAD_LIMIT)
    _start_pilot_replay_executor()

    async def _reconcile_lifecycle_periodically():
        interval = max(60, int(os.getenv("LIFECYCLE_RECONCILE_SECONDS", "300")))
        while True:
            try:
                await anyio.to_thread.run_sync(refresh_expired_status)
            except Exception:
                logger.exception("Lifecycle reconciliation failed")
            await asyncio.sleep(interval)

    # Run once before accepting traffic, then reconcile periodically. Serving
    # surfaces also validate dates directly, so a temporary sync failure cannot
    # expose expired content.
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


def _is_admin(profile: dict[str, Any]) -> bool:
    return "admin" in [str(r).lower() for r in (profile.get("roles") or [])]


def _publication_actor(profile: dict[str, Any]) -> dict[str, Any]:
    """Bind a publish request to the server-loaded identity, never body data."""
    return {
        "reviewer": profile.get("username") or "System",
        "reviewer_id": profile.get("user_id"),
        "reviewer_roles": profile.get("roles") or [],
    }


def _publication_payload(result, response: Response | None = None) -> dict[str, Any]:
    """Keep the HTTP result aligned with the durable serving transition."""
    payload = result.payload if isinstance(result, PublicationOutcome) else result.to_dict()
    if response is not None and result.ok and result.state != "published":
        # A concurrent worker owns the outbox row.  The document is still
        # unservable, so this is accepted/pending rather than publish success.
        response.status_code = status.HTTP_202_ACCEPTED
    return payload


def _publish_document_command(
    *,
    runtime,
    job_id: int,
    doc_id: int | None,
    publish_mode: str | None,
    profile: dict[str, Any],
) -> PublicationOutcome:
    return runtime.publication_coordinator.publish_job(
        PublicationCommand(
            job_id=job_id,
            doc_id=doc_id,
            publish_mode=normalize_publish_mode(publish_mode),
        ),
        _document_actor(profile),
    )


def _assert_metadata_actor(doc_id: int, profile: dict[str, Any]) -> None:
    allowed, reason = validate_document_metadata_actor(
        doc_id,
        profile.get("user_id"),
        profile.get("roles") or [],
    )
    if not allowed:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=reason)


def _rag_base_url() -> str:
    return os.getenv("RAG_SERVER_URL", "http://127.0.0.1:8100").rstrip("/")


def _rag_headers() -> dict[str, str]:
    headers = {"Content-Type": "application/json"}
    token = os.getenv("RAG_SERVICE_TOKEN", "").strip()
    if token:
        headers["X-RAG-Service-Token"] = token
    return headers


def _sse(event: str, data: Any) -> str:
    return f"event: {event}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n"


def _iter_sse_events(response):
    event = "message"
    data_lines = []
    for raw_line in response.iter_lines(decode_unicode=True):
        line = raw_line.decode("utf-8") if isinstance(raw_line, bytes) else str(raw_line or "")
        if not line:
            if data_lines:
                raw_data = "\n".join(data_lines)
                try:
                    payload = json.loads(raw_data)
                except Exception:
                    payload = {"message": raw_data}
                yield event, payload
            event = "message"
            data_lines = []
            continue
        if line.startswith(":"):
            continue
        if line.startswith("event:"):
            event = line[6:].strip() or "message"
        elif line.startswith("data:"):
            data_lines.append(line[5:].lstrip())
    if data_lines:
        raw_data = "\n".join(data_lines)
        try:
            payload = json.loads(raw_data)
        except Exception:
            payload = {"message": raw_data}
        yield event, payload


def _pilot_route(profile: dict[str, Any], request_id: str):
    from mech_chatbot.evaluation.crag_pilot import (
        assign_pilot_route,
        load_pilot_config,
    )

    config = load_pilot_config()
    if config is None:
        return None
    return assign_pilot_route(
        config,
        user_id=str(profile.get("user_id") or ""),
        department=str(profile.get("department") or ""),
        request_id=request_id,
        sites=profile.get("allowed_sites") or [],
    )


def _pilot_outcome(answer: str, debug: dict[str, Any], *, provider_error=False):
    from mech_chatbot.evaluation.outcomes import (
        REFUSAL_OUTCOMES,
        classify_actual_outcome,
    )

    actual = classify_actual_outcome(answer)
    generation = debug.get("generation_metrics") or {}
    return {
        "refusal": actual in REFUSAL_OUTCOMES,
        "access_denied": actual == "access_denied",
        "provider_error": bool(provider_error),
        "correction_count": int(debug.get("correction_count") or 0),
        "repair_count": int(
            debug.get("repair_count") or generation.get("repair_count") or 0
        ),
        "query_type": str(
            debug.get("route") or debug.get("evaluation_group") or "unknown"
        )[:50],
    }


def _execute_pilot_replay(replay) -> None:
    from mech_chatbot.evaluation.crag_pilot import load_pilot_config, refresh_replay_auth

    try:
        current = load_pilot_config()
    except ValueError:
        current = None
    expected_target_id = (
        current.control_deployment_id
        if current is not None and replay.target_arm == "control"
        else current.candidate_deployment_id
        if current is not None
        else None
    )
    if (
        current is None
        or current.experiment_id != replay.metadata["experiment_id"]
        or current.snapshot_fingerprint != replay.metadata["snapshot_fingerprint"]
        or expected_target_id != replay.target_deployment_id
    ):
        log_trace(
            "pilot_replay_result",
            replay.metadata["matched_pair_id"],
            matched_pair_id=replay.metadata["matched_pair_id"],
            experiment_id=replay.metadata["experiment_id"],
            assigned_arm=replay.metadata["assigned_arm"],
            target_arm=replay.target_arm,
            target_deployment_id=replay.target_deployment_id,
            status="dropped",
            fallback_reason="pilot_disabled_or_contract_changed",
        )
        return
    replay = refresh_replay_auth(replay)
    headers = {**_rag_headers(), **replay.headers}
    started = time.perf_counter()
    status = "error"
    replay_trace_id = None
    error_type = None
    try:
        with requests.post(
            f"{replay.target_url}/chat/stream",
            headers=headers,
            json=replay.payload,
            timeout=(10, int(os.getenv("CRAG_PILOT_REPLAY_TIMEOUT_SECONDS", "300"))),
            stream=True,
        ) as response:
            response.raise_for_status()
            for event, payload in _iter_sse_events(response):
                if event == "done" and isinstance(payload, dict):
                    replay_trace_id = payload.get("trace_id")
                    status = "success"
                elif event == "error":
                    error_type = "replay_stream_error"
                    break
    except Exception as exc:
        error_type = type(exc).__name__
        logger.warning("CRAG pilot replay failed: %s", exc)
    finally:
        log_trace(
            "pilot_replay_result",
            replay_trace_id or replay.metadata["matched_pair_id"],
            matched_pair_id=replay.metadata["matched_pair_id"],
            experiment_id=replay.metadata["experiment_id"],
            assigned_arm=replay.metadata["assigned_arm"],
            target_arm=replay.target_arm,
            target_deployment_id=replay.target_deployment_id,
            snapshot_fingerprint=replay.metadata["snapshot_fingerprint"],
            status=status,
            latency_ms=int((time.perf_counter() - started) * 1000),
            error_type=error_type,
        )


def _schedule_pilot_replay(route, payload, outcome, trace_id, profile) -> bool:
    from mech_chatbot.evaluation.crag_pilot import (
        build_replay_request,
        should_sample_for_adjudication,
    )

    if route is None or not route.eligible:
        return False
    sampled = should_sample_for_adjudication(
        route.experiment_id, route.matched_pair_id, outcome
    )
    log_trace(
        "pilot_assignment",
        trace_id,
        experiment_id=route.experiment_id,
        matched_pair_id=route.matched_pair_id,
        assignment_version=route.assignment_version,
        assigned_arm=route.arm,
        deployment_id=route.deployment_id,
        snapshot_fingerprint=route.snapshot_fingerprint,
        cohort_sha256=route.cohort_sha256,
        actor_hash=route.actor_hash,
        department=str(profile.get("department") or "")[:100],
        roles=sorted(str(role)[:50] for role in (profile.get("roles") or [])),
        sites=sorted(str(site)[:100] for site in (profile.get("allowed_sites") or [])),
        query_type=outcome.get("query_type"),
        refusal=bool(outcome.get("refusal")),
        access_denied=bool(outcome.get("access_denied")),
        provider_error=bool(outcome.get("provider_error")),
        correction_count=int(outcome.get("correction_count") or 0),
        repair_count=int(outcome.get("repair_count") or 0),
        sampled_for_adjudication=sampled,
    )
    if not sampled:
        return False
    replay = build_replay_request(route, payload, original_trace_id=trace_id)
    executor = _PILOT_REPLAY_EXECUTOR
    capacity = _PILOT_REPLAY_CAPACITY
    if executor is None or capacity is None or not capacity.acquire(blocking=False):
        log_trace(
            "pilot_replay_result",
            trace_id,
            matched_pair_id=route.matched_pair_id,
            experiment_id=route.experiment_id,
            assigned_arm=route.arm,
            target_arm=replay.target_arm,
            target_deployment_id=replay.target_deployment_id,
            status="dropped",
            fallback_reason="replay_queue_full_or_stopped",
        )
        return False
    try:
        future = executor.submit(_execute_pilot_replay, replay)
    except Exception:
        capacity.release()
        raise
    future.add_done_callback(lambda _future: capacity.release())
    return True


def _document_actor(profile: dict[str, Any]) -> DocumentActor:
    return DocumentActor(
        user_id=_safe_int(profile.get("user_id")),
        username=profile.get("username"),
        roles=tuple(str(role) for role in (profile.get("roles") or []) if role),
        allowed_departments=tuple(
            str(dept) for dept in (profile.get("allowed_departments") or []) if dept
        ),
    )


def _read_upload_command(
    file: UploadFile,
    *,
    owner_department: str,
    shared_departments: tuple[str, ...],
    domain: str | None,
    security_level: str | None,
    process_stage: str | None,
    site: str | None,
    upload_metadata: Mapping[str, Any],
) -> UploadDocumentCommand:
    return UploadDocumentCommand(
        file_name=file.filename or "",
        content=file.file.read(),
        owner_department=owner_department,
        shared_departments=shared_departments,
        domain=domain,
        security_level=security_level,
        process_stage=process_stage,
        site=site,
        upload_metadata=upload_metadata,
    )


def _raise_upload_failure(failure: UploadFailure) -> None:
    status_code = 403 if failure.code == "unauthorized" else 400
    raise HTTPException(status_code=status_code, detail=failure.message)


def _upload_error_payload(failure: UploadFailure) -> dict[str, Any]:
    return {"file_name": failure.file_name, "error": failure.message}


def _citation_list(retrieved_docs: list[Any]) -> list[dict[str, Any]]:
    return build_citation_list(retrieved_docs, page_has_vision=page_has_vision)


def _filter_citations_by_answer(
    citations: list[dict[str, Any]],
    answer: str,
    *,
    allow_legacy_fallback: bool = False,
) -> list[dict[str, Any]]:
    """Keep sources explicitly referenced by the generated answer.

    Live answers must cite canonical SourceIDs (`D<DocID>P<PageNo>`), so a
    filename or DocID alone can never silently resolve to the wrong page.
    Filename/DocID matching remains only for explicitly legacy history rows.
    """
    return filter_citations_by_answer(
        citations,
        answer,
        allow_legacy_fallback=allow_legacy_fallback,
    )


def _answer_body_without_reference_appendix(value: str) -> str:
    """Remove the separately persisted legacy reference appendix.

    Older chat rows stored `answer + ref_text` in TraLoi_Bot. Matching source
    names against that full string would select every historical candidate,
    because the polluted appendix itself contains all filenames.
    """
    body = str(value or "")
    markers = (
        "\n\n---\n**Nguồn tham chiếu:**",
        "\n\n---\n**Nguon tham chieu:**",
        "\n\n---\n**References:**",
    )
    positions = [body.find(marker) for marker in markers if body.find(marker) >= 0]
    return body[: min(positions)].rstrip() if positions else body


def _citation_ref_text(citations: list[dict[str, Any]]) -> str:
    return citation_ref_text(citations)


def _chat_image_id_from_path(raw_path: Any) -> str | None:
    if not raw_path:
        return None
    name = os.path.basename(str(raw_path))
    return name or None


def _chat_image_url_from_path(raw_path: Any) -> str | None:
    image_id = _chat_image_id_from_path(raw_path)
    return f"/api/files/chat-images/{image_id}" if image_id else None


def _sources_for_chat_ids(chat_ids: list[int]) -> dict[int, list[dict[str, Any]]]:
    rows = app.state.runtime.app_support_queries.answer_sources_for_chat_ids(
        tuple(int(chat_id) for chat_id in chat_ids if chat_id)
    )
    out: dict[int, list[dict[str, Any]]] = {}
    for chat_id, doc_id, file_name, version_no, chunk_ref, score, has_vision in rows:
        page_no = _safe_int(chunk_ref)
        if doc_id is None or page_no is None:
            continue
        verified_vision = bool(has_vision) and page_has_vision(int(doc_id), page_no)
        out.setdefault(int(chat_id), []).append(
            {
                "doc_id": int(doc_id),
                "page_no": page_no,
                "file_name": file_name,
                "version_no": version_no,
                "score": score,
                "source_id": f"D{int(doc_id)}P{page_no}",
                "has_vision": verified_vision,
                "page_url": (
                    f"/api/files/documents/{int(doc_id)}/pages/{page_no}"
                    if verified_vision
                    else None
                ),
                "original_url": f"/api/files/documents/{int(doc_id)}/original",
            }
        )
    return out


def _decorate_history_messages(messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
    chat_ids = [int(m["chat_id"]) for m in messages if m.get("chat_id")]
    sources = _sources_for_chat_ids(chat_ids)
    for message in messages:
        image_url = _chat_image_url_from_path(message.get("image"))
        if image_url:
            message["image_url"] = image_url
        chat_id = message.get("chat_id")
        if chat_id:
            answer_body = _answer_body_without_reference_appendix(message.get("content") or "")
            filtered = _filter_citations_by_answer(
                sources.get(int(chat_id), []), answer_body, allow_legacy_fallback=True
            )
            message["content"] = answer_body
            message["citations"] = filtered
            message["ref_text"] = _citation_ref_text(filtered)
        message.pop("ref_images", None)
    return messages


auth_router = APIRouter(prefix="/api/auth", tags=["auth"])


class LoginRequest(BaseModel):
    username: str = Field(..., min_length=1, max_length=255)
    password: str = Field(..., min_length=1, max_length=255)


@auth_router.post("/login")
def login(req: LoginRequest, response: Response):
    profile = authenticate_user(req.username.strip(), req.password)
    if not profile:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Sai tên đăng nhập hoặc mật khẩu")
    token, payload = app_security.create_session_token(
        user_id=int(profile["user_id"]),
        username=str(profile["username"]),
    )
    app_security.set_session_cookie(response, token)
    return {"ok": True, "user": _public_profile(profile, csrf=payload.csrf)}


@auth_router.get("/me")
def me(request: Request):
    payload = _session_payload(request)
    profile = load_user_profile(user_id=payload.user_id, username=payload.username)
    if not profile:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="User is inactive or invalid")
    return {"ok": True, "user": _public_profile(profile, csrf=payload.csrf)}


@auth_router.post("/logout")
def logout(request: Request, response: Response):
    payload = _session_payload(request)
    app_security.require_csrf(request, payload)
    app_security.clear_session_cookie(response)
    return {"ok": True}


class LanguageRequest(BaseModel):
    language: str


@auth_router.patch("/me/preferences")
def update_preferences(req: LanguageRequest, profile: dict[str, Any] = Depends(csrf_profile)):
    if not update_user_preferred_language(profile.get("user_id"), req.language):
        raise HTTPException(status_code=400, detail="Invalid language")
    return {"ok": True}


@auth_router.post("/refresh")
def refresh_session(request: Request, response: Response):
    """Xoay vong (rotate) session token dua tren cookie hien tai va tra ve
    profile + csrf_token moi. Yeu cau CSRF de tranh bi lam dung tu cross-site.
    Frontend goi dinh ky/khi gan het han de giu phien lien tuc."""
    payload = _session_payload(request)
    app_security.require_csrf(request, payload)
    profile = load_user_profile(user_id=payload.user_id, username=payload.username)
    if not profile:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="User is inactive or invalid")
    token, new_payload = app_security.create_session_token(
        user_id=int(profile["user_id"]),
        username=str(profile["username"]),
    )
    app_security.set_session_cookie(response, token)
    return {"ok": True, "user": _public_profile(profile, csrf=new_payload.csrf)}


class ChatMessageRequest(BaseModel):
    session_id: str = Field(..., min_length=1, max_length=100)
    question: str = Field(..., min_length=1, max_length=20000)
    image_token: str | None = None
    chat_history: list[dict[str, Any]] = Field(default_factory=list)
    current_part_ids: list[str] = Field(default_factory=list)
    conversation_context: dict[str, Any] | None = None


def _sign_image_upload(user_id: int, image_id: str) -> str:
    token, _ = app_security.create_session_token(user_id=user_id, username=f"image:{image_id}", ttl_seconds=15 * 60)
    return token


def _verify_image_upload(profile: dict[str, Any], image_token: str | None) -> str | None:
    if not image_token:
        return None
    payload = app_security.verify_session_token(image_token)
    if payload.user_id != int(profile.get("user_id")):
        raise HTTPException(status_code=403, detail="Image token owner mismatch")
    if not payload.username.startswith("image:"):
        raise HTTPException(status_code=400, detail="Invalid image token")
    image_id = payload.username.split(":", 1)[1]
    path = chat_image_path(image_id)
    if not path or not path.exists():
        raise HTTPException(status_code=404, detail="Uploaded image not found")
    return str(path)


@chat_router.get("/sessions")
def sessions(profile: dict[str, Any] = Depends(current_profile)):
    return {
        "sessions": get_all_sessions(
            username=profile.get("username"),
            is_admin=False,
        )
    }


@chat_router.post("/history")
def history(body: dict[str, Any], profile: dict[str, Any] = Depends(csrf_profile)):
    session_id = str(body.get("session_id") or "").strip()
    if not session_id:
        raise HTTPException(status_code=400, detail="Missing session_id")
    messages = get_chat_history(
        session_id,
        username=profile.get("username"),
        is_admin=_is_admin(profile),
        user_clearance=profile.get("max_security_level", "public"),
        allowed_departments=profile.get("allowed_departments") or [],
        allowed_sites=profile.get("allowed_sites") or [],
    )
    return {"messages": _decorate_history_messages(messages)}


@chat_router.delete("/sessions/{session_id}")
def delete_session(session_id: str, profile: dict[str, Any] = Depends(csrf_profile)):
    return {
        "ok": True,
        "deleted": clear_chat_history(
            session_id,
            username=profile.get("username"),
            is_admin=False,
        ),
    }


@chat_router.post("/upload-image")
def upload_chat_image(
    request: Request,
    file: UploadFile = File(...),
    profile: dict[str, Any] = Depends(csrf_profile),
):
    allowed_ext = {".png", ".jpg", ".jpeg", ".bmp", ".gif", ".webp", ".tif", ".tiff"}
    ext = Path(file.filename or "").suffix.lower()
    if ext not in allowed_ext:
        raise HTTPException(status_code=400, detail="Only image files are supported")
    raw = file.file.read()
    if len(raw) > 15 * 1024 * 1024:
        raise HTTPException(status_code=400, detail="File is too large")
    out_dir = data_raw_root() / "Chat_Images"
    out_dir.mkdir(parents=True, exist_ok=True)
    image_id = f"{uuid4().hex}{ext}"
    path = out_dir / image_id
    path.write_bytes(raw)
    return {
        "ok": True,
        "image_id": image_id,
        "image_token": _sign_image_upload(int(profile["user_id"]), image_id),
        "file_name": file.filename,
    }


def _chat_actor(profile: dict[str, Any]) -> ChatActor:
    return ChatActor(
        user_id=int(profile["user_id"]),
        username=str(profile.get("username") or ""),
        roles=frozenset(str(role) for role in (profile.get("roles") or [])),
        department=(
            str(profile["department"])
            if profile.get("department") is not None
            else None
        ),
        allowed_departments=frozenset(
            str(department)
            for department in (profile.get("allowed_departments") or [])
        ),
        allowed_sites=frozenset(
            str(site) for site in (profile.get("allowed_sites") or [])
        ),
        max_security_level=str(profile.get("max_security_level") or "public"),
        response_language=str(profile.get("preferred_language") or "vi"),
    )


def _chat_event_sse(event: Any) -> str:
    if isinstance(event, ChatThinking):
        return _sse("thinking", {"message": event.message})
    if isinstance(event, ChatDelta):
        return _sse("delta", {"text": event.text})
    if isinstance(event, ChatCitation):
        return _sse("citation", dict(event.citation))
    if isinstance(event, ChatWarning):
        payload = {"message": event.message}
        if event.detail is not None:
            payload["detail"] = event.detail
        return _sse("warning", payload)
    if isinstance(event, ChatError):
        if isinstance(event.detail, dict):
            payload = dict(event.detail)
            payload.setdefault("message", event.message)
        else:
            payload = {"message": event.message}
            if event.detail is not None:
                payload["detail"] = event.detail
        if event.http_status is not None:
            payload.setdefault("status", event.http_status)
        if event.elapsed_ms is not None:
            payload.setdefault("elapsed_ms", event.elapsed_ms)
        return _sse("error", payload)
    if isinstance(event, ChatDone):
        return _sse(
            "done",
            {
                "chat_id": event.chat_id,
                "ref_text": event.ref_text,
                "citations": event.citations,
                "new_part_ids": event.new_part_ids,
                "conversation_context": event.conversation_context,
                "elapsed_ms": event.elapsed_ms,
            },
        )
    raise TypeError(f"Unsupported chat event: {type(event).__name__}")


@chat_router.post("/message")
def chat_message(
    request: Request,
    req: ChatMessageRequest,
    profile: dict[str, Any] = Depends(csrf_profile),
):
    image_path = _verify_image_upload(profile, req.image_token)
    command = ChatTurnCommand(
        request_id=f"{req.session_id}|{uuid4().hex}",
        session_id=req.session_id,
        question=req.question.strip(),
        image_path=Path(image_path) if image_path else None,
        history=tuple(dict(item) for item in req.chat_history),
        current_part_ids=tuple(req.current_part_ids),
        conversation_context=(
            dict(req.conversation_context) if req.conversation_context else None
        ),
    )
    runner = request.app.state.runtime.chat_turn_runner

    def generate():
        for event in runner.stream(command, _chat_actor(profile)):
            yield _chat_event_sse(event)

    return StreamingResponse(
        generate(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@chat_router.post("/feedback")
def feedback(body: dict[str, Any], profile: dict[str, Any] = Depends(csrf_profile)):
    chat_id = _safe_int(body.get("chat_id"))
    rating = _safe_int(body.get("rating"))
    if not chat_id or not rating:
        raise HTTPException(status_code=400, detail="Missing feedback data")
    update_chat_feedback(chat_id, 1 if rating > 0 else -1, voter_username=profile.get("username"))
    return {"ok": True}


def _file_response(path: Path, filename: str | None = None) -> FileResponse:
    if not path.exists() or not path.is_file():
        raise HTTPException(status_code=404, detail="File not found")
    media_type = mimetypes.guess_type(str(path))[0] or "application/octet-stream"
    return FileResponse(path, media_type=media_type, filename=filename)


def _resolve_protected_file(
    request: Request,
    reference: ProtectedFileReference,
    profile: dict[str, Any],
):
    try:
        return request.app.state.runtime.protected_file_resolver.resolve(
            reference,
            ProtectedFileActor.from_profile(profile),
        )
    except ProtectedFileError as exc:
        status_code = {
            "unauthorized": 403,
            "not_found": 404,
            "storage_failed": 503,
        }.get(exc.code, 500)
        raise HTTPException(status_code=status_code, detail=exc.detail) from exc


@files_router.get("/documents/{doc_id}/pages/{page_no}")
def citation_page(
    doc_id: int,
    page_no: int,
    request: Request,
    profile: dict[str, Any] = Depends(current_profile),
):
    authorized = _resolve_protected_file(
        request,
        ProtectedFileReference.page(doc_id, page_no),
        profile,
    )
    if authorized.placeholder:
        # Word/Excel/CSV sources do not necessarily produce a rendered PNG.
        # Return a harmless image placeholder instead of a broken <img>; the
        # citation still links to the protected original document.
        svg = f"""<svg xmlns="http://www.w3.org/2000/svg" width="640" height="360" viewBox="0 0 640 360">
<rect width="640" height="360" fill="#111827"/>
<rect x="24" y="24" width="592" height="312" rx="16" fill="#1f2937" stroke="#475569"/>
<text x="320" y="160" text-anchor="middle" fill="#cbd5e1" font-family="Arial,sans-serif" font-size="24">Không có ảnh xem trước</text>
<text x="320" y="202" text-anchor="middle" fill="#94a3b8" font-family="Arial,sans-serif" font-size="18">Doc {int(doc_id)} · Trang {int(page_no)}</text>
<text x="320" y="242" text-anchor="middle" fill="#64748b" font-family="Arial,sans-serif" font-size="15">Nhấn vào nguồn để mở tài liệu gốc</text>
</svg>"""
        return Response(
            content=svg,
            media_type="image/svg+xml",
            headers={"Cache-Control": "private, no-store"},
        )
    return _file_response(authorized.path)


@files_router.get("/documents/{doc_id}/original")
def original_document(
    doc_id: int,
    request: Request,
    profile: dict[str, Any] = Depends(current_profile),
):
    authorized = _resolve_protected_file(
        request,
        ProtectedFileReference.original(doc_id),
        profile,
    )
    return _file_response(authorized.path, filename=authorized.filename)


@files_router.get("/chat-images/{image_id}")
def chat_image(
    image_id: str,
    request: Request,
    profile: dict[str, Any] = Depends(current_profile),
):
    authorized = _resolve_protected_file(
        request,
        ProtectedFileReference.chat_image(image_id),
        profile,
    )
    return _file_response(authorized.path)


@data_router.get("/dashboard")
def dashboard(profile: dict[str, Any] = Depends(current_profile)):
    return get_role_dashboard(profile)


@documents_router.get("/documents")
def documents(
    dept: str | None = None,
    domain: str | None = None,
    sec: str | None = None,
    eff_mode: str | None = None,
    bucket: str | None = None,
    soon_days: int = 30,
    search: str | None = None,
    profile: dict[str, Any] = Depends(current_profile),
):
    global_read_admin = _is_admin(profile)
    legacy_bucket = {"con": "effective", "sap": "expiring_soon", "het": "expired"}.get(eff_mode)
    requested_bucket = str(bucket or legacy_bucket or "").strip().lower().replace("-", "_") or None
    if requested_bucket and requested_bucket not in {"effective", "expired", "expiring_soon", "needs_review"}:
        raise HTTPException(status_code=422, detail="Invalid lifecycle bucket")
    if requested_bucket and requested_bucket != "effective" and not role_allows(profile.get("roles"), "reviewer"):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Lifecycle bucket requires reviewer access")
    rows = list_documents(
        allowed_departments=profile.get("allowed_departments") or [],
        max_security_level=profile.get("max_security_level") or "public",
        allowed_sites=profile.get("allowed_sites") or [],
        dept=dept,
        domain=domain,
        sec=sec,
        eff_mode=eff_mode,
        bucket=requested_bucket,
        soon_days=soon_days,
        search_kw=search,
        global_read_admin=global_read_admin,
    )
    if global_read_admin:
        write_audit_log(
            profile.get("username"),
            "admin_global_read_catalog",
            "TaiLieu",
            None,
            {
                "result_count": len(rows),
                "department_filter": dept,
                "domain_filter": domain,
                "security_filter": sec,
                "effective_filter": eff_mode,
                "has_search": bool(search),
            },
        )
    return {"documents": [dict(row._mapping) if hasattr(row, "_mapping") else list(row) for row in rows]}


@documents_router.get("/documents/lifecycle-counts")
def document_lifecycle_counts(soon_days: int = 30, profile: dict[str, Any] = Depends(current_profile)):
    counts = get_document_lifecycle_counts(
        allowed_departments=profile.get("allowed_departments") or [],
        max_security_level=profile.get("max_security_level") or "public",
        allowed_sites=profile.get("allowed_sites") or [],
        global_read_admin=_is_admin(profile),
        soon_days=soon_days,
    )
    if not role_allows(profile.get("roles"), "reviewer"):
        counts = {"effective": counts.get("effective", 0)}
    return {"counts": counts, "soon_days": max(0, min(int(soon_days), 365))}


@documents_router.post("/documents/upload")
def documents_upload(
    request: Request,
    file: UploadFile = File(...),
    thu_muc: str = Form(...),
    domain: str | None = Form(None),
    security_level: str | None = Form(None),
    cong_doan: str | None = Form(None),
    site: str | None = Form(None),
    meta_json: str | None = Form(None),
    extra_departments_json: str | None = Form(None),
    profile: dict[str, Any] = Depends(csrf_profile),
):
    """Nhan file tai len tu web-ui, luu vao Uploads/<thu_muc> va tao IngestionJob
    (Status='pending') de worker xu ly. Yeu cau vai tro uploader/reviewer/admin
    + CSRF. Tra ve job_id de UI dieu huong sang trang tien trinh ingest."""
    _assert_any_role(profile, "uploader", "reviewer", "admin")
    dept = (thu_muc or "").strip()
    upload_meta = _parse_json_obj(meta_json, "meta_json")
    extra_departments = _parse_json_or_csv_list(extra_departments_json, "extra_departments_json")
    command = _read_upload_command(
        file,
        owner_department=dept,
        shared_departments=tuple(extra_departments),
        domain=domain,
        security_level=security_level,
        process_stage=cong_doan,
        site=site,
        upload_metadata=upload_meta,
    )
    try:
        receipt = request.app.state.runtime.document_upload.enqueue(
            command,
            _document_actor(profile),
        )
    except UploadRejected as exc:
        _raise_upload_failure(exc.failure)
    return {"ok": True, "job_id": receipt.job_id, "file_name": receipt.file_name}


@documents_router.post("/documents/upload-batch")
def documents_upload_batch(
    request: Request,
    files: list[UploadFile] = File(...),
    thu_muc: str | None = Form(None),
    domain: str | None = Form(None),
    security_level: str | None = Form(None),
    cong_doan: str | None = Form(None),
    site: str | None = Form(None),
    meta_json: str | None = Form(None),
    extra_departments_json: str | None = Form(None),
    assignments_json: str | None = Form(None),
    profile: dict[str, Any] = Depends(csrf_profile),
):
    _assert_any_role(profile, "uploader", "reviewer", "admin")
    if not files:
        raise HTTPException(status_code=400, detail="Chưa chọn tệp")
    if len(files) > 50:
        raise HTTPException(status_code=400, detail="Một lần upload tối đa 50 tệp")

    upload_meta = _parse_json_obj(meta_json, "meta_json")
    assignments = _parse_json_list(assignments_json, "assignments_json")
    default_dept = (thu_muc or "").strip()
    default_extra = _parse_json_or_csv_list(extra_departments_json, "extra_departments_json")
    errors: list[dict[str, Any]] = []
    commands: list[UploadDocumentCommand] = []

    for index, upload in enumerate(files):
        assignment = assignments[index] if index < len(assignments) and isinstance(assignments[index], dict) else {}
        dept = str(assignment.get("thu_muc") or default_dept).strip()
        if not dept:
            errors.append({"file_name": upload.filename, "error": "Thiếu phòng ban"})
            continue
        extra = _split_csv(assignment.get("extra_departments") or default_extra)
        commands.append(
            _read_upload_command(
                upload,
                owner_department=dept,
                shared_departments=tuple(extra),
                domain=assignment.get("domain") or domain,
                security_level=assignment.get("security_level") or security_level,
                process_stage=assignment.get("cong_doan") or cong_doan,
                site=assignment.get("site") or site,
                upload_metadata=upload_meta,
            )
        )

    result = request.app.state.runtime.document_upload.enqueue_batch(
        tuple(commands),
        _document_actor(profile),
    )
    created = [
        {
            "job_id": receipt.job_id,
            "file_name": receipt.file_name,
            "thu_muc": receipt.owner_department,
        }
        for receipt in result.jobs
    ]
    errors.extend(_upload_error_payload(failure) for failure in result.errors)
    return {
        "ok": not errors,
        "jobs": created,
        "errors": errors,
        "created": len(created),
        "failed": len(errors),
    }


@documents_router.get("/ingestion/jobs")
def ingestion_jobs(status_value: str | None = None, profile: dict[str, Any] = Depends(current_profile)):
    rows = list_ingestion_jobs(
        status=status_value,
        is_admin=False,
        username=profile.get("username"),
        allowed_departments=profile.get("allowed_departments") or [],
    )
    return {"jobs": _rows_to_json(rows)}


@data_router.get("/analytics/usage")
def usage(days: int = 30, profile: dict[str, Any] = Depends(require_any_role("reviewer", "admin"))):
    return get_usage_analytics(days=days)


@data_router.get("/analytics/observability")
def observability(days: int = 30, profile: dict[str, Any] = Depends(require_any_role("platform_admin"))):
    return get_observability(days=days)


@data_router.get("/audit")
def audit(limit: int = 100, profile: dict[str, Any] = Depends(require_any_role("platform_admin"))):
    rows = list_audit_logs(row_limit=limit)
    return {"logs": _rows_to_json(rows)}


@data_router.post("/access/request")
def access_request(body: dict[str, Any], profile: dict[str, Any] = Depends(csrf_profile)):
    result = create_access_request(
        user_id=profile.get("user_id"),
        username=profile.get("username"),
        request_type=str(body.get("request_type") or ""),
        requested_level=body.get("requested_level"),
        requested_dept=body.get("requested_dept"),
        question_text=body.get("question_text"),
        reason=body.get("reason"),
    )
    if not result:
        raise HTTPException(status_code=400, detail="Could not create access request")
    return result


@data_router.get("/access/requests")
def access_requests(
    status_value: str = "pending",
    limit: int = 200,
    profile: dict[str, Any] = Depends(require_any_role("security_admin")),
):
    return {
        "requests": _rows_to_json(list_access_requests(status=status_value, limit=limit)),
        "pending_count": count_pending_access_requests(),
    }


@data_router.get("/access/my-requests")
def my_access_requests(limit: int = 50, profile: dict[str, Any] = Depends(current_profile)):
    return {"requests": _rows_to_json(get_user_access_requests(profile.get("user_id"), limit=limit))}


@data_router.post("/access/requests/{request_id}/resolve")
def access_request_resolve(request_id: int, body: dict[str, Any], profile: dict[str, Any] = Depends(csrf_profile)):
    _assert_any_role(profile, "security_admin")
    result = resolve_access_request(
        request_id=request_id,
        decision=str(body.get("decision") or ""),
        reviewer_username=profile.get("username"),
        reviewer_id=profile.get("user_id"),
        review_note=body.get("review_note"),
    )
    return {"ok": bool(result), "result": result}


@data_router.get("/access/users")
def access_users(limit: int = 1000, profile: dict[str, Any] = Depends(require_any_role("security_admin"))):
    return {"users": _rows_to_json(list_users_with_access(limit=limit))}


@data_router.get("/access/grants")
def access_grants(limit: int = 100, profile: dict[str, Any] = Depends(require_any_role("security_admin"))):
    return {"grants": _rows_to_json(get_grant_history(limit=limit))}


@data_router.post("/access/users/{user_id}/revoke-clearance")
def access_revoke_clearance(user_id: int, body: dict[str, Any], profile: dict[str, Any] = Depends(csrf_profile)):
    _assert_any_role(profile, "security_admin")
    result = revoke_user_clearance(
        user_id=user_id,
        new_level=body.get("new_level") or "public",
        actor_username=profile.get("username"),
        actor_id=profile.get("user_id"),
        reason=body.get("reason"),
    )
    return {"ok": bool(result), "result": result}


@data_router.post("/access/users/{user_id}/revoke-department")
def access_revoke_department(user_id: int, body: dict[str, Any], profile: dict[str, Any] = Depends(csrf_profile)):
    _assert_any_role(profile, "security_admin")
    result = revoke_user_department(
        user_id=user_id,
        dept=str(body.get("department") or ""),
        actor_username=profile.get("username"),
        actor_id=profile.get("user_id"),
        reason=body.get("reason"),
    )
    return {"ok": bool(result), "result": result}


@documents_router.get("/documents/pending-review")
def documents_pending_review(profile: dict[str, Any] = Depends(require_any_role("reviewer", "admin"))):
    return {"documents": _rows_to_json(list_pending_review_docs())}


@documents_router.post("/documents/reconcile-serving")
def documents_reconcile_serving(
    body: dict[str, Any],
    profile: dict[str, Any] = Depends(csrf_profile),
):
    _assert_any_role(profile, "platform_admin")
    return reconcile_serving_state(
        limit=_safe_int(body.get("limit")) or 500,
        worker_id=f"admin:{profile.get('username') or 'System'}",
    )


@documents_router.get("/documents/expiring")
def documents_expiring(profile: dict[str, Any] = Depends(require_any_role("reviewer", "admin"))):
    return {"documents": _rows_to_json(list_expiring_documents())}


@documents_router.get("/documents/bulk-meta")
def documents_bulk_meta(
    dept: str | None = None,
    domain: str | None = None,
    profile: dict[str, Any] = Depends(require_any_role("reviewer", "admin")),
):
    if dept:
        for suffix in (" (disabled)", " (archived)"):
            if dept.endswith(suffix):
                dept = dept[: -len(suffix)]
                break
    return {
        "documents": _rows_to_json(list_docs_for_bulk_meta(dept=dept, domain=domain)),
        "departments": list_bulk_meta_departments(),
    }


@documents_router.patch("/documents/bulk-metadata")
def documents_bulk_metadata(body: dict[str, Any], profile: dict[str, Any] = Depends(csrf_profile)):
    raw_ids = body.get("doc_ids") or []
    metadata = body.get("metadata") or {}
    if not isinstance(raw_ids, list) or not raw_ids:
        raise HTTPException(status_code=400, detail="doc_ids không hợp lệ")
    if not isinstance(metadata, dict) or not metadata:
        raise HTTPException(status_code=400, detail="metadata không hợp lệ")
    ok = 0
    fail = 0
    pending = 0
    fields = {k: v for k, v in metadata.items() if k not in {"attributes", "domain"} and v not in (None, "")}
    attrs = metadata.get("attributes")
    domain = metadata.get("domain")
    for raw_id in raw_ids:
        doc_id = _safe_int(raw_id)
        if not doc_id:
            fail += 1
            continue
        try:
            _assert_metadata_actor(doc_id, profile)
            result = update_document_common_metadata(
                doc_id,
                reviewer=profile.get("username") or "System",
                attributes=attrs,
                domain=domain,
                **fields,
            )
            ok += 1 if result else 0
            fail += 0 if result else 1
        except Exception:
            logger.exception("bulk metadata update failed for doc_id=%s", doc_id)
            fail += 1
    return {"ok": fail == 0, "updated": ok, "failed": fail}


def _documents_review_bulk_impl(
    body: dict[str, Any],
    profile: dict[str, Any],
    runtime: Any,
):
    _assert_any_role(profile, "reviewer", "admin")
    items = body.get("items") or []
    action = str(body.get("action") or "").strip()
    publish_mode = normalize_publish_mode(str(body.get("publish_mode") or "standalone").strip())
    reason = str(body.get("reason") or "")
    if not isinstance(items, list) or not items:
        raise HTTPException(status_code=400, detail="items không hợp lệ")
    if action not in {"publish", "reject", "delete"}:
        raise HTTPException(status_code=400, detail="action không hợp lệ")
    review_items = tuple(
        ReviewItem(
            job_id=_safe_int(item.get("job_id")) if isinstance(item, dict) else None,
            doc_id=_safe_int(item.get("doc_id")) if isinstance(item, dict) else None,
        )
        for item in items
    )
    result = runtime.review_documents.execute(
        ReviewDocumentsCommand(
            action=action,
            publish_mode=publish_mode,
            reason=reason,
            items=review_items,
        ),
        _document_actor(profile),
    )
    return {
        "ok": result.ok,
        "updated": result.updated,
        "pending": result.pending,
        "failed": result.failed,
        "failures": list(result.failures),
    }


def documents_review_bulk(
    body: dict[str, Any],
    profile: dict[str, Any] = Depends(csrf_profile),
):
    return _documents_review_bulk_impl(body, profile, app.state.runtime)


@documents_router.post("/documents/review/bulk")
def documents_review_bulk_route(
    body: dict[str, Any],
    request: Request,
    profile: dict[str, Any] = Depends(csrf_profile),
):
    return _documents_review_bulk_impl(body, profile, request.app.state.runtime)


@documents_router.patch("/documents/{doc_id}/current")
def document_set_current(doc_id: int, profile: dict[str, Any] = Depends(csrf_profile)):
    _assert_any_role(profile, "reviewer", "admin")
    # Directly flipping IsCurrent bypasses the publication contract and can
    # expose a Qdrant/SQL mixed serving state.  Kept as an explicit response
    # for older clients instead of silently retaining the unsafe operation.
    raise HTTPException(
        status_code=status.HTTP_409_CONFLICT,
        detail="Dùng publish-new-version hoặc publish-standalone để đổi tài liệu hiện hành",
    )


@documents_router.patch("/documents/{doc_id}/expired")
def document_mark_expired(doc_id: int, profile: dict[str, Any] = Depends(csrf_profile)):
    _assert_any_role(profile, "reviewer", "admin")
    _assert_metadata_actor(doc_id, profile)
    return {"ok": bool(mark_document_expired(doc_id, reviewer=profile.get("username") or "System"))}


@documents_router.patch("/documents/{doc_id}/metadata")
def document_update_metadata(doc_id: int, body: dict[str, Any], profile: dict[str, Any] = Depends(csrf_profile)):
    _assert_metadata_actor(doc_id, profile)
    fields = {k: v for k, v in body.items() if k not in {"attributes", "domain"}}
    result = update_document_common_metadata(
        doc_id,
        reviewer=profile.get("username") or "System",
        attributes=body.get("attributes"),
        domain=body.get("domain"),
        **fields,
    )
    return {"ok": bool(result), "result": result}


@documents_router.get("/documents/{doc_id}/publish-contract")
def document_publish_contract(
    doc_id: int,
    profile: dict[str, Any] = Depends(require_any_role("reviewer", "admin")),
):
    return validate_publish_contract(doc_id).to_dict()


@documents_router.patch("/documents/{doc_id}/governance")
def document_update_governance(
    doc_id: int,
    body: dict[str, Any],
    profile: dict[str, Any] = Depends(csrf_profile),
):
    _assert_any_role(profile, "platform_admin")
    try:
        result = update_document_governance_metadata(
            doc_id,
            knowledge_owner_user_id=body.get("knowledge_owner_user_id"),
            knowledge_approver_user_id=body.get("knowledge_approver_user_id"),
            taxonomy_version=body.get("taxonomy_version"),
            parent_applicable=body.get("parent_applicable"),
            parent_section=body.get("parent_section"),
            parent_page=body.get("parent_page"),
            updated_by=profile.get("username") or "System",
        )
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except RuntimeError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    if not result:
        raise HTTPException(status_code=404, detail="Khong tim thay tai lieu")
    return {"ok": True}


@documents_router.patch("/documents/{doc_id}/site")
def document_backfill_site(
    doc_id: int,
    body: dict[str, Any],
    profile: dict[str, Any] = Depends(csrf_profile),
):
    """Platform-only remediation for documents blocked by the strict site gate."""
    _assert_any_role(profile, "platform_admin")
    site = str(body.get("site") or "").strip()
    if not site:
        raise HTTPException(status_code=422, detail="site la bat buoc khi backfill")
    result = update_document_common_metadata(
        doc_id,
        reviewer=profile.get("username") or "System",
        site=site,
    )
    if not result:
        raise HTTPException(status_code=404, detail="Khong cap nhat duoc site cho tai lieu")
    return {"ok": True, "doc_id": doc_id, "site": site}


@documents_router.post("/documents/{doc_id}/publish-new-version")
def document_publish_new_version(
    doc_id: int,
    request: Request,
    response: Response,
    profile: dict[str, Any] = Depends(csrf_profile),
):
    result = _publish_document_command(
        runtime=request.app.state.runtime,
        job_id=0,
        doc_id=doc_id,
        publish_mode="new_version",
        profile=profile,
    )
    return _publication_payload(result, response)


@documents_router.post("/documents/{doc_id}/publish-new-variant")
def document_publish_new_variant(
    doc_id: int,
    request: Request,
    response: Response,
    profile: dict[str, Any] = Depends(csrf_profile),
):
    result = _publish_document_command(
        runtime=request.app.state.runtime,
        job_id=0,
        doc_id=doc_id,
        publish_mode="new_variant",
        profile=profile,
    )
    return _publication_payload(result, response)


@documents_router.post("/documents/{doc_id}/publish-standalone")
def document_publish_standalone(
    doc_id: int,
    request: Request,
    response: Response,
    profile: dict[str, Any] = Depends(csrf_profile),
):
    result = _publish_document_command(
        runtime=request.app.state.runtime,
        job_id=0,
        doc_id=doc_id,
        publish_mode="standalone",
        profile=profile,
    )
    return _publication_payload(result, response)


@documents_router.post("/documents/{doc_id}/reject")
def document_reject(doc_id: int, profile: dict[str, Any] = Depends(csrf_profile)):
    _assert_any_role(profile, "reviewer", "admin")
    return {"ok": bool(reject_document(doc_id, reviewer=profile.get("username") or "System"))}


@documents_router.post("/documents/{doc_id}/archive")
def document_archive(doc_id: int, profile: dict[str, Any] = Depends(csrf_profile)):
    _assert_any_role(profile, "reviewer", "admin")
    return {"ok": bool(archive_document(doc_id, reviewer=profile.get("username") or "System"))}


@documents_router.delete("/documents/{doc_id}")
def document_delete(doc_id: int, profile: dict[str, Any] = Depends(csrf_profile)):
    _assert_any_role(profile, "admin")
    return {"ok": bool(delete_document_completely(doc_id, reviewer=profile.get("username") or "System"))}


@documents_router.get("/ingestion/eta")
def ingestion_eta(profile: dict[str, Any] = Depends(require_any_role("uploader", "reviewer", "admin"))):
    eta = queue_eta_seconds()
    if isinstance(eta, dict):
        return eta
    return {"pending": 0, "avg_seconds": 0, "eta_seconds": eta}


@documents_router.get("/ingestion/bulk-action-jobs")
def ingestion_bulk_jobs(profile: dict[str, Any] = Depends(require_any_role("reviewer", "admin"))):
    return {"jobs": _rows_to_json(list_bulk_action_jobs())}


@documents_router.post("/ingestion/jobs/bulk-delete")
def ingestion_bulk_delete(body: dict[str, Any], profile: dict[str, Any] = Depends(csrf_profile)):
    _assert_any_role(profile, "admin")
    return {"ok": bool(bulk_delete_ingestion_jobs(body.get("ids") or []))}


@documents_router.patch("/ingestion/jobs/{job_id}/priority")
def ingestion_set_priority(job_id: int, body: dict[str, Any], profile: dict[str, Any] = Depends(csrf_profile)):
    _assert_any_role(profile, "reviewer", "admin")
    return {"ok": bool(set_job_priority(job_id, _safe_int(body.get("priority")) or 0))}


@documents_router.post("/ingestion/jobs/{job_id}/cancel")
def ingestion_cancel(job_id: int, profile: dict[str, Any] = Depends(csrf_profile)):
    _assert_any_role(profile, "uploader", "reviewer", "admin")
    return {"ok": bool(cancel_job(job_id, canceled_by=profile.get("username") or "System"))}


@documents_router.post("/ingestion/jobs/{job_id}/requeue")
def ingestion_requeue(job_id: int, profile: dict[str, Any] = Depends(csrf_profile)):
    _assert_any_role(profile, "reviewer", "admin")
    return {"ok": bool(requeue_job(job_id))}


@documents_router.post("/ingestion/jobs/{job_id}/pending-review")
def ingestion_pending_review(job_id: int, profile: dict[str, Any] = Depends(csrf_profile)):
    _assert_any_role(profile, "reviewer", "admin")
    return {"ok": bool(mark_job_pending_review(job_id))}


@documents_router.post("/ingestion/jobs/{job_id}/publish")
def ingestion_publish(
    job_id: int,
    request: Request,
    response: Response,
    profile: dict[str, Any] = Depends(csrf_profile),
):
    # Never let a queue row say "published" without passing the document
    # contract and Qdrant serving transition.
    result = _publish_document_command(
        runtime=request.app.state.runtime,
        job_id=job_id,
        doc_id=None,
        publish_mode="standalone",
        profile=profile,
    )
    if result.state == "not_found":
        raise HTTPException(status_code=404, detail="Không tìm thấy tài liệu của ingestion job")
    return _publication_payload(result, response)


@documents_router.post("/ingestion/jobs/{job_id}/reject")
def ingestion_reject(job_id: int, body: dict[str, Any], profile: dict[str, Any] = Depends(csrf_profile)):
    _assert_any_role(profile, "reviewer", "admin")
    reason = str(body.get("reason") or "")
    return {"ok": bool(reject_ingestion_job(job_id, reason) or mark_job_rejected(job_id))}


@documents_router.delete("/ingestion/jobs/{job_id}")
def ingestion_delete(job_id: int, profile: dict[str, Any] = Depends(csrf_profile)):
    _assert_any_role(profile, "admin")
    return {"ok": bool(delete_ingestion_job(job_id))}


@data_router.get("/users")
def users(profile: dict[str, Any] = Depends(require_any_role("security_admin"))):
    return {"users": _rows_to_json(list_users_basic())}


@data_router.get("/users/{user_id}")
def user_detail(user_id: int, profile: dict[str, Any] = Depends(require_any_role("security_admin"))):
    return {
        "user_id": user_id,
        "roles": get_user_roles(user_id),
        "departments": get_user_departments(user_id),
        "clearance": get_user_clearance(user_id),
        "sites": get_user_sites(user_id),
    }


@data_router.post("/users")
def user_create(body: dict[str, Any], profile: dict[str, Any] = Depends(csrf_profile)):
    _assert_any_role(profile, "security_admin")
    password = str(body.get("password") or "")
    if len(password) < 8:
        raise HTTPException(status_code=400, detail="Password must be at least 8 characters")
    password_hash = bcrypt.hashpw(password.encode("utf-8"), bcrypt.gensalt()).decode("utf-8")
    result = create_user_with_roles(
        username=str(body.get("username") or "").strip(),
        password_hash=password_hash,
        display_name=body.get("display_name"),
        department=body.get("department"),
        selected_roles=body.get("roles") or [],
        depts=body.get("departments") or [],
    )
    if result:
        user_id = _safe_int(result.get("user_id") if isinstance(result, dict) else result)
        if user_id:
            set_user_sites(user_id, body.get("sites") or [])
            set_user_clearance(user_id, body.get("max_level") or "public")
    return {"ok": bool(result), "result": result}


@data_router.patch("/users/{user_id}/active")
def user_active(user_id: int, body: dict[str, Any], profile: dict[str, Any] = Depends(csrf_profile)):
    _assert_any_role(profile, "security_admin")
    return {
        "ok": bool(
            set_user_active_status(
                user_id,
                bool(body.get("is_active")),
                actor_username=profile.get("username"),
                actor_id=profile.get("user_id"),
            )
        )
    }


@data_router.patch("/users/{user_id}/roles")
def user_roles(user_id: int, body: dict[str, Any], profile: dict[str, Any] = Depends(csrf_profile)):
    _assert_any_role(profile, "security_admin")
    return {
        "ok": bool(
            update_user_active_and_roles(
                user_id,
                bool(body.get("is_active", True)),
                body.get("add_roles") or [],
                body.get("del_roles") or [],
            )
        )
    }


@data_router.patch("/users/{user_id}/departments")
def user_departments(user_id: int, body: dict[str, Any], profile: dict[str, Any] = Depends(csrf_profile)):
    _assert_any_role(profile, "security_admin")
    return {"ok": bool(set_user_departments(user_id, body.get("departments") or []))}


@data_router.patch("/users/{user_id}/sites")
def user_sites(user_id: int, body: dict[str, Any], profile: dict[str, Any] = Depends(csrf_profile)):
    _assert_any_role(profile, "security_admin")
    return {"ok": bool(set_user_sites(user_id, body.get("sites") or []))}


@data_router.patch("/users/{user_id}/clearance")
def user_clearance(user_id: int, body: dict[str, Any], profile: dict[str, Any] = Depends(csrf_profile)):
    _assert_any_role(profile, "security_admin")
    return {"ok": bool(set_user_clearance(user_id, body.get("max_level") or "public"))}


@data_router.patch("/users/{user_id}/password")
def user_password(user_id: int, body: dict[str, Any], profile: dict[str, Any] = Depends(csrf_profile)):
    _assert_any_role(profile, "security_admin")
    password = str(body.get("password") or "")
    if len(password) < 8:
        raise HTTPException(status_code=400, detail="Password must be at least 8 characters")
    password_hash = bcrypt.hashpw(password.encode("utf-8"), bcrypt.gensalt()).decode("utf-8")
    return {"ok": bool(update_user_password(user_id, password_hash))}


@data_router.delete("/users/{user_id}")
def user_delete(user_id: int, profile: dict[str, Any] = Depends(csrf_profile)):
    _assert_any_role(profile, "security_admin")
    return {
        "ok": bool(
            delete_user_account(
                user_id,
                actor_username=profile.get("username"),
                actor_id=profile.get("user_id"),
            )
        )
    }


@data_router.get("/catalog/departments")
def catalog_departments(active_only: bool = True, profile: dict[str, Any] = Depends(current_profile)):
    return {"departments": list_known_departments(active_only=active_only)}


@data_router.get("/catalog/missing-site-documents")
def catalog_missing_site_documents(
    limit: int = 500,
    profile: dict[str, Any] = Depends(require_any_role("platform_admin")),
):
    return {"documents": list_missing_site_documents(limit=limit)}


@data_router.get("/catalog/knowledge-governance")
def catalog_knowledge_governance(
    profile: dict[str, Any] = Depends(require_any_role("platform_admin")),
):
    return {"governance": list_department_knowledge_governance()}


@data_router.get("/catalog/domain-profiles")
def catalog_domain_profiles(
    profile: dict[str, Any] = Depends(require_any_role("platform_admin")),
):
    return {"profiles": list_department_domain_profiles()}


@data_router.get("/catalog/rollout/readiness")
def catalog_rollout_readiness(
    department_code: str | None = None,
    profile: dict[str, Any] = Depends(require_any_role("platform_admin")),
):
    return {"departments": get_department_rollout_readiness(department_code)}


@data_router.get("/catalog/rollout/plans")
def catalog_rollout_plans(
    profile: dict[str, Any] = Depends(require_any_role("platform_admin")),
):
    return {"plans": list_department_rollout_plans()}


@data_router.put("/catalog/departments/{code}/rollout-plan")
def catalog_rollout_plan_set(
    code: str,
    body: dict[str, Any],
    profile: dict[str, Any] = Depends(csrf_profile),
):
    _assert_any_role(profile, "platform_admin")
    try:
        plan = upsert_department_rollout_plan(
            code,
            wave_number=body.get("wave_number"),
            rollout_status=body.get("rollout_status") or "planned",
            evaluation_question_target=body.get("evaluation_question_target") or 75,
            updated_by=profile.get("username") or "System",
        )
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return {"ok": True, "plan": plan}


@data_router.post("/catalog/departments/{code}/evaluation-gate")
def catalog_evaluation_gate_record(
    code: str,
    body: dict[str, Any],
    profile: dict[str, Any] = Depends(csrf_profile),
):
    _assert_any_role(profile, "platform_admin")
    try:
        gate = record_department_evaluation_gate(
            code,
            batch_id=body.get("batch_id"),
            question_count=body.get("question_count"),
            source_top5_rate=body.get("source_top5_rate"),
            citation_or_refusal_rate=body.get("citation_or_refusal_rate"),
            evidence_support_rate=body.get("evidence_support_rate"),
            rbac_site_publication_leaks=body.get("rbac_site_publication_leaks", 0),
            notes=body.get("notes"),
            evaluated_by=profile.get("username") or "System",
        )
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return {"ok": True, "gate": gate}


@data_router.get("/catalog/departments/{code}/knowledge-governance")
def catalog_department_governance(
    code: str,
    profile: dict[str, Any] = Depends(require_any_role("platform_admin")),
):
    result = get_department_knowledge_governance(code)
    if result is None:
        raise HTTPException(status_code=404, detail="Khong tim thay knowledge governance cua phong ban")
    return result


@data_router.put("/catalog/departments/{code}/knowledge-governance")
def catalog_department_governance_set(
    code: str,
    body: dict[str, Any],
    profile: dict[str, Any] = Depends(csrf_profile),
):
    _assert_any_role(profile, "platform_admin")
    try:
        saved = upsert_department_knowledge_governance(
            code,
            knowledge_owner_user_id=body.get("knowledge_owner_user_id"),
            knowledge_approver_user_id=body.get("knowledge_approver_user_id"),
            taxonomy_version=body.get("taxonomy_version"),
            external_processing_policy=body.get("external_processing_policy") or "all_external",
            is_active=bool(body.get("is_active", True)),
            updated_by=profile.get("username") or "System",
        )
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return {"ok": True, "governance": saved}


@data_router.get("/catalog/departments/{code}/domain-profile")
def catalog_department_domain_profile(
    code: str,
    profile: dict[str, Any] = Depends(require_any_role("platform_admin")),
):
    result = get_department_domain_profile(code)
    if result is None:
        raise HTTPException(status_code=404, detail="Khong tim thay domain profile cua phong ban")
    return result


@data_router.put("/catalog/departments/{code}/domain-profile")
def catalog_department_domain_profile_set(
    code: str,
    body: dict[str, Any],
    profile: dict[str, Any] = Depends(csrf_profile),
):
    _assert_any_role(profile, "platform_admin")
    try:
        saved = upsert_department_domain_profile(
            code,
            document_types=body.get("document_types") or [],
            required_metadata=body.get("required_metadata") or [],
            router_patterns=body.get("router_patterns") or [],
            parent_context_enabled=bool(body.get("parent_context_enabled", True)),
            is_active=bool(body.get("is_active", True)),
            updated_by=profile.get("username") or "System",
        )
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return {"ok": True, "profile": saved}


@data_router.get("/catalog/departments/{code}")
def catalog_department(code: str, profile: dict[str, Any] = Depends(require_any_role("platform_admin"))):
    return get_department_summary(code)


@data_router.post("/catalog/departments")
def catalog_department_upsert(body: dict[str, Any], profile: dict[str, Any] = Depends(csrf_profile)):
    _assert_any_role(profile, "platform_admin")
    return {
        "ok": bool(
            upsert_department(
                code=str(body.get("code") or ""),
                name=body.get("name"),
                domain=body.get("domain"),
                site=body.get("site"),
                is_active=bool(body.get("is_active", True)),
                status=body.get("status"),
            )
        )
    }


@data_router.patch("/catalog/departments/{code}/status")
def catalog_department_status(code: str, body: dict[str, Any], profile: dict[str, Any] = Depends(csrf_profile)):
    _assert_any_role(profile, "platform_admin")
    return {
        "ok": bool(
            set_department_status(
                code,
                status=str(body.get("status") or ""),
                actor=profile.get("username") or "System",
                force=bool(body.get("force", False)),
            )
        )
    }


@data_router.post("/catalog/departments/{code}/archive")
def catalog_department_archive(code: str, body: dict[str, Any], profile: dict[str, Any] = Depends(csrf_profile)):
    _assert_any_role(profile, "platform_admin")
    return {
        "ok": bool(
            archive_department(
                code,
                actor=profile.get("username") or "System",
                force=bool(body.get("force", False)),
            )
        )
    }


@data_router.post("/catalog/departments/reassign")
def catalog_department_reassign(body: dict[str, Any], profile: dict[str, Any] = Depends(csrf_profile)):
    _assert_any_role(profile, "platform_admin")
    return {
        "ok": bool(
            reassign_department_data(
                source_code=str(body.get("source_code") or ""),
                target_code=str(body.get("target_code") or ""),
                actor=profile.get("username") or "System",
                move_users=bool(body.get("move_users", True)),
            )
        )
    }


@data_router.get("/catalog/sites")
def catalog_sites(active_only: bool = True, profile: dict[str, Any] = Depends(current_profile)):
    return {"sites": list_known_sites(active_only=active_only)}


@data_router.post("/catalog/sites")
def catalog_site_upsert(body: dict[str, Any], profile: dict[str, Any] = Depends(csrf_profile)):
    _assert_any_role(profile, "platform_admin")
    return {
        "ok": bool(
            upsert_site(
                code=str(body.get("code") or ""),
                name=body.get("name"),
                is_active=bool(body.get("is_active", True)),
            )
        )
    }


@data_router.get("/glossary")
def glossary(domain: str | None = None, active_only: bool = False, profile: dict[str, Any] = Depends(current_profile)):
    return {"terms": _rows_to_json(list_domain_glossary(domain=domain, active_only=active_only))}


@data_router.post("/glossary")
def glossary_upsert(body: dict[str, Any], profile: dict[str, Any] = Depends(csrf_profile)):
    _assert_any_role(profile, "admin", "reviewer")
    result = upsert_glossary_term(
        term=str(body.get("term") or ""),
        domain=body.get("domain"),
        synonyms=body.get("synonyms"),
        expansion=body.get("expansion"),
        is_active=bool(body.get("is_active", True)),
        glossary_id=body.get("glossary_id"),
    )
    # upsert_glossary_term tra ve dict {"ok": bool, ...}; bool(dict) luon True nen phai lay .get("ok").
    if isinstance(result, dict):
        if not result.get("ok"):
            raise HTTPException(status_code=400, detail=result.get("message") or "Khong luu duoc thuat ngu")
        return result
    return {"ok": bool(result)}


@data_router.patch("/glossary/{glossary_id}/active")
def glossary_active(glossary_id: int, body: dict[str, Any], profile: dict[str, Any] = Depends(csrf_profile)):
    _assert_any_role(profile, "admin", "reviewer")
    return {"ok": bool(set_glossary_active(glossary_id, bool(body.get("is_active"))))}


@data_router.delete("/glossary/{glossary_id}")
def glossary_delete(glossary_id: int, profile: dict[str, Any] = Depends(csrf_profile)):
    _assert_any_role(profile, "admin")
    return {"ok": bool(delete_glossary_term(glossary_id))}


@data_router.get("/materials")
def materials(profile: dict[str, Any] = Depends(current_profile)):
    return {"materials": _rows_to_json(list_materials())}


@data_router.post("/materials")
def material_upsert(body: dict[str, Any], profile: dict[str, Any] = Depends(csrf_profile)):
    _assert_any_role(profile, "admin", "reviewer")
    return {
        "ok": bool(
            upsert_material(
                code=str(body.get("code") or ""),
                display=body.get("display"),
                category=body.get("category"),
                is_active=bool(body.get("is_active", True)),
                material_id=body.get("material_id"),
            )
        )
    }


@data_router.post("/materials/{material_id}/synonyms")
def material_synonym_add(material_id: int, body: dict[str, Any], profile: dict[str, Any] = Depends(csrf_profile)):
    _assert_any_role(profile, "admin", "reviewer")
    return {"ok": bool(add_material_synonym(material_id, str(body.get("synonym") or "")))}


@data_router.delete("/materials/{material_id}")
def material_delete(material_id: int, profile: dict[str, Any] = Depends(csrf_profile)):
    _assert_any_role(profile, "admin")
    return {"ok": bool(delete_material(material_id))}


@data_router.delete("/materials/synonyms/{synonym_id}")
def material_synonym_delete(synonym_id: int, profile: dict[str, Any] = Depends(csrf_profile)):
    _assert_any_role(profile, "admin", "reviewer")
    return {"ok": bool(delete_material_synonym(synonym_id))}


@documents_router.get("/lifecycle")
def lifecycle_overview(soon_days: int = 30, profile: dict[str, Any] = Depends(require_any_role("reviewer", "admin"))):
    result = {"expired": [], "expiring_soon": [], "needs_review": [], "counts": {}}
    for bucket in ("expired", "expiring_soon", "needs_review"):
        rows = list_documents(
            allowed_departments=profile.get("allowed_departments") or [],
            max_security_level=profile.get("max_security_level") or "public",
            allowed_sites=profile.get("allowed_sites") or [],
            global_read_admin=_is_admin(profile), bucket=bucket, soon_days=soon_days,
        )
        result[bucket] = _rows_to_json(rows)
        result["counts"][bucket] = len(rows)
    return result


@documents_router.post("/lifecycle/refresh-expired")
def lifecycle_refresh(profile: dict[str, Any] = Depends(csrf_profile)):
    _assert_any_role(profile, "admin")
    return {"ok": bool(refresh_expired_status())}


@documents_router.patch("/lifecycle/documents/{doc_id}")
def lifecycle_set_document(doc_id: int, body: dict[str, Any], profile: dict[str, Any] = Depends(csrf_profile)):
    _assert_any_role(profile, "reviewer", "admin")
    _assert_metadata_actor(doc_id, profile)
    return {
        "ok": bool(
            set_document_lifecycle(
                doc_id,
                effective_date=body.get("effective_date"),
                expiry_date=body.get("expiry_date"),
                review_date=body.get("review_date"),
                reviewer=profile.get("username") or "System",
            )
        )
    }


@documents_router.post("/lifecycle/documents/{doc_id}/reviewed")
def lifecycle_mark_reviewed(doc_id: int, body: dict[str, Any], profile: dict[str, Any] = Depends(csrf_profile)):
    _assert_any_role(profile, "reviewer", "admin")
    _assert_metadata_actor(doc_id, profile)
    return {
        "ok": bool(
            mark_document_reviewed(
                doc_id,
                reviewer=profile.get("username") or "System",
                next_review_days=_safe_int(body.get("next_review_days")) or 180,
            )
        )
    }


@data_router.get("/feedback")
def feedbacks(only_pending: bool = False, profile: dict[str, Any] = Depends(require_any_role("reviewer", "admin"))):
    return {"feedbacks": _rows_to_json(list_feedbacks(only_pending=only_pending))}


@data_router.post("/feedback/{feedback_id}/classify")
def feedback_classify(feedback_id: int, body: dict[str, Any], profile: dict[str, Any] = Depends(csrf_profile)):
    _assert_any_role(profile, "reviewer", "admin")
    correct_answer = body.get("correct_answer")
    result = classify_feedback_and_get_source(
        feedback_id,
        failure_type=body.get("failure_type"),
        correct_answer=correct_answer,
        reviewer_note=body.get("reviewer_note"),
    )
    golden_hash = None
    regression_qid = None
    if correct_answer and str(correct_answer).strip():
        row = app.state.runtime.app_support_queries.feedback_review_context(feedback_id)
        if row:
            question, source_doc_id, department, site = row
            golden_hash = upsert_golden_answer(
                question=question,
                answer=correct_answer,
                source_doc_id=source_doc_id,
                department=department,
                site=site,
                created_by=profile.get("username") or "reviewer",
                feedback_id=feedback_id,
            )
            regression_qid = ensure_regression_question(
                question=question,
                expected_doc_id=source_doc_id,
                department=department,
                site=site,
                created_by=profile.get("username") or "reviewer",
            )
    return {"result": result, "golden_hash": golden_hash, "regression_qid": regression_qid}


@data_router.delete("/feedback/{feedback_id}")
def feedback_delete(feedback_id: int, profile: dict[str, Any] = Depends(csrf_profile)):
    _assert_any_role(profile, "admin", "reviewer")
    return {"ok": bool(delete_feedback(feedback_id))}


@data_router.get("/regression/questions")
def regression_questions(active_only: bool = True, profile: dict[str, Any] = Depends(require_any_role("reviewer", "admin"))):
    return {"questions": _rows_to_json(list_regression_questions(active_only=active_only))}


@data_router.post("/regression/questions")
def regression_question_add(body: dict[str, Any], profile: dict[str, Any] = Depends(csrf_profile)):
    _assert_any_role(profile, "reviewer", "admin")
    result = add_regression_question(
        question=str(body.get("question") or ""),
        expected_doc_id=body.get("expected_doc_id"),
        expected_keywords=body.get("expected_keywords"),
        department=body.get("department"),
        site=body.get("site"),
        created_by=profile.get("username") or "System",
    )
    return {
        "ok": result is not None,
        "result": result,
    }


@data_router.patch("/regression/questions/{reg_qid}/active")
def regression_question_active(reg_qid: int, body: dict[str, Any], profile: dict[str, Any] = Depends(csrf_profile)):
    _assert_any_role(profile, "reviewer", "admin")
    return {"ok": bool(set_regression_question_active(reg_qid, bool(body.get("is_active"))))}


@data_router.get("/regression/runs")
def regression_runs(batch_id: str | None = None, profile: dict[str, Any] = Depends(require_any_role("reviewer", "admin"))):
    return {"runs": _rows_to_json(get_regression_runs(batch_id=batch_id))}


@data_router.post("/regression/run")
def regression_run(body: dict[str, Any], profile: dict[str, Any] = Depends(csrf_profile)):
    _assert_any_role(profile, "reviewer", "admin")
    from mech_chatbot.rag.regression import run_regression_batch
    raw_limit = body.get("limit")
    limit = int(raw_limit) if raw_limit else None
    summary = run_regression_batch(limit=limit, run_by=profile.get("username") or "System")
    return {"ok": True, "summary": summary}


@data_router.get("/quality/documents")
def quality_documents(limit: int = 50, worst_first: bool = True, profile: dict[str, Any] = Depends(require_any_role("reviewer", "admin"))):
    return {"documents": _rows_to_json(get_doc_quality_ranking(limit=limit, worst_first=worst_first))}


@data_router.post("/quality/recompute")
def quality_recompute(profile: dict[str, Any] = Depends(csrf_profile)):
    _assert_any_role(profile, "admin", "reviewer")
    recomputed = recompute_doc_quality_scores()
    return {"ok": recomputed is not None, "recomputed": recomputed}


@data_router.post("/quality/cleanup")
def quality_cleanup(profile: dict[str, Any] = Depends(csrf_profile)):
    _assert_any_role(profile, "platform_admin")
    return cleanup_dangling_records()


@data_router.get("/analytics/departments")
def analytics_departments(profile: dict[str, Any] = Depends(require_any_role("reviewer", "admin"))):
    return dashboard_by_department()


@data_router.get("/analytics/cache")
def analytics_cache(profile: dict[str, Any] = Depends(require_any_role("platform_admin"))):
    return sc_stats()


@data_router.get("/settings")
def settings(profile: dict[str, Any] = Depends(require_any_role("platform_admin"))):
    return {"settings": get_all_app_settings()}


@data_router.get("/settings/external-ai-policy")
def external_ai_policy(profile: dict[str, Any] = Depends(require_any_role("platform_admin"))):
    """Metadata-only provider policy status for the admin settings screen."""
    return {"profiles": list_external_ai_provider_profiles()}


@data_router.put("/settings/external-ai-policy/{provider}")
def external_ai_policy_set(
    provider: str,
    body: dict[str, Any],
    profile: dict[str, Any] = Depends(csrf_profile),
):
    _assert_any_role(profile, "platform_admin")
    try:
        saved = upsert_external_ai_provider_profile(
            provider,
            endpoint=body.get("endpoint"),
            default_model=body.get("default_model"),
            secret_reference=body.get("secret_reference"),
            allowed_surfaces=body.get("allowed_surfaces") or [],
            retention_mode=body.get("retention_mode"),
            policy_version=body.get("policy_version"),
            approved_by=body.get("approved_by"),
            risk_acceptance_ref=body.get("risk_acceptance_ref"),
            review_expires_at=body.get("review_expires_at"),
            is_active=bool(body.get("is_active", True)),
            updated_by=profile.get("username") or "System",
        )
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    invalidate_external_ai_provider_profiles()
    return {"ok": True, "profile": saved}


@data_router.put("/settings/{key}")
def setting_set(key: str, body: dict[str, Any], profile: dict[str, Any] = Depends(csrf_profile)):
    _assert_any_role(profile, "platform_admin")
    return {"ok": bool(set_app_setting(key, body.get("value"), updated_by=profile.get("username") or "System"))}


@data_router.get("/admin/graph/proposals")
def graph_proposals(
    status_value: str = "pending",
    limit: int = 100,
    profile: dict[str, Any] = Depends(require_any_role("knowledge_approver", "reviewer", "admin")),
):
    _assert_any_role(profile, "knowledge_approver", "reviewer", "admin")
    return {"proposals": _rows_to_json(list_graph_proposals(status=status_value, limit=limit))}


def _review_graph_proposal_endpoint(proposal_id: int, action: str, body: dict[str, Any], profile: dict[str, Any]):
    _assert_any_role(profile, "knowledge_approver", "reviewer", "admin")
    result = review_graph_proposal(
        proposal_id,
        action,
        reviewer=profile.get("username") or "System",
        note=body.get("note"),
    )
    if not result.get("ok") and result.get("reason") == "not_found":
        raise HTTPException(status_code=404, detail="Graph proposal not found")
    if not result.get("ok"):
        raise HTTPException(status_code=409, detail=result.get("reason") or "Graph proposal conflict")
    write_audit_log(
        action=f"graph_proposal_{action}",
        username=profile.get("username") or "System",
        entity_type="graph_proposal",
        entity_id=int(proposal_id),
        details={"status": action},
        user_id=profile.get("user_id"),
    )
    return result


@data_router.post("/admin/graph/proposals/{proposal_id}/approve")
def graph_proposal_approve(
    proposal_id: int,
    body: dict[str, Any],
    profile: dict[str, Any] = Depends(csrf_profile),
):
    return _review_graph_proposal_endpoint(proposal_id, "approve", body, profile)


@data_router.post("/admin/graph/proposals/{proposal_id}/reject")
def graph_proposal_reject(
    proposal_id: int,
    body: dict[str, Any],
    profile: dict[str, Any] = Depends(csrf_profile),
):
    return _review_graph_proposal_endpoint(proposal_id, "reject", body, profile)


@data_router.get("/admin/graph/community-summaries")
def community_summaries(
    status_value: str = "pending",
    limit: int = 100,
    profile: dict[str, Any] = Depends(
        require_any_role("knowledge_approver", "reviewer", "admin")
    ),
):
    _assert_any_role(profile, "knowledge_approver", "reviewer", "admin")
    return {
        "summaries": _rows_to_json(
            list_community_summaries(status=status_value, limit=limit)
        )
    }


def _review_community_summary_endpoint(
    summary_id: int, action: str, body: dict[str, Any], profile: dict[str, Any]
):
    _assert_any_role(profile, "knowledge_approver", "reviewer", "admin")
    result = review_community_summary(
        summary_id,
        action,
        reviewer=profile.get("username") or "System",
        note=body.get("note"),
    )
    if not result.get("ok") and result.get("reason") == "not_found":
        raise HTTPException(status_code=404, detail="Community summary not found")
    if not result.get("ok"):
        raise HTTPException(
            status_code=409,
            detail=result.get("reason") or "Community summary conflict",
        )
    write_audit_log(
        action=f"graph_community_summary_{action}",
        username=profile.get("username") or "System",
        entity_type="graph_community_summary",
        entity_id=int(summary_id),
        details={"status": action},
        user_id=profile.get("user_id"),
    )
    return result


def _resolve_chat_citations(
    citation_docs: Any,
    streamed_candidates: Any,
    answer: str,
) -> tuple[tuple[dict[str, Any], ...], str]:
    return resolve_chat_citations(
        citation_docs,
        streamed_candidates,
        answer,
        page_has_vision=page_has_vision,
    )


def _chat_actor_profile(actor: ChatActor) -> dict[str, Any]:
    return {
        "user_id": actor.user_id,
        "username": actor.username,
        "department": actor.department,
        "roles": sorted(actor.roles),
        "allowed_departments": sorted(actor.allowed_departments),
        "allowed_sites": sorted(actor.allowed_sites),
        "max_security_level": actor.max_security_level,
        "preferred_language": actor.response_language,
    }


def _build_default_app_runtime():
    from mech_chatbot.evaluation.crag_pilot import (
        assign_pilot_route,
        load_pilot_config,
    )

    return build_default_app_runtime(
        application_settings,
        post=lambda *args, **kwargs: requests.post(*args, **kwargs),
        base_url=lambda: _rag_base_url(),
        headers=lambda: _rag_headers(),
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
        raw_root=lambda: data_raw_root(),
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


@data_router.post("/admin/graph/community-summaries/{summary_id}/approve")
def community_summary_approve(
    summary_id: int,
    body: dict[str, Any],
    profile: dict[str, Any] = Depends(csrf_profile),
):
    return _review_community_summary_endpoint(summary_id, "approve", body, profile)


@data_router.post("/admin/graph/community-summaries/{summary_id}/reject")
def community_summary_reject(
    summary_id: int,
    body: dict[str, Any],
    profile: dict[str, Any] = Depends(csrf_profile),
):
    return _review_community_summary_endpoint(summary_id, "reject", body, profile)


app.include_router(auth_router)
app.include_router(chat_router)
app.include_router(files_router)
app.include_router(documents_router)
app.include_router(data_router)


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
        if ".." in request_path or ".." in path or "%2f" in raw_path_text or "%5c" in raw_path_text:
            return False
        leaf = path.rsplit("/", 1)[-1]
        return "." not in leaf


static_dir = _project_root() / "web-ui" / "dist"
if static_dir.exists():
    app.mount("/", SPAStaticFiles(directory=str(static_dir), html=True), name="web")


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(
        "mech_chatbot.api.app_server:app",
        host=os.getenv("APP_SERVER_HOST", "0.0.0.0"),
        port=int(os.getenv("APP_SERVER_PORT", "8080")),
        reload=False,
    )
