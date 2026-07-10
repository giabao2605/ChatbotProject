"""Browser-facing FastAPI app for the Vue migration.

This service does not load RAG models. It authenticates browser users, proxies
chat questions to the internal rag-server, owns SQL persistence/audit for chat,
and serves protected files through SQL-backed RBAC checks.
"""
from __future__ import annotations

import json
import mimetypes
import os
import re
import time
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any
from uuid import uuid4

import bcrypt
import requests
from dotenv import load_dotenv
from fastapi import APIRouter, Depends, FastAPI, File, Form, HTTPException, Request, Response, UploadFile, status
from fastapi.responses import FileResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field
from sqlalchemy import text
from starlette.exceptions import HTTPException as StarletteHTTPException

from mech_chatbot.api import app_security
from mech_chatbot.api.file_access import (
    LEVEL_ORDER,
    can_access_document,
    chat_image_path,
    data_raw_root,
    normalize_security_level,
    original_file_path,
    page_image_path,
)
from mech_chatbot.auth.core import authenticate_user, load_user_profile, update_user_preferred_language
from mech_chatbot.config.logging import logger
from mech_chatbot.db.engine import engine
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
    get_doc_quality_ranking,
    get_grant_history,
    get_chat_history,
    get_dashboard_stats,
    get_lifecycle_overview,
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
    list_documents,
    list_expiring_documents,
    list_ingestion_jobs,
    list_feedbacks,
    list_known_departments,
    list_known_sites,
    list_materials,
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
    publish_as_new_variant,
    publish_as_new_version,
    publish_as_standalone,
    queue_eta_seconds,
    reassign_department_data,
    requeue_job,
    recompute_doc_quality_scores,
    reject_document,
    reject_ingestion_job,
    resolve_access_request,
    refresh_expired_status,
    revoke_user_clearance,
    revoke_user_department,
    save_answer_sources,
    save_chat_history,
    sc_stats,
    set_app_setting,
    set_department_status,
    set_document_current,
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
    update_user_active_and_roles,
    update_user_password,
    upsert_golden_answer,
    upsert_department,
    upsert_glossary_term,
    upsert_material,
    upsert_site,
    ensure_regression_question,
    write_audit_log,
)

load_dotenv()

# ---------------------------------------------------------------------------
# Lifespan: raise anyio thread limiter so blocking def endpoints don't starve
# ---------------------------------------------------------------------------
_APP_THREAD_LIMIT = int(os.getenv("APP_THREAD_LIMIT", "60"))


@asynccontextmanager
async def _lifespan(app: FastAPI):
    import anyio
    anyio.to_thread.current_default_thread_limiter().total_tokens = _APP_THREAD_LIMIT
    logger.info("App server anyio thread limiter raised to %d", _APP_THREAD_LIMIT)
    yield


app = FastAPI(title="Mech Chatbot App API", version="0.1.0", lifespan=_lifespan)


@app.get("/api/health", tags=["system"])
def app_health():
    db_status = "unavailable"
    if engine is not None:
        try:
            with engine.connect() as conn:
                conn.execute(text("SELECT 1"))
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


def _public_profile(profile: dict[str, Any], csrf: str | None = None) -> dict[str, Any]:
    out = {
        "user_id": profile.get("user_id"),
        "username": profile.get("username"),
        "display_name": profile.get("display_name"),
        "department": profile.get("department"),
        "roles": profile.get("roles") or [],
        "allowed_departments": profile.get("allowed_departments") or [],
        "max_security_level": profile.get("max_security_level") or "public",
        "allowed_sites": profile.get("allowed_sites") or [],
        "preferred_language": profile.get("preferred_language") or "vi",
    }
    if csrf is not None:
        out["csrf_token"] = csrf
    return out


def _session_payload(request: Request) -> app_security.SessionPayload:
    return app_security.verify_session_token(request.cookies.get(app_security.SESSION_COOKIE_NAME))


def current_profile(request: Request) -> dict[str, Any]:
    payload = _session_payload(request)
    profile = load_user_profile(user_id=payload.user_id, username=payload.username)
    if not profile:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="User is inactive or invalid")
    return profile


def csrf_profile(request: Request) -> dict[str, Any]:
    payload = _session_payload(request)
    app_security.require_csrf(request, payload)
    profile = load_user_profile(user_id=payload.user_id, username=payload.username)
    if not profile:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="User is inactive or invalid")
    return profile


def require_any_role(*roles: str):
    allowed = {r.lower() for r in roles}

    def _dep(profile: dict[str, Any] = Depends(current_profile)) -> dict[str, Any]:
        current = {str(r).lower() for r in (profile.get("roles") or [])}
        if "admin" not in current and not (current & allowed):
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Forbidden")
        return profile

    return _dep


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


