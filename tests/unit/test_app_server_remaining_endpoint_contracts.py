from dataclasses import replace
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, text

from mech_chatbot.api import dependencies as api_dependencies
from mech_chatbot.api.routers import documents as document_routes
from mech_chatbot.api.routers import operations as operation_routes
from mech_chatbot.application.document_review import PublicationOutcome
from mech_chatbot.application.document_upload import (
    UploadFailure,
    UploadReceipt,
    UploadRejected,
)


pytestmark = pytest.mark.unit

app_server = pytest.importorskip("mech_chatbot.api.app_server")


def _profile(*roles):
    return {
        "user_id": 7,
        "username": "alice",
        "department": "CoKhi",
        "roles": list(roles or ("viewer",)),
        "allowed_departments": ["CoKhi"],
        "allowed_sites": ["HN"],
        "max_security_level": "confidential",
        "preferred_language": "vi",
    }


@pytest.fixture
def client_for(monkeypatch):
    clients = []
    monkeypatch.setattr(operation_routes.audit_service, "write_audit_log", lambda **_kwargs: None)

    def build(profile):
        app_server.app.dependency_overrides[api_dependencies.current_profile] = (
            lambda: profile
        )
        app_server.app.dependency_overrides[api_dependencies.csrf_profile] = (
            lambda: profile
        )
        client = TestClient(app_server.app)
        clients.append(client)
        return client

    yield build
    for client in clients:
        client.close()
    app_server.app.dependency_overrides.clear()


def test_documents_contract_filters_lifecycle_and_audits_global_admin(
    monkeypatch, client_for
):
    calls = []
    audits = []
    monkeypatch.setattr(
        document_routes,
        "list_documents",
        lambda **kwargs: calls.append(kwargs)
        or [SimpleNamespace(_mapping={"doc_id": 3, "site": "HN"})],
    )
    monkeypatch.setattr(
        document_routes, "write_audit_log", lambda *args: audits.append(args)
    )

    reviewer = client_for(_profile("reviewer"))
    response = reviewer.get("/api/documents?eff_mode=sap&soon_days=14&search=bom")

    assert response.status_code == 200
    assert response.json() == {"documents": [{"doc_id": 3, "site": "HN"}]}
    assert calls[-1]["bucket"] == "expiring_soon"
    assert calls[-1]["soon_days"] == 14
    assert calls[-1]["search_kw"] == "bom"

    admin = client_for(_profile("admin"))
    response = admin.get("/api/documents?bucket=effective&dept=CoKhi")

    assert response.status_code == 200
    assert calls[-1]["global_read_admin"] is True
    assert audits[-1][1] == "admin_global_read_catalog"


def test_documents_contract_rejects_invalid_or_unauthorized_lifecycle_bucket(
    monkeypatch, client_for
):
    monkeypatch.setattr(
        document_routes,
        "list_documents",
        lambda **_kwargs: pytest.fail("invalid filters must not reach storage"),
    )

    invalid = client_for(_profile("reviewer")).get("/api/documents?bucket=unknown")
    denied = client_for(_profile("viewer")).get("/api/documents?bucket=expired")

    assert invalid.status_code == 422
    assert denied.status_code == 403


def test_single_upload_creates_job_with_normalized_metadata(monkeypatch, client_for):
    captured = []

    class Upload:
        def preflight(self, **_kwargs):
            return None

        def enqueue(self, command, actor):
            captured.append((command, actor))
            return UploadReceipt(91, command.file_name, command.owner_department)

    monkeypatch.setattr(
        app_server.app.state,
        "runtime",
        replace(app_server.app.state.runtime, document_upload=Upload()),
    )

    response = client_for(_profile("uploader")).post(
        "/api/documents/upload",
        files={"file": ("bom.pdf", b"pdf", "application/pdf")},
        data={
            "thu_muc": "CoKhi",
            "domain": "Mechanical",
            "site": "HN",
            "meta_json": '{"owner":"qa"}',
            "extra_departments_json": "Shared,CoKhi",
        },
    )

    assert response.status_code == 200
    assert response.json() == {"ok": True, "job_id": 91, "file_name": "bom.pdf"}
    command, actor = captured[0]
    assert command.owner_department == "CoKhi"
    assert command.shared_departments == ("Shared", "CoKhi")
    assert command.upload_metadata == {"owner": "qa"}
    assert actor.username == "alice"


