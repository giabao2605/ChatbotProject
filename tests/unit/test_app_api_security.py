from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
import subprocess
import sys

import pytest
from fastapi import HTTPException, Response

from mech_chatbot.config.settings import AppProcessSettings, Settings

pytestmark = pytest.mark.unit

security = pytest.importorskip("mech_chatbot.api.app_security")


class _Req:
    def __init__(self, method="POST", token=""):
        self.method = method
        self.headers = {"X-CSRF-Token": token} if token else {}


def test_importing_security_helpers_does_not_load_global_settings_module():
    source_root = Path(__file__).resolve().parents[2] / "src"
    result = subprocess.run(
        [
            sys.executable,
            "-c",
            (
                "import sys; "
                "import mech_chatbot.api.app_security; "
                "print('mech_chatbot.config.settings' in sys.modules)"
            ),
        ],
        cwd=source_root,
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 0
    assert result.stdout.strip() == "False"


def _app_settings(
    secret: str = "test-secret",
    **environment: str,
) -> AppProcessSettings:
    return AppProcessSettings.from_settings(
        Settings.from_env({"APP_SESSION_SECRET": secret, **environment})
    )


def test_session_token_round_trip():
    settings = _app_settings()

    token, payload = security.create_session_token(
        user_id=7,
        username="alice",
        ttl_seconds=60,
        settings=settings,
    )
    verified = security.verify_session_token(token, settings=settings)

    assert verified.user_id == 7
    assert verified.username == "alice"
    assert verified.csrf == payload.csrf


def test_session_token_rejects_tampering():
    settings = _app_settings()
    token, _payload = security.create_session_token(
        user_id=7,
        username="alice",
        ttl_seconds=60,
        settings=settings,
    )
    body, sig = token.split(".")

    with pytest.raises(HTTPException) as exc:
        security.verify_session_token(f"{body}x.{sig}", settings=settings)

    assert exc.value.status_code == 401


def test_missing_session_secret_fails_without_echoing_secret_values():
    settings = _app_settings("")

    with pytest.raises(RuntimeError) as exc:
        security.create_session_token(
            user_id=7,
            username="alice",
            settings=settings,
        )

    assert "APP_SESSION_SECRET" in str(exc.value)


def test_session_token_expires():
    settings = _app_settings()
    token, _payload = security.create_session_token(
        user_id=7,
        username="alice",
        ttl_seconds=-1,
        settings=settings,
    )

    with pytest.raises(HTTPException) as exc:
        security.verify_session_token(token, settings=settings)

    assert exc.value.status_code == 401


def test_csrf_required_for_mutating_request():
    settings = _app_settings()
    _token, payload = security.create_session_token(
        user_id=7,
        username="alice",
        ttl_seconds=60,
        settings=settings,
    )

    with pytest.raises(HTTPException) as exc:
        security.require_csrf(_Req(method="POST", token="wrong"), payload)

    assert exc.value.status_code == 403
    security.require_csrf(_Req(method="POST", token=payload.csrf), payload)
    security.require_csrf(_Req(method="GET"), payload)


def test_session_security_uses_the_explicit_snapshot_not_later_environment(
    monkeypatch,
):
    settings = _app_settings(
        "snapshot-secret",
        APP_COOKIE_SECURE="true",
        APP_COOKIE_SAMESITE="strict",
        APP_SESSION_TTL_SECONDS="600",
    )
    monkeypatch.setenv("APP_SESSION_SECRET", "later-secret")
    monkeypatch.setenv("APP_COOKIE_SECURE", "false")

    token, _payload = security.create_session_token(
        user_id=7,
        username="alice",
        settings=settings,
    )
    verified = security.verify_session_token(token, settings=settings)

    assert verified.user_id == 7
    assert security.session_ttl_seconds(settings=settings) == 600


def test_session_cookie_uses_typed_security_settings():
    settings = _app_settings(
        APP_COOKIE_SECURE="true",
        APP_COOKIE_SAMESITE="strict",
        APP_SESSION_TTL_SECONDS="600",
    )
    response = Response()

    security.set_session_cookie(response, "signed-token", settings=settings)

    cookie = response.headers["set-cookie"]
    assert "HttpOnly" in cookie
    assert "Max-Age=600" in cookie
    assert "SameSite=strict" in cookie
    assert "Secure" in cookie


def test_request_binding_keeps_concurrent_security_settings_isolated():
    first = _app_settings("first-secret")
    second = _app_settings("second-secret")

    def issue(settings, user_id, username):
        with security.bind_security_settings(settings):
            return security.create_session_token(
                user_id=user_id,
                username=username,
            )[0]

    with ThreadPoolExecutor(max_workers=2) as executor:
        first_future = executor.submit(issue, first, 1, "first")
        second_future = executor.submit(issue, second, 2, "second")
        first_token = first_future.result()
        second_token = second_future.result()

    assert security.verify_session_token(first_token, settings=first).user_id == 1
    assert security.verify_session_token(second_token, settings=second).user_id == 2
    with pytest.raises(HTTPException):
        security.verify_session_token(first_token, settings=second)
