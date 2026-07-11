import os
import uuid

import pytest
from sqlalchemy import text

from mech_chatbot.db.engine import engine
from mech_chatbot.db.repositories import publication


pytestmark = [pytest.mark.integration]


if os.getenv("RUN_DB_TESTS") != "1":
    pytest.skip("Set RUN_DB_TESTS=1 to run SQL integration tests", allow_module_level=True)


@pytest.fixture
def draft_doc(monkeypatch):
    marker = uuid.uuid4().hex
    department = f"publish-test-{marker[:16]}"
    username = f"publish-test-{marker[:16]}"
    with engine.begin() as conn:
        conn.execute(
            text(
                """
                INSERT INTO dbo.Departments (DeptCode, DeptName, Domain, IsActive, Status, Site)
                VALUES (:department, :department, 'generic', 1, 'active', 'TEST')
                """
            ),
            {"department": department},
        )
        user_row = conn.execute(
            text(
                """
                INSERT INTO dbo.Users (Username, PasswordHash, DisplayName, Department, IsActive)
                OUTPUT INSERTED.UserID
                VALUES (:username, 'integration-test-only', :username, :department, 1)
                """
            ),
            {"username": username, "department": department},
        ).fetchone()
        user_id = int(user_row[0])
        role_id = conn.execute(
            text("SELECT RoleID FROM dbo.Roles WHERE RoleName = 'knowledge_approver'")
        ).scalar_one()
        conn.execute(
            text("INSERT INTO dbo.UserDepartments (UserID, Department) VALUES (:user_id, :department)"),
            {"user_id": user_id, "department": department},
        )
        conn.execute(
            text("INSERT INTO dbo.UserRoles (UserID, RoleID) VALUES (:user_id, :role_id)"),
            {"user_id": user_id, "role_id": role_id},
        )
        conn.execute(
            text(
                """
                INSERT INTO dbo.DepartmentKnowledgeGovernance
                    (DeptCode, KnowledgeOwnerUserID, KnowledgeApproverUserID,
                     TaxonomyVersion, ExternalProcessingPolicy, IsActive, UpdatedBy)
                VALUES
                    (:department, :user_id, :user_id, 'v1', 'all_external', 1, 'integration-test')
                """
            ),
            {"department": department, "user_id": user_id},
        )
        conn.execute(
            text(
                """
                INSERT INTO dbo.DepartmentDomainProfile
                    (DeptCode, DocumentTypesJson, RequiredMetadataJson, RouterPatternsJson,
                     ParentContextEnabled, IsActive, UpdatedBy)
                VALUES
                    (:department, '["policy"]', '[]', '[]', 1, 1, 'integration-test')
                """
            ),
            {"department": department},
        )
        row = conn.execute(
            text(
                """
                INSERT INTO dbo.TaiLieu
                    (TenFile, ThuMuc, TrangThaiVector, TrangThai, ReviewStatus,
                     LifecycleStatus, IsCurrent, IsArchived, VersionNo, VariantCode,
                     Site, Domain, SecurityLevel, OwnerDepartment, SourceSystem,
                     ExternalProcessingPolicy, ClassificationRationale,
                     ClassificationModel, ClassificationJson, PublicationState,
                     PublicationVersion, PublicationRetryCount, PublicationUpdatedAt,
                     Servable, KnowledgeOwnerUserID, KnowledgeApproverUserID,
                     TaxonomyVersion)
                OUTPUT INSERTED.DocID
                VALUES
                    (:file_name, :department, 1, 'pending_review', 'pending_review',
                     'draft', 0, 0, 1, 'default', 'TEST', 'generic',
                     'internal', :department, 'integration_test', 'all_external',
                     'integration_test', 'test-model',
                     '{"document_type":"policy"}', 'draft', 1, 0, GETDATE(), 0,
                     :user_id, :user_id, 'v1')
                """
            ),
            {
                "file_name": f"publication-integration-{marker}.pdf",
                "department": department,
                "user_id": user_id,
            },
        ).fetchone()
        doc_id = int(row[0])
        conn.execute(
            text(
                "INSERT INTO dbo.PhongBanChiaSe (DocID, DeptCode) VALUES (:doc_id, :department)"
            ),
            {"doc_id": doc_id, "department": department},
        )
        conn.execute(
            text(
                "INSERT INTO dbo.TaiLieuKyThuat (DocID, TrangSo, LoaiTaiLieu) VALUES (:doc_id, 1, 'Policy')"
            ),
            {"doc_id": doc_id},
        )

    monkeypatch.setattr(publication._r_audit, "write_audit_log", lambda *a, **k: None)
    monkeypatch.setattr(
        publication._r_semantic_cache,
        "_invalidate_semantic_cache",
        lambda *a, **k: None,
    )
    yield {
        "doc_id": doc_id,
        "reviewer_id": user_id,
        "reviewer_roles": ["knowledge_approver"],
    }

    with engine.begin() as conn:
        conn.execute(text("DELETE FROM dbo.TaiLieu WHERE DocID = :doc_id"), {"doc_id": doc_id})
        conn.execute(
            text("DELETE FROM dbo.DepartmentKnowledgeGovernance WHERE DeptCode = :department"),
            {"department": department},
        )
        conn.execute(
            text("DELETE FROM dbo.DepartmentDomainProfile WHERE DeptCode = :department"),
            {"department": department},
        )
        conn.execute(text("DELETE FROM dbo.UserRoles WHERE UserID = :user_id"), {"user_id": user_id})
        conn.execute(text("DELETE FROM dbo.UserDepartments WHERE UserID = :user_id"), {"user_id": user_id})
        conn.execute(text("DELETE FROM dbo.Users WHERE UserID = :user_id"), {"user_id": user_id})
        conn.execute(text("DELETE FROM dbo.Departments WHERE DeptCode = :department"), {"department": department})


