from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from mech_chatbot.api import app_security, app_server, rag_server
from mech_chatbot.api import dependencies
from mech_chatbot.api.routers import documents as document_routes
from mech_chatbot.api.routers import operations as operation_routes
from mech_chatbot.application.chat_turn import ChatCitation, ChatDelta, ChatDone
from mech_chatbot.application.document_review import PublicationCoordinator, ReviewDocuments
from mech_chatbot.application.document_upload import DocumentUpload, StoredUpload
from mech_chatbot.rag.execution import (
    RagCancelled,
    RagCitation,
    RagCompleted,
    RagFailed,
    RagPrepared,
    RagToken,
)


pytestmark = pytest.mark.unit

APP_PROFILE = {
    "user_id": 7,
    "username": "wave6-admin",
    "display_name": "Wave 6 Admin",
    "department": "CoKhi",
    "roles": ["admin", "reviewer", "uploader", "security_admin", "platform_admin"],
    "allowed_departments": ["CoKhi", "QA", "Shared"],
    "allowed_sites": ["HN"],
    "max_security_level": "confidential",
    "preferred_language": "vi",
}
RAG_HEADERS = {"X-RAG-Service-Token": "wave6-service-token"}
RAG_PROFILE = {
    "user_id": 17,
    "username": "wave6-viewer",
    "department": "Technical",
    "roles": ["viewer"],
    "allowed_departments": ["Technical"],
    "max_security_level": "internal",
    "allowed_sites": ["HQ"],
}


@pytest.fixture
def app_client(monkeypatch):
    app_server.app.dependency_overrides.clear()
    monkeypatch.delenv("RAG_SERVICE_TOKEN", raising=False)
    monkeypatch.setattr(
        app_server.app.state,
        "process_settings",
        replace(
            app_server.app.state.process_settings,
            session_secret="wave6-session-secret",
        ),
    )
    monkeypatch.setattr(
        dependencies,
        "load_user_profile",
        lambda **_identity: dict(APP_PROFILE),
    )
    token, payload = app_security.create_session_token(
        user_id=APP_PROFILE["user_id"],
        username=APP_PROFILE["username"],
        settings=app_server.app.state.process_settings,
    )
    client = TestClient(app_server.app)
    client.cookies.set(app_security.SESSION_COOKIE_NAME, token)
    client.headers["X-CSRF-Token"] = payload.csrf
    try:
        yield client
    finally:
        client.close()
        app_server.app.dependency_overrides.clear()


@pytest.fixture
def rag_client(monkeypatch):
    from mech_chatbot.auth import core

    executor = ThreadPoolExecutor(max_workers=2)
    monkeypatch.setattr(rag_server, "RAG_REQUIRE_SERVICE_AUTH", True)
    monkeypatch.setattr(rag_server, "RAG_SERVICE_TOKEN", RAG_HEADERS["X-RAG-Service-Token"])
    # Temporary runtime harness seams are unavoidable here: entering the real
    # lifespan would load production RAG models instead of testing HTTP/SSE.
    monkeypatch.setattr(rag_server, "_rag_ready", True)
    monkeypatch.setattr(rag_server, "_rag_executor", executor)
    monkeypatch.setattr(rag_server, "_rag_semaphore", rag_server.asyncio.Semaphore(2))
    monkeypatch.setattr(
        core,
        "load_user_profile",
        lambda user_id=None, username=None: {
            **RAG_PROFILE,
            "user_id": user_id or RAG_PROFILE["user_id"],
            "username": username or RAG_PROFILE["username"],
        },
    )
    client = TestClient(rag_server.app)
    try:
        yield client
    finally:
        client.close()
        executor.shutdown(wait=True)


class _MappedRow:
    _mapping = {"user_id": 1, "username": "mapped"}


class _BoundaryExecutor:
    events = ()

    def run(self, _request, _invocation, cancellation):
        del cancellation
        yield from self.events


def _install_rag_events(monkeypatch, events):
    boundary = type("ConfiguredBoundaryExecutor", (_BoundaryExecutor,), {"events": tuple(events)})
    monkeypatch.setattr(
        rag_server.app.state,
        "rag_runtime",
        SimpleNamespace(executor=boundary()),
        raising=False,
    )


def _stream_text(client, *, headers=None, payload=None):
    with client.stream(
        "POST",
        "/chat/stream",
        headers=headers or RAG_HEADERS,
        json=payload or {"username": "wave6-viewer", "user_question": "How?"},
    ) as response:
        return response.status_code, "".join(response.iter_text())