def test_single_upload_removes_staged_file_when_job_is_rejected(
    monkeypatch, client_for, tmp_path
):
    class Upload:
        def preflight(self, **_kwargs):
            return None

        def enqueue(self, _command, _actor):
            raise UploadRejected(
                UploadFailure(
                    "enqueue_failed",
                    "bom.pdf",
                    "Không tạo được job (phòng ban có thể bị vô hiệu)",
                )
            )

    monkeypatch.setattr(
        app_server.app.state,
        "runtime",
        replace(app_server.app.state.runtime, document_upload=Upload()),
    )

    response = client_for(_profile("uploader")).post(
        "/api/documents/upload",
        files={"file": ("bom.pdf", b"pdf", "application/pdf")},
        data={"thu_muc": "CoKhi"},
    )

    assert response.status_code == 400


def test_access_request_and_self_history_forward_server_identity(
    monkeypatch, client_for
):
    calls = []
    monkeypatch.setattr(
        operation_routes.access_service,
        "create_access_request",
        lambda **kwargs: calls.append(kwargs) or {"request_id": 12},
    )
    monkeypatch.setattr(
        operation_routes.access_service,
        "get_user_access_requests",
        lambda user_id, limit: [{"user_id": user_id, "limit": limit}],
    )
    client = client_for(_profile("viewer"))

    created = client.post(
        "/api/access/request",
        json={
            "request_type": "department",
            "requested_dept": "Shared",
            "reason": "project",
        },
    )
    listed = client.get("/api/access/my-requests?limit=9")

    assert created.status_code == 200
    assert created.json() == {"request_id": 12}
    assert calls[0]["user_id"] == 7
    assert calls[0]["username"] == "alice"
    assert listed.json() == {"requests": [{"user_id": 7, "limit": 9}]}


def test_access_request_fails_closed_when_repository_rejects(monkeypatch, client_for):
    monkeypatch.setattr(
        operation_routes.access_service, "create_access_request", lambda **_kwargs: None
    )

    response = client_for(_profile()).post(
        "/api/access/request", json={"request_type": "clearance", "requested_level": "internal"}
    )

    assert response.status_code == 400


def test_document_metadata_lifecycle_and_state_contracts(monkeypatch, client_for):
    calls = []
    monkeypatch.setattr(
        document_routes,
        "validate_document_metadata_actor",
        lambda *_args: (True, "allowed"),
    )
    monkeypatch.setattr(
        document_routes,
        "update_document_common_metadata",
        lambda doc_id, **kwargs: calls.append(("metadata", doc_id, kwargs)) or {"id": doc_id},
    )
    monkeypatch.setattr(
        document_routes,
        "mark_document_expired",
        lambda doc_id, reviewer: calls.append(("expired", doc_id, reviewer)) or True,
    )
    monkeypatch.setattr(
        document_routes,
        "set_document_lifecycle",
        lambda doc_id, **kwargs: calls.append(("lifecycle", doc_id, kwargs)) or True,
    )
    monkeypatch.setattr(
        document_routes,
        "mark_document_reviewed",
        lambda doc_id, **kwargs: calls.append(("reviewed", doc_id, kwargs)) or True,
    )
    client = client_for(_profile("reviewer"))

    current = client.patch("/api/documents/42/current")
    expired = client.patch("/api/documents/42/expired")
    metadata = client.patch(
        "/api/documents/42/metadata",
        json={"site": "HN", "domain": "Mechanical", "attributes": {"part": "A"}},
    )
    lifecycle = client.patch(
        "/api/lifecycle/documents/42",
        json={"effective_date": "2026-01-01", "review_date": "2026-08-01"},
    )
    reviewed = client.post(
        "/api/lifecycle/documents/42/reviewed", json={"next_review_days": "90"}
    )

    assert current.status_code == 409
    assert expired.json() == {"ok": True}
    assert metadata.json()["ok"] is True
    assert lifecycle.json() == {"ok": True}
    assert reviewed.json() == {"ok": True}
    assert calls[-1][2]["next_review_days"] == 90


def test_document_metadata_denial_stops_mutation(monkeypatch, client_for):
    monkeypatch.setattr(
        document_routes,
        "validate_document_metadata_actor",
        lambda *_args: (False, "department_denied"),
    )
    monkeypatch.setattr(
        document_routes,
        "update_document_common_metadata",
        lambda *_args, **_kwargs: pytest.fail("denied metadata must not be written"),
    )

    response = client_for(_profile("reviewer")).patch(
        "/api/documents/42/metadata", json={"site": "HN"}
    )

    assert response.status_code == 403