def _safe_int(value: Any) -> int | None:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _parse_json_obj(raw: str | None, field_name: str) -> dict[str, Any] | None:
    if not raw:
        return None
    try:
        parsed = json.loads(raw)
    except Exception:
        raise HTTPException(status_code=400, detail=f"{field_name} không hợp lệ (JSON)")
    if not isinstance(parsed, dict):
        raise HTTPException(status_code=400, detail=f"{field_name} phải là object JSON")
    return {k: v for k, v in parsed.items() if v not in (None, "")} or None


def _parse_json_list(raw: str | None, field_name: str) -> list[Any]:
    if not raw:
        return []
    try:
        parsed = json.loads(raw)
    except Exception:
        raise HTTPException(status_code=400, detail=f"{field_name} không hợp lệ (JSON)")
    if not isinstance(parsed, list):
        raise HTTPException(status_code=400, detail=f"{field_name} phải là array JSON")
    return parsed


def _split_csv(value: Any) -> list[str]:
    if isinstance(value, list):
        source = value
    elif isinstance(value, str):
        source = re.split(r"[\s,]+", value)
    else:
        source = []
    return [str(x).strip() for x in source if str(x).strip()]


def _parse_json_or_csv_list(raw: str | None, field_name: str) -> list[str]:
    if not raw:
        return []
    stripped = raw.strip()
    if stripped.startswith("["):
        return _split_csv(_parse_json_list(stripped, field_name))
    return _split_csv(stripped)


def _assert_upload_department(profile: dict[str, Any], dept: str) -> None:
    if _is_admin(profile):
        return
    allowed = set(profile.get("allowed_departments") or [])
    if dept not in allowed:
        raise HTTPException(status_code=403, detail=f"Không có quyền upload vào phòng ban {dept}")


def _store_upload_file(file: UploadFile, dept: str) -> tuple[str, str]:
    allowed_ext = {
        ".pdf", ".docx", ".doc", ".xlsx", ".xls", ".txt", ".md", ".csv", ".pptx",
        ".png", ".jpg", ".jpeg", ".bmp", ".gif", ".webp", ".tif", ".tiff",
    }
    original_name = file.filename or ""
    ext = Path(original_name).suffix.lower()
    if ext not in allowed_ext:
        raise HTTPException(status_code=400, detail=f"Định dạng tệp không được hỗ trợ: {original_name}")
    raw = file.file.read()
    if not raw:
        raise HTTPException(status_code=400, detail=f"Tệp rỗng: {original_name}")
    if len(raw) > 100 * 1024 * 1024:
        raise HTTPException(status_code=400, detail=f"Tệp quá lớn (giới hạn 100MB): {original_name}")
    safe_dept = re.sub(r"[^A-Za-z0-9_\-]", "_", (dept or "").strip()) or "CHUNG"
    out_dir = data_raw_root() / "Uploads" / safe_dept
    out_dir.mkdir(parents=True, exist_ok=True)
    stored_path = out_dir / f"{uuid4().hex}{ext}"
    stored_path.write_bytes(raw)
    return original_name, str(stored_path)


def _row_to_json(row: Any) -> Any:
    if hasattr(row, "_mapping"):
        return dict(row._mapping)
    if isinstance(row, dict):
        return row
    if isinstance(row, (list, tuple)):
        return list(row)
    return row


def _rows_to_json(rows: Any) -> list[Any]:
    return [_row_to_json(row) for row in (rows or [])]


def _assert_any_role(profile: dict[str, Any], *roles: str) -> None:
    current = {str(role).lower() for role in (profile.get("roles") or [])}
    allowed = {role.lower() for role in roles}
    if "admin" not in current and not (current & allowed):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Forbidden")


def _citation_list(retrieved_docs: list[Any]) -> list[dict[str, Any]]:
    seen: set[tuple[int, int]] = set()
    citations: list[dict[str, Any]] = []
    for item in retrieved_docs:
        if not isinstance(item, dict):
            continue
        doc_id = _safe_int(item.get("doc_id"))
        page_no = _safe_int(item.get("trang") or item.get("trang_so"))
        if doc_id is None or page_no is None:
            continue
        key = (doc_id, page_no)
        if key in seen:
            continue
        seen.add(key)
        citations.append(
            {
                "doc_id": doc_id,
                "page_no": page_no,
                "file_name": item.get("file_goc"),
                "score": item.get("score"),
                "page_url": f"/api/files/documents/{doc_id}/pages/{page_no}",
                "original_url": f"/api/files/documents/{doc_id}/original",
            }
        )
    return citations


def _chat_image_id_from_path(raw_path: Any) -> str | None:
    if not raw_path:
        return None
    name = os.path.basename(str(raw_path))
    return name or None


def _chat_image_url_from_path(raw_path: Any) -> str | None:
    image_id = _chat_image_id_from_path(raw_path)
    return f"/api/files/chat-images/{image_id}" if image_id else None