def test_browser_chat_serializes_only_attributed_sources_from_turn_runner(
    app_client, monkeypatch
):
    citation = {
        "doc_id": 42,
        "page_no": 3,
        "file_name": "bom.pdf",
        "source_id": "D42P3",
    }

    class ScriptedRunner:
        def stream(self, _command, _actor):
            yield ChatDelta("See SourceID: D42P3")
            yield ChatCitation(citation)
            yield ChatDone(
                chat_id=51,
                ref_text="legacy",
                citations=(citation,),
                new_part_ids=(),
                conversation_context=None,
                elapsed_ms=1,
            )

    monkeypatch.setattr(
        app_server.app.state,
        "runtime",
        replace(app_server.app.state.runtime, chat_turn_runner=ScriptedRunner()),
    )

    response = app_client.post(
        "/api/chat/message",
        json={"session_id": "wave6", "question": "Show the BOM"},
    )

    assert response.status_code == 200
    assert "event: done" in response.text
    assert "event: error" not in response.text
    assert "D42P3" in response.text
    assert '"doc_id": 42' in response.text
    assert '"chat_id": 51' in response.text


def test_upload_contracts_reject_wrong_metadata_shape_and_report_missing_batch_department(
    app_client, monkeypatch, tmp_path
):
    created = []
    del tmp_path

    class Storage:
        def store(self, *, file_name, content, owner_department):
            del content, owner_department
            return StoredUpload(original_name=file_name, stored_path=f"staged/{file_name}")

        def delete(self, _stored_path):
            return True

    class Jobs:
        def create_job(self, **record):
            created.append(record)
            return 71

    monkeypatch.setattr(
        app_server.app.state,
        "runtime",
        replace(
            app_server.app.state.runtime,
            document_upload=DocumentUpload(storage=Storage(), job_store=Jobs()),
        ),
    )

    wrong_shape = app_client.post(
        "/api/documents/upload",
        files={"file": ("manual.pdf", b"pdf", "application/pdf")},
        data={"thu_muc": "CoKhi", "meta_json": "[]"},
    )
    accepted_csv = app_client.post(
        "/api/documents/upload",
        files={"file": ("manual.pdf", b"pdf", "application/pdf")},
        data={
            "thu_muc": "CoKhi",
            "meta_json": "{}",
            "extra_departments_json": "QA, Shared",
        },
    )
    missing_department = app_client.post(
        "/api/documents/upload-batch",
        files=[("files", ("manual.pdf", b"pdf", "application/pdf"))],
    )

    assert wrong_shape.status_code == 400
    assert accepted_csv.status_code == 200
    assert created[0]["owner_department"] == "CoKhi"
    assert created[0]["shared_departments"] == ("CoKhi", "QA", "Shared")
    assert missing_department.status_code == 200
    assert missing_department.json()["errors"][0]["error"] == "Thiếu phòng ban"


def test_bulk_review_reports_missing_identifiers_and_executes_scoped_delete_paths(
    app_client, monkeypatch
):
    calls = []

    class ReviewStore:
        def reject_job(self, job_id, reason):
            calls.append(("reject-job", job_id, reason))
            return True

        def mark_job_rejected(self, _job_id):
            raise AssertionError("successful reject must not use fallback")

        def reject_document(self, doc_id, reviewer):
            del reviewer
            calls.append(("reject-doc", doc_id))

        def delete_document(self, doc_id, reviewer):
            del reviewer
            calls.append(("delete-doc", doc_id))

        def delete_job(self, job_id):
            calls.append(("delete-job", job_id))

    class Publication:
        def resolve_latest_doc_id(self, _job_id):
            return 42

        def publish_document(self, _command, _actor):
            raise AssertionError("missing identifiers must fail before publication")

        def mark_job_published(self, _job_id):
            raise AssertionError("missing identifiers must fail before publication")

    review_documents = ReviewDocuments(
        review_store=ReviewStore(),
        publication=PublicationCoordinator(publication=Publication()),
    )
    monkeypatch.setattr(
        app_server.app.state,
        "runtime",
        replace(app_server.app.state.runtime, review_documents=review_documents),
    )

    missing_publish = app_client.post(
        "/api/documents/review/bulk",
        json={"action": "publish", "items": [{"doc_id": 1}]},
    )
    rejected = app_client.post(
        "/api/documents/review/bulk",
        json={
            "action": "reject",
            "reason": "invalid",
            "items": [{"doc_id": 4}, {"doc_id": 5, "job_id": 6}],
        },
    )
    deleted = app_client.post(
        "/api/documents/review/bulk",
        json={
            "action": "delete",
            "items": [{"doc_id": 7}, {"job_id": 8}, {}],
        },
    )

    assert missing_publish.json()["failed"] == 1
    assert rejected.json() == {
        "ok": False,
        "updated": 1,
        "pending": 0,
        "failed": 1,
        "failures": [],
    }
    assert deleted.json()["updated"] == 3
    assert calls == [
        ("reject-job", 6, "invalid"),
        ("reject-doc", 5),
        ("delete-doc", 7),
        ("delete-job", 8),
    ]


