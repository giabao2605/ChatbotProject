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
import secrets
import time
from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
from threading import RLock
from typing import Any, Protocol

from fastapi import HTTPException, Request, Response, status

SESSION_COOKIE_NAME = "mech_app_session"
DEFAULT_SESSION_TTL_SECONDS = 45 * 60
_VALID_SAMESITE = {"lax", "strict", "none"}


class AppSecuritySettings(Protocol):
    session_secret: str
    cookie_secure: bool
    cookie_samesite: str
    session_ttl_seconds: int


_REQUEST_SETTINGS: ContextVar[AppSecuritySettings | None] = ContextVar(
    "app_security_settings",
    default=None,
)


@dataclass(frozen=True)
class SessionPayload:
    user_id: int
    username: str
    exp: int
    csrf: str
    session_id: str = ""


# Session revocation is intentionally process-local.  The current app contract
# has no shared session store, so this closes replay in the serving process
# without pretending to provide multi-worker or multi-instance revocation.
_SESSION_REVOCATIONS: dict[str, int] = {}
_SESSION_REVOCATIONS_LOCK = RLock()
_REVOCATION_STORE: ContextVar[Any] = ContextVar('session_revocation_store', default=None)


@contextmanager
def bind_session_revocations(store):
    token = _REVOCATION_STORE.set(store)
    try:
        yield
    finally:
        _REVOCATION_STORE.reset(token)


