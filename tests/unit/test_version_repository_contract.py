from __future__ import annotations

from unittest.mock import Mock

import pytest

from mech_chatbot.db.repositories import version

from ._small_repository_fakes import Engine, Result


pytestmark = pytest.mark.unit


def _install(monkeypatch, outcomes=(), **engine_kwargs):
    fake = Engine(outcomes, **engine_kwargs)
    monkeypatch.setattr(version, "_ensure_engine", lambda: None)
    monkeypatch.setattr(version, "engine", fake)
    monkeypatch.setattr(version._r_qdrant, "update_qdrant_metadata", Mock(return_value=True))
    monkeypatch.setattr(version._r_feedback, "mark_feedback_stale_for_doc", Mock())
    monkeypatch.setattr(version._r_audit, "write_audit_log", Mock())
    monkeypatch.setattr(version._r_semantic_cache, "_invalidate_semantic_cache", Mock())
    monkeypatch.setattr(version._r_publication, "publish_document", Mock(return_value=True))
    return fake


def test_full_metadata_updates_sql_family_qdrant_feedback_and_audit(monkeypatch):
    fake = _install(
        monkeypatch,
        (
            Result(),
            Result(),
            Result(),
            Result(),
            Result(),
            Result(row=(44,)),
            Result(),
        ),
    )
    monkeypatch.setattr(version._r_document, "_normalize_doc_type_label", lambda value: value.strip().lower())

    result = version.update_document_full_metadata(
        7,
        base_code=" ab-12 ",
        version_no=3,
        version_label="C",
        variant_code="left",
        variant_group="pump",
        loai_tai_lieu=" Drawing ",
        domain="mechanical",
        security_level="confidential",
        site="HQ",
        cong_doan="assembly",
        reviewer="reviewer-1",
    )

    assert result is True
    assert fake.connection.calls[-1][1] == {"fid": 44, "id": 7}
    version._r_qdrant.update_qdrant_metadata.assert_called_once_with(
        7,
        {
            "base_code": "ab-12",
            "version_no": 3,
            "variant_code": "left",
            "variant_group": "pump",
            "loai_tai_lieu": "drawing",
            "domain": "mechanical",
            "security_level": "confidential",
            "site": "HQ",
            "cong_doan": "assembly",
        },
    )
    version._r_feedback.mark_feedback_stale_for_doc.assert_called_once_with(7, resolved_by_doc_id=7)
    version._r_audit.write_audit_log.assert_called_once_with(
        "reviewer-1",
        "update_metadata",
        "TaiLieu",
        7,
        {"base_code": "ab-12", "version": 3, "variant": "left"},
    )


def test_full_metadata_creates_missing_family_and_returns_best_effort_sync_status(monkeypatch):
    fake = _install(
        monkeypatch,
        (Result(), Result(row=None), Result(), Result(row=(55,)), Result()),
    )
    version._r_qdrant.update_qdrant_metadata.return_value = False

    result = version.update_document_full_metadata(8, base_code="new-1")

    assert result is False
    assert "INSERT INTO DocumentFamily" in fake.connection.calls[2][0]
    assert fake.connection.calls[4][1] == {"fid": 55, "id": 8}


def test_full_metadata_without_optional_values_skips_qdrant_but_marks_feedback_stale(monkeypatch):
    _install(monkeypatch, (Result(),))

    assert version.update_document_full_metadata(9) is True
    version._r_qdrant.update_qdrant_metadata.assert_not_called()
    version._r_feedback.mark_feedback_stale_for_doc.assert_called_once_with(9, resolved_by_doc_id=9)


@pytest.mark.parametrize(
    ("operation", "action"),
    [
        (version.publish_as_new_version, "new_version"),
        (version.publish_as_new_variant, "new_variant"),
        (version.publish_as_standalone, "standalone"),
    ],
)
def test_publish_wrappers_forward_identity_and_boolean_result(monkeypatch, operation, action):
    _install(monkeypatch)
    version._r_publication.publish_document.return_value = {"ok": True}

    result = operation(12, reviewer="rv", reviewer_id=4, reviewer_roles=["reviewer"])

    assert result is True
    version._r_publication.publish_document.assert_called_once_with(
        12,
        action=action,
        reviewer="rv",
        reviewer_id=4,
        reviewer_roles=["reviewer"],
    )


