"""Compatibility imports for the CRAG controlled-demo authorization."""

from mech_chatbot.governance.crag_demo_authorization import (
    REVIEW_MODES,
    SCHEMA,
    SERIES_SCHEMA,
    SMOKE_SCHEMA,
    build_crag_demo_authorization,
    validate_crag_demo_authorization,
)

__all__ = [
    "REVIEW_MODES",
    "SCHEMA",
    "SERIES_SCHEMA",
    "SMOKE_SCHEMA",
    "build_crag_demo_authorization",
    "validate_crag_demo_authorization",
]
