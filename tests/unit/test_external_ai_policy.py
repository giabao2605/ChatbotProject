from datetime import datetime, timedelta
from pathlib import Path

import pytest

from mech_chatbot.llm import external_ai


pytestmark = pytest.mark.unit


@pytest.fixture(autouse=True)
def _clear_provider_profile_cache(monkeypatch):
    monkeypatch.delenv("EXTERNAL_AI_LOCAL_DEVELOPMENT", raising=False)
    monkeypatch.delenv("APP_ENV", raising=False)
    external_ai.invalidate_external_ai_provider_profiles()
    yield
    external_ai.invalidate_external_ai_provider_profiles()


def _profile(provider="proxyllm", surfaces=("generation", "vision_ocr"), *, expires_in_days=30):
    return external_ai.ExternalAIProviderProfile(
        provider=provider,
        endpoint="https://provider.example/v1",
        default_model="model-test",
        secret_reference="env:TEST_API_KEY",
        allowed_surfaces=tuple(surfaces),
        retention_mode="test-retention",
        policy_version="test-v1",
        approved_by="tester",
        risk_acceptance_ref="test:risk-acceptance",
        review_expires_at=datetime.now() + timedelta(days=expires_in_days),
        is_active=True,
    )


def test_all_external_policy_builds_metadata_only_spec():
    spec = external_ai.make_external_call_spec(
        provider="voyage",
        model="rerank-2.5-lite",
        surface="reranking",
        trace_id="trace-1",
        doc_ids=[3, 1, 3, None],
        security_levels=["confidential", "internal"],
        policies=["all_external"],
        input_chars=321,
        profile=_profile("voyage", ("reranking",)),
    )

    assert spec.doc_ids == (1, 3)
    assert spec.security_levels == ("confidential", "internal")
    assert spec.input_chars == 321
    assert spec.input_token_estimate == 81
    assert spec.policy_version == "test-v1"
    assert not hasattr(spec, "prompt")
    assert not hasattr(spec, "content")


def test_internal_only_policy_fails_closed():
    with pytest.raises(external_ai.ExternalProcessingDenied):
        external_ai.make_external_call_spec(
            provider="voyage",
            model="rerank-2.5-lite",
            surface="reranking",
            policies=["all_external", "internal_only"],
            profile=_profile("voyage", ("reranking",)),
        )


def test_audited_call_records_success_and_error(monkeypatch):
    events = []

    def _record(spec, status, latency_ms, error_type=None):
        events.append((status, error_type, spec.surface))
        return True

    monkeypatch.setattr(external_ai, "_record_external_call", _record)

    with external_ai.audited_external_call(
            provider="proxyllm",
            model="gpt-test",
            surface="generation",
            profile=_profile(),
        ):
        pass

    with pytest.raises(ValueError):
        with external_ai.audited_external_call(
            provider="proxyllm",
            model="gpt-test",
            surface="vision_ocr",
            profile=_profile(),
        ):
            raise ValueError("boom")

    assert events == [
        ("started", None, "generation"),
        ("success", None, "generation"),
        ("started", None, "vision_ocr"),
        ("error", "ValueError", "vision_ocr"),
    ]


def test_client_captures_actor_context():
    client = external_ai.ExternalAIClient("proxyllm", profile=_profile(surfaces=("generation",)))

    with external_ai.external_processing_context("alice", True):
        spec = client.prepare_call(model="model-test", surface="generation", input_chars=12)

    assert spec.actor_username == "alice"
    assert spec.actor_is_admin is True
    assert spec.input_bytes == 12


def test_denied_surface_fails_closed():
    client = external_ai.ExternalAIClient("proxyllm", profile=_profile(surfaces=("generation",)))

    with pytest.raises(external_ai.ExternalProcessingDenied, match="Surface"):
        client.prepare_call(model="model-test", surface="vision_ocr")


def test_expired_profile_fails_closed():
    expired = external_ai.ExternalAIClient(
        "proxyllm",
        profile=_profile(expires_in_days=-1),
    )
    with pytest.raises(external_ai.ExternalProcessingDenied, match="qua han"):
        expired.prepare_call(model="model-test", surface="generation")


