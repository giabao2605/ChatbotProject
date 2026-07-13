"""Shared provider policy, audit, and result-normalization boundary.

Every outbound AI call must enter through :func:`audited_external_call`. The
boundary stores only metadata; prompts, chunks, images, API keys, and model
responses are intentionally excluded from audit persistence.
"""

from __future__ import annotations

from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass, field
from datetime import datetime
from functools import lru_cache
import json
import math
import os
import time
from typing import Any, Iterable, Mapping

from sqlalchemy import text

from mech_chatbot.config.logging import logger


ALL_EXTERNAL = "all_external"
INTERNAL_ONLY = "internal_only"


class ExternalProcessingDenied(RuntimeError):
    """Raised when a call conflicts with the configured data policy."""


class ExternalAIComplianceAuditUnavailable(ExternalProcessingDenied):
    """Raised before egress when the compliance audit cannot be written."""


class ExternalAICallCancelled(Exception):
    """Signal client-disconnected external work without classifying it as an error.

    Streaming callers can raise this from inside :func:`audited_external_call`.
    The boundary records ``status='cancelled'`` once, then re-raises it so the
    caller can stop its own stream cleanly.
    """


@dataclass(frozen=True)
class ExternalAIProviderProfile:
    """Safe provider metadata. ``secret_reference`` is never a raw secret."""

    provider: str
    endpoint: str
    default_model: str
    secret_reference: str
    allowed_surfaces: tuple[str, ...]
    retention_mode: str
    policy_version: str
    approved_by: str
    risk_acceptance_ref: str
    review_expires_at: datetime | None
    is_active: bool = True


@dataclass(frozen=True)
class ExternalAIErrorMetadata:
    error_type: str
    retryable: bool
    status_code: int | None = None


@dataclass(frozen=True)
class ExternalAIResult:
    """Provider-neutral result envelope used by adapters and tests."""

    kind: str
    provider: str
    model: str | None
    text: str | None = None
    items: tuple[dict[str, Any], ...] = field(default_factory=tuple)
    error: ExternalAIErrorMetadata | None = None


@dataclass(frozen=True)
class ExternalAIProviderRuntime:
    """Resolved adapter configuration without exposing the secret reference."""

    provider: str
    endpoint: str
    model: str
    api_key: str | None
    profile: ExternalAIProviderProfile


@dataclass(frozen=True)
class ExternalCallSpec:
    provider: str
    endpoint: str | None
    model: str | None
    surface: str
    trace_id: str | None = None
    actor_username: str | None = None
    actor_is_admin: bool | None = None
    doc_ids: tuple[int, ...] = ()
    security_levels: tuple[str, ...] = ()
    policies: tuple[str, ...] = ()
    policy_version: str | None = None
    retention_mode: str | None = None
    risk_acceptance_ref: str | None = None
    input_chars: int | None = None
    input_bytes: int | None = None
    input_token_estimate: int | None = None


_ACTOR_USERNAME: ContextVar[str | None] = ContextVar("external_ai_actor", default=None)
_ACTOR_IS_ADMIN: ContextVar[bool | None] = ContextVar("external_ai_actor_is_admin", default=None)
_TRACE_ID: ContextVar[str | None] = ContextVar("external_ai_trace_id", default=None)
_DOC_IDS: ContextVar[tuple[int, ...]] = ContextVar("external_ai_doc_ids", default=())
_SECURITY_LEVELS: ContextVar[tuple[str, ...]] = ContextVar("external_ai_security_levels", default=())
_POLICIES: ContextVar[tuple[str, ...]] = ContextVar("external_ai_policies", default=())

_LOCAL_DEVELOPMENT_FLAG = "EXTERNAL_AI_LOCAL_DEVELOPMENT"
_APPLICATION_ENVIRONMENT_FLAG = "APP_ENV"
_LOCAL_APPLICATION_ENVIRONMENTS = frozenset({"development", "local"})


_DEFAULT_SURFACES = (
    "document_classification",
    "intent_routing",
    "query_disambiguation",
    "chat_history_summary",
    "hyde",
    "interaction_routing",
    "evidence_verification",
    "claim_repair",
    "generation",
    "vision_ocr",
)