def test_platform_metadata_reads_and_updates_fail_closed_on_missing_records(
    app_client, monkeypatch
):
    monkeypatch.setattr(operation_routes.knowledge_governance_service, "get_department_knowledge_governance", lambda _code: None)
    monkeypatch.setattr(operation_routes.knowledge_governance_service, "get_department_domain_profile", lambda _code: None)
    monkeypatch.setattr(document_routes, "update_document_governance_metadata", lambda *_args, **_kwargs: False)
    monkeypatch.setattr(document_routes, "update_document_common_metadata", lambda *_args, **_kwargs: False)

    governance_read = app_client.get(
        "/api/catalog/departments/CoKhi/knowledge-governance"
    )
    domain_read = app_client.get("/api/catalog/departments/CoKhi/domain-profile")
    governance_update = app_client.patch(
        "/api/documents/9/governance", json={"taxonomy_version": "v2"}
    )
    site_update = app_client.patch(
        "/api/documents/9/site", json={"site": "HN"}
    )

    assert [
        governance_read.status_code,
        domain_read.status_code,
        governance_update.status_code,
        site_update.status_code,
    ] == [404, 404, 404, 404]


def test_user_creation_and_password_contracts_preserve_repository_outcomes(
    app_client, monkeypatch
):
    creation_results = iter([False, {"user_id": 8}])
    site_calls = []
    clearance_calls = []
    monkeypatch.setattr(
        operation_routes.ui_query_service,
        "create_user_with_roles",
        lambda **_record: next(creation_results),
    )
    monkeypatch.setattr(
        operation_routes.org_service,
        "set_user_sites",
        lambda *args: site_calls.append(args) or True,
    )
    monkeypatch.setattr(
        operation_routes.access_service,
        "set_user_clearance",
        lambda *args: clearance_calls.append(args) or True,
    )
    monkeypatch.setattr(operation_routes.ui_query_service, "update_user_password", lambda *_args: False)

    rejected = app_client.post(
        "/api/users", json={"username": "first", "password": "long-enough"}
    )
    invalid_id = app_client.post(
        "/api/users",
        json={
            "username": "second",
            "password": "long-enough",
            "sites": ["HQ"],
            "max_level": "internal",
        },
    )
    short_password = app_client.patch(
        "/api/users/8/password", json={"password": "short"}
    )
    storage_rejected = app_client.patch(
        "/api/users/8/password", json={"password": "long-enough"}
    )

    assert rejected.json() == {"ok": False, "result": False}
    assert invalid_id.json() == {"ok": True, "result": {"user_id": 8}}
    assert site_calls == [(8, ["HQ"])]
    assert clearance_calls == [(8, "internal")]
    assert short_password.status_code == 400
    assert storage_rejected.json() == {"ok": False}