def test_inactive_profile_fails_closed():
    inactive_profile = external_ai.ExternalAIProviderProfile(
        **{**_profile().__dict__, "is_active": False}
    )
    client = external_ai.ExternalAIClient("proxyllm", profile=inactive_profile)

    with pytest.raises(external_ai.ExternalProcessingDenied, match="dang bi tat"):
        client.prepare_call(model="model-test", surface="generation")


def test_missing_managed_profile_fails_closed_outside_local_development(monkeypatch):
    monkeypatch.delenv("EXTERNAL_AI_LOCAL_DEVELOPMENT", raising=False)
    monkeypatch.setattr(external_ai, "_load_managed_provider_profile", lambda _provider: None)

    assert external_ai.get_external_ai_provider_profile("voyage") is None
    with pytest.raises(external_ai.ExternalProcessingDenied, match="Khong co ExternalAIProviderProfile"):
        external_ai.make_external_call_spec(
            provider="voyage",
            model="rerank-2.5-lite",
            surface="reranking",
            policies=("all_external",),
        )


def test_bootstrap_profile_requires_explicit_local_development_flag(monkeypatch):
    monkeypatch.setattr(external_ai, "_load_managed_provider_profile", lambda _provider: None)
    monkeypatch.setenv("EXTERNAL_AI_LOCAL_DEVELOPMENT", "true")
    monkeypatch.setenv("APP_ENV", "pilot")

    assert external_ai.get_external_ai_provider_profile("voyage") is None

    monkeypatch.setenv("APP_ENV", "development")
    external_ai.invalidate_external_ai_provider_profiles()
    bootstrap = external_ai.get_external_ai_provider_profile("voyage")

    assert bootstrap is not None
    assert bootstrap.review_expires_at is None
    assert bootstrap.risk_acceptance_ref == "notion:92459b78-3e54-4c47-8322-d44ab2b65664"


def test_proxyllm_bootstrap_profile_allows_governed_claim_repair(monkeypatch):
    monkeypatch.setattr(external_ai, "_load_managed_provider_profile", lambda _provider: None)
    monkeypatch.setenv("EXTERNAL_AI_LOCAL_DEVELOPMENT", "true")
    monkeypatch.setenv("APP_ENV", "development")

    profile = external_ai.get_external_ai_provider_profile("proxyllm")

    assert profile is not None
    client = external_ai.ExternalAIClient("proxyllm", profile=profile)
    spec = client.prepare_call(model="model-test", surface="claim_repair")
    assert spec.surface == "claim_repair"
    assert spec.policy_version == "risk-accepted-v4-claim-repair"


def test_claim_repair_profile_migration_is_additive_and_audited():
    migration = (
        Path(__file__).resolve().parents[2]
        / "database"
        / "migrations"
        / "V0035__allow_governed_claim_repair_surface.sql"
    ).read_text(encoding="utf-8")

    assert "JSON_MODIFY" in migration
    assert "claim_repair" in migration
    assert "external_ai_claim_repair_surface_enabled" in migration
    assert "raw prompt" not in migration.lower()
    assert "Expected exactly one proxyllm profile" in migration
    assert "must be a JSON array" in migration
    verification = migration.index("claim_repair surface update could not be verified")
    audit = migration.index("external_ai_claim_repair_surface_enabled")
    assert verification < audit


def test_audit_unavailable_blocks_before_external_call_body(monkeypatch):
    monkeypatch.delenv("EXTERNAL_AI_LOCAL_DEVELOPMENT", raising=False)
    monkeypatch.setattr(external_ai, "_record_external_call", lambda *args, **kwargs: False)
    called = False

    with pytest.raises(external_ai.ExternalAIComplianceAuditUnavailable):
        with external_ai.audited_external_call(
            provider="proxyllm",
            model="gpt-test",
            surface="generation",
            profile=_profile(),
        ):
            called = True

    assert called is False


