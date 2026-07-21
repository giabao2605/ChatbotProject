"""HTTP transport for browser chat sessions and streamed chat turns.

The router owns request validation and HTTP/SSE serialization only. The chat
turn lifecycle itself is delegated to the application-owned runner exposed by
``request.app.state.runtime``.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any
from uuid import uuid4

from fastapi import APIRouter, Depends, File, HTTPException, Request, UploadFile
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from mech_chatbot.api import app_security
from mech_chatbot.api.dependencies import csrf_profile, current_profile
from mech_chatbot.api.file_access import chat_image_path, data_raw_root, page_has_vision
from mech_chatbot.api.transport_utils import safe_int
from mech_chatbot.application.chat_citations import (
    build_citation_list,
    citation_ref_text,
    filter_citations_by_answer,
    resolve_chat_citations,
)
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
from mech_chatbot.services.chat_service import (
    clear_chat_history,
    get_all_sessions,
    get_chat_history,
    update_chat_feedback,
)


router = APIRouter(prefix="/api/chat", tags=["chat"])


class ChatMessageRequest(BaseModel):
    session_id: str = Field(..., min_length=1, max_length=100)
    question: str = Field(..., min_length=1, max_length=20000)
    image_token: str | None = None
    chat_history: list[dict[str, Any]] = Field(default_factory=list)
    current_part_ids: list[str] = Field(default_factory=list)
    conversation_context: dict[str, Any] | None = None


def _is_admin(profile: dict[str, Any]) -> bool:
    return "admin" in [str(role).lower() for role in (profile.get("roles") or [])]


def _sse(event: str, data: Any) -> str:
    return f"event: {event}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n"


def _citation_list(retrieved_docs: list[Any]) -> list[dict[str, Any]]:
    return build_citation_list(retrieved_docs, page_has_vision=page_has_vision)


def _filter_citations_by_answer(
    citations: list[dict[str, Any]],
    answer: str,
    *,
    allow_legacy_fallback: bool = False,
) -> list[dict[str, Any]]:
    """Keep sources explicitly referenced by the generated answer."""
    return filter_citations_by_answer(
        citations,
        answer,
        allow_legacy_fallback=allow_legacy_fallback,
    )


def _answer_body_without_reference_appendix(value: str) -> str:
    """Remove the separately persisted legacy reference appendix."""
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


def _sources_for_chat_ids(
    chat_ids: list[int],
    runtime: Any,
) -> dict[int, list[dict[str, Any]]]:
    rows = runtime.app_support_queries.answer_sources_for_chat_ids(
        tuple(int(chat_id) for chat_id in chat_ids if chat_id)
    )
    out: dict[int, list[dict[str, Any]]] = {}
    for chat_id, doc_id, file_name, version_no, chunk_ref, score, has_vision in rows:
        page_no = safe_int(chunk_ref)
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


def _decorate_history_messages(
    messages: list[dict[str, Any]],
    runtime: Any,
) -> list[dict[str, Any]]:
    chat_ids = [int(message["chat_id"]) for message in messages if message.get("chat_id")]
    sources = _sources_for_chat_ids(chat_ids, runtime)
    decorated: list[dict[str, Any]] = []
    for message in messages:
        rendered = {key: value for key, value in message.items() if key != "ref_images"}
        image_url = _chat_image_url_from_path(message.get("image"))
        if image_url:
            rendered["image_url"] = image_url
        chat_id = message.get("chat_id")
        if chat_id:
            answer_body = _answer_body_without_reference_appendix(
                message.get("content") or ""
            )
            filtered = _filter_citations_by_answer(
                sources.get(int(chat_id), []),
                answer_body,
                allow_legacy_fallback=True,
            )
            rendered["content"] = answer_body
            rendered["citations"] = filtered
            rendered["ref_text"] = _citation_ref_text(filtered)
        decorated.append(rendered)
    return decorated


def _sign_image_upload(user_id: int, image_id: str) -> str:
    token, _ = app_security.create_session_token(
        user_id=user_id,
        username=f"image:{image_id}",
        ttl_seconds=15 * 60,
    )
    return token


def _verify_image_upload(
    profile: dict[str, Any],
    image_token: str | None,
) -> str | None:
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


@router.get("/sessions")
def sessions(profile: dict[str, Any] = Depends(current_profile)):
    return {
        "sessions": get_all_sessions(
            username=profile.get("username"),
            is_admin=False,
        )
    }


@router.post("/history")
def history(
    body: dict[str, Any],
    request: Request,
    profile: dict[str, Any] = Depends(csrf_profile),
):
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
    return {
        "messages": _decorate_history_messages(
            messages,
            request.app.state.runtime,
        )
    }


@router.delete("/sessions/{session_id}")
def delete_session(
    session_id: str,
    profile: dict[str, Any] = Depends(csrf_profile),
):
    return {
        "ok": True,
        "deleted": clear_chat_history(
            session_id,
            username=profile.get("username"),
            is_admin=False,
        ),
    }


@router.post("/upload-image")
def upload_chat_image(
    file: UploadFile = File(...),
    profile: dict[str, Any] = Depends(csrf_profile),
):
    allowed_ext = {
        ".png",
        ".jpg",
        ".jpeg",
        ".bmp",
        ".gif",
        ".webp",
        ".tif",
        ".tiff",
    }
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


@router.post("/message")
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


@router.post("/feedback")
def feedback(
    body: dict[str, Any],
    profile: dict[str, Any] = Depends(csrf_profile),
):
    chat_id = safe_int(body.get("chat_id"))
    rating = safe_int(body.get("rating"))
    if not chat_id or not rating:
        raise HTTPException(status_code=400, detail="Missing feedback data")
    update_chat_feedback(
        chat_id,
        1 if rating > 0 else -1,
        voter_username=profile.get("username"),
    )
    return {"ok": True}


__all__ = [
    "ChatMessageRequest",
    "_chat_actor_profile",
    "_citation_list",
    "_filter_citations_by_answer",
    "_resolve_chat_citations",
    "router",
]
