import json

import pytest
from fastapi.testclient import TestClient

pytestmark = pytest.mark.unit

app_server = pytest.importorskip("mech_chatbot.api.app_server")


class _FakeResponse:
    def __init__(self, status_code=200, payload=None, text="", events=None):
        self.status_code = status_code
        self._payload = payload or {}
        self.text = text
        self.ok = 200 <= status_code < 400
        self.events = events or []

    def json(self):
        return self._payload

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def iter_lines(self, decode_unicode=True):
        for event, data in self.events:
            yield f"event: {event}"
            yield "data: " + json.dumps(data, ensure_ascii=False)
            yield ""


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


def _admin_profile():
    profile = _profile()
    profile["roles"] = ["admin"]
    return profile


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


def test_ingestion_eta_returns_flat_queue_metrics(monkeypatch):
    monkeypatch.setattr(
        app_server,
        "queue_eta_seconds",
        lambda: {"pending": 2, "avg_seconds": 4.5, "eta_seconds": 9},
    )

    assert app_server.ingestion_eta(profile={"roles": ["admin"]}) == {
        "pending": 2,
        "avg_seconds": 4.5,
        "eta_seconds": 9,
    }


def test_dashboard_requires_admin_role():
    with pytest.raises(app_server.HTTPException) as exc_info:
        app_server.require_any_role("admin")({"roles": ["viewer"]})

    assert exc_info.value.status_code == 403


def test_dashboard_endpoint_blocks_a_viewer():
    app_server.app.dependency_overrides[app_server.current_profile] = _profile
    try:
        with TestClient(app_server.app) as client:
            response = client.get("/api/dashboard")
    finally:
        app_server.app.dependency_overrides.clear()

    assert response.status_code == 403


def test_external_ai_policy_endpoint_returns_metadata_only(monkeypatch):
    profiles = [{
        "provider": "voyage",
        "default_model": "rerank-2.5-lite",
        "secret_reference": "env:VOYAGE_API_KEY",
        "allowed_surfaces": ["reranking"],
        "policy_version": "risk-accepted-v3",
    }]
    monkeypatch.setattr(app_server, "list_external_ai_provider_profiles", lambda: profiles)
    app_server.app.dependency_overrides[app_server.current_profile] = _admin_profile
    try:
        with TestClient(app_server.app) as client:
            response = client.get("/api/settings/external-ai-policy")
    finally:
        app_server.app.dependency_overrides.clear()

    assert response.status_code == 200
    assert response.json() == {"profiles": profiles}


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

    def fake_post(url, headers, json, timeout, stream):
        posts.append({"url": url, "headers": headers, "json": json, "timeout": timeout, "stream": stream})
        return _FakeResponse(
            events=[
                ("accepted", {"ok": True}),
                ("metadata", {
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
                        "citation_docs": [
                            {
                                "doc_id": 42,
                                "trang": 3,
                                "file_goc": "bom.pdf",
                                "version_no": 1,
                                "score": 0.91,
                                "security_level": "confidential",
                                "source_id": "D42P3",
                            }
                        ],
                    },
                }),
                ("delta", {"text": "Tra loi dung "}),
                ("delta", {"text": "[Nguồn: bom.pdf, Trang 3, Version 1, SourceID D42P3]"}),
                ("done", {"ok": True, "elapsed_ms": 25}),
            ]
        )

    monkeypatch.setattr(app_server.requests, "post", fake_post)
    monkeypatch.setattr(app_server, "save_chat_history", lambda **_kwargs: 123)
    monkeypatch.setattr(app_server, "save_answer_sources", lambda chat_id, docs: saved_sources.append((chat_id, docs)))
    monkeypatch.setattr(app_server, "write_audit_log", lambda **kwargs: audit_actions.append(kwargs))
    monkeypatch.setattr(app_server, "page_has_vision", lambda doc_id, page_no: (doc_id, page_no) == (42, 3))

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
    assert posts[0]["url"].endswith("/chat/stream")
    assert posts[0]["stream"] is True
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
    assert saved_sources == [(123, [{
        "doc_id": 42,
        "file_goc": "bom.pdf",
        "version_no": 1,
        "trang": 3,
        "score": 0.91,
        "source_id": "D42P3",
    }])]
    assert [item["action"] for item in audit_actions] == ["chat_query", "read_confidential"]

    events = _events(response.text)
    assert [name for name, _data in events] == ["thinking", "delta", "delta", "citation", "done"]
    done = events[-1][1]
    assert done["chat_id"] == 123
    assert done["new_part_ids"] == ["P123"]
    assert done["conversation_context"] == {"topic": "bom"}
    assert done["citations"] == [
        {
            "doc_id": 42,
            "page_no": 3,
            "file_name": "bom.pdf",
            "version_no": 1,
            "score": 0.91,
            "source_id": "D42P3",
            "has_vision": True,
            "page_url": "/api/files/documents/42/pages/3",
            "original_url": "/api/files/documents/42/original",
        }
    ]


def test_text_citation_has_download_without_preview(monkeypatch):
    monkeypatch.setattr(app_server, "page_has_vision", lambda _doc_id, _page_no: False)
    citations = app_server._citation_list([
        {
            "doc_id": 9,
            "trang": 1,
            "file_goc": "policy.docx",
            "score": 0.88,
            "source_id": "D9P1",
        }
    ])
    assert citations == [{
        "doc_id": 9,
        "page_no": 1,
        "file_name": "policy.docx",
        "score": 0.88,
        "source_id": "D9P1",
        "has_vision": False,
        "page_url": None,
        "original_url": "/api/files/documents/9/original",
    }]


def test_live_citation_filter_requires_exact_source_id():
    citations = [
        {"doc_id": 42, "page_no": 3, "file_name": "bom.pdf", "source_id": "D42P3"},
        {"doc_id": 42, "page_no": 4, "file_name": "bom.pdf", "source_id": "D42P4"},
    ]

    assert app_server._filter_citations_by_answer(citations, "Nguồn: bom.pdf, Trang 3") == []
    assert app_server._filter_citations_by_answer(
        citations,
        "Nguồn: bom.pdf, Trang 3, SourceID D42P3",
    ) == [citations[0]]


def test_bulk_publish_returns_pending_without_marking_job_published(monkeypatch):
    class _PendingPublication:
        ok = True
        state = "processing"

        def __bool__(self):
            return True

        def to_dict(self):
            return {"ok": True, "doc_id": 42, "state": self.state}

    marked = []
    monkeypatch.setattr(app_server, "publish_document", lambda *_args, **_kwargs: _PendingPublication())
    monkeypatch.setattr(app_server, "mark_job_published", lambda job_id: marked.append(job_id))

    result = app_server.documents_review_bulk(
        {"action": "publish", "items": [{"job_id": 9, "doc_id": 42}]},
        profile={"roles": ["reviewer"], "username": "reviewer", "user_id": 7},
    )

    assert result == {
        "ok": True,
        "updated": 0,
        "pending": 1,
        "failed": 0,
        "failures": [{"ok": True, "doc_id": 42, "state": "processing"}],
    }
    assert marked == []


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