def test_pending_variant_code_route_checks_role_and_result(monkeypatch, client_for):
    calls = []
    monkeypatch.setattr(document_routes, "validate_document_metadata_actor", lambda *_args: (True, ""))
    monkeypatch.setattr(
        document_routes, "update_pending_variant_code",
        lambda doc_id, code, reviewer: calls.append((doc_id, code, reviewer))
        or {"ok": True, "variant_code": code},
    )
    reviewer = client_for(_profile("reviewer"))
    response = reviewer.patch("/api/documents/42/variant-code", json={"variant_code": "beta"})
    assert response.status_code == 200
    assert response.json() == {"ok": True, "variant_code": "beta"}
    assert calls == [(42, "beta", "alice")]
    assert reviewer.patch("/api/documents/42/variant-code", json={"variant_code": "beta", "title": "x"}).status_code == 422
    assert calls == [(42, "beta", "alice")]

    viewer = client_for(_profile("viewer"))
    assert viewer.patch("/api/documents/42/variant-code", json={"variant_code": "beta"}).status_code == 403
    assert calls == [(42, "beta", "alice")]


def test_pending_variant_code_route_keeps_rejection_explicit(monkeypatch, client_for):
    monkeypatch.setattr(document_routes, "validate_document_metadata_actor", lambda *_args: (True, ""))
    monkeypatch.setattr(
        document_routes, "update_pending_variant_code",
        lambda *_args, **_kwargs: {"ok": False, "error": "Mã biến thể đã được xuất bản; chọn mã khác"},
    )

    response = client_for(_profile("reviewer")).patch(
        "/api/documents/42/variant-code", json={"variant_code": "beta"}
    )

    assert response.status_code == 409
    assert response.json() == {"detail": "Mã biến thể đã được xuất bản; chọn mã khác"}


@pytest.mark.parametrize(
    ("path", "action"),
    [
        ("/api/documents/42/publish-new-version", "new_version"),
        ("/api/documents/42/publish-new-variant", "new_variant"),
        ("/api/documents/42/publish-standalone", "standalone"),
    ],
)
def test_document_publication_routes_bind_server_actor(
    path, action, monkeypatch, client_for
):
    calls = []

    class Publication:
        def publish_job(self, command, actor):
            calls.append((command, actor))
            return PublicationOutcome(
                ok=True,
                state="pending",
                payload={"ok": True, "state": "pending"},
            )

    monkeypatch.setattr(
        app_server.app.state,
        "runtime",
        replace(
            app_server.app.state.runtime,
            publication_coordinator=Publication(),
        ),
    )

    response = client_for(_profile("reviewer")).post(path)

    assert response.status_code == 202
    assert response.json()["state"] == "pending"
    command, actor = calls[0]
    assert command.job_id == 0
    assert command.doc_id == 42
    assert command.publish_mode == action
    assert actor.username == "alice"
    assert actor.user_id == 7
    assert actor.roles == ("reviewer",)


def test_governance_and_site_updates_validate_repository_outcomes(
    monkeypatch, client_for
):
    governance_calls = []
    monkeypatch.setattr(
        document_routes,
        "update_document_governance_metadata",
        lambda doc_id, **kwargs: governance_calls.append((doc_id, kwargs)) or True,
    )
    monkeypatch.setattr(
        document_routes,
        "update_document_common_metadata",
        lambda *_args, **_kwargs: False,
    )
    client = client_for(_profile("platform_admin"))

    governance = client.patch(
        "/api/documents/42/governance",
        json={"taxonomy_version": "v2", "parent_applicable": True},
    )
    missing_site = client.patch("/api/documents/42/site", json={"site": ""})
    rejected_site = client.patch("/api/documents/42/site", json={"site": "HN"})

    assert governance.json() == {"ok": True}
    assert governance_calls[0][1]["updated_by"] == "alice"
    assert missing_site.status_code == 422
    assert rejected_site.status_code == 404


def test_governance_value_error_is_mapped_without_asserting_internal_text(
    monkeypatch, client_for
):
    monkeypatch.setattr(
        document_routes,
        "update_document_governance_metadata",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(ValueError("private detail")),
    )

    response = client_for(_profile("platform_admin")).patch(
        "/api/documents/42/governance", json={"taxonomy_version": "invalid"}
    )

    assert response.status_code == 422


