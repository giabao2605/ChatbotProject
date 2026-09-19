from datetime import datetime, timedelta
from pathlib import Path

import pytest

from mech_chatbot.config.settings import ExternalAiSettings
from mech_chatbot.llm import external_ai


pytestmark = pytest.mark.unit


def _external_settings(
    *,
    environment: str = "",
    local: bool = False,
    policy: str = "all_external",
    execution_context: str = "production",
) -> ExternalAiSettings:
    return ExternalAiSettings(
        application_environment=environment,
        local_development=local,
        processing_policy=policy,
        execution_context=execution_context,
    )


@pytest.fixture(autouse=True)
def _provider_profile_test_boundary():
    yield


def _profile(
    provider="proxyllm",
    surfaces=("generation", "vision_ocr"),
    *,
    expires_in_days=30,
    policy_version="test-v1",
):
    return external_ai.ExternalAIProviderProfile(
        provider=provider,
        endpoint="https://provider.example/v1",
        default_model="model-test",
        secret_reference="env:TEST_API_KEY",
        allowed_surfaces=tuple(surfaces),
        retention_mode="test-retention",
        policy_version=policy_version,
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


def test_policy_comes_from_explicit_settings_snapshot_not_ambient_environment(
    monkeypatch,
):
    monkeypatch.setenv("EXTERNAL_PROCESSING_POLICY", "all_external")

    with pytest.raises(external_ai.ExternalProcessingDenied):
        external_ai.make_external_call_spec(
            provider="voyage",
            model="rerank-2.5-lite",
            surface="reranking",
            profile=_profile("voyage", ("reranking",)),
            settings=_external_settings(policy="internal_only"),
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


def test_evaluation_only_profile_is_allowed_only_in_evaluation_context():
    profile = _profile(
        "jina",
        ("reranking",),
        policy_version="evaluation-only-v1",
    )

    spec = external_ai.make_external_call_spec(
        provider="jina",
        model="jina-reranker-v3",
        surface="reranking",
        profile=profile,
        settings=_external_settings(execution_context="evaluation"),
    )

    assert spec.policy_version == "evaluation-only-v1"


@pytest.mark.parametrize("execution_context", ["production", "local", "test", ""])
def test_evaluation_only_profile_fails_closed_outside_evaluation(
    execution_context,
):
    profile = _profile(
        "jina",
        ("reranking",),
        policy_version="evaluation-only-v1",
    )

    with pytest.raises(
        external_ai.ExternalProcessingDenied,
        match="evaluation",
    ):
        external_ai.make_external_call_spec(
            provider="jina",
            model="jina-reranker-v3",
            surface="reranking",
            profile=profile,
            settings=_external_settings(execution_context=execution_context),
        )


def test_inactive_evaluation_only_profile_fails_closed_in_evaluation():
    profile = external_ai.ExternalAIProviderProfile(
        **{
            **_profile(
                "jina",
                ("reranking",),
                policy_version="evaluation-only-v1",
            ).__dict__,
            "is_active": False,
        }
    )

    with pytest.raises(external_ai.ExternalProcessingDenied, match="dang bi tat"):
        external_ai.make_external_call_spec(
            provider="jina",
            model="jina-reranker-v3",
            surface="reranking",
            profile=profile,
            settings=_external_settings(execution_context="evaluation"),
        )


def test_missing_managed_profile_fails_closed_outside_local_development(monkeypatch):
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

    assert external_ai.get_external_ai_provider_profile(
        "voyage",
        settings=_external_settings(environment="pilot", local=True),
    ) is None

    bootstrap = external_ai.get_external_ai_provider_profile(
        "voyage",
        settings=_external_settings(environment="development", local=True),
    )

    assert bootstrap is not None
    assert bootstrap.review_expires_at is None
    assert bootstrap.risk_acceptance_ref == "notion:92459b78-3e54-4c47-8322-d44ab2b65664"


def test_proxyllm_bootstrap_profile_allows_governed_claim_repair(monkeypatch):
    monkeypatch.setattr(external_ai, "_load_managed_provider_profile", lambda _provider: None)
    settings = _external_settings(environment="development", local=True)

    profile = external_ai.get_external_ai_provider_profile(
        "proxyllm",
        settings=settings,
    )

    assert profile is not None
    client = external_ai.ExternalAIClient(
        "proxyllm",
        profile=profile,
        settings=settings,
    )
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


def test_jina_evaluation_profile_migration_is_additive_metadata_only_and_audited():
    migration = (
        Path(__file__).resolve().parents[2]
        / "database"
        / "migrations"
        / "V0040__managed_jina_rerank_profile.sql"
    ).read_text(encoding="utf-8")

    assert "IF NOT EXISTS (SELECT 1 FROM dbo.ExternalAIProviderProfile WHERE Provider = 'jina')" in migration
    assert "https://api.jina.ai/v1" in migration
    assert "jina-reranker-v3" in migration
    assert "env:JINA_API_KEY" in migration
    assert 'N\'["reranking"]\'' in migration
    assert "provider_default_no_training" in migration
    assert "DATEADD(day, 30, GETDATE())" in migration
    assert "DATEADD(day, 30, GETDATE()), 0" in migration
    assert "evaluation-only" in migration
    assert "019fab5f-2aa9-71d2-9bc1-0ecaf3b6d931" in migration
    assert "external_ai_jina_evaluation_profile_created" in migration
    assert "UPDATE dbo.ExternalAIProviderProfile" not in migration
    assert "JINA_API_KEY=" not in migration
    assert "release_decisions" not in migration


def test_jina_evaluation_activation_migration_is_exact_fail_closed_and_idempotent():
    migration = (
        Path(__file__).resolve().parents[2]
        / "database"
        / "migrations"
        / "V0041__activate_evaluation_only_jina_profile.sql"
    ).read_text(encoding="utf-8")

    for exact_value in (
        "https://api.jina.ai/v1",
        "jina-reranker-v3",
        "env:JINA_API_KEY",
        'N\'["reranking"]\'',
        "provider_default_no_training",
        "evaluation-only-v1",
        "technical-evaluation-only",
        "codex-thread:019fab5f-2aa9-71d2-9bc1-0ecaf3b6d931",
        "V0040 migration",
        "codex-eval-closeout",
    ):
        assert exact_value in migration
    assert "ReviewExpiresAt > GETDATE()" in migration
    assert "ReviewExpiresAt <= DATEADD(day, 30, GETDATE())" in migration
    assert "IsActive = 0" in migration
    assert "AND UpdatedBy = 'V0040 migration'" in migration
    assert "OR (IsActive = 0 AND UpdatedBy = 'codex-eval-closeout')" in migration
    assert "OR (IsActive = 1 AND UpdatedBy = 'V0041 migration')" in migration
    assert "SET IsActive = 1" in migration
    assert "THROW" in migration
    assert "@jina_profile_activated = 1" in migration
    assert "external_ai_jina_evaluation_profile_activated" in migration
    assert "NOT EXISTS" in migration
    assert "JINA_API_KEY=" not in migration
    assert "release_decisions" not in migration


def test_jina_production_activation_migration_is_exact_fail_closed_and_audited():
    migration = (
        Path(__file__).resolve().parents[2]
        / "database"
        / "migrations"
        / "V0042__authorize_jina_production_reranking.sql"
    ).read_text(encoding="utf-8")

    for exact_value in (
        "https://api.jina.ai/v1",
        "jina-reranker-v3",
        "env:JINA_API_KEY",
        'N\'["reranking"]\'',
        "provider_default_no_training",
        "risk-accepted-v1-jina-production",
        "owner-production-approval",
        "owner-decision:2026-07-31:jina-primary-voyage-fallback",
        "V0041 migration",
        "V0042 migration",
    ):
        assert exact_value in migration
    assert "Expected exactly one Jina profile" in migration
    assert "evaluation-only-v1" in migration
    assert "ReviewExpiresAt = DATEADD(day, 90, GETDATE())" in migration
    assert "SET PolicyVersion = 'risk-accepted-v1-jina-production'" in migration
    assert "external_ai_jina_production_authorized" in migration
    assert "JINA_API_KEY=" not in migration
    assert "release_decisions" not in migration


def test_audit_unavailable_blocks_before_external_call_body(monkeypatch):
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
    monkeypatch.setattr(external_ai, "_record_external_call", lambda *args, **kwargs: False)
    called = False

    with external_ai.audited_external_call(
        provider="proxyllm",
        model="gpt-test",
        surface="generation",
        profile=_profile(),
        settings=_external_settings(environment="local", local=True),
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
        resolved_secrets={"TEST_RUNTIME_KEY": "runtime-key"},
    )

    assert runtime.endpoint == "https://provider.example/v1"
    assert runtime.model == "model-test"
    assert runtime.api_key == "runtime-key"


def test_provider_runtime_never_substitutes_a_different_key_for_secret_uri(monkeypatch):
    profile = _profile("voyage", ("reranking",))
    profile = external_ai.ExternalAIProviderProfile(
        **{**profile.__dict__, "secret_reference": "secret://voyage/key"}
    )
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
        resolved_secrets={"UNRELATED_PROVIDER_KEY": "must-not-be-used"},
    )

    assert runtime.api_key is None
