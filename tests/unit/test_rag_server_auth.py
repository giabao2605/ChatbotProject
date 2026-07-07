import asyncio

import pytest
from fastapi import HTTPException

pytestmark = pytest.mark.unit

rag_server = pytest.importorskip("mech_chatbot.api.rag_server")


def test_require_service_auth_rejects_bad_token(monkeypatch):
    monkeypatch.setattr(rag_server, "RAG_REQUIRE_SERVICE_AUTH", True)
    monkeypatch.setattr(rag_server, "RAG_SERVICE_TOKEN", "expected-token")

    with pytest.raises(HTTPException) as exc:
        asyncio.run(rag_server.require_service_auth(x_rag_service_token="wrong-token"))

    assert exc.value.status_code == 401


def test_require_service_auth_accepts_matching_token(monkeypatch):
    monkeypatch.setattr(rag_server, "RAG_REQUIRE_SERVICE_AUTH", True)
    monkeypatch.setattr(rag_server, "RAG_SERVICE_TOKEN", "expected-token")

    asyncio.run(rag_server.require_service_auth(x_rag_service_token="expected-token"))


def test_resolve_user_profile_uses_db_identity_not_body_rbac(monkeypatch):
    from mech_chatbot.auth import core

    captured = {}

    def fake_load_user_profile(user_id=None, username=None):
        captured["user_id"] = user_id
        captured["username"] = username
        return {
            "user_id": user_id,
            "username": username,
            "department": "Technical",
            "roles": ["viewer"],
            "allowed_departments": ["Technical"],
            "max_security_level": "public",
            "allowed_sites": ["HN"],
        }

    monkeypatch.setattr(core, "load_user_profile", fake_load_user_profile)

    req = rag_server.ChatRequest(
        user_id=7,
        username="bob",
        user_question="test",
        user_roles=["admin"],
        allowed_departments=["Finance"],
        max_security_level="confidential",
        allowed_sites=["ALL"],
    )

    profile = rag_server.resolve_user_profile(req)

    assert captured == {"user_id": 7, "username": "bob"}
    assert profile["roles"] == ["viewer"]
    assert profile["allowed_departments"] == ["Technical"]
    assert profile["max_security_level"] == "public"
