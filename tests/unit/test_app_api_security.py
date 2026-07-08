import pytest
from fastapi import HTTPException

pytestmark = pytest.mark.unit

security = pytest.importorskip("mech_chatbot.api.app_security")


class _Req:
    def __init__(self, method="POST", token=""):
        self.method = method
        self.headers = {"X-CSRF-Token": token} if token else {}


def test_session_token_round_trip(monkeypatch):
    monkeypatch.setenv("APP_SESSION_SECRET", "test-secret")

    token, payload = security.create_session_token(user_id=7, username="alice", ttl_seconds=60)
    verified = security.verify_session_token(token)

    assert verified.user_id == 7
    assert verified.username == "alice"
    assert verified.csrf == payload.csrf


def test_session_token_rejects_tampering(monkeypatch):
    monkeypatch.setenv("APP_SESSION_SECRET", "test-secret")
    token, _payload = security.create_session_token(user_id=7, username="alice", ttl_seconds=60)
    body, sig = token.split(".")

    with pytest.raises(HTTPException) as exc:
        security.verify_session_token(f"{body}x.{sig}")

    assert exc.value.status_code == 401


def test_session_token_expires(monkeypatch):
    monkeypatch.setenv("APP_SESSION_SECRET", "test-secret")
    token, _payload = security.create_session_token(user_id=7, username="alice", ttl_seconds=-1)

    with pytest.raises(HTTPException) as exc:
        security.verify_session_token(token)

    assert exc.value.status_code == 401


def test_csrf_required_for_mutating_request(monkeypatch):
    monkeypatch.setenv("APP_SESSION_SECRET", "test-secret")
    _token, payload = security.create_session_token(user_id=7, username="alice", ttl_seconds=60)

    with pytest.raises(HTTPException) as exc:
        security.require_csrf(_Req(method="POST", token="wrong"), payload)

    assert exc.value.status_code == 403
    security.require_csrf(_Req(method="POST", token=payload.csrf), payload)
    security.require_csrf(_Req(method="GET"), payload)
