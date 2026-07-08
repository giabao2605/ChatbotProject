"""Security helpers for the browser-facing app API.

This module deliberately uses only stdlib primitives. The browser-facing API
keeps the cookie HttpOnly and returns a CSRF token from /api/auth/me so the Vue
client can echo it in X-CSRF-Token for mutating requests.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import secrets
import time
from dataclasses import dataclass
from typing import Any

from fastapi import HTTPException, Request, Response, status

SESSION_COOKIE_NAME = "mech_app_session"
DEFAULT_SESSION_TTL_SECONDS = 45 * 60
_VALID_SAMESITE = {"lax", "strict", "none"}


@dataclass(frozen=True)
class SessionPayload:
    user_id: int
    username: str
    exp: int
    csrf: str


def _b64url_encode(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode("ascii")


def _b64url_decode(raw: str) -> bytes:
    pad = "" if len(raw) % 4 == 0 else "=" * (4 - (len(raw) % 4))
    return base64.urlsafe_b64decode((raw + pad).encode("ascii"))


def _session_secret() -> bytes:
    secret = (
        os.getenv("APP_SESSION_SECRET")
        or os.getenv("CHAT_BRIDGE_SECRET")
        or os.getenv("RAG_SERVICE_TOKEN")
        or ""
    ).strip()
    if not secret:
        raise RuntimeError(
            "APP_SESSION_SECRET is not configured. Set APP_SESSION_SECRET "
            "or reuse CHAT_BRIDGE_SECRET/RAG_SERVICE_TOKEN for local migration."
        )
    return secret.encode("utf-8")


def _sign(body: str) -> str:
    return _b64url_encode(
        hmac.new(_session_secret(), body.encode("ascii"), hashlib.sha256).digest()
    )


def _cookie_secure() -> bool:
    return os.getenv("APP_COOKIE_SECURE", "").strip().lower() in {"1", "true", "yes", "on"}


def _cookie_samesite() -> str:
    value = os.getenv("APP_COOKIE_SAMESITE", "lax").strip().lower()
    return value if value in _VALID_SAMESITE else "lax"


def session_ttl_seconds() -> int:
    raw = os.getenv("APP_SESSION_TTL_SECONDS", "").strip()
    if not raw:
        return DEFAULT_SESSION_TTL_SECONDS
    try:
        return max(60, min(int(raw), 24 * 3600))
    except ValueError:
        return DEFAULT_SESSION_TTL_SECONDS


def create_session_token(*, user_id: int, username: str, ttl_seconds: int | None = None) -> tuple[str, SessionPayload]:
    ttl = ttl_seconds if ttl_seconds is not None else session_ttl_seconds()
    payload: dict[str, Any] = {
        "user_id": int(user_id),
        "username": str(username),
        "exp": int(time.time()) + int(ttl),
        "csrf": secrets.token_urlsafe(32),
    }
    body = _b64url_encode(json.dumps(payload, separators=(",", ":")).encode("utf-8"))
    token = f"{body}.{_sign(body)}"
    return token, SessionPayload(**payload)


def verify_session_token(token: str | None) -> SessionPayload:
    if not token:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Missing session")
    parts = token.split(".")
    if len(parts) != 2:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Malformed session")
    body, sig = parts
    expected = _sign(body)
    if not hmac.compare_digest(sig, expected):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid session")
    try:
        raw = json.loads(_b64url_decode(body).decode("utf-8"))
        payload = SessionPayload(
            user_id=int(raw["user_id"]),
            username=str(raw["username"]),
            exp=int(raw["exp"]),
            csrf=str(raw["csrf"]),
        )
    except Exception as exc:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid session payload") from exc
    if payload.exp < int(time.time()):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Session expired")
    return payload


def set_session_cookie(response: Response, token: str) -> None:
    response.set_cookie(
        key=SESSION_COOKIE_NAME,
        value=token,
        httponly=True,
        secure=_cookie_secure(),
        samesite=_cookie_samesite(),
        max_age=session_ttl_seconds(),
        path="/",
    )


def clear_session_cookie(response: Response) -> None:
    response.delete_cookie(key=SESSION_COOKIE_NAME, path="/")


def require_csrf(request: Request, payload: SessionPayload) -> None:
    if request.method.upper() in {"GET", "HEAD", "OPTIONS"}:
        return
    supplied = request.headers.get("X-CSRF-Token", "")
    if not supplied or not hmac.compare_digest(supplied, payload.csrf):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Invalid CSRF token")
