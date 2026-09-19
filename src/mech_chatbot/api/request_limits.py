"""Small request-boundary guards shared by API adapters."""

from __future__ import annotations

import json
from collections.abc import Mapping
from threading import Lock
from time import monotonic
from typing import BinaryIO

from fastapi import HTTPException, Request
from starlette.types import ASGIApp, Message, Receive, Scope, Send


class UploadTooLarge(ValueError):
    pass


class RequestBodyLimitMiddleware:
    """Reject oversized request bodies before multipart parsing."""

    def __init__(self, app: ASGIApp, limits: Mapping[str, int]):
        self.app = app
        self.limits = dict(limits)

    async def __call__(
        self,
        scope: Scope,
        receive: Receive,
        send: Send,
    ) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        path = str(scope.get("path") or "/").rstrip("/") or "/"
        limit = self.limits.get(path)
        if limit is None:
            await self.app(scope, receive, send)
            return

        content_length = next(
            (
                value
                for key, value in scope.get("headers") or ()
                if key.lower() == b"content-length"
            ),
            None,
        )
        if content_length is not None:
            try:
                declared_size = int(content_length)
            except (TypeError, ValueError):
                await _send_body_error(send, 400, "Invalid Content-Length")
                return
            if declared_size < 0:
                await _send_body_error(send, 400, "Invalid Content-Length")
                return
            if declared_size > limit:
                await _send_body_error(send, 413, "Request body is too large")
                return

        received = 0
        response_started = False

        async def limited_receive() -> Message:
            nonlocal received
            message = await receive()
            if message["type"] == "http.request":
                received += len(message.get("body") or b"")
                if received > limit:
                    raise UploadTooLarge
            return message

        async def tracked_send(message: Message) -> None:
            nonlocal response_started
            if message["type"] == "http.response.start":
                response_started = True
            await send(message)

        try:
            await self.app(scope, limited_receive, tracked_send)
        except UploadTooLarge:
            if response_started:
                raise
            await _send_body_error(send, 413, "Request body is too large")


async def _send_body_error(send: Send, status: int, detail: str) -> None:
    body = json.dumps({"detail": detail}).encode("utf-8")
    await send(
        {
            "type": "http.response.start",
            "status": status,
            "headers": (
                (b"content-type", b"application/json"),
                (b"content-length", str(len(body)).encode("ascii")),
            ),
        }
    )
    await send({"type": "http.response.body", "body": body})


def read_upload_limited(stream: BinaryIO, max_bytes: int) -> bytes:
    """Read at most ``max_bytes + 1`` before accepting or rejecting an upload."""
    body = bytearray()
    while len(body) <= max_bytes:
        chunk = stream.read(min(1024 * 1024, max_bytes + 1 - len(body)))
        if not chunk:
            break
        body.extend(chunk)
    if len(body) > max_bytes:
        raise UploadTooLarge
    return bytes(body)


class _WindowLimiter:
    def __init__(self, limit: int, window_seconds: int, max_keys: int = 4096):
        self.limit = limit
        self.window_seconds = window_seconds
        self.max_keys = max_keys
        self._entries: dict[str, tuple[int, float]] = {}
        self._lock = Lock()

    def allow(self, key: str) -> bool:
        now = monotonic()
        with self._lock:
            entry = self._entries.get(key)
            if entry is None and len(self._entries) >= self.max_keys:
                expired = [
                    current
                    for current, (_, started) in self._entries.items()
                    if now - started >= self.window_seconds
                ]
                for current in expired:
                    del self._entries[current]
                if len(self._entries) >= self.max_keys:
                    return False
            count, started = entry or (0, now)
            if now - started >= self.window_seconds:
                count, started = 0, now
            if count >= self.limit:
                return False
            self._entries[key] = (count + 1, started)
            return True


_registry_lock = Lock()


def enforce_request_rate_limit(
    request: Request,
    profile: dict,
    *,
    scope: str,
    limit: int,
    window_seconds: int = 60,
) -> None:
    """Apply one bounded budget per user, falling back to direct client IP."""
    config = (scope, limit, window_seconds)
    with _registry_lock:
        registry = getattr(request.app.state, "_request_rate_limiters", None)
        if registry is None:
            registry = {}
            request.app.state._request_rate_limiters = registry
        limiter = registry.get(config)
        if limiter is None:
            limiter = _WindowLimiter(limit, window_seconds)
            registry[config] = limiter
    user = profile.get("user_id")
    if user is None:
        user = profile.get("username")
    client = getattr(request, "client", None)
    host = getattr(client, "host", None) or "unknown"
    key = f"user:{user}" if user is not None else f"ip:{host}"
    if not limiter.allow(key):
        raise HTTPException(
            status_code=429,
            detail="Too many requests. Please try again later.",
            headers={"Retry-After": str(window_seconds)},
        )