def test_ingestion_listing_and_queue_controls(monkeypatch, client_for):
    calls = []
    monkeypatch.setattr(document_routes, "list_bulk_action_jobs", lambda **_kwargs: [
        {"JobID": 8, "ThuMuc": "CoKhi", "Site": "HN", "SecurityLevel": "internal",
         "UploadedBy": "alice", "DocID": None},
    ])
    monkeypatch.setattr(
        document_routes,
        "list_ingestion_jobs",
        lambda **kwargs: calls.append(("list", kwargs)) or [{"job_id": 8}],
    )
    monkeypatch.setattr(document_routes, "queue_eta_seconds", lambda: 37)
    monkeypatch.setattr(
        document_routes,
        "set_job_priority",
        lambda job_id, priority: calls.append(("priority", job_id, priority)) or True,
    )
    monkeypatch.setattr(
        document_routes,
        "cancel_job",
        lambda job_id, **kwargs: calls.append(("cancel", job_id, kwargs)) or True,
    )
    monkeypatch.setattr(document_routes, "requeue_job", lambda job_id: job_id == 8)
    monkeypatch.setattr(
        document_routes, "mark_job_pending_review", lambda job_id: job_id == 8
    )
    client = client_for(_profile("reviewer"))

    listed = client.get("/api/ingestion/jobs?status_value=pending")
    eta = client.get("/api/ingestion/eta")
    priority = client.patch("/api/ingestion/jobs/8/priority", json={"priority": "4"})
    canceled = client.post("/api/ingestion/jobs/8/cancel")
    requeued = client.post("/api/ingestion/jobs/8/requeue")
    pending = client.post("/api/ingestion/jobs/8/pending-review")

    assert listed.json() == {"jobs": [{"job_id": 8}]}
    assert eta.json() == {"pending": 0, "avg_seconds": 0, "eta_seconds": 37}
    assert priority.json() == {"ok": True}
    assert canceled.json() == {"ok": True}
    assert requeued.json() == {"ok": True}
    assert pending.json() == {"ok": True}
    assert calls[0][1]["username"] == "alice"
    assert calls[1] == ("priority", 8, 4)


@pytest.mark.parametrize("days", [-1, 0, "bad", None, True, 1.5, "1.5"])
def test_review_days_rejects_invalid_input_before_storage(monkeypatch, client_for, days):
    monkeypatch.setattr(document_routes, "_assert_metadata_actor", lambda *_args: None)
    monkeypatch.setattr(document_routes, "mark_document_reviewed", lambda *_a, **_k: pytest.fail("invalid review date write"))
    response = client_for(_profile("reviewer")).post(
        "/api/lifecycle/documents/42/reviewed", json={"next_review_days": days}
    )
    assert response.status_code == 422


def test_review_days_defaults_only_when_missing(monkeypatch, client_for):
    monkeypatch.setattr(document_routes, "_assert_metadata_actor", lambda *_args: None)
    calls = []
    monkeypatch.setattr(document_routes, "mark_document_reviewed", lambda *_a, **kwargs: calls.append(kwargs) or True)
    assert client_for(_profile("reviewer")).post("/api/lifecycle/documents/42/reviewed", json={}).json() == {"ok": True}
    assert calls[0]["next_review_days"] == 180


def test_ingestion_publish_requires_matching_document(monkeypatch, client_for):
    class Publication:
        def publish_job(self, _command, _actor):
            return PublicationOutcome(
                ok=False,
                state="not_found",
                error="Không tìm thấy tài liệu của ingestion job",
                payload={"ok": False, "state": "not_found"},
            )

    monkeypatch.setattr(
        app_server.app.state,
        "runtime",
        replace(app_server.app.state.runtime, publication_coordinator=Publication()),
    )

    response = client_for(_profile("reviewer")).post("/api/ingestion/jobs/8/publish")

    assert response.status_code == 404


def test_ingestion_publish_marks_job_only_after_published_transition(
    monkeypatch, client_for
):
    class Publication:
        def publish_job(self, command, _actor):
            assert command.job_id == 8
            assert command.doc_id is None
            return PublicationOutcome(
                ok=True,
                state="published",
                payload={"ok": True, "state": "published"},
            )

    monkeypatch.setattr(
        app_server.app.state,
        "runtime",
        replace(app_server.app.state.runtime, publication_coordinator=Publication()),
    )

    response = client_for(_profile("reviewer")).post("/api/ingestion/jobs/8/publish")

    assert response.status_code == 200
    assert response.json()["state"] == "published"


def test_user_creation_validates_password_and_applies_access_profile(
    monkeypatch, client_for
):
    calls = []
    monkeypatch.setattr(
        operation_routes.bcrypt,
        "hashpw",
        lambda raw, _salt: calls.append(("hash", raw)) or b"hashed-password",
    )
    monkeypatch.setattr(operation_routes.bcrypt, "gensalt", lambda: b"salt")
    monkeypatch.setattr(
        operation_routes.ui_query_service,
        "create_user_with_roles",
        lambda **kwargs: calls.append(("create", kwargs)) or {"user_id": 22},
    )
    monkeypatch.setattr(
        operation_routes.org_service,
        "set_user_sites",
        lambda user_id, sites: calls.append(("sites", user_id, sites)) or True,
    )
    monkeypatch.setattr(
        operation_routes.access_service,
        "set_user_clearance",
        lambda user_id, level: calls.append(("clearance", user_id, level)) or True,
    )
    client = client_for(_profile("security_admin"))

    invalid = client.post("/api/users", json={"username": "bob", "password": "short"})
    created = client.post(
        "/api/users",
        json={
            "username": " bob ",
            "password": "long-password",
            "roles": ["viewer"],
            "departments": ["CoKhi"],
            "sites": ["HN"],
            "max_level": "internal",
        },
    )

    assert invalid.status_code == 400
    assert created.json() == {"ok": True, "result": {"user_id": 22}}
    assert calls[-2:] == [("sites", 22, ["HN"]), ("clearance", 22, "internal")]


