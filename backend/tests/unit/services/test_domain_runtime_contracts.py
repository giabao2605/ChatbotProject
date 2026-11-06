from __future__ import annotations

from datetime import date
from types import SimpleNamespace

import pytest


pytestmark = pytest.mark.unit


def _servable_metadata(**overrides: object) -> dict[str, object]:
    metadata: dict[str, object] = {
        "servable": True,
        "publication_state": "published",
        "lifecycle_status": "published",
        "review_status": "approved",
        "is_current": True,
        "effective_status": "effective",
        "effective_date": None,
        "expiry_date": None,
    }
    return {**metadata, **overrides}


def test_domain_serving_state_owns_the_neutral_serving_policy() -> None:
    from mech_chatbot.domain.serving_state import (
        filter_currently_servable,
        is_currently_servable,
    )

    current = SimpleNamespace(metadata=_servable_metadata(expiry_date="2026-07-24"))
    expired = SimpleNamespace(metadata=_servable_metadata(expiry_date="2026-07-23"))

    assert is_currently_servable(
        current.metadata,
        today=date(2026, 7, 24),
        require_current=True,
    )
    assert filter_currently_servable(
        [expired, current],
        today=date(2026, 7, 24),
    ) == [current]


def test_rag_serving_state_remains_a_static_compatibility_wrapper() -> None:
    from mech_chatbot.domain.serving_state import (
        filter_currently_servable as domain_filter,
        is_currently_servable as domain_check,
    )
    from mech_chatbot.rag.serving_state import (
        filter_currently_servable as legacy_filter,
        is_currently_servable as legacy_check,
    )

    assert legacy_check is domain_check
    assert legacy_filter is domain_filter


def test_domain_ingestion_progress_owns_the_shared_progress_contract() -> None:
    from mech_chatbot.domain.ingestion_progress import (
        IngestionPhase,
        IngestionProgressEvent,
    )

    phase: IngestionPhase = "embedding"
    event = IngestionProgressEvent(phase, "Đang tạo embedding...")

    assert event.phase == "embedding"
    assert event.message == "Đang tạo embedding..."


def test_ingestion_progress_remains_a_static_compatibility_wrapper() -> None:
    from mech_chatbot.domain.ingestion_progress import (
        IngestionPhase as DomainPhase,
        IngestionProgressEvent as DomainEvent,
    )
    from mech_chatbot.ingestion.progress import (
        IngestionPhase as LegacyPhase,
        IngestionProgressEvent as LegacyEvent,
    )

    assert LegacyPhase is DomainPhase
    assert LegacyEvent is DomainEvent