def _state(doc_id):
    with engine.connect() as conn:
        doc = conn.execute(
            text(
                """
                SELECT LifecycleStatus, ReviewStatus, IsCurrent, Servable,
                       PublicationState, PublicationRetryCount
                FROM dbo.TaiLieu WHERE DocID = :doc_id
                """
            ),
            {"doc_id": doc_id},
        ).fetchone()
        outbox = conn.execute(
            text(
                "SELECT TOP 1 Status, AttemptCount FROM dbo.PublicationOutbox WHERE DocID = :doc_id ORDER BY OutboxID DESC"
            ),
            {"doc_id": doc_id},
        ).fetchone()
    return doc, outbox


def test_publication_success_reaches_published_and_servable(draft_doc, monkeypatch):
    doc_id = draft_doc["doc_id"]
    calls = []
    batch_calls = []

    def qdrant_ok(doc_id, metadata, require_points=False):
        calls.append((doc_id, metadata, require_points))
        return True

    monkeypatch.setattr(publication._r_qdrant, "update_qdrant_metadata", qdrant_ok)
    monkeypatch.setattr(
        publication._r_qdrant,
        "batch_update_qdrant_metadata",
        lambda updates, require_points=False: batch_calls.append((updates, require_points)) or True,
    )

    result = publication.publish_document(doc_id, reviewer="integration", **{
        "reviewer_id": draft_doc["reviewer_id"],
        "reviewer_roles": draft_doc["reviewer_roles"],
    })
    doc, outbox = _state(doc_id)

    assert result.ok is True
    assert result.state == "published"
    assert tuple(doc[:5]) == ("published", "approved", True, True, "published")
    assert tuple(outbox) == ("done", 1)
    assert calls[0][1]["servable"] is False
    assert len(batch_calls) == 1
    updates, require_points = batch_calls[0]
    assert require_points is True
    assert set(updates) == {doc_id}
    assert updates[doc_id]["servable"] is True
    assert updates[doc_id]["publication_version"] == 2
    assert updates[doc_id]["supersedes_doc_id"] is None


def test_vector_sync_failure_keeps_document_unservable_and_retryable(draft_doc, monkeypatch):
    doc_id = draft_doc["doc_id"]
    calls = []

    def qdrant_fails_staging(doc_id, metadata, require_points=False):
        calls.append((doc_id, metadata, require_points))
        return metadata.get("publication_state") != "qdrant_synced"

    monkeypatch.setattr(
        publication._r_qdrant,
        "update_qdrant_metadata",
        qdrant_fails_staging,
    )

    result = publication.publish_document(doc_id, reviewer="integration", **{
        "reviewer_id": draft_doc["reviewer_id"],
        "reviewer_roles": draft_doc["reviewer_roles"],
    })
    doc, outbox = _state(doc_id)

    assert result.ok is False
    assert result.state == "failed"
    assert doc[3] is False
    assert doc[4] == "failed"
    assert doc[5] == 1
    assert tuple(outbox) == ("failed", 1)
    assert calls[-1][1] == {"servable": False, "publication_state": "failed"}