def test_local_development_may_continue_when_audit_unavailable(monkeypatch):
    monkeypatch.setenv("EXTERNAL_AI_LOCAL_DEVELOPMENT", "true")
    monkeypatch.setenv("APP_ENV", "local")
    monkeypatch.setattr(external_ai, "_record_external_call", lambda *args, **kwargs: False)
    called = False

    with external_ai.audited_external_call(
        provider="proxyllm",
        model="gpt-test",
        surface="generation",
        profile=_profile(),
    ):
        called = True

    assert called is True


def test_external_call_emits_metadata_only_latency_trace(monkeypatch):
    events = []
    monkeypatch.setattr(external_ai, "_record_external_call", lambda *args, **kwargs: True)
    monkeypatch.setattr(
        external_ai,
        "_emit_external_call_trace",
        lambda spec, **kwargs: events.append((spec, kwargs)),
    )

    with external_ai.audited_external_call(
        provider="proxyllm",
        model="gpt-test",
        surface="generation",
        trace_id="trace-router",
        profile=_profile(),
    ):
        pass

    assert len(events) == 1
    spec, metadata = events[0]
    assert spec.trace_id == "trace-router"
    assert metadata["status"] == "success"
    assert metadata["latency_ms"] >= 0
    assert set(metadata) == {"status", "latency_ms", "error_type"}


def test_cancelled_external_call_is_audited_without_error_status(monkeypatch):
    statuses = []

    def _record(spec, status, latency_ms, error_type=None):
        statuses.append((status, error_type))
        return True

    monkeypatch.setattr(external_ai, "_record_external_call", _record)

    with pytest.raises(external_ai.ExternalAICallCancelled):
        with external_ai.audited_external_call(
            provider="proxyllm",
            model="gpt-test",
            surface="generation",
            profile=_profile(),
        ):
            raise external_ai.ExternalAICallCancelled()

    assert statuses == [("started", None), ("cancelled", None)]


def test_result_normalizers_do_not_keep_raw_provider_payload():
    text_result = external_ai.normalize_text_result(
        type("Response", (), {"content": "hello"})(),
        provider="proxyllm",
        model="model-test",
    )
    rerank_result = external_ai.normalize_rerank_result(
        {"data": [{"index": 1, "relevance_score": 0.9}]},
        provider="voyage",
        model="rerank-test",
    )

    assert text_result.text == "hello"
    assert rerank_result.items == ({"index": 1, "relevance_score": 0.9},)
    assert not hasattr(rerank_result, "raw_payload")


def test_provider_runtime_uses_the_profile_secret_reference(monkeypatch):
    profile = _profile("voyage", ("reranking",))
    profile = external_ai.ExternalAIProviderProfile(
        **{**profile.__dict__, "secret_reference": "env:TEST_RUNTIME_KEY"}
    )
    monkeypatch.setenv("TEST_RUNTIME_KEY", "runtime-key")
    monkeypatch.setattr(
        external_ai,
        "_load_managed_provider_profile",
        lambda _provider: profile.__dict__,
    )

    runtime = external_ai.get_provider_runtime(
        "voyage",
        fallback_endpoint="https://fallback.invalid/v1",
        fallback_model="fallback-model",
        fallback_secret_envs=("UNRELATED_PROVIDER_KEY",),
    )

    assert runtime.endpoint == "https://provider.example/v1"
    assert runtime.model == "model-test"
    assert runtime.api_key == "runtime-key"


def test_provider_runtime_never_substitutes_a_different_key_for_secret_uri(monkeypatch):
    profile = _profile("voyage", ("reranking",))
    profile = external_ai.ExternalAIProviderProfile(
        **{**profile.__dict__, "secret_reference": "secret://voyage/key"}
    )
    monkeypatch.setenv("UNRELATED_PROVIDER_KEY", "must-not-be-used")
    monkeypatch.setattr(
        external_ai,
        "_load_managed_provider_profile",
        lambda _provider: profile.__dict__,
    )

    runtime = external_ai.get_provider_runtime(
        "voyage",
        fallback_endpoint="https://fallback.invalid/v1",
        fallback_model="fallback-model",
        fallback_secret_envs=("UNRELATED_PROVIDER_KEY",),
    )

    assert runtime.api_key is None