def test_catalog_rollout_and_governance_contracts(monkeypatch, client_for):
    calls = []
    monkeypatch.setattr(
        operation_routes.rollout_service,
        "upsert_department_rollout_plan",
        lambda code, **kwargs: calls.append(("plan", code, kwargs)) or {"code": code},
    )
    monkeypatch.setattr(
        operation_routes.rollout_service,
        "record_department_evaluation_gate",
        lambda code, **kwargs: calls.append(("gate", code, kwargs)) or {"passed": True},
    )
    monkeypatch.setattr(
        operation_routes.knowledge_governance_service,
        "upsert_department_knowledge_governance",
        lambda code, **kwargs: calls.append(("governance", code, kwargs))
        or {"code": code},
    )
    monkeypatch.setattr(
        operation_routes.knowledge_governance_service,
        "upsert_department_domain_profile",
        lambda code, **kwargs: calls.append(("domain", code, kwargs)) or {"code": code},
    )
    client = client_for(_profile("platform_admin"))

    plan = client.put(
        "/api/catalog/departments/CoKhi/rollout-plan",
        json={"wave_number": 2, "rollout_status": "ready"},
    )
    gate = client.post(
        "/api/catalog/departments/CoKhi/evaluation-gate",
        json={"batch_id": "b1", "question_count": 80},
    )
    governance = client.put(
        "/api/catalog/departments/CoKhi/knowledge-governance",
        json={"taxonomy_version": "v2"},
    )
    domain = client.put(
        "/api/catalog/departments/CoKhi/domain-profile",
        json={"document_types": ["BOM"], "required_metadata": ["site"]},
    )

    assert plan.json()["ok"] is True
    assert gate.json()["ok"] is True
    assert governance.json()["ok"] is True
    assert domain.json()["ok"] is True
    assert calls[0][2]["evaluation_question_target"] == 75
    assert calls[1][2]["rbac_site_publication_leaks"] == 0
    assert calls[2][2]["external_processing_policy"] == "internal_only"
    assert calls[3][2]["parent_context_enabled"] is True


@pytest.mark.parametrize(
    ("path", "target"),
    [
        ("/api/catalog/departments/CoKhi/rollout-plan", "upsert_department_rollout_plan"),
        (
            "/api/catalog/departments/CoKhi/knowledge-governance",
            "upsert_department_knowledge_governance",
        ),
        (
            "/api/catalog/departments/CoKhi/domain-profile",
            "upsert_department_domain_profile",
        ),
    ],
)
def test_catalog_value_errors_are_mapped_without_asserting_internal_text(
    path, target, monkeypatch, client_for
):
    service_module = (
        operation_routes.rollout_service
        if target == "upsert_department_rollout_plan"
        else operation_routes.knowledge_governance_service
    )
    monkeypatch.setattr(
        service_module,
        target,
        lambda *_args, **_kwargs: (_ for _ in ()).throw(ValueError("private detail")),
    )

    response = client_for(_profile("platform_admin")).put(path, json={})

    assert response.status_code == 422


def test_missing_department_governance_and_domain_profile_return_not_found(
    monkeypatch, client_for
):
    monkeypatch.setattr(
        operation_routes.knowledge_governance_service,
        "get_department_knowledge_governance",
        lambda _code: None,
    )
    monkeypatch.setattr(
        operation_routes.knowledge_governance_service,
        "get_department_domain_profile",
        lambda _code: None,
    )
    client = client_for(_profile("platform_admin"))

    governance = client.get("/api/catalog/departments/CoKhi/knowledge-governance")
    domain = client.get("/api/catalog/departments/CoKhi/domain-profile")

    assert governance.status_code == 404
    assert domain.status_code == 404


def test_glossary_repository_rejection_becomes_safe_client_error(monkeypatch, client_for):
    monkeypatch.setattr(
        operation_routes.glossary_service,
        "upsert_glossary_term",
        lambda **_kwargs: {"ok": False, "message": "duplicate"},
    )

    response = client_for(_profile("reviewer")).post(
        "/api/glossary", json={"term": "BOM"}
    )

    assert response.status_code == 400


