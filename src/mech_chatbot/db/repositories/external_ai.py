"""Persistence for metadata-only external AI provider profiles.

API keys are deliberately never read from or written to this repository. A
profile stores only an environment/secret-manager reference such as
``env:VOYAGE_API_KEY``.
"""

from __future__ import annotations

from datetime import datetime
import json
import re
from typing import Any

from sqlalchemy import text

from ..engine import _ensure_engine, engine
from . import audit as _r_audit


__all__ = [
    "get_external_ai_provider_profile",
    "list_external_ai_provider_profiles",
    "upsert_external_ai_provider_profile",
]


_PROVIDER_RE = re.compile(r"^[a-z0-9][a-z0-9_-]{0,99}$")


def _surfaces(value: Any) -> list[str]:
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except json.JSONDecodeError:
            value = [value]
    if not isinstance(value, (list, tuple, set)):
        return []
    return sorted({str(item).strip() for item in value if str(item).strip()})


def _iso(value: Any) -> str | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value.isoformat()
    return str(value)


def _profile_from_row(row: Any) -> dict[str, Any]:
    review_expires_at = _iso(row["ReviewExpiresAt"])
    review_state = "unknown"
    try:
        review_state = "expired" if row["ReviewExpiresAt"] < datetime.now() else "current"
    except (TypeError, ValueError):
        pass
    return {
        "provider": str(row["Provider"]),
        "endpoint": str(row["Endpoint"]),
        "default_model": str(row["DefaultModel"]),
        "secret_reference": str(row["SecretReference"]),
        "allowed_surfaces": _surfaces(row["AllowedSurfacesJson"]),
        "retention_mode": str(row["RetentionMode"]),
        "policy_version": str(row["PolicyVersion"]),
        "approved_by": str(row["ApprovedBy"]),
        "risk_acceptance_ref": str(row["RiskAcceptanceRef"]),
        "review_expires_at": review_expires_at,
        "review_state": review_state,
        "is_active": bool(row["IsActive"]),
        "updated_at": _iso(row["UpdatedAt"]),
        "updated_by": str(row["UpdatedBy"]),
    }


def list_external_ai_provider_profiles() -> list[dict[str, Any]]:
    """Return provider policy metadata safe for the admin UI and audits."""
    _ensure_engine()
    if engine is None:
        return []
    with engine.connect() as conn:
        rows = conn.execute(
            text(
                """
                SELECT Provider, Endpoint, DefaultModel, SecretReference,
                       AllowedSurfacesJson, RetentionMode, PolicyVersion,
                       ApprovedBy, RiskAcceptanceRef, ReviewExpiresAt,
                       IsActive, UpdatedAt, UpdatedBy
                FROM dbo.ExternalAIProviderProfile
                ORDER BY Provider
                """
            )
        ).mappings().all()
    return [_profile_from_row(row) for row in rows]


def get_external_ai_provider_profile(provider: str) -> dict[str, Any] | None:
    normalized = str(provider or "").strip().lower()
    if not normalized:
        return None
    _ensure_engine()
    if engine is None:
        return None
    with engine.connect() as conn:
        row = conn.execute(
            text(
                """
                SELECT Provider, Endpoint, DefaultModel, SecretReference,
                       AllowedSurfacesJson, RetentionMode, PolicyVersion,
                       ApprovedBy, RiskAcceptanceRef, ReviewExpiresAt,
                       IsActive, UpdatedAt, UpdatedBy
                FROM dbo.ExternalAIProviderProfile
                WHERE Provider = :provider
                """
            ),
            {"provider": normalized},
        ).mappings().first()
    return _profile_from_row(row) if row else None


