import pytest
from fastapi.testclient import TestClient

from mech_chatbot.api import dependencies
from mech_chatbot.api import app_server
from mech_chatbot.api.routers import operations


pytestmark = pytest.mark.unit


def test_quality_documents_rejects_negative_limit_before_query(monkeypatch):
    profile = {"user_id": 7, "username": "reviewer_b", "roles": ["reviewer"]}
    app_server.app.dependency_overrides[dependencies.current_profile] = lambda: profile
    monkeypatch.setattr(
        operations.feedback_service,
        "get_doc_quality_ranking",
        lambda **kwargs: pytest.fail("invalid limit reached database query"),
    )
    try:
        client = TestClient(app_server.app)
        response = client.get("/api/quality/documents?limit=-1")
        client.close()
        assert response.status_code == 422
    finally:
        app_server.app.dependency_overrides.clear()