def _sources_for_chat_ids(chat_ids: list[int]) -> dict[int, list[dict[str, Any]]]:
    if not chat_ids or engine is None:
        return {}
    keys, params = [], {}
    for i, chat_id in enumerate(chat_ids):
        key = f"cid_{i}"
        keys.append(f":{key}")
        params[key] = chat_id
    with engine.connect() as conn:
        rows = conn.execute(
            text(
                """
                SELECT ChatID, DocID, FileName, ChunkRef, Score
                FROM dbo.AnswerSource
                WHERE ChatID IN (
                """
                + ", ".join(keys)
                + ") ORDER BY ChatID, RankNo"
            ),
            params,
        ).fetchall()
    out: dict[int, list[dict[str, Any]]] = {}
    for chat_id, doc_id, file_name, chunk_ref, score in rows:
        page_no = _safe_int(chunk_ref)
        if doc_id is None or page_no is None:
            continue
        out.setdefault(int(chat_id), []).append(
            {
                "doc_id": int(doc_id),
                "page_no": page_no,
                "file_name": file_name,
                "score": score,
                "page_url": f"/api/files/documents/{int(doc_id)}/pages/{page_no}",
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
            message["citations"] = sources.get(int(chat_id), [])
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


chat_router = APIRouter(prefix="/api/chat", tags=["chat"])


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
            is_admin=_is_admin(profile),
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
    )
    return {"messages": _decorate_history_messages(messages)}


@chat_router.delete("/sessions/{session_id}")
def delete_session(session_id: str, profile: dict[str, Any] = Depends(csrf_profile)):
    return {
        "ok": True,
        "deleted": clear_chat_history(
            session_id,
            username=profile.get("username"),
            is_admin=_is_admin(profile),
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


@chat_router.post("/message")
def chat_message(req: ChatMessageRequest, profile: dict[str, Any] = Depends(csrf_profile)):
    image_path = _verify_image_upload(profile, req.image_token)
    lang = profile.get("preferred_language") or "vi"

    def generate():
        yield _sse("thinking", {"message": "Đang suy nghĩ"})
        rag_payload = {
            "user_id": profile.get("user_id"),
            "username": profile.get("username"),
            "user_question": req.question.strip(),
            "image_path": image_path,
            "chat_history": req.chat_history,
            "current_part_ids": req.current_part_ids,
            "response_language": lang,
            "conversation_context": req.conversation_context,
        }
        try:
            resp = requests.post(
                f"{_rag_base_url()}/chat",
                headers=_rag_headers(),
                json=rag_payload,
                timeout=int(os.getenv("APP_RAG_CHAT_TIMEOUT_SECONDS", "300")),
            )
            if not resp.ok:
                yield _sse(
                    "error",
                    {
                        "message": "RAG server busy" if resp.status_code == 503 else f"RAG server error HTTP {resp.status_code}",
                        "detail": resp.text,
                        "status": resp.status_code,
                    },
                )
                return
            data = resp.json()
            answer = data.get("response") or ""
            ref_text = data.get("ref_text") or ""
            debug = data.get("debug_info") or {}
            retrieved_docs = debug.get("retrieved_docs") if isinstance(debug, dict) else []
            if not isinstance(retrieved_docs, list):
                retrieved_docs = []
            citations = _citation_list(retrieved_docs)

            chat_id = None
            try:
                chat_id = save_chat_history(
                    session_id=req.session_id,
                    user_msg=req.question.strip(),
                    bot_msg=answer + ref_text,
                    image_path=image_path,
                    ref_images=[],
                    username=profile.get("username"),
                )
                if chat_id and retrieved_docs:
                    save_answer_sources(chat_id, retrieved_docs)
                write_audit_log(
                    username=profile.get("username"),
                    action="chat_query",
                    entity_type="LichSuChat",
                    entity_id=chat_id,
                    details={"prompt": req.question.strip(), "session_id": req.session_id},
                )
                confidential = [
                    d for d in retrieved_docs
                    if isinstance(d, dict) and normalize_security_level(d.get("security_level")) == "confidential"
                ]
                if confidential:
                    write_audit_log(
                        username=profile.get("username"),
                        action="read_confidential",
                        entity_type="LichSuChat",
                        entity_id=chat_id,
                        details={
                            "session_id": req.session_id,
                            "prompt": req.question.strip(),
                            "so_tai_lieu_mat": len(confidential),
                        },
                    )
            except Exception as exc:
                logger.error("Could not persist chat turn: %s", exc, exc_info=True)
                yield _sse("warning", {"message": "Không lưu được lịch sử chat", "detail": str(exc)})

            for token in re.findall(r"\S+\s*|\s+", answer) or [answer]:
                yield _sse("delta", {"text": token})
                time.sleep(0.016)
            yield _sse(
                "done",
                {
                    "chat_id": chat_id,
                    "ref_text": ref_text,
                    "citations": citations,
                    "new_part_ids": data.get("new_part_ids") or [],
                    "conversation_context": debug.get("conversation_context") if isinstance(debug, dict) else None,
                },
            )
        except Exception as exc:
            logger.error("chat_message failed: %s", exc, exc_info=True)
            yield _sse("error", {"message": str(exc)})

    return StreamingResponse(generate(), media_type="text/event-stream")


@chat_router.post("/feedback")
def feedback(body: dict[str, Any], profile: dict[str, Any] = Depends(csrf_profile)):
    chat_id = _safe_int(body.get("chat_id"))
    rating = _safe_int(body.get("rating"))
    if not chat_id or not rating:
        raise HTTPException(status_code=400, detail="Missing feedback data")
    update_chat_feedback(chat_id, 1 if rating > 0 else -1, voter_username=profile.get("username"))
    return {"ok": True}


files_router = APIRouter(prefix="/api/files", tags=["files"])


def _file_response(path: Path, filename: str | None = None) -> FileResponse:
    if not path.exists() or not path.is_file():
        raise HTTPException(status_code=404, detail="File not found")
    media_type = mimetypes.guess_type(str(path))[0] or "application/octet-stream"
    return FileResponse(path, media_type=media_type, filename=filename)


@files_router.get("/documents/{doc_id}/pages/{page_no}")
def citation_page(doc_id: int, page_no: int, profile: dict[str, Any] = Depends(current_profile)):
    decision, record = can_access_document(profile, doc_id)
    if not decision.allowed:
        raise HTTPException(status_code=403, detail=decision.reason)
    path = page_image_path(doc_id, page_no)
    if normalize_security_level(decision.security_level) == "confidential":
        write_audit_log(profile.get("username"), "view_citation_page", "TaiLieu", doc_id, {"page_no": page_no})
    if path is None:
        raise HTTPException(status_code=404, detail="Page image not found")
    return _file_response(path)


@files_router.get("/documents/{doc_id}/original")
def original_document(doc_id: int, profile: dict[str, Any] = Depends(current_profile)):
    decision, record = can_access_document(profile, doc_id)
    if not decision.allowed or record is None:
        raise HTTPException(status_code=403, detail=decision.reason)
    path = original_file_path(record)
    if path is None:
        raise HTTPException(status_code=404, detail="Original file not found")
    write_audit_log(
        profile.get("username"),
        "download_original",
        "TaiLieu",
        doc_id,
        {"file": record.ten_file, "security_level": decision.security_level, "source": "app-api"},
    )
    return _file_response(path, filename=record.ten_file or path.name)


@files_router.get("/chat-images/{image_id}")
def chat_image(image_id: str, profile: dict[str, Any] = Depends(current_profile)):
    path = chat_image_path(image_id)
    if path is None:
        raise HTTPException(status_code=404, detail="Image not found")
    if engine is None:
        raise HTTPException(status_code=503, detail="Database is not ready")
    query = """
        SELECT TOP 1 ChatID
        FROM dbo.LichSuChat
        WHERE HinhAnhUpload LIKE :suffix
    """
    params: dict[str, Any] = {"suffix": f"%{image_id}"}
    if not _is_admin(profile):
        query += " AND Username = :username"
        params["username"] = profile.get("username")
    with engine.connect() as conn:
        row = conn.execute(text(query), params).fetchone()
    if not row:
        raise HTTPException(status_code=403, detail="Image is not visible to this user")
    return _file_response(path)


data_router = APIRouter(prefix="/api", tags=["operations"])


@data_router.get("/dashboard")
def dashboard(profile: dict[str, Any] = Depends(current_profile)):
    return {
        "stats": get_dashboard_stats(),
        "recent_documents": _rows_to_json(list_recent_documents()),
        "recent_failed_jobs": _rows_to_json(list_recent_failed_jobs()),
    }


@data_router.get("/documents")
def documents(
    dept: str | None = None,
    domain: str | None = None,
    sec: str | None = None,
    eff_mode: str | None = None,
    search: str | None = None,
    profile: dict[str, Any] = Depends(current_profile),
):
    rows = list_documents(
        is_admin=_is_admin(profile),
        allowed_departments=profile.get("allowed_departments") or [],
        dept=dept,
        domain=domain,
        sec=sec,
        eff_mode=eff_mode,
        search_kw=search,
    )
    return {"documents": [dict(row._mapping) if hasattr(row, "_mapping") else list(row) for row in rows]}


@data_router.post("/documents/upload")
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
    _assert_upload_department(profile, dept)
    original_name, stored_path = _store_upload_file(file, dept)
    from mech_chatbot.db.repositories.jobs import create_ingestion_job
    upload_meta = _parse_json_obj(meta_json, "meta_json")
    extra_departments = _parse_json_or_csv_list(extra_departments_json, "extra_departments_json")
    phong_ban = [dept] + [d for d in extra_departments if d != dept]
    job_id = create_ingestion_job(
        file_name=original_name,
        file_path=stored_path,
        thu_muc=dept,
        uploaded_by=profile.get("username"),
        domain=domain,
        security_level=security_level,
        cong_doan=cong_doan,
        site=site,
        phong_ban=phong_ban,
        upload_meta=upload_meta,
    )
    if not job_id:
        try:
            Path(stored_path).unlink(missing_ok=True)
        except Exception:
            pass
        raise HTTPException(status_code=400, detail="Không tạo được job (phòng ban có thể bị vô hiệu)")
    return {"ok": True, "job_id": job_id, "file_name": original_name}


@data_router.post("/documents/upload-batch")
def documents_upload_batch(
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

    from mech_chatbot.db.repositories.jobs import create_ingestion_job

    upload_meta = _parse_json_obj(meta_json, "meta_json")
    assignments = _parse_json_list(assignments_json, "assignments_json")
    default_dept = (thu_muc or "").strip()
    default_extra = _parse_json_or_csv_list(extra_departments_json, "extra_departments_json")
    created: list[dict[str, Any]] = []
    errors: list[dict[str, Any]] = []

    for index, upload in enumerate(files):
        assignment = assignments[index] if index < len(assignments) and isinstance(assignments[index], dict) else {}
        dept = str(assignment.get("thu_muc") or default_dept).strip()
        if not dept:
            errors.append({"file_name": upload.filename, "error": "Thiếu phòng ban"})
            continue
        try:
            _assert_upload_department(profile, dept)
            original_name, stored_path = _store_upload_file(upload, dept)
            extra = _split_csv(assignment.get("extra_departments") or default_extra)
            phong_ban = [dept] + [d for d in extra if d != dept]
            job_id = create_ingestion_job(
                file_name=original_name,
                file_path=stored_path,
                thu_muc=dept,
                uploaded_by=profile.get("username"),
                domain=assignment.get("domain") or domain,
                security_level=assignment.get("security_level") or security_level,
                cong_doan=assignment.get("cong_doan") or cong_doan,
                site=assignment.get("site") or site,
                phong_ban=phong_ban,
                upload_meta=upload_meta,
            )
            if not job_id:
                Path(stored_path).unlink(missing_ok=True)
                errors.append({"file_name": original_name, "error": "Không tạo được job"})
            else:
                created.append({"job_id": job_id, "file_name": original_name, "thu_muc": dept})
        except HTTPException as exc:
            errors.append({"file_name": upload.filename, "error": exc.detail})
        except Exception as exc:
            logger.exception("upload batch failed for %s", upload.filename)
            errors.append({"file_name": upload.filename, "error": str(exc)})

    return {"ok": not errors, "jobs": created, "errors": errors, "created": len(created), "failed": len(errors)}


@data_router.get("/ingestion/jobs")
def ingestion_jobs(status_value: str | None = None, profile: dict[str, Any] = Depends(current_profile)):
    rows = list_ingestion_jobs(
        status=status_value,
        is_admin=_is_admin(profile),
        username=profile.get("username"),
        allowed_departments=profile.get("allowed_departments") or [],
    )
    return {"jobs": _rows_to_json(rows)}


@data_router.get("/analytics/usage")
def usage(days: int = 30, profile: dict[str, Any] = Depends(require_any_role("reviewer", "admin"))):
    return get_usage_analytics(days=days)


@data_router.get("/analytics/observability")
def observability(days: int = 30, profile: dict[str, Any] = Depends(require_any_role("admin"))):
    return get_observability(days=days)


@data_router.get("/audit")
def audit(limit: int = 100, profile: dict[str, Any] = Depends(require_any_role("admin"))):
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
    profile: dict[str, Any] = Depends(require_any_role("admin", "reviewer")),
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
    _assert_any_role(profile, "admin", "reviewer")
    result = resolve_access_request(
        request_id=request_id,
        decision=str(body.get("decision") or ""),
        reviewer_username=profile.get("username"),
        reviewer_id=profile.get("user_id"),
        review_note=body.get("review_note"),
    )
    return {"ok": bool(result), "result": result}


@data_router.get("/access/users")
def access_users(limit: int = 1000, profile: dict[str, Any] = Depends(require_any_role("admin"))):
    return {"users": _rows_to_json(list_users_with_access(limit=limit))}


@data_router.get("/access/grants")
def access_grants(limit: int = 100, profile: dict[str, Any] = Depends(require_any_role("admin"))):
    return {"grants": _rows_to_json(get_grant_history(limit=limit))}


@data_router.post("/access/users/{user_id}/revoke-clearance")
def access_revoke_clearance(user_id: int, body: dict[str, Any], profile: dict[str, Any] = Depends(csrf_profile)):
    _assert_any_role(profile, "admin")
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
    _assert_any_role(profile, "admin")
    result = revoke_user_department(
        user_id=user_id,
        dept=str(body.get("department") or ""),
        actor_username=profile.get("username"),
        actor_id=profile.get("user_id"),
        reason=body.get("reason"),
    )
    return {"ok": bool(result), "result": result}


@data_router.get("/documents/pending-review")
def documents_pending_review(profile: dict[str, Any] = Depends(require_any_role("reviewer", "admin"))):
    return {"documents": _rows_to_json(list_pending_review_docs())}


@data_router.get("/documents/expiring")
def documents_expiring(profile: dict[str, Any] = Depends(require_any_role("reviewer", "admin"))):
    return {"documents": _rows_to_json(list_expiring_documents())}


@data_router.get("/documents/bulk-meta")
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


@data_router.patch("/documents/bulk-metadata")
def documents_bulk_metadata(body: dict[str, Any], profile: dict[str, Any] = Depends(csrf_profile)):
    _assert_any_role(profile, "reviewer", "admin")
    raw_ids = body.get("doc_ids") or []
    metadata = body.get("metadata") or {}
    if not isinstance(raw_ids, list) or not raw_ids:
        raise HTTPException(status_code=400, detail="doc_ids không hợp lệ")
    if not isinstance(metadata, dict) or not metadata:
        raise HTTPException(status_code=400, detail="metadata không hợp lệ")
    ok = 0
    fail = 0
    fields = {k: v for k, v in metadata.items() if k not in {"attributes", "domain"} and v not in (None, "")}
    attrs = metadata.get("attributes")
    domain = metadata.get("domain")
    for raw_id in raw_ids:
        doc_id = _safe_int(raw_id)
        if not doc_id:
            fail += 1
            continue
        try:
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


@data_router.post("/documents/review/bulk")
def documents_review_bulk(body: dict[str, Any], profile: dict[str, Any] = Depends(csrf_profile)):
    _assert_any_role(profile, "reviewer", "admin")
    items = body.get("items") or []
    action = str(body.get("action") or "").strip()
    publish_mode = str(body.get("publish_mode") or "standalone").strip()
    reason = str(body.get("reason") or "")
    if not isinstance(items, list) or not items:
        raise HTTPException(status_code=400, detail="items không hợp lệ")
    if action not in {"publish", "reject", "delete"}:
        raise HTTPException(status_code=400, detail="action không hợp lệ")
    ok = 0
    fail = 0
    reviewer = profile.get("username") or "System"
    for item in items:
        if not isinstance(item, dict):
            fail += 1
            continue
        job_id = _safe_int(item.get("job_id"))
        doc_id = _safe_int(item.get("doc_id"))
        try:
            if action == "publish":
                if not doc_id or not job_id:
                    raise RuntimeError("Thiếu DocID hoặc JobID")
                if publish_mode == "new_version":
                    result = publish_as_new_version(doc_id, reviewer=reviewer)
                elif publish_mode == "new_variant":
                    result = publish_as_new_variant(doc_id, reviewer=reviewer)
                else:
                    result = publish_as_standalone(doc_id, reviewer=reviewer)
                if not result:
                    raise RuntimeError("Publish thất bại")
                mark_job_published(job_id)
            elif action == "reject":
                if not job_id:
                    raise RuntimeError("Thiếu JobID")
                reject_ingestion_job(job_id, reason) or mark_job_rejected(job_id)
                if doc_id:
                    reject_document(doc_id, reviewer=reviewer)
            elif action == "delete":
                if doc_id:
                    delete_document_completely(doc_id, reviewer=reviewer)
                if job_id:
                    delete_ingestion_job(job_id)
            ok += 1
        except Exception:
            logger.exception("bulk review action failed: action=%s job_id=%s doc_id=%s", action, job_id, doc_id)
            fail += 1
    return {"ok": fail == 0, "updated": ok, "failed": fail}


@data_router.patch("/documents/{doc_id}/current")
def document_set_current(doc_id: int, profile: dict[str, Any] = Depends(csrf_profile)):
    _assert_any_role(profile, "reviewer", "admin")
    return {"ok": bool(set_document_current(doc_id))}


@data_router.patch("/documents/{doc_id}/expired")
def document_mark_expired(doc_id: int, profile: dict[str, Any] = Depends(csrf_profile)):
    _assert_any_role(profile, "reviewer", "admin")
    return {"ok": bool(mark_document_expired(doc_id))}


@data_router.patch("/documents/{doc_id}/metadata")
def document_update_metadata(doc_id: int, body: dict[str, Any], profile: dict[str, Any] = Depends(csrf_profile)):
    _assert_any_role(profile, "reviewer", "admin")
    fields = {k: v for k, v in body.items() if k not in {"attributes", "domain"}}
    result = update_document_common_metadata(
        doc_id,
        reviewer=profile.get("username") or "System",
        attributes=body.get("attributes"),
        domain=body.get("domain"),
        **fields,
    )
    return {"ok": bool(result), "result": result}


@data_router.post("/documents/{doc_id}/publish-new-version")
def document_publish_new_version(doc_id: int, profile: dict[str, Any] = Depends(csrf_profile)):
    _assert_any_role(profile, "reviewer", "admin")
    return {"ok": bool(publish_as_new_version(doc_id, reviewer=profile.get("username") or "System"))}


@data_router.post("/documents/{doc_id}/publish-new-variant")
def document_publish_new_variant(doc_id: int, profile: dict[str, Any] = Depends(csrf_profile)):
    _assert_any_role(profile, "reviewer", "admin")
    return {"ok": bool(publish_as_new_variant(doc_id, reviewer=profile.get("username") or "System"))}


@data_router.post("/documents/{doc_id}/publish-standalone")
def document_publish_standalone(doc_id: int, profile: dict[str, Any] = Depends(csrf_profile)):
    _assert_any_role(profile, "reviewer", "admin")
    return {"ok": bool(publish_as_standalone(doc_id, reviewer=profile.get("username") or "System"))}


@data_router.post("/documents/{doc_id}/reject")
def document_reject(doc_id: int, profile: dict[str, Any] = Depends(csrf_profile)):
    _assert_any_role(profile, "reviewer", "admin")
    return {"ok": bool(reject_document(doc_id, reviewer=profile.get("username") or "System"))}


@data_router.post("/documents/{doc_id}/archive")
def document_archive(doc_id: int, profile: dict[str, Any] = Depends(csrf_profile)):
    _assert_any_role(profile, "reviewer", "admin")
    return {"ok": bool(archive_document(doc_id, reviewer=profile.get("username") or "System"))}


@data_router.delete("/documents/{doc_id}")
def document_delete(doc_id: int, profile: dict[str, Any] = Depends(csrf_profile)):
    _assert_any_role(profile, "admin")
    return {"ok": bool(delete_document_completely(doc_id, reviewer=profile.get("username") or "System"))}


@data_router.get("/ingestion/eta")
def ingestion_eta(profile: dict[str, Any] = Depends(require_any_role("uploader", "reviewer", "admin"))):
    eta = queue_eta_seconds()
    if isinstance(eta, dict):
        return eta
    return {"pending": 0, "avg_seconds": 0, "eta_seconds": eta}


@data_router.get("/ingestion/bulk-action-jobs")
def ingestion_bulk_jobs(profile: dict[str, Any] = Depends(require_any_role("reviewer", "admin"))):
    return {"jobs": _rows_to_json(list_bulk_action_jobs())}


@data_router.post("/ingestion/jobs/bulk-delete")
def ingestion_bulk_delete(body: dict[str, Any], profile: dict[str, Any] = Depends(csrf_profile)):
    _assert_any_role(profile, "admin")
    return {"ok": bool(bulk_delete_ingestion_jobs(body.get("ids") or []))}


@data_router.patch("/ingestion/jobs/{job_id}/priority")
def ingestion_set_priority(job_id: int, body: dict[str, Any], profile: dict[str, Any] = Depends(csrf_profile)):
    _assert_any_role(profile, "reviewer", "admin")
    return {"ok": bool(set_job_priority(job_id, _safe_int(body.get("priority")) or 0))}


@data_router.post("/ingestion/jobs/{job_id}/cancel")
def ingestion_cancel(job_id: int, profile: dict[str, Any] = Depends(csrf_profile)):
    _assert_any_role(profile, "uploader", "reviewer", "admin")
    return {"ok": bool(cancel_job(job_id, canceled_by=profile.get("username") or "System"))}


@data_router.post("/ingestion/jobs/{job_id}/requeue")
def ingestion_requeue(job_id: int, profile: dict[str, Any] = Depends(csrf_profile)):
    _assert_any_role(profile, "reviewer", "admin")
    return {"ok": bool(requeue_job(job_id))}


@data_router.post("/ingestion/jobs/{job_id}/pending-review")
def ingestion_pending_review(job_id: int, profile: dict[str, Any] = Depends(csrf_profile)):
    _assert_any_role(profile, "reviewer", "admin")
    return {"ok": bool(mark_job_pending_review(job_id))}


@data_router.post("/ingestion/jobs/{job_id}/publish")
def ingestion_publish(job_id: int, profile: dict[str, Any] = Depends(csrf_profile)):
    _assert_any_role(profile, "reviewer", "admin")
    return {"ok": bool(mark_job_published(job_id))}


@data_router.post("/ingestion/jobs/{job_id}/reject")
def ingestion_reject(job_id: int, body: dict[str, Any], profile: dict[str, Any] = Depends(csrf_profile)):
    _assert_any_role(profile, "reviewer", "admin")
    reason = str(body.get("reason") or "")
    return {"ok": bool(reject_ingestion_job(job_id, reason) or mark_job_rejected(job_id))}


@data_router.delete("/ingestion/jobs/{job_id}")
def ingestion_delete(job_id: int, profile: dict[str, Any] = Depends(csrf_profile)):
    _assert_any_role(profile, "admin")
    return {"ok": bool(delete_ingestion_job(job_id))}


@data_router.get("/users")
def users(profile: dict[str, Any] = Depends(require_any_role("admin"))):
    return {"users": _rows_to_json(list_users_basic())}


@data_router.get("/users/{user_id}")
def user_detail(user_id: int, profile: dict[str, Any] = Depends(require_any_role("admin"))):
    return {
        "user_id": user_id,
        "roles": get_user_roles(user_id),
        "departments": get_user_departments(user_id),
        "clearance": get_user_clearance(user_id),
        "sites": get_user_sites(user_id),
    }


@data_router.post("/users")
def user_create(body: dict[str, Any], profile: dict[str, Any] = Depends(csrf_profile)):
    _assert_any_role(profile, "admin")
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
    _assert_any_role(profile, "admin")
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
    _assert_any_role(profile, "admin")
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
    _assert_any_role(profile, "admin")
    return {"ok": bool(set_user_departments(user_id, body.get("departments") or []))}


@data_router.patch("/users/{user_id}/sites")
def user_sites(user_id: int, body: dict[str, Any], profile: dict[str, Any] = Depends(csrf_profile)):
    _assert_any_role(profile, "admin")
    return {"ok": bool(set_user_sites(user_id, body.get("sites") or []))}


@data_router.patch("/users/{user_id}/clearance")
def user_clearance(user_id: int, body: dict[str, Any], profile: dict[str, Any] = Depends(csrf_profile)):
    _assert_any_role(profile, "admin")
    return {"ok": bool(set_user_clearance(user_id, body.get("max_level") or "public"))}


@data_router.patch("/users/{user_id}/password")
def user_password(user_id: int, body: dict[str, Any], profile: dict[str, Any] = Depends(csrf_profile)):
    _assert_any_role(profile, "admin")
    password = str(body.get("password") or "")
    if len(password) < 8:
        raise HTTPException(status_code=400, detail="Password must be at least 8 characters")
    password_hash = bcrypt.hashpw(password.encode("utf-8"), bcrypt.gensalt()).decode("utf-8")
    return {"ok": bool(update_user_password(user_id, password_hash))}


@data_router.delete("/users/{user_id}")
def user_delete(user_id: int, profile: dict[str, Any] = Depends(csrf_profile)):
    _assert_any_role(profile, "admin")
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


@data_router.get("/catalog/departments/{code}")
def catalog_department(code: str, profile: dict[str, Any] = Depends(require_any_role("admin"))):
    return get_department_summary(code)


@data_router.post("/catalog/departments")
def catalog_department_upsert(body: dict[str, Any], profile: dict[str, Any] = Depends(csrf_profile)):
    _assert_any_role(profile, "admin")
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
    _assert_any_role(profile, "admin")
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
    _assert_any_role(profile, "admin")
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
    _assert_any_role(profile, "admin")
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
    _assert_any_role(profile, "admin")
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


@data_router.get("/lifecycle")
def lifecycle_overview(soon_days: int = 30, profile: dict[str, Any] = Depends(require_any_role("reviewer", "admin"))):
    return get_lifecycle_overview(soon_days=soon_days)


@data_router.post("/lifecycle/refresh-expired")
def lifecycle_refresh(profile: dict[str, Any] = Depends(csrf_profile)):
    _assert_any_role(profile, "admin")
    return {"ok": bool(refresh_expired_status())}


@data_router.patch("/lifecycle/documents/{doc_id}")
def lifecycle_set_document(doc_id: int, body: dict[str, Any], profile: dict[str, Any] = Depends(csrf_profile)):
    _assert_any_role(profile, "reviewer", "admin")
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


@data_router.post("/lifecycle/documents/{doc_id}/reviewed")
def lifecycle_mark_reviewed(doc_id: int, body: dict[str, Any], profile: dict[str, Any] = Depends(csrf_profile)):
    _assert_any_role(profile, "reviewer", "admin")
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
        with engine.connect() as conn:
            row = conn.execute(
                text(
                    "SELECT Question, SourceDocID, Department, Site "
                    "FROM FeedbackReview WHERE FeedbackID = :fid"
                ),
                {"fid": feedback_id},
            ).fetchone()
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
    _assert_any_role(profile, "admin")
    return cleanup_dangling_records()


@data_router.get("/analytics/departments")
def analytics_departments(profile: dict[str, Any] = Depends(require_any_role("reviewer", "admin"))):
    return dashboard_by_department()


@data_router.get("/analytics/cache")
def analytics_cache(profile: dict[str, Any] = Depends(require_any_role("admin"))):
    return sc_stats()


@data_router.get("/settings")
def settings(profile: dict[str, Any] = Depends(require_any_role("admin"))):
    return {"settings": get_all_app_settings()}


@data_router.put("/settings/{key}")
def setting_set(key: str, body: dict[str, Any], profile: dict[str, Any] = Depends(csrf_profile)):
    _assert_any_role(profile, "admin")
    return {"ok": bool(set_app_setting(key, body.get("value"), updated_by=profile.get("username") or "System"))}


app.include_router(auth_router)
app.include_router(chat_router)
app.include_router(files_router)
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
