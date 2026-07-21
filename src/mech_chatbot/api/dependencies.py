"""Shared FastAPI dependencies for the browser API."""

from __future__ import annotations

import sys
from typing import Any

from fastapi import Depends, HTTPException, Request, status

from mech_chatbot.api import app_security
from mech_chatbot.auth.authorization import role_allows
from mech_chatbot.auth.core import load_user_profile


def _profile_loader():
    app_server = sys.modules.get("mech_chatbot.api.app_server")
    if app_server is not None and hasattr(app_server, "load_user_profile"):
        return getattr(app_server, "load_user_profile")
    return load_user_profile


def _role_checker():
    app_server = sys.modules.get("mech_chatbot.api.app_server")
    if app_server is not None and hasattr(app_server, "role_allows"):
        return getattr(app_server, "role_allows")
    return role_allows


def public_profile(profile: dict[str, Any], csrf: str | None = None) -> dict[str, Any]:
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


def session_payload(request: Request) -> app_security.SessionPayload:
    return app_security.verify_session_token(
        request.cookies.get(app_security.SESSION_COOKIE_NAME)
    )


def current_profile(request: Request) -> dict[str, Any]:
    payload = session_payload(request)
    profile = _profile_loader()(user_id=payload.user_id, username=payload.username)
    if not profile:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="User is inactive or invalid",
        )
    return profile


def csrf_profile(request: Request) -> dict[str, Any]:
    payload = session_payload(request)
    app_security.require_csrf(request, payload)
    profile = _profile_loader()(user_id=payload.user_id, username=payload.username)
    if not profile:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="User is inactive or invalid",
        )
    return profile


def require_any_role(*roles: str):
    def _dep(profile: dict[str, Any] = Depends(current_profile)) -> dict[str, Any]:
        if not _role_checker()(profile.get("roles"), *roles):
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Forbidden",
            )
        return profile

    return _dep


__all__ = [
    "csrf_profile",
    "current_profile",
    "public_profile",
    "require_any_role",
    "session_payload",
]