def _fallback_provider_profile(provider: str) -> ExternalAIProviderProfile | None:
    normalized = str(provider or "").strip().lower()
    # This is intentionally usable only after _is_explicit_local_development()
    # has opted in. It must never become an implicit pilot/production profile.
    if normalized == "proxyllm":
        return ExternalAIProviderProfile(
            provider="proxyllm",
            endpoint=os.getenv("PROXYLLM_BASE_URL", "https://api.proxyllm.eu/v1"),
            default_model=os.getenv("GPT_MODEL_NAME", "gpt-5.4"),
            secret_reference="env:PROXYLLM_API_KEY",
            allowed_surfaces=_DEFAULT_SURFACES,
            retention_mode="provider_default_no_opt_out",
            policy_version="risk-accepted-v4-claim-repair",
            approved_by="documented-risk-acceptance",
            risk_acceptance_ref="notion:92459b78-3e54-4c47-8322-d44ab2b65664",
            # Local bootstrap is deliberately not a synthetic 90-day review.
            # Managed deployments require ReviewExpiresAt from SQL.
            review_expires_at=None,
        )
    if normalized == "voyage":
        return ExternalAIProviderProfile(
            provider="voyage",
            endpoint="https://api.voyageai.com/v1",
            default_model=os.getenv("VOYAGE_RERANK_MODEL", "rerank-2.5-lite"),
            secret_reference="env:VOYAGE_API_KEY",
            allowed_surfaces=("reranking",),
            retention_mode="provider_default_no_opt_out",
            policy_version="risk-accepted-v3",
            approved_by="documented-risk-acceptance",
            risk_acceptance_ref="notion:92459b78-3e54-4c47-8322-d44ab2b65664",
            review_expires_at=None,
        )
    return None


def _as_datetime(value: Any) -> datetime | None:
    if value is None or value == "":
        return None
    if isinstance(value, datetime):
        return value.replace(tzinfo=None)
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00")).replace(tzinfo=None)
    except ValueError:
        return None


def _clean_tokens(values: Iterable | None) -> tuple[str, ...]:
    return tuple(sorted({str(value).strip() for value in (values or []) if str(value).strip()}))


def _clean_doc_ids(values: Iterable | None) -> tuple[int, ...]:
    out = set()
    for value in values or []:
        try:
            out.add(int(value))
        except (TypeError, ValueError):
            continue
    return tuple(sorted(out))


def _profile_from_mapping(value: Mapping[str, Any]) -> ExternalAIProviderProfile:
    return ExternalAIProviderProfile(
        provider=str(value.get("provider") or "").strip().lower(),
        endpoint=str(value.get("endpoint") or "").strip(),
        default_model=str(value.get("default_model") or "").strip(),
        secret_reference=str(value.get("secret_reference") or "").strip(),
        allowed_surfaces=_clean_tokens(value.get("allowed_surfaces")),
        retention_mode=str(value.get("retention_mode") or "").strip(),
        policy_version=str(value.get("policy_version") or "").strip(),
        approved_by=str(value.get("approved_by") or "").strip(),
        risk_acceptance_ref=str(value.get("risk_acceptance_ref") or "").strip(),
        review_expires_at=_as_datetime(value.get("review_expires_at")),
        is_active=bool(value.get("is_active", True)),
    )


def _is_explicit_local_development() -> bool:
    """Return true only for the explicit local-development escape hatch.

    Both an opt-in flag and an explicitly local application environment are
    required. This prevents a copied flag alone from enabling bootstrap
    profiles in pilot or production.
    """

    local_flag_enabled = str(os.getenv(_LOCAL_DEVELOPMENT_FLAG, "")).strip().lower() in {
        "1", "true", "yes", "on"
    }
    application_environment = str(os.getenv(_APPLICATION_ENVIRONMENT_FLAG, "")).strip().lower()
    return local_flag_enabled and application_environment in _LOCAL_APPLICATION_ENVIRONMENTS