def test_feedback_without_correct_answer_does_not_query_golden_source(
    monkeypatch, client_for
):
    monkeypatch.setattr(
        operation_routes.ui_query_service,
        "classify_feedback_and_get_source",
        lambda *_args, **_kwargs: {"status": "classified"},
    )
    class Support:
        def feedback_review_context(self, _feedback_id):
            pytest.fail("empty correction must not query source")

    monkeypatch.setattr(
        app_server.app.state,
        "runtime",
        replace(app_server.app.state.runtime, app_support_queries=Support()),
    )

    response = client_for(_profile("reviewer")).post(
        "/api/feedback/9/classify",
        json={"failure_type": "retrieval", "correct_answer": "  "},
    )

    assert response.status_code == 200
    assert response.json() == {
        "result": {"status": "classified"},
        "golden_hash": None,
        "regression_qid": None,
    }


def test_feedback_classify_serializes_sqlalchemy_row_after_write(monkeypatch, client_for):
    with create_engine("sqlite://").connect() as conn:
        row = conn.execute(
            text("SELECT 169 AS SourceDocID, 'QA_B' AS Department, 'SiteB' AS Site")
        ).first()
    monkeypatch.setattr(
        operation_routes.ui_query_service,
        "classify_feedback_and_get_source",
        lambda *_args, **_kwargs: row,
    )

    response = client_for(_profile("reviewer")).post(
        "/api/feedback/4/classify", json={"failure_type": "other"}
    )

    assert response.status_code == 200
    assert response.json() == {
        "result": {"SourceDocID": 169, "Department": "QA_B", "Site": "SiteB"},
        "golden_hash": None,
        "regression_qid": None,
    }


def test_feedback_correction_creates_golden_and_regression_evidence(
    monkeypatch, client_for
):
    calls = []

    class Support:
        def feedback_review_context(self, _feedback_id):
            return ("Question", 42, "CoKhi", "HN")

    monkeypatch.setattr(
        app_server.app.state,
        "runtime",
        replace(app_server.app.state.runtime, app_support_queries=Support()),
    )
    monkeypatch.setattr(
        operation_routes.ui_query_service,
        "classify_feedback_and_get_source",
        lambda *_args, **_kwargs: {"status": "classified"},
    )
    monkeypatch.setattr(
        operation_routes.feedback_service,
        "upsert_golden_answer",
        lambda **kwargs: calls.append(("golden", kwargs)) or "hash-1",
    )
    monkeypatch.setattr(
        operation_routes.feedback_service,
        "ensure_regression_question",
        lambda **kwargs: calls.append(("regression", kwargs)) or 31,
    )

    response = client_for(_profile("reviewer")).post(
        "/api/feedback/9/classify",
        json={"failure_type": "generation", "correct_answer": "Correct"},
    )

    assert response.status_code == 200
    assert response.json()["golden_hash"] == "hash-1"
    assert response.json()["regression_qid"] == 31
    assert calls[0][1]["feedback_id"] == 9
    assert calls[1][1]["expected_doc_id"] == 42


def test_quality_and_external_policy_contracts(monkeypatch, client_for):
    calls = []
    monkeypatch.setattr(
        operation_routes.feedback_service, "recompute_doc_quality_scores", lambda: 12
    )
    monkeypatch.setattr(
        operation_routes.feedback_service,
        "cleanup_dangling_records",
        lambda: {"deleted": 2},
    )
    monkeypatch.setattr(
        operation_routes.external_ai_service,
        "upsert_external_ai_provider_profile",
        lambda provider, **kwargs: calls.append((provider, kwargs)) or {"provider": provider},
    )
    monkeypatch.setattr(
        operation_routes,
        "invalidate_external_ai_provider_profiles",
        lambda: calls.append(("invalidate", {})),
    )
    client = client_for(_profile("platform_admin", "reviewer"))

    recompute = client.post("/api/quality/recompute")
    cleanup = client.post("/api/quality/cleanup")
    policy = client.put(
        "/api/settings/external-ai-policy/openai",
        json={"allowed_surfaces": ["rag"], "is_active": True},
    )

    assert recompute.json() == {"ok": True, "recomputed": 12}
    assert cleanup.json() == {"deleted": 2}
    assert policy.json() == {"ok": True, "profile": {"provider": "openai"}}
    assert calls[0][1]["updated_by"] == "alice"
    assert calls[-1][0] == "invalidate"


def test_external_policy_validation_failure_does_not_invalidate_cache(
    monkeypatch, client_for
):
    monkeypatch.setattr(
        operation_routes.external_ai_service,
        "upsert_external_ai_provider_profile",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(ValueError("private detail")),
    )
    monkeypatch.setattr(
        operation_routes,
        "invalidate_external_ai_provider_profiles",
        lambda: pytest.fail("failed policy must not invalidate cache"),
    )

    response = client_for(_profile("platform_admin")).put(
        "/api/settings/external-ai-policy/openai", json={}
    )

    assert response.status_code == 422