def test_reject_document_fails_closed_before_sql_when_qdrant_cannot_be_disabled(monkeypatch):
    fake = _install(monkeypatch)
    version._r_qdrant.update_qdrant_metadata.return_value = False

    assert version.reject_document(7, reviewer="rv") is False
    assert fake.connection.calls == []
    version._r_audit.write_audit_log.assert_not_called()


def test_reject_document_disables_serving_then_updates_sql_and_invalidates_cache(monkeypatch):
    fake = _install(monkeypatch, (Result(),))

    assert version.reject_document(7, reviewer="rv") is True
    assert fake.connection.calls[0][1] == {"id": 7, "rev": "rv"}
    version._r_qdrant.update_qdrant_metadata.assert_called_once_with(
        7,
        {
            "servable": False,
            "publication_state": "failed",
            "lifecycle_status": "rejected",
            "review_status": "rejected",
        },
    )
    version._r_audit.write_audit_log.assert_called_once_with("rv", "reject_document", "TaiLieu", 7, {})
    version._r_semantic_cache._invalidate_semantic_cache.assert_called_once_with("doc.reject")


def test_archive_document_fails_closed_before_sql_when_qdrant_has_no_points(monkeypatch):
    fake = _install(monkeypatch)
    version._r_qdrant.update_qdrant_metadata.return_value = False

    assert version.archive_document(7) is False
    assert fake.connection.calls == []


def test_archive_document_updates_sql_audit_and_cache_after_qdrant(monkeypatch):
    fake = _install(monkeypatch, (Result(),))

    assert version.archive_document(7, reviewer="rv") is True
    assert fake.connection.calls[0][1] == {"id": 7}
    version._r_qdrant.update_qdrant_metadata.assert_called_once_with(
        7,
        {
            "servable": False,
            "is_current": False,
            "is_archived": True,
            "lifecycle_status": "archived",
        },
        require_points=True,
    )
    version._r_audit.write_audit_log.assert_called_once_with("rv", "archive_document", "TaiLieu", 7, {})
    version._r_semantic_cache._invalidate_semantic_cache.assert_called_once_with("doc.archive")


def test_legacy_rollback_resolves_family_then_delegates(monkeypatch):
    _install(monkeypatch, (Result(row=(88,)),))
    delegated = Mock(return_value=True)
    monkeypatch.setattr(version, "rollback_to_version_by_family", delegated)

    result = version.rollback_to_version(
        "AB-1", 4, "left", "rv", reviewer_id=3, reviewer_roles=["reviewer"]
    )

    assert result is True
    delegated.assert_called_once_with(
        88, 4, "left", "rv", reviewer_id=3, reviewer_roles=["reviewer"]
    )


def test_legacy_rollback_returns_false_for_missing_family_or_database_error(monkeypatch):
    _install(monkeypatch, (Result(row=None),))
    assert version.rollback_to_version("missing", 1) is False

    _install(monkeypatch, connect_error=RuntimeError("offline"))
    assert version.rollback_to_version("AB-1", 1) is False


def test_family_rollback_publishes_selected_document_with_default_variant(monkeypatch):
    fake = _install(monkeypatch, (Result(row=(91,)),))

    result = version.rollback_to_version_by_family(
        88, 4, "", "rv", reviewer_id=3, reviewer_roles=["reviewer"]
    )

    assert result is True
    assert fake.connection.calls[0][1] == {"fid": 88, "vc": "default", "vn": 4}
    version._r_publication.publish_document.assert_called_once_with(
        91,
        action="new_version",
        reviewer="rv",
        reviewer_id=3,
        reviewer_roles=["reviewer"],
    )


def test_family_rollback_returns_false_for_missing_target_or_database_error(monkeypatch):
    _install(monkeypatch, (Result(row=None),))
    assert version.rollback_to_version_by_family(88, 4) is False

    _install(monkeypatch, connect_error=RuntimeError("offline"))
    assert version.rollback_to_version_by_family(88, 4) is False