def _load_managed_provider_profile(provider: str) -> Mapping[str, Any] | None:
    """Load one profile from the managed persistence boundary."""

    from mech_chatbot.db.repositories.external_ai import (
        get_external_ai_provider_profile as _get_profile,
    )

    return _get_profile(provider)


@lru_cache(maxsize=16)
def get_external_ai_provider_profile(provider: str) -> ExternalAIProviderProfile | None:
    """Load a managed profile and fail closed outside explicit local development."""
    normalized = str(provider or "").strip().lower()
    if not normalized:
        return None
    try:
        stored = _load_managed_provider_profile(normalized)
        if stored:
            return _profile_from_mapping(stored)
    except Exception:
        # Do not include database exception text: it can contain driver details
        # and does not help a caller decide whether egress is allowed.
        logger.error(
            "Managed External AI profile lookup failed for %s; refusing external processing",
            normalized,
        )

    if _is_explicit_local_development():
        fallback = _fallback_provider_profile(normalized)
        if fallback is not None:
            logger.warning(
                "Using local-development External AI bootstrap profile for %s; "
                "do not enable this outside local development",
                normalized,
            )
        return fallback

    logger.warning(
        "No managed External AI provider profile for %s; refusing external processing",
        normalized,
    )
    return None


def invalidate_external_ai_provider_profiles() -> None:
    get_external_ai_provider_profile.cache_clear()


def _resolve_secret_reference(reference: str) -> str | None:
    """Resolve only env-backed references; secret-manager URIs stay opaque."""
    normalized = str(reference or "").strip()
    if normalized.startswith("env:"):
        return os.getenv(normalized[4:].strip()) or None
    # A secret:// resolver is deployment-specific.  Do not attempt to parse
    # or log it; deployments must inject the resolved value into the adapter.
    return None


def get_provider_runtime(
    provider: str,
    *,
    fallback_endpoint: str,
    fallback_model: str,
    fallback_secret_envs: Iterable[str] = (),
) -> ExternalAIProviderRuntime:
    """Resolve endpoint/model/secret from one managed provider profile.

    ``fallback_*`` remain in the signature for adapter compatibility, but are
    never used to substitute a missing managed profile in pilot/production.
    Explicit local development receives a fully populated bootstrap profile
    from :func:`_fallback_provider_profile` instead.
    """
    del fallback_endpoint, fallback_model, fallback_secret_envs
    normalized_provider = str(provider or "").strip().lower()
    profile = get_external_ai_provider_profile(normalized_provider)
    if profile is None:
        raise ExternalProcessingDenied(
            f"Khong co ExternalAIProviderProfile cho provider '{normalized_provider}'"
        )
    endpoint = str(profile.endpoint or "").strip()
    model = str(profile.default_model or "").strip()
    if not endpoint or not model or not profile.secret_reference:
        raise ExternalProcessingDenied(
            f"ExternalAIProviderProfile cua '{normalized_provider}' khong day du"
        )
    api_key = _resolve_secret_reference(profile.secret_reference)
    return ExternalAIProviderRuntime(
        provider=normalized_provider,
        endpoint=endpoint,
        model=model,
        api_key=api_key,
        profile=profile,
    )


@contextmanager
def external_processing_context(
    actor_username: str | None,
    actor_is_admin: bool | None = None,
    trace_id: str | None = None,
):
    """Attach the initiating user to all nested audited calls in this worker."""
    actor_token = _ACTOR_USERNAME.set(str(actor_username).strip()[:100] if actor_username else None)
    admin_token = _ACTOR_IS_ADMIN.set(actor_is_admin if actor_is_admin is None else bool(actor_is_admin))
    trace_token = _TRACE_ID.set(str(trace_id).strip()[:100] if trace_id else None)
    try:
        yield
    finally:
        _ACTOR_USERNAME.reset(actor_token)
        _ACTOR_IS_ADMIN.reset(admin_token)
        _TRACE_ID.reset(trace_token)


@contextmanager
def external_document_context(doc_ids=None, security_levels=None, policies=None):
    """Attach source-document metadata without replacing actor/trace context."""
    doc_token = _DOC_IDS.set(_clean_doc_ids(doc_ids))
    security_token = _SECURITY_LEVELS.set(_clean_tokens(security_levels))
    policy_token = _POLICIES.set(_clean_tokens(policies))
    try:
        yield
    finally:
        _DOC_IDS.reset(doc_token)
        _SECURITY_LEVELS.reset(security_token)
        _POLICIES.reset(policy_token)