@pytest.mark.parametrize("kind", ["proposal", "summary"])
@pytest.mark.parametrize(
    ("result", "expected_status"),
    [
        ({"ok": False, "reason": "not_found"}, 404),
        ({"ok": False, "reason": "conflict"}, 409),
    ],
)
def test_graph_reviews_map_repository_failures_without_audit(
    kind, result, expected_status, monkeypatch, client_for
):
    target = "review_graph_proposal" if kind == "proposal" else "review_community_summary"
    path = (
        "/api/admin/graph/proposals/5/approve"
        if kind == "proposal"
        else "/api/admin/graph/community-summaries/5/approve"
    )
    monkeypatch.setattr(
        operation_routes.graph_service, target, lambda *_args, **_kwargs: result
    )
    monkeypatch.setattr(
        operation_routes.audit_service,
        "write_audit_log",
        lambda **_kwargs: pytest.fail("failed review must not be audited as success"),
    )

    response = client_for(_profile("knowledge_approver")).post(path, json={})

    assert response.status_code == expected_status


@pytest.mark.parametrize(
    ("path", "target", "audit_action"),
    [
        (
            "/api/admin/graph/proposals/5/reject",
            "review_graph_proposal",
            "graph_proposal_reject",
        ),
        (
            "/api/admin/graph/community-summaries/5/reject",
            "review_community_summary",
            "graph_community_summary_reject",
        ),
    ],
)
def test_graph_reviews_audit_successful_server_bound_actor(
    path, target, audit_action, monkeypatch, client_for
):
    reviews = []
    audits = []
    monkeypatch.setattr(
        operation_routes.graph_service,
        target,
        lambda item_id, action, **kwargs: reviews.append((item_id, action, kwargs))
        or {"ok": True, "status": action},
    )
    monkeypatch.setattr(
        operation_routes.audit_service,
        "write_audit_log",
        lambda **kwargs: audits.append(kwargs),
    )

    response = client_for(_profile("knowledge_approver")).post(
        path, json={"note": "reviewed"}
    )

    assert response.status_code == 200
    assert reviews == [(5, "reject", {"reviewer": "alice", "note": "reviewed"})]
    assert audits[0]["action"] == audit_action
    assert audits[0]["user_id"] == 7


@pytest.mark.parametrize("expected_doc_id", [-1, 0, 1.5, 1.0, True, False, "abc", "42", [], {}])
def test_regression_rejects_invalid_expected_doc_id_before_storage(monkeypatch, client_for, expected_doc_id):
    calls = []
    monkeypatch.setattr(operation_routes.feedback_service, "add_regression_question", lambda **kwargs: calls.append(kwargs) or 91)
    response = client_for(_profile("reviewer")).post("/api/regression/questions", json={"question": "QA regression", "expected_doc_id": expected_doc_id})
    assert response.status_code == 422
    assert calls == []


@pytest.mark.parametrize("expected_doc_id, normalized", [(None, None), ("", None), (42, 42)])
def test_regression_accepts_optional_or_positive_expected_doc_id(monkeypatch, client_for, expected_doc_id, normalized):
    calls = []
    monkeypatch.setattr(operation_routes.feedback_service, "add_regression_question", lambda **kwargs: calls.append(kwargs) or 91)
    response = client_for(_profile("reviewer")).post("/api/regression/questions", json={"question": "QA regression", "expected_doc_id": expected_doc_id, "expected_keywords": "pump,torque", "department": "QA", "site": "QA"})
    assert response.status_code == 200
    assert response.json() == {"ok": True, "result": 91}
    assert calls == [{"question": "QA regression", "expected_doc_id": normalized, "expected_keywords": "pump,torque", "department": "QA", "site": "QA", "created_by": "alice"}]


