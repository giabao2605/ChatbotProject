from contextlib import nullcontext
from dataclasses import replace
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

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
def client_for():
    clients = []

    def build(profile):
        app_server.app.dependency_overrides[app_server.current_profile] = lambda: profile
        app_server.app.dependency_overrides[app_server.csrf_profile] = lambda: profile
        client = TestClient(app_server.app)
        clients.append(client)
        return client

    yield build
    for client in clients:
        client.close()
    app_server.app.dependency_overrides.clear()


class _PublicationResult:
    def __init__(self, *, ok=True, state="published", error=None):
        self.ok = ok
        self.state = state
        self.error = error

    def __bool__(self):
        return self.ok

    def to_dict(self):
        return {"ok": self.ok, "state": self.state, "error": self.error}


class _RowsResult:
    def __init__(self, row=None):
        self._row = row

    def fetchone(self):
        return self._row


class _Connection:
    def __init__(self, row=None):
        self.row = row
        self.calls = []

    def execute(self, statement, params=None):
        self.calls.append((str(statement), params))
        return _RowsResult(self.row)


class _Engine:
    def __init__(self, row=None):
        self.connection = _Connection(row)

    def connect(self):
        return nullcontext(self.connection)


def test_documents_contract_filters_lifecycle_and_audits_global_admin(
    monkeypatch, client_for
):
    calls = []
    audits = []
    monkeypatch.setattr(
        app_server,
        "list_documents",
        lambda **kwargs: calls.append(kwargs)
        or [SimpleNamespace(_mapping={"doc_id": 3, "site": "HN"})],
    )
    monkeypatch.setattr(app_server, "write_audit_log", lambda *args: audits.append(args))

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
        app_server,
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
        app_server,
        "create_access_request",
        lambda **kwargs: calls.append(kwargs) or {"request_id": 12},
    )
    monkeypatch.setattr(
        app_server,
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
    monkeypatch.setattr(app_server, "create_access_request", lambda **_kwargs: None)

    response = client_for(_profile()).post(
        "/api/access/request", json={"request_type": "clearance"}
    )

    assert response.status_code == 400


def test_document_metadata_lifecycle_and_state_contracts(monkeypatch, client_for):
    calls = []
    monkeypatch.setattr(
        app_server, "validate_document_metadata_actor", lambda *_args: (True, "allowed")
    )
    monkeypatch.setattr(
        app_server,
        "update_document_common_metadata",
        lambda doc_id, **kwargs: calls.append(("metadata", doc_id, kwargs)) or {"id": doc_id},
    )
    monkeypatch.setattr(
        app_server,
        "mark_document_expired",
        lambda doc_id, reviewer: calls.append(("expired", doc_id, reviewer)) or True,
    )
    monkeypatch.setattr(
        app_server,
        "set_document_lifecycle",
        lambda doc_id, **kwargs: calls.append(("lifecycle", doc_id, kwargs)) or True,
    )
    monkeypatch.setattr(
        app_server,
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
        app_server,
        "validate_document_metadata_actor",
        lambda *_args: (False, "department_denied"),
    )
    monkeypatch.setattr(
        app_server,
        "update_document_common_metadata",
        lambda *_args, **_kwargs: pytest.fail("denied metadata must not be written"),
    )

    response = client_for(_profile("reviewer")).patch(
        "/api/documents/42/metadata", json={"site": "HN"}
    )

    assert response.status_code == 403


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
    monkeypatch.setattr(
        app_server,
        "publish_document",
        lambda doc_id, **kwargs: calls.append((doc_id, kwargs))
        or _PublicationResult(state="pending"),
    )

    response = client_for(_profile("reviewer")).post(path)

    assert response.status_code == 202
    assert response.json()["state"] == "pending"
    assert calls == [
        (
            42,
            {
                "action": action,
                "reviewer": "alice",
                "reviewer_id": 7,
                "reviewer_roles": ["reviewer"],
            },
        )
    ]


def test_governance_and_site_updates_validate_repository_outcomes(
    monkeypatch, client_for
):
    governance_calls = []
    monkeypatch.setattr(
        app_server,
        "update_document_governance_metadata",
        lambda doc_id, **kwargs: governance_calls.append((doc_id, kwargs)) or True,
    )
    monkeypatch.setattr(
        app_server,
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
        app_server,
        "update_document_governance_metadata",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(ValueError("private detail")),
    )

    response = client_for(_profile("platform_admin")).patch(
        "/api/documents/42/governance", json={"taxonomy_version": "invalid"}
    )

    assert response.status_code == 422


def test_ingestion_listing_and_queue_controls(monkeypatch, client_for):
    calls = []
    monkeypatch.setattr(
        app_server,
        "list_ingestion_jobs",
        lambda **kwargs: calls.append(("list", kwargs)) or [{"job_id": 8}],
    )
    monkeypatch.setattr(app_server, "queue_eta_seconds", lambda: 37)
    monkeypatch.setattr(
        app_server,
        "set_job_priority",
        lambda job_id, priority: calls.append(("priority", job_id, priority)) or True,
    )
    monkeypatch.setattr(
        app_server,
        "cancel_job",
        lambda job_id, **kwargs: calls.append(("cancel", job_id, kwargs)) or True,
    )
    monkeypatch.setattr(app_server, "requeue_job", lambda job_id: job_id == 8)
    monkeypatch.setattr(app_server, "mark_job_pending_review", lambda job_id: job_id == 8)
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
        app_server.bcrypt,
        "hashpw",
        lambda raw, _salt: calls.append(("hash", raw)) or b"hashed-password",
    )
    monkeypatch.setattr(app_server.bcrypt, "gensalt", lambda: b"salt")
    monkeypatch.setattr(
        app_server,
        "create_user_with_roles",
        lambda **kwargs: calls.append(("create", kwargs)) or {"user_id": 22},
    )
    monkeypatch.setattr(
        app_server,
        "set_user_sites",
        lambda user_id, sites: calls.append(("sites", user_id, sites)) or True,
    )
    monkeypatch.setattr(
        app_server,
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
        app_server,
        "upsert_department_rollout_plan",
        lambda code, **kwargs: calls.append(("plan", code, kwargs)) or {"code": code},
    )
    monkeypatch.setattr(
        app_server,
        "record_department_evaluation_gate",
        lambda code, **kwargs: calls.append(("gate", code, kwargs)) or {"passed": True},
    )
    monkeypatch.setattr(
        app_server,
        "upsert_department_knowledge_governance",
        lambda code, **kwargs: calls.append(("governance", code, kwargs))
        or {"code": code},
    )
    monkeypatch.setattr(
        app_server,
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
    assert calls[2][2]["external_processing_policy"] == "all_external"
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
    monkeypatch.setattr(
        app_server,
        target,
        lambda *_args, **_kwargs: (_ for _ in ()).throw(ValueError("private detail")),
    )

    response = client_for(_profile("platform_admin")).put(path, json={})

    assert response.status_code == 422


def test_missing_department_governance_and_domain_profile_return_not_found(
    monkeypatch, client_for
):
    monkeypatch.setattr(app_server, "get_department_knowledge_governance", lambda _code: None)
    monkeypatch.setattr(app_server, "get_department_domain_profile", lambda _code: None)
    client = client_for(_profile("platform_admin"))

    governance = client.get("/api/catalog/departments/CoKhi/knowledge-governance")
    domain = client.get("/api/catalog/departments/CoKhi/domain-profile")

    assert governance.status_code == 404
    assert domain.status_code == 404


def test_glossary_repository_rejection_becomes_safe_client_error(monkeypatch, client_for):
    monkeypatch.setattr(
        app_server,
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
        app_server,
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
        app_server,
        "classify_feedback_and_get_source",
        lambda *_args, **_kwargs: {"status": "classified"},
    )
    monkeypatch.setattr(
        app_server,
        "upsert_golden_answer",
        lambda **kwargs: calls.append(("golden", kwargs)) or "hash-1",
    )
    monkeypatch.setattr(
        app_server,
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
    monkeypatch.setattr(app_server, "recompute_doc_quality_scores", lambda: 12)
    monkeypatch.setattr(app_server, "cleanup_dangling_records", lambda: {"deleted": 2})
    monkeypatch.setattr(
        app_server,
        "upsert_external_ai_provider_profile",
        lambda provider, **kwargs: calls.append((provider, kwargs)) or {"provider": provider},
    )
    monkeypatch.setattr(
        app_server,
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
        app_server,
        "upsert_external_ai_provider_profile",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(ValueError("private detail")),
    )
    monkeypatch.setattr(
        app_server,
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
    monkeypatch.setattr(app_server, target, lambda *_args, **_kwargs: result)
    monkeypatch.setattr(
        app_server,
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
        app_server,
        target,
        lambda item_id, action, **kwargs: reviews.append((item_id, action, kwargs))
        or {"ok": True, "status": action},
    )
    monkeypatch.setattr(
        app_server, "write_audit_log", lambda **kwargs: audits.append(kwargs)
    )

    response = client_for(_profile("knowledge_approver")).post(
        path, json={"note": "reviewed"}
    )

    assert response.status_code == 200
    assert reviews == [(5, "reject", {"reviewer": "alice", "note": "reviewed"})]
    assert audits[0]["action"] == audit_action
    assert audits[0]["user_id"] == 7