def _assert_profile_allows(profile: ExternalAIProviderProfile, surface: str) -> None:
    if not profile.is_active:
        raise ExternalProcessingDenied(f"Provider '{profile.provider}' dang bi tat")
    if profile.review_expires_at is None and not _is_explicit_local_development():
        raise ExternalProcessingDenied(
            f"Provider '{profile.provider}' thieu han review policy"
        )
    if profile.review_expires_at and profile.review_expires_at < datetime.now():
        raise ExternalProcessingDenied(f"Provider '{profile.provider}' da qua han review policy")
    if not profile.allowed_surfaces:
        raise ExternalProcessingDenied(
            f"Provider '{profile.provider}' chua khai bao external surface"
        )
    if surface not in profile.allowed_surfaces:
        raise ExternalProcessingDenied(
            f"Surface '{surface}' khong duoc profile '{profile.provider}' cho phep"
        )


def make_external_call_spec(
    *,
    provider,
    model,
    surface,
    endpoint=None,
    trace_id=None,
    actor_username=None,
    actor_is_admin=None,
    doc_ids=None,
    security_levels=None,
    policies=None,
    input_chars=None,
    input_bytes=None,
    input_token_estimate=None,
    profile: ExternalAIProviderProfile | None = None,
):
    normalized_provider = str(provider or "").strip().lower()
    normalized_surface = str(surface or "unknown").strip()[:50]
    resolved_doc_ids = _clean_doc_ids(doc_ids) if doc_ids is not None else _DOC_IDS.get()
    resolved_security_levels = _clean_tokens(security_levels) if security_levels is not None else _SECURITY_LEVELS.get()
    normalized_policies = _clean_tokens(policies) if policies is not None else _POLICIES.get()
    if not normalized_policies:
        normalized_policies = (os.getenv("EXTERNAL_PROCESSING_POLICY", ALL_EXTERNAL).strip() or ALL_EXTERNAL,)
    denied = [policy for policy in normalized_policies if policy != ALL_EXTERNAL]
    if denied:
        raise ExternalProcessingDenied("External processing bi chan boi policy: " + ", ".join(denied))

    resolved_profile = profile or get_external_ai_provider_profile(normalized_provider)
    if resolved_profile is None:
        raise ExternalProcessingDenied(f"Khong co ExternalAIProviderProfile cho provider '{normalized_provider}'")
    _assert_profile_allows(resolved_profile, normalized_surface)

    chars = max(0, int(input_chars or 0))
    bytes_estimate = max(0, int(input_bytes if input_bytes is not None else chars))
    token_estimate = max(
        0,
        int(input_token_estimate if input_token_estimate is not None else math.ceil(chars / 4)),
    )
    actor = actor_username if actor_username is not None else _ACTOR_USERNAME.get()
    is_admin = actor_is_admin if actor_is_admin is not None else _ACTOR_IS_ADMIN.get()
    return ExternalCallSpec(
        provider=normalized_provider[:100],
        endpoint=(str(endpoint or resolved_profile.endpoint)[:500] if (endpoint or resolved_profile.endpoint) else None),
        model=(str(model or resolved_profile.default_model)[:150] if (model or resolved_profile.default_model) else None),
        surface=normalized_surface,
        trace_id=(str(trace_id or _TRACE_ID.get())[:100] if (trace_id or _TRACE_ID.get()) else None),
        actor_username=(str(actor).strip()[:100] if actor else None),
        actor_is_admin=(None if is_admin is None else bool(is_admin)),
        doc_ids=resolved_doc_ids,
        security_levels=resolved_security_levels,
        policies=normalized_policies,
        policy_version=resolved_profile.policy_version[:100] or None,
        retention_mode=resolved_profile.retention_mode[:100] or None,
        risk_acceptance_ref=resolved_profile.risk_acceptance_ref[:500] or None,
        input_chars=chars,
        input_bytes=bytes_estimate,
        input_token_estimate=token_estimate,
    )