def test_row_glossary_lifecycle_and_feedback_branches_remain_json_safe(
    app_client, monkeypatch
):
    glossary_results = iter([True, {"ok": True, "term": "BOM"}])
    lifecycle_buckets = []
    monkeypatch.setattr(
        operation_routes.ui_query_service,
        "list_users_basic",
        lambda: [_MappedRow(), {"username": "dict"}, (3, "tuple"), "scalar"],
    )
    monkeypatch.setattr(
        operation_routes.glossary_service,
        "upsert_glossary_term",
        lambda **_record: next(glossary_results),
    )
    monkeypatch.setattr(
        document_routes,
        "list_documents",
        lambda **scope: lifecycle_buckets.append(scope["bucket"]) or [],
    )
    monkeypatch.setattr(operation_routes.ui_query_service, "classify_feedback_and_get_source", lambda *_args, **_kwargs: {"ok": True})

    class Support:
        def feedback_review_context(self, _feedback_id):
            return None

    monkeypatch.setattr(
        app_server.app.state,
        "runtime",
        replace(app_server.app.state.runtime, app_support_queries=Support()),
    )

    users = app_client.get("/api/users")
    primitive = app_client.post("/api/glossary", json={"term": "BOM"})
    mapping = app_client.post("/api/glossary", json={"term": "BOM"})
    lifecycle = app_client.get("/api/lifecycle")
    feedback = app_client.post(
        "/api/feedback/4/classify", json={"correct_answer": "verified"}
    )

    assert users.json()["users"] == [
        {"user_id": 1, "username": "mapped"},
        {"username": "dict"},
        [3, "tuple"],
        "scalar",
    ]
    assert primitive.json() == {"ok": True}
    assert mapping.json() == {"ok": True, "term": "BOM"}
    assert lifecycle.status_code == 200
    assert lifecycle_buckets == ["expired", "expiring_soon", "needs_review"]
    assert feedback.json()["golden_hash"] is None


def test_rag_service_auth_can_be_explicitly_disabled(rag_client, monkeypatch):
    from mech_chatbot import services

    monkeypatch.setattr(rag_server, "RAG_REQUIRE_SERVICE_AUTH", False)
    monkeypatch.setattr(services, "get_all_sessions", lambda **_scope: [])

    response = rag_client.post(
        "/chat/sessions", json={"username": "wave6-viewer"}
    )

    assert response.status_code == 200
    assert response.json() == {"sessions": []}


def test_rag_stream_covers_repeated_tokens_citations_and_completion_without_tokens(
    rag_client, monkeypatch
):
    monkeypatch.setattr(rag_server, "pop_trace_stage_metrics", lambda _trace: {})
    _install_rag_events(
        monkeypatch,
        [
            RagPrepared("", (), (), {}),
            RagToken("first"),
            RagToken("second"),
            RagCitation({"doc_id": 3, "page_no": 2}),
            RagCompleted("answered", "trace", {}),
        ],
    )
    status, with_tokens = _stream_text(rag_client)

    _install_rag_events(
        monkeypatch,
        [RagPrepared("", (), (), {}), RagCompleted("answered", "trace", {})],
    )
    no_token_status, without_tokens = _stream_text(rag_client)

    assert status == no_token_status == 200
    assert with_tokens.count("event: token") == 2
    assert "event: citation" in with_tokens
    assert "event: done" in with_tokens
    assert "event: token" not in without_tokens
    assert "event: done" in without_tokens


@pytest.mark.parametrize(
    "terminal",
    [
        RagCancelled("cancelled"),
        RagCancelled("cancelled", cause=RuntimeError("provider stopped")),
        RagFailed(
            "provider_error",
            "provider stopped",
            False,
            RuntimeError("provider stopped"),
        ),
        None,
    ],
)
def test_rag_stream_terminal_failures_never_emit_a_done_event(
    rag_client, monkeypatch, terminal
):
    events = [RagPrepared("", (), (), {})]
    if terminal is not None:
        events.append(terminal)
    _install_rag_events(monkeypatch, events)

    status, transcript = _stream_text(rag_client)

    assert status == 200
    assert "event: accepted" in transcript
    assert "event: done" not in transcript
    if isinstance(terminal, (RagFailed,)) or (
        isinstance(terminal, RagCancelled) and terminal.cause is not None
    ) or terminal is None:
        assert "event: error" in transcript


def test_rag_history_save_reports_unsaved_and_unattributed_outcomes(
    rag_client, monkeypatch
):
    from mech_chatbot import services

    saved = {"evidence": [], "sources": [], "audits": []}
    chat_ids = iter([0, 81])
    monkeypatch.setattr(services, "save_chat_history", lambda **_record: next(chat_ids))
    monkeypatch.setattr(
        services,
        "save_answer_evidence",
        lambda chat_id, docs: saved["evidence"].append((chat_id, docs)),
    )
    monkeypatch.setattr(
        services,
        "save_answer_sources",
        lambda chat_id, docs: saved["sources"].append((chat_id, docs)),
    )
    monkeypatch.setattr(
        services,
        "write_audit_log",
        lambda **record: saved["audits"].append(record),
    )
    payload = {
        "username": "wave6-viewer",
        "session_id": "wave6",
        "user_msg": "How?",
        "bot_msg": "Answer without attribution",
        "retrieved_docs": [
            {
                "doc_id": 9,
                "trang": 1,
                "file_goc": "internal.pdf",
                "security_level": "internal",
            }
        ],
    }

    unsaved = rag_client.post("/chat/history/save", headers=RAG_HEADERS, json=payload)
    unattributed = rag_client.post(
        "/chat/history/save", headers=RAG_HEADERS, json=payload
    )

    assert unsaved.json() == {"ok": False, "chat_id": 0}
    assert unattributed.json() == {"ok": True, "chat_id": 81}
    assert saved["evidence"] == [(81, payload["retrieved_docs"])]
    assert saved["sources"] == []
    assert [record["action"] for record in saved["audits"]] == [
        "chat_query",
        "chat_query",
    ]