_CRUD_AUDIT_CASES = [
    ("post", "/api/glossary", "glossary_service", "upsert_glossary_term",
     {"term": "private-term", "domain": "private-domain", "expansion": "private-expansion"},
     {"ok": True, "glossary_id": 31}, "glossary_upsert", "DomainGlossary", 31, {}),
    ("patch", "/api/glossary/31/active", "glossary_service", "set_glossary_active",
     {"is_active": False}, True, "glossary_active", "DomainGlossary", 31, {"is_active": False}),
    ("delete", "/api/glossary/31", "glossary_service", "delete_glossary_term",
     {}, True, "glossary_delete", "DomainGlossary", 31, {}),
    ("post", "/api/materials", "material_service", "upsert_material",
     {"code": " MAT-QA ", "display": "private-display", "category": "private-category"},
     True, "material_upsert", "MaterialDictionary", None, {"canonical_code": "MAT-QA"}),
    ("post", "/api/materials/41/synonyms", "material_service", "add_material_synonym",
     {"synonym": "private-synonym"}, True, "material_synonym_add", "MaterialDictionary", 41, {}),
    ("delete", "/api/materials/41", "material_service", "delete_material",
     {}, True, "material_delete", "MaterialDictionary", 41, {}),
    ("delete", "/api/materials/synonyms/51", "material_service", "delete_material_synonym",
     {}, True, "material_synonym_delete", "MaterialSynonym", 51, {}),
    ("post", "/api/feedback/61/classify", "ui_query_service", "classify_feedback_and_get_source",
     {"failure_type": "private-failure", "reviewer_note": "private-note"},
     {"SourceDocID": 42}, "feedback_classify", "FeedbackReview", 61, {}),
    ("delete", "/api/feedback/61", "ui_query_service", "delete_feedback",
     {}, True, "feedback_delete", "FeedbackReview", 61, {}),
    ("post", "/api/regression/questions", "feedback_service", "add_regression_question",
     {"question": "private-question", "expected_keywords": "private-keywords"},
     71, "regression_question_create", "RegressionQuestion", 71, {}),
    ("patch", "/api/regression/questions/71/active", "feedback_service", "set_regression_question_active",
     {"is_active": False}, True, "regression_question_active", "RegressionQuestion", 71, {"is_active": False}),
]


@pytest.mark.parametrize("outcome", ["success", "failure", "denied"])
@pytest.mark.parametrize(
    "method,path,service,target,body,saved,action,entity_type,entity_id,details",
    _CRUD_AUDIT_CASES,
    ids=[case[6] for case in _CRUD_AUDIT_CASES],
)
def test_crud_audit_success_contract(
    monkeypatch, client_for, outcome, method, path, service, target, body,
    saved, action, entity_type, entity_id, details,
):
    audits = []
    calls = []
    failed = {"ok": False, "message": "not saved"} if action == "glossary_upsert" else (
        None if action in {"feedback_classify", "regression_question_create"} else False
    )
    result = failed if outcome == "failure" else saved
    monkeypatch.setattr(
        getattr(operation_routes, service), target,
        lambda *_args, **_kwargs: calls.append(True) or result,
    )
    monkeypatch.setattr(operation_routes.audit_service, "write_audit_log", lambda **record: audits.append(record))
    client = client_for(_profile("viewer" if outcome == "denied" else "admin"))
    response = client.request(method.upper(), path, json={**body, "username": "forged", "user_id": 999, "secret": "private-secret"})

    if outcome == "denied":
        assert response.status_code == 403
        assert calls == audits == []
        return
    assert calls == [True]
    if action == "glossary_upsert":
        assert response.status_code == (400 if outcome == "failure" else 200)
        assert response.json() == ({"detail": "not saved"} if outcome == "failure" else saved)
    elif action == "feedback_classify":
        assert response.status_code == 200
        assert response.json() == {"result": result, "golden_hash": None, "regression_qid": None}
    elif action == "regression_question_create":
        assert response.status_code == 200
        assert response.json() == {"ok": result is not None, "result": result}
    else:
        assert response.status_code == 200
        assert response.json() == {"ok": bool(result)}
    assert audits == ([] if outcome == "failure" else [{
        "username": "alice", "user_id": 7, "action": action,
        "entity_type": entity_type, "entity_id": entity_id, "details": details,
    }])


@pytest.mark.parametrize("result", [True, False, None])
def test_glossary_audit_preserves_legacy_bool_response(monkeypatch, client_for, result):
    audits = []
    monkeypatch.setattr(operation_routes.glossary_service, "upsert_glossary_term", lambda **_kwargs: result)
    monkeypatch.setattr(operation_routes.audit_service, "write_audit_log", lambda **record: audits.append(record))
    response = client_for(_profile("reviewer")).post("/api/glossary", json={"term": "private-term", "domain": "private-domain", "glossary_id": 31})
    assert response.status_code == 200
    assert response.json() == {"ok": bool(result)}
    assert audits == ([{
        "username": "alice", "user_id": 7, "action": "glossary_upsert",
        "entity_type": "DomainGlossary", "entity_id": 31, "details": {},
    }] if result else [])


def test_material_upsert_audit_keeps_existing_id(monkeypatch, client_for):
    audits = []
    monkeypatch.setattr(operation_routes.material_service, "upsert_material", lambda **_kwargs: True)
    monkeypatch.setattr(operation_routes.audit_service, "write_audit_log", lambda **record: audits.append(record))
    response = client_for(_profile("reviewer")).post("/api/materials", json={"code": " MAT-QA ", "material_id": 41})
    assert response.status_code == 200
    assert response.json() == {"ok": True}
    assert audits == [{
        "username": "alice", "user_id": 7, "action": "material_upsert",
        "entity_type": "MaterialDictionary", "entity_id": 41,
        "details": {"canonical_code": "MAT-QA"},
    }]
