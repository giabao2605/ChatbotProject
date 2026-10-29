"""Regression tests for API success flags that disagreed with stored outcomes."""

import pytest
from fastapi import HTTPException
from sqlalchemy.exc import IntegrityError

from mech_chatbot.api.routers import operations


pytestmark = pytest.mark.unit


def test_resolved_access_request_reports_completed_request_as_failure(monkeypatch):
    monkeypatch.setattr(
        operations.access_service,
        "resolve_access_request",
        lambda **_kwargs: {"ok": False, "message": "yeu cau da duoc xu ly"},
    )

    result = operations.access_request_resolve(
        11, {"decision": "rejected"}, {"roles": ["security_admin"]},
    )

    assert result["ok"] is False
    assert result["result"]["message"] == "yeu cau da duoc xu ly"


def test_department_status_rejects_invalid_value_before_repository(monkeypatch):
    monkeypatch.setattr(
        operations.org_service,
        "set_department_status",
        lambda *_args, **_kwargs: pytest.fail("invalid status must not be stored"),
    )

    with pytest.raises(HTTPException) as error:
        operations.catalog_department_status(
            "QA", {"status": "QA_INVALID"}, {"roles": ["platform_admin"]},
        )

    assert error.value.status_code == 422


def test_department_archive_returns_repository_rejection(monkeypatch):
    monkeypatch.setattr(
        operations.org_service,
        "archive_department",
        lambda *_args, **_kwargs: {"ok": False, "message": "department has users"},
    )

    result = operations.catalog_department_archive(
        "QA", {}, {"roles": ["platform_admin"]},
    )

    assert result["ok"] is False
    assert "users" in result["message"]


def test_department_reassign_returns_repository_rejection(monkeypatch):
    monkeypatch.setattr(
        operations.org_service,
        "reassign_department_data",
        lambda **_kwargs: {"ok": False, "message": "Không thể chuyển phòng ban."},
    )

    result = operations.catalog_department_reassign(
        {"source_code": "QA_OLD", "target_code": "QA_NEW"},
        {"roles": ["platform_admin"]},
    )

    assert result == {"ok": False, "message": "Không thể chuyển phòng ban."}


@pytest.mark.parametrize("body", [
    {},
    {"request_type": "QA_INVALID"},
    {"request_type": "department"},
    {"request_type": "clearance", "requested_level": "QA_INVALID"},
])
def test_access_request_rejects_missing_or_invalid_target_before_write(monkeypatch, body):
    monkeypatch.setattr(
        operations.access_service,
        "create_access_request",
        lambda **_kwargs: pytest.fail("invalid request must not be stored"),
    )

    with pytest.raises(HTTPException) as error:
        operations.access_request(body, {"user_id": 98, "username": "viewer_b"})

    assert error.value.status_code == 422


def test_access_request_maps_ui_clearance_type_to_repository_security(monkeypatch):
    captured = {}

    def create(**kwargs):
        captured.update(kwargs)
        return {"request_id": 16, "created": True}

    monkeypatch.setattr(operations.access_service, "create_access_request", create)

    result = operations.access_request(
        {"request_type": "clearance", "requested_level": "confidential"},
        {"user_id": 98, "username": "viewer_b"},
    )

    assert result["created"] is True
    assert captured["request_type"] == "security"


def test_duplicate_username_returns_conflict_without_sql_detail(monkeypatch):
    monkeypatch.setattr(operations.bcrypt, "hashpw", lambda *_args: b"hashed")
    monkeypatch.setattr(operations.bcrypt, "gensalt", lambda: b"salt")

    def create(**_kwargs):
        raise IntegrityError(
            "INSERT INTO Users (Username) VALUES (:username)",
            {"username": "QA_existing"}, Exception("unique username"),
        )

    monkeypatch.setattr(operations.ui_query_service, "_create_user_with_roles", create)

    with pytest.raises(HTTPException) as error:
        operations.user_create(
            {"username": "QA_existing", "password": "long-enough"},
            {"roles": ["security_admin"]},
        )

    assert error.value.status_code == 409
    assert "tên đăng nhập" in error.value.detail.lower()
    assert "INSERT" not in error.value.detail


def test_user_status_rejection_is_not_reported_as_success(monkeypatch):
    monkeypatch.setattr(
        operations.ui_query_service, "set_user_active_status",
        lambda *_args, **_kwargs: {"ok": False, "message": "Không thể vô hiệu hóa tài khoản đang đăng nhập."},
    )
    result = operations.user_active(
        7, {"is_active": False}, {"roles": ["security_admin"], "user_id": 7},
    )
    assert result["ok"] is False


def test_user_delete_rejection_is_not_reported_as_success(monkeypatch):
    monkeypatch.setattr(
        operations.ui_query_service, "delete_user_account",
        lambda *_args, **_kwargs: {"ok": False, "message": "Không thể xóa admin hoạt động cuối cùng."},
    )
    result = operations.user_delete(7, {"roles": ["security_admin"], "user_id": 8})
    assert result["ok"] is False
