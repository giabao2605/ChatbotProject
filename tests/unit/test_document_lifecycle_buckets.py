from datetime import date, timedelta

import pytest

from mech_chatbot.api import app_server
from mech_chatbot.db.repositories.lifecycle import classify_lifecycle

pytestmark = pytest.mark.unit


@pytest.mark.parametrize(
    ("expiry_offset", "review_offset", "status", "expected"),
    [
        (-1, -10, "effective", "expired"),
        (0, -10, "effective", "expiring_soon"),
        (30, -10, "effective", "expiring_soon"),
        (31, -1, "effective", "needs_review"),
        (31, 1, "effective", "effective"),
        (None, None, "active", "effective"),
        (365, -10, "expired", "expired"),
    ],
)
def test_lifecycle_bucket_boundaries_are_exclusive(expiry_offset, review_offset, status, expected):
    today = date(2026, 7, 11)
    expiry = today + timedelta(days=expiry_offset) if expiry_offset is not None else None
    review = today + timedelta(days=review_offset) if review_offset is not None else None

    assert classify_lifecycle(expiry, review, today=today, soon_days=30, effective_status=status) == expected


def test_lifecycle_bucket_priority_prefers_expiring_over_review():
    today = date(2026, 7, 11)
    assert classify_lifecycle(
        today + timedelta(days=10),
        today - timedelta(days=5),
        today=today,
        soon_days=30,
        effective_status="effective",
    ) == "expiring_soon"


def test_viewer_cannot_use_legacy_filter_to_open_expired_bucket():
    profile = {
        "roles": ["viewer"], "allowed_departments": ["HR"],
        "allowed_sites": ["HN"], "max_security_level": "internal",
    }
    with pytest.raises(app_server.HTTPException) as exc_info:
        app_server.documents(eff_mode="het", profile=profile)

    assert exc_info.value.status_code == 403


def test_invalid_document_bucket_is_rejected_before_repository_call():
    with pytest.raises(app_server.HTTPException) as exc_info:
        app_server.documents(bucket="unknown", profile={"roles": ["reviewer"]})

    assert exc_info.value.status_code == 422
