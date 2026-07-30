"""Provider-smoke artifact contract shared by rollout governance."""

from __future__ import annotations

from datetime import datetime, timedelta
import hashlib
import json


PROVIDER_SMOKE_MAX_AGE = timedelta(minutes=30)


def provider_configuration_for_settings(settings) -> dict[str, object]:
    from mech_chatbot.config.settings import LlmSettings
    from mech_chatbot.llm.llm_client import get_llm_endpoint

    llm_settings = LlmSettings.from_settings(settings)
    return {
        "endpoint": str(llm_settings.base_url or get_llm_endpoint()).strip(),
        "model": str(llm_settings.model_name).strip(),
        "max_concurrent_rag": settings.MAX_CONCURRENT_RAG,
    }


def provider_configuration_sha256(configuration: dict[str, object]) -> str:
    return hashlib.sha256(
        json.dumps(configuration, sort_keys=True).encode("utf-8")
    ).hexdigest()


def provider_configuration_sha256_for_settings(settings) -> str:
    return provider_configuration_sha256(
        provider_configuration_for_settings(settings)
    )


def _aware_datetime(value: object) -> datetime | None:
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return None
    return parsed if parsed.tzinfo is not None else None


def provider_smoke_artifact_valid(
    artifact: object,
    *,
    expected_provider_sha256: str,
) -> bool:
    if not isinstance(artifact, dict) or not expected_provider_sha256:
        return False
    provider_outcome = artifact.get("provider_outcome")
    if not isinstance(provider_outcome, dict):
        return False
    try:
        return all((
            artifact.get("schema") == "provider-smoke-v1",
            artifact.get("passed") is True,
            int(artifact.get("request_count") or 0) == 5,
            int(artifact.get("successful_requests") or 0) == 5,
            int(artifact.get("failed_requests") or 0) == 0,
            int(artifact.get("provider_retries") or 0) == 0,
            artifact.get("provider_configuration_sha256")
            == expected_provider_sha256,
            provider_outcome.get("provider_blocked") is False,
        ))
    except (TypeError, ValueError):
        return False


def provider_smoke_fresh_for_baseline(
    artifact: object,
    *,
    baseline_started_at: object,
) -> bool:
    """Return whether a smoke completed no more than 30 minutes before baseline."""
    return provider_smoke_fresh_for_arms(
        artifact,
        arm_started_at=(baseline_started_at,),
    )


def provider_smoke_fresh_for_arms(
    artifact: object,
    *,
    arm_started_at: tuple[object, ...],
) -> bool:
    """Return whether smoke is fresh at every declared arm start."""
    if not isinstance(artifact, dict):
        return False
    smoke_completed_at = _aware_datetime(artifact.get("completed_at"))
    arm_starts = tuple(_aware_datetime(value) for value in arm_started_at)
    if (
        smoke_completed_at is None
        or not arm_starts
        or any(value is None for value in arm_starts)
    ):
        return False
    return all(
        timedelta(0) < start - smoke_completed_at <= PROVIDER_SMOKE_MAX_AGE
        for start in arm_starts
        if start is not None
    )


__all__ = [
    "PROVIDER_SMOKE_MAX_AGE",
    "provider_configuration_for_settings",
    "provider_configuration_sha256",
    "provider_configuration_sha256_for_settings",
    "provider_smoke_artifact_valid",
    "provider_smoke_fresh_for_arms",
    "provider_smoke_fresh_for_baseline",
]
