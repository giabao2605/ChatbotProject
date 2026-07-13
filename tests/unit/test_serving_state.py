from datetime import date, timedelta
from types import SimpleNamespace

from mech_chatbot.rag.serving_state import filter_currently_servable, is_currently_servable


def _meta(**overrides):
    value = {
        "servable": True,
        "publication_state": "published",
        "lifecycle_status": "published",
        "review_status": "approved",
        "is_current": True,
        "effective_status": "effective",
        "effective_date": None,
        "expiry_date": None,
    }
    value.update(overrides)
    return value


def test_serving_state_accepts_current_effective_document():
    assert is_currently_servable(_meta(), today=date(2026, 7, 11), require_current=True)


def test_serving_state_denies_expired_date_before_reconciliation():
    assert not is_currently_servable(
        _meta(expiry_date="2026-07-10", effective_status="effective"),
        today=date(2026, 7, 11),
    )


def test_serving_state_denies_future_and_invalid_effective_dates():
    assert not is_currently_servable(_meta(effective_date="2026-07-12"), today=date(2026, 7, 11))
    assert not is_currently_servable(_meta(expiry_date="not-a-date"), today=date(2026, 7, 11))


def test_post_filter_removes_expired_results_from_every_retrieval_branch():
    current = SimpleNamespace(metadata=_meta(expiry_date="2026-07-11"))
    expired = SimpleNamespace(metadata=_meta(expiry_date="2026-07-10"))
    assert filter_currently_servable([expired, current], today=date(2026, 7, 11)) == [current]