def external_error_metadata(exc: BaseException) -> ExternalAIErrorMetadata:
    status_code = getattr(exc, "status_code", None) or getattr(exc, "status", None)
    try:
        status_code = int(status_code) if status_code is not None else None
    except (TypeError, ValueError):
        status_code = None
    message = str(exc).lower()
    retryable = bool(
        status_code in {408, 409, 425, 429, 500, 502, 503, 504}
        or any(token in message for token in ("timeout", "rate limit", "overload", "no_capacity", "temporarily unavailable"))
    )
    return ExternalAIErrorMetadata(type(exc).__name__, retryable, status_code)


def normalize_text_result(response: Any, *, provider: str, model: str | None, kind: str = "text_generation") -> ExternalAIResult:
    content = getattr(response, "content", None)
    if content is None:
        content = getattr(response, "text", response)
    return ExternalAIResult(kind=kind, provider=provider, model=model, text=str(content or ""))


def normalize_rerank_result(payload: Any, *, provider: str, model: str | None) -> ExternalAIResult:
    if not isinstance(payload, Mapping):
        raise ValueError("Rerank provider response phai la object JSON")
    values = payload.get("data", payload.get("results", []))
    if not isinstance(values, list):
        raise ValueError("Rerank provider response khong co danh sach ket qua hop le")
    items = tuple(item for item in values if isinstance(item, dict))
    return ExternalAIResult(kind="rerank", provider=provider, model=model, items=items)


def _record_external_call(
    spec: ExternalCallSpec,
    status: str,
    latency_ms: float,
    error_type: str | None = None,
) -> bool:
    """Write one metadata-only audit record and report whether it succeeded.

    Callers must use :func:`_preflight_compliance_audit` before an outbound HTTP
    request. Returning ``False`` lets that boundary fail closed in managed
    environments while retaining an explicit local-development escape hatch.
    """
    try:
        from mech_chatbot.db.engine import engine

        if engine is None:
            return False
        values = {
            "trace_id": spec.trace_id,
            "actor_username": spec.actor_username,
            "actor_is_admin": spec.actor_is_admin,
            "provider": spec.provider,
            "endpoint": spec.endpoint,
            "model": spec.model,
            "surface": spec.surface,
            "doc_ids": json.dumps(spec.doc_ids),
            "security": json.dumps(spec.security_levels),
            "policy_version": spec.policy_version,
            "retention_mode": spec.retention_mode,
            "risk_acceptance_ref": spec.risk_acceptance_ref,
            "input_chars": spec.input_chars,
            "input_bytes": spec.input_bytes,
            "input_token_estimate": spec.input_token_estimate,
            "status": str(status)[:30],
            "latency_ms": max(0, int(latency_ms or 0)),
            "error_type": (str(error_type)[:100] if error_type else None),
        }
        with engine.begin() as conn:
            try:
                conn.execute(
                    text(
                        """
                        INSERT INTO dbo.ExternalAICallAudit
                            (TraceID, ActorUsername, ActorIsAdmin, Provider, Endpoint,
                             Model, Surface, DocIDsJson, SecurityJson, PolicyVersion,
                             RetentionMode, RiskAcceptanceRef, InputChars, InputBytes,
                             InputTokenEstimate, Status, LatencyMs, ErrorType)
                        VALUES
                            (:trace_id, :actor_username, :actor_is_admin, :provider, :endpoint,
                             :model, :surface, :doc_ids, :security, :policy_version,
                             :retention_mode, :risk_acceptance_ref, :input_chars, :input_bytes,
                             :input_token_estimate, :status, :latency_ms, :error_type)
                        """
                    ),
                    values,
                )
            except Exception:
                # Existing deployments can receive the code before V0021. Keep
                # the original audit schema functional until migration startup.
                conn.execute(
                    text(
                        """
                        INSERT INTO dbo.ExternalAICallAudit
                            (TraceID, Provider, Model, Surface, DocIDsJson,
                             SecurityJson, InputChars, Status, LatencyMs, ErrorType)
                        VALUES
                            (:trace_id, :provider, :model, :surface, :doc_ids,
                             :security, :input_chars, :status, :latency_ms, :error_type)
                        """
                    ),
                    values,
                )
        return True
    except Exception as exc:
        logger.warning("External AI audit write failed: %s", exc)
        return False