def upsert_external_ai_provider_profile(
    provider: str,
    *,
    endpoint: str,
    default_model: str,
    secret_reference: str,
    allowed_surfaces: list[str] | tuple[str, ...],
    retention_mode: str,
    policy_version: str,
    approved_by: str,
    risk_acceptance_ref: str,
    review_expires_at: str,
    is_active: bool = True,
    updated_by: str = "System",
) -> dict[str, Any]:
    """Upsert one policy profile without ever accepting a raw API key."""
    normalized = str(provider or "").strip().lower()
    if not _PROVIDER_RE.fullmatch(normalized):
        raise ValueError("Provider chi duoc dung chu thuong, so, '-' va '_'")

    required = {
        "endpoint": endpoint,
        "default_model": default_model,
        "secret_reference": secret_reference,
        "retention_mode": retention_mode,
        "policy_version": policy_version,
        "approved_by": approved_by,
        "risk_acceptance_ref": risk_acceptance_ref,
        "review_expires_at": review_expires_at,
    }
    missing = [name for name, value in required.items() if not str(value or "").strip()]
    if missing:
        raise ValueError("Thieu truong profile: " + ", ".join(missing))

    normalized_surfaces = _surfaces(allowed_surfaces)
    if not normalized_surfaces:
        raise ValueError("Profile phai cho phep it nhat mot external surface")
    if not str(secret_reference).strip().startswith(("env:", "secret://")):
        raise ValueError("SecretReference phai la env:... hoac secret://..., khong phai API key")

    _ensure_engine()
    if engine is None:
        raise RuntimeError("Database chua san sang")
    params = {
        "provider": normalized,
        "endpoint": str(endpoint).strip()[:500],
        "default_model": str(default_model).strip()[:150],
        "secret_reference": str(secret_reference).strip()[:200],
        "allowed_surfaces": json.dumps(normalized_surfaces, ensure_ascii=False),
        "retention_mode": str(retention_mode).strip()[:100],
        "policy_version": str(policy_version).strip()[:100],
        "approved_by": str(approved_by).strip()[:200],
        "risk_acceptance_ref": str(risk_acceptance_ref).strip()[:500],
        "review_expires_at": str(review_expires_at).strip(),
        "is_active": 1 if is_active else 0,
        "updated_by": str(updated_by or "System").strip()[:100],
    }
    with engine.begin() as conn:
        conn.execute(
            text(
                """
                MERGE dbo.ExternalAIProviderProfile AS target
                USING (SELECT :provider AS Provider) AS source
                ON target.Provider = source.Provider
                WHEN MATCHED THEN UPDATE SET
                    Endpoint = :endpoint,
                    DefaultModel = :default_model,
                    SecretReference = :secret_reference,
                    AllowedSurfacesJson = :allowed_surfaces,
                    RetentionMode = :retention_mode,
                    PolicyVersion = :policy_version,
                    ApprovedBy = :approved_by,
                    RiskAcceptanceRef = :risk_acceptance_ref,
                    ReviewExpiresAt = TRY_CONVERT(DATETIME, :review_expires_at),
                    IsActive = :is_active,
                    UpdatedAt = GETDATE(),
                    UpdatedBy = :updated_by
                WHEN NOT MATCHED THEN INSERT (
                    Provider, Endpoint, DefaultModel, SecretReference,
                    AllowedSurfacesJson, RetentionMode, PolicyVersion,
                    ApprovedBy, RiskAcceptanceRef, ReviewExpiresAt,
                    IsActive, UpdatedBy
                ) VALUES (
                    :provider, :endpoint, :default_model, :secret_reference,
                    :allowed_surfaces, :retention_mode, :policy_version,
                    :approved_by, :risk_acceptance_ref,
                    TRY_CONVERT(DATETIME, :review_expires_at), :is_active, :updated_by
                );
                """
            ),
            params,
        )

    _r_audit.write_audit_log(
        updated_by,
        "external_ai_provider_profile_upsert",
        "ExternalAIProviderProfile",
        None,
        {
            "provider": normalized,
            "endpoint": params["endpoint"],
            "default_model": params["default_model"],
            "secret_reference": params["secret_reference"],
            "allowed_surfaces": normalized_surfaces,
            "retention_mode": params["retention_mode"],
            "policy_version": params["policy_version"],
            "risk_acceptance_ref": params["risk_acceptance_ref"],
            "is_active": bool(is_active),
        },
    )
    return get_external_ai_provider_profile(normalized) or {}