def _b64url_encode(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode("ascii")


def _b64url_decode(raw: str) -> bytes:
    pad = "" if len(raw) % 4 == 0 else "=" * (4 - (len(raw) % 4))
    return base64.urlsafe_b64decode((raw + pad).encode("ascii"))


@contextmanager
def bind_security_settings(settings: AppSecuritySettings) -> Iterator[None]:
    """Bind one immutable app-process snapshot to the current request context."""

    token = _REQUEST_SETTINGS.set(settings)
    try:
        yield
    finally:
        _REQUEST_SETTINGS.reset(token)


def _resolved_settings(
    settings: AppSecuritySettings | None,
) -> AppSecuritySettings:
    resolved = settings or _REQUEST_SETTINGS.get()
    if resolved is None:
        raise RuntimeError("App security settings are not bound to this request.")
    return resolved


def _session_secret(settings: AppSecuritySettings | None = None) -> bytes:
    secret = _resolved_settings(settings).session_secret.strip()
    if not secret:
        raise RuntimeError(
            "APP_SESSION_SECRET is not configured. Set APP_SESSION_SECRET "
            "or reuse CHAT_BRIDGE_SECRET/RAG_SERVICE_TOKEN for local migration."
        )
    return secret.encode("utf-8")


def _sign(
    body: str,
    settings: AppSecuritySettings | None = None,
) -> str:
    return _b64url_encode(
        hmac.new(
            _session_secret(settings),
            body.encode("ascii"),
            hashlib.sha256,
        ).digest()
    )


def _cookie_secure(settings: AppSecuritySettings | None = None) -> bool:
    return _resolved_settings(settings).cookie_secure


def _cookie_samesite(settings: AppSecuritySettings | None = None) -> str:
    value = _resolved_settings(settings).cookie_samesite
    return value if value in _VALID_SAMESITE else "lax"


def session_ttl_seconds(
    settings: AppSecuritySettings | None = None,
) -> int:
    return _resolved_settings(settings).session_ttl_seconds


def create_session_token(
    *,
    user_id: int,
    username: str,
    ttl_seconds: int | None = None,
    session_id: str | None = None,
    issued_at: int | None = None,
    settings: AppSecuritySettings | None = None,
) -> tuple[str, SessionPayload]:
    ttl = (
        ttl_seconds
        if ttl_seconds is not None
        else session_ttl_seconds(settings)
    )
    payload: dict[str, Any] = {
        "user_id": int(user_id),
        "username": str(username),
        "exp": (int(time.time()) if issued_at is None else int(issued_at)) + int(ttl),
        "csrf": secrets.token_urlsafe(32),
        "session_id": str(session_id or secrets.token_urlsafe(32)),
    }
    body = _b64url_encode(json.dumps(payload, separators=(",", ":")).encode("utf-8"))
    token = f"{body}.{_sign(body, settings)}"
    return token, SessionPayload(**payload)


def verify_session_token(
    token: str | None,
    *,
    settings: AppSecuritySettings | None = None,
) -> SessionPayload:
    if not token:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Missing session")
    parts = token.split(".")
    if len(parts) != 2:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Malformed session")
    body, sig = parts
    expected = _sign(body, settings)
    if not hmac.compare_digest(sig, expected):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid session")
    try:
        raw = json.loads(_b64url_decode(body).decode("utf-8"))
        payload = SessionPayload(
            user_id=int(raw["user_id"]),
            username=str(raw["username"]),
            exp=int(raw["exp"]),
            csrf=str(raw["csrf"]),
            session_id=str(raw.get("session_id") or ""),
        )
    except Exception as exc:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid session payload") from exc
    if payload.exp <= int(time.time()):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Session expired")
    if _is_session_revoked(token, payload.session_id):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Session revoked")
    return payload


def revoke_session(
    token: str | None,
    *,
    session_id: str | None = None,
    expires_at: int | None = None,
    settings: AppSecuritySettings | None = None,
) -> None:
    """Revoke a session lineage without retaining the bearer token itself."""

    if not token and not session_id:
        return
    now = int(time.time())
    expiry = int(expires_at or (now + DEFAULT_SESSION_TTL_SECONDS))
    if session_id:
        configured = settings or _REQUEST_SETTINGS.get()
        ttl = session_ttl_seconds(configured) if configured else DEFAULT_SESSION_TTL_SECONDS
        # Refresh expiry is anchored before authentication. Any refresh that
        # authenticated before this logout expires no later than now + TTL.
        expiry = max(expiry, now + ttl)
    if expiry <= now:
        return
    keys = []
    if token:
        keys.append(_token_revocation_key(token))
    if session_id:
        keys.append(_session_revocation_key(session_id))
    store = _REVOCATION_STORE.get()
    if store is not None:
        try:
            store.revoke(keys, expiry, now)
        except Exception as exc:
            raise HTTPException(status_code=503, detail="Session service unavailable") from exc
        return
    with _SESSION_REVOCATIONS_LOCK:
        _purge_session_revocations(now)
        for key in keys:
            current = _SESSION_REVOCATIONS.get(key)
            if current is None or current < expiry:
                _SESSION_REVOCATIONS[key] = expiry


def _is_session_revoked(token: str, session_id: str) -> bool:
    now = int(time.time())
    store = _REVOCATION_STORE.get()
    if store is not None:
        keys = [_token_revocation_key(token)]
        if session_id:
            keys.append(_session_revocation_key(session_id))
        try:
            return store.is_revoked(keys, now)
        except Exception as exc:
            raise HTTPException(status_code=503, detail="Session service unavailable") from exc
    with _SESSION_REVOCATIONS_LOCK:
        _purge_session_revocations(now)
        return (
            _SESSION_REVOCATIONS.get(_token_revocation_key(token), 0) > now
            or (
                bool(session_id)
                and _SESSION_REVOCATIONS.get(_session_revocation_key(session_id), 0)
                > now
            )
        )


def _token_revocation_key(token: str) -> str:
    return f"token:{hashlib.sha256(token.encode('utf-8')).hexdigest()}"


def _session_revocation_key(session_id: str) -> str:
    return "session:" + session_id


def _purge_session_revocations(now: int) -> None:
    expired = [
        key
        for key, expiry in _SESSION_REVOCATIONS.items()
        if expiry <= now
    ]
    for key in expired:
        del _SESSION_REVOCATIONS[key]


def set_session_cookie(
    response: Response,
    token: str,
    *,
    settings: AppSecuritySettings | None = None,
) -> None:
    response.set_cookie(
        key=SESSION_COOKIE_NAME,
        value=token,
        httponly=True,
        secure=_cookie_secure(settings),
        samesite=_cookie_samesite(settings),
        max_age=session_ttl_seconds(settings),
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