def test_admin_rag_audit_ignores_malformed_evidence_without_prompt_leakage(
    rag_client, monkeypatch
):
    from mech_chatbot import services
    from mech_chatbot.auth import core

    audits = []
    monkeypatch.setattr(
        core,
        "load_user_profile",
        lambda **_identity: {**RAG_PROFILE, "roles": ["admin"]},
    )
    monkeypatch.setattr(
        services,
        "write_audit_log",
        lambda *args, **kwargs: audits.append((args, kwargs)),
    )
    _install_rag_events(
        monkeypatch,
        [
            RagPrepared(
                "",
                (),
                (),
                {
                    "retrieved_docs": [
                        1,
                        {"doc_id": "invalid"},
                        {"doc_id": "7", "security_level": "secret"},
                    ]
                },
            ),
            RagToken("safe answer"),
            RagCompleted(
                "answered",
                "trace",
                {
                    "retrieved_docs": [
                        1,
                        {"doc_id": "invalid"},
                        {"doc_id": "7", "security_level": "secret"},
                    ]
                },
            ),
        ],
    )

    response = rag_client.post(
        "/chat",
        headers=RAG_HEADERS,
        json={"username": "wave6-admin", "user_question": "private prompt"},
    )

    assert response.status_code == 200
    assert audits
    details = audits[0][0][4]
    assert details["doc_ids"] == [7]
    assert details["security_levels"] == ["secret"]
    assert "private prompt" not in str(details)


def test_signed_replay_streams_skip_live_audit_on_cancel_and_failure(
    rag_client, monkeypatch
):
    from mech_chatbot import services
    from mech_chatbot.evaluation.crag_pilot import (
        PilotConfig,
        assign_pilot_route,
        build_replay_request,
    )

    config = PilotConfig(
        experiment_id="wave6-experiment",
        assignment_salt="wave6-assignment-salt",
        eligible_department="Technical",
        cohort_sha256="wave6-cohort",
        control_url="http://control.example",
        candidate_url="http://candidate.example",
        control_deployment_id="control-deployment",
        candidate_deployment_id="candidate-deployment",
        snapshot_fingerprint="wave6-snapshot",
    )
    route = assign_pilot_route(
        config,
        user_id="17",
        department="Technical",
        request_id="wave6-request",
        sites=["HQ"],
    )
    payload = {"username": "wave6-viewer", "user_question": "How?"}
    monkeypatch.setenv("CRAG_PILOT_ASSIGNMENT_SALT", config.assignment_salt)
    monkeypatch.setenv("RAG_DEPLOYMENT_ID", route.opposite_deployment_id)
    audits = []
    monkeypatch.setattr(
        services,
        "write_audit_log",
        lambda *args, **kwargs: audits.append((args, kwargs)),
    )

    _install_rag_events(
        monkeypatch, [RagPrepared("", (), (), {}), RagCancelled("cancelled")]
    )
    cancelled_replay = build_replay_request(
        route, payload, original_trace_id="original-cancelled"
    )
    cancelled_status, cancelled_transcript = _stream_text(
        rag_client, headers={**RAG_HEADERS, **cancelled_replay.headers}, payload=payload
    )

    _install_rag_events(
        monkeypatch,
        [
            RagPrepared("", (), (), {}),
            RagFailed(
                "provider_error",
                "provider stopped",
                False,
                RuntimeError("provider stopped"),
            ),
        ],
    )
    failed_replay = build_replay_request(
        route, payload, original_trace_id="original-failed"
    )
    failed_status, failed_transcript = _stream_text(
        rag_client, headers={**RAG_HEADERS, **failed_replay.headers}, payload=payload
    )

    assert cancelled_status == failed_status == 200
    assert "event: done" not in cancelled_transcript
    assert "event: error" in failed_transcript
    assert audits == []