def _preflight_compliance_audit(spec: ExternalCallSpec) -> None:
    """Verify audit persistence before an external request is allowed to start."""

    try:
        written = _record_external_call(spec, status="started", latency_ms=0)
    except Exception:
        written = False
    if written:
        return
    if _is_explicit_local_development():
        logger.warning(
            "External AI compliance audit unavailable; continuing only in explicit local development "
            "(%s=true and %s=%s)",
            _LOCAL_DEVELOPMENT_FLAG,
            _APPLICATION_ENVIRONMENT_FLAG,
            os.getenv(_APPLICATION_ENVIRONMENT_FLAG),
        )
        return
    raise ExternalAIComplianceAuditUnavailable(
        "Khong the ghi compliance audit truoc khi goi external AI; da chan egress"
    )


class ExternalAIClient:
    """One policy/audit entry point shared by provider-specific adapters."""

    def __init__(self, provider: str, profile: ExternalAIProviderProfile | None = None):
        self.provider = str(provider or "").strip().lower()
        self.profile = profile

    def prepare_call(self, **kwargs: Any) -> ExternalCallSpec:
        return make_external_call_spec(provider=self.provider, profile=self.profile, **kwargs)

    @contextmanager
    def audited_call(self, **kwargs: Any):
        spec = self.prepare_call(**kwargs)
        started = time.perf_counter()
        # This is deliberately before handing control to the adapter.  A
        # failed audit store must not result in an untracked provider request
        # in pilot or production.
        _preflight_compliance_audit(spec)
        try:
            yield spec
        except ExternalAICallCancelled:
            _record_external_call(
                spec,
                status="cancelled",
                latency_ms=(time.perf_counter() - started) * 1000,
            )
            raise
        except Exception as exc:
            _record_external_call(
                spec,
                status="error",
                latency_ms=(time.perf_counter() - started) * 1000,
                error_type=external_error_metadata(exc).error_type,
            )
            raise
        else:
            _record_external_call(spec, status="success", latency_ms=(time.perf_counter() - started) * 1000)


@contextmanager
def audited_external_call(**kwargs: Any):
    """Compatibility function; all existing callers now use ``ExternalAIClient``."""
    provider = kwargs.pop("provider", None)
    profile = kwargs.pop("profile", None)
    client = ExternalAIClient(provider, profile=profile)
    with client.audited_call(**kwargs) as spec:
        yield spec


def text_char_count(messages) -> int:
    total = 0
    for item in messages or []:
        content = getattr(item, "content", item)
        try:
            total += len(content) if isinstance(content, str) else len(json.dumps(content))
        except Exception:
            total += len(str(content or ""))
    return total


def text_byte_count(messages) -> int:
    total = 0
    for item in messages or []:
        content = getattr(item, "content", item)
        try:
            serialized = content if isinstance(content, str) else json.dumps(content, ensure_ascii=False)
            total += len(str(serialized).encode("utf-8"))
        except Exception:
            total += len(str(content or "").encode("utf-8"))
    return total


__all__ = [
    "ALL_EXTERNAL",
    "INTERNAL_ONLY",
    "ExternalAICallCancelled",
    "ExternalAIClient",
    "ExternalAIComplianceAuditUnavailable",
    "ExternalAIErrorMetadata",
    "ExternalAIProviderProfile",
    "ExternalAIProviderRuntime",
    "ExternalAIResult",
    "ExternalCallSpec",
    "ExternalProcessingDenied",
    "audited_external_call",
    "external_document_context",
    "external_error_metadata",
    "external_processing_context",
    "get_external_ai_provider_profile",
    "get_provider_runtime",
    "invalidate_external_ai_provider_profiles",
    "make_external_call_spec",
    "normalize_rerank_result",
    "normalize_text_result",
    "text_char_count",
    "text_byte_count",
]
