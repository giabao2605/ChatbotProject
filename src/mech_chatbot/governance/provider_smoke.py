"""Provider-smoke artifact contract shared by rollout governance."""

from __future__ import annotations


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


__all__ = ["provider_smoke_artifact_valid"]
