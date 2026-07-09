import json

import pytest
from fastapi.testclient import TestClient

pytestmark = pytest.mark.unit

app_server = pytest.importorskip("mech_chatbot.api.app_server")


class _FakeResponse:
    def __init__(self, status_code=200, payload=None, text=""):
        self.status_code = status_code
        self._payload = payload or {}
        self.text = text
        self.ok = 200 <= status_code < 400

    def json(self):
        return self._payload


def _profile():
    return {
        "user_id": 7,
        "username": "alice",
        "display_name": "Alice",
        "department": "CoKhi",
        "roles": ["viewer"],
        "allowed_departments": ["CoKhi"],
        "max_security_level": "confidential",
        "allowed_sites": ["HN"],
        "preferred_language": "vi",
    }


def _events(body):
    parsed = []
    for block in body.strip().split("\n\n"):
        event = None
        data = None
        for line in block.splitlines():
            if line.startswith("event: "):
                event = line.removeprefix("event: ")
            if line.startswith("data: "):
                data = json.loads(line.removeprefix("data: "))
        if event:
            parsed.append((event, data))
    return parsed


@pytest.fixture
def client():
    app_server.app.dependency_overrides[app_server.csrf_profile] = _profile
    try:
        yield TestClient(app_server.app)
    finally:
        app_server.app.dependency_overrides.clear()


def test_chat_message_persists_sources_audit_and_streams_sse(monkeypatch, client):
    posts = []
    saved_sources = []
    audit_actions = []

    def fake_post(url, headers, json, timeout):
        posts.append({"url": url, "headers": headers, "json": json, "timeout": timeout})
        return _FakeResponse(
            payload={
                "response": "Tra loi dung",
                "ref_text": "\nNguon: PDF",
                "new_part_ids": ["P123"],
                "debug_info": {
                    "conversation_context": {"topic": "bom"},
                    "retrieved_docs": [
                        {
                            "doc_id": 42,
                            "trang": 3,
                            "file_goc": "bom.pdf",
                            "score": 0.91,
                            "security_level": "confidential",
                        }
                    ],
                },
            }
        )

    monkeypatch.setattr(app_server.requests, "post", fake_post)
    monkeypatch.setattr(app_server.time, "sleep", lambda _seconds: None)
    monkeypatch.setattr(app_server, "save_chat_history", lambda **_kwargs: 123)
    monkeypatch.setattr(app_server, "save_answer_sources", lambda chat_id, docs: saved_sources.append((chat_id, docs)))
    monkeypatch.setattr(app_server, "write_audit_log", lambda **kwargs: audit_actions.append(kwargs))

    response = client.post(
        "/api/chat/message",
        json={
            "session_id": "s1",
            "question": "  cau hoi ve BOM  ",
            "chat_history": [{"role": "user", "content": "prev"}],
            "current_part_ids": ["OLD"],
            "conversation_context": {"prev": True},
        },
    )

    assert response.status_code == 200
    assert posts[0]["url"].endswith("/chat")
    assert posts[0]["json"] == {
        "user_id": 7,
        "username": "alice",
        "user_question": "cau hoi ve BOM",
        "image_path": None,
        "chat_history": [{"role": "user", "content": "prev"}],
        "current_part_ids": ["OLD"],
        "response_language": "vi",
        "conversation_context": {"prev": True},
    }
    assert saved_sources == [(123, [
        {
            "doc_id": 42,
            "trang": 3,
            "file_goc": "bom.pdf",
            "score": 0.91,
            "security_level": "confidential",
        }
    ])]
    assert [item["action"] for item in audit_actions] == ["chat_query", "read_confidential"]

    events = _events(response.text)
    assert [name for name, _data in events] == ["thinking", "delta", "delta", "delta", "done"]
    done = events[-1][1]
    assert done["chat_id"] == 123
    assert done["new_part_ids"] == ["P123"]
    assert done["conversation_context"] == {"topic": "bom"}
    assert done["citations"] == [
        {
            "doc_id": 42,
            "page_no": 3,
            "file_name": "bom.pdf",
            "score": 0.91,
            "page_url": "/api/files/documents/42/pages/3",
            "original_url": "/api/files/documents/42/original",
        }
    ]


def test_chat_message_emits_busy_error_without_persisting(monkeypatch, client):
    saved = []

    monkeypatch.setattr(
        app_server.requests,
        "post",
        lambda *_args, **_kwargs: _FakeResponse(status_code=503, text="busy"),
    )
    monkeypatch.setattr(app_server, "save_chat_history", lambda **kwargs: saved.append(kwargs))

    response = client.post("/api/chat/message", json={"session_id": "s1", "question": "hello"})

    assert response.status_code == 200
    assert saved == []
    events = _events(response.text)
    assert [name for name, _data in events] == ["thinking", "error"]
    assert events[-1][1]["status"] == 503
    assert events[-1][1]["message"] == "RAG server busy"
