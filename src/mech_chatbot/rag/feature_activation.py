"""Compatibility interface for the governance-owned activation policy."""

from mech_chatbot.governance.feature_activation import (
    ACTIVATION_PROFILES,
    ActivationStatus,
    FEATURE_FLAGS,
    MILESTONE_FLAGS,
    VERSION_DEFAULTS,
    VERSION_FIELDS,
    _artifact_commit,
    _artifact_review_mode,
    activation_status,
    current_git_commit,
    feature_flags,
    feature_versions,
    profile_environment,
    validate_controlled_demo_decision_ledger,
    validate_release_decision_ledger,
)


__all__ = [
    "ACTIVATION_PROFILES",
    "ActivationStatus",
    "FEATURE_FLAGS",
    "MILESTONE_FLAGS",
    "VERSION_DEFAULTS",
    "VERSION_FIELDS",
    "activation_status",
    "current_git_commit",
    "feature_flags",
    "feature_versions",
    "profile_environment",
    "validate_controlled_demo_decision_ledger",
    "validate_release_decision_ledger",
]
