from dataclasses import dataclass
import json

import pytest

from mech_chatbot.config.settings import Settings
from mech_chatbot.config.repository_runtime import (
    bind_repository_runtime,
    current_repository_engine,
    current_qdrant_runtime,
)
from mech_chatbot.composition import maintenance_runtime
from scripts.eval.provider_smoke import (
    provider_environment_for_settings,
    provider_configuration_sha256,
    resolve_provider_configuration,
    run_configured_provider_smoke,
    validate_provider_smoke_artifact,
)


pytestmark = pytest.mark.unit


def _settings_snapshot() -> Settings:
    return Settings.from_env(
        {
            "PROXYLLM_API_KEY": "test-provider-key",
            "PROXYLLM_BASE_URL": "https://provider.example/v1",
            "GPT_MODEL_NAME": "snapshot-model",
            "MAX_CONCURRENT_RAG": "7",
        }
    )


@dataclass(frozen=True)
class _FakeAdapterSettings:
    base_url: str
    model_name: str


@dataclass(frozen=True)
class _FakeAdapter:
    settings: _FakeAdapterSettings
    calls: list

    def invoke(self, _messages, **kwargs):
        self.calls.append(kwargs)
        return "OK"


def test_provider_configuration_uses_snapshot_without_building_client(monkeypatch):
    from mech_chatbot.llm import llm_client

    settings = _settings_snapshot()
    monkeypatch.setattr(
        llm_client,
        "build_llm_adapter",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("metadata resolution must not build a provider client")
        ),
    )

    assert resolve_provider_configuration(settings) == {
        "endpoint": "https://provider.example/v1",
        "model": "snapshot-model",
        "max_concurrent_rag": 7,
    }


def test_provider_configuration_hash_matches_adapter_after_whitespace_normalization():
    settings = Settings.from_env(
        {
            "PROXYLLM_API_KEY": "test-provider-key",
            "PROXYLLM_BASE_URL": "  https://provider.example/v1  ",
            "GPT_MODEL_NAME": "  snapshot-model  ",
            "MAX_CONCURRENT_RAG": "7",
        }
    )
    adapter = _FakeAdapter(
        settings=_FakeAdapterSettings(
            base_url="https://provider.example/v1",
            model_name="snapshot-model",
        ),
        calls=[],
    )

    settings_configuration = resolve_provider_configuration(settings)
    adapter_configuration = resolve_provider_configuration(settings, adapter)

    assert settings_configuration == {
        "endpoint": "https://provider.example/v1",
        "model": "snapshot-model",
        "max_concurrent_rag": 7,
    }
    assert provider_configuration_sha256(settings_configuration) == (
        provider_configuration_sha256(adapter_configuration)
    )


def test_provider_environment_is_frozen_from_the_same_settings_snapshot():
    assert provider_environment_for_settings(_settings_snapshot()) == {
        "PROXYLLM_API_KEY": "test-provider-key",
        "PROXYLLM_BASE_URL": "https://provider.example/v1",
        "GPT_MODEL_NAME": "snapshot-model",
        "MAX_CONCURRENT_RAG": "7",
    }


def test_provider_smoke_validator_rejects_malformed_provider_outcome(tmp_path):
    path = tmp_path / "provider-smoke.json"
    path.write_text(
        json.dumps({
            "schema": "provider-smoke-v1",
            "passed": True,
            "request_count": 5,
            "successful_requests": 5,
            "failed_requests": 0,
            "provider_retries": 0,
            "provider_configuration_sha256": "provider-v1",
            "provider_outcome": "malformed",
        }),
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="provider smoke artifact"):
        validate_provider_smoke_artifact(
            path,
            expected_provider_sha256="provider-v1",
        )


def test_configured_smoke_builds_and_uses_snapshot_owned_adapter():
    settings = _settings_snapshot()
    adapter = _FakeAdapter(
        settings=_FakeAdapterSettings(
            base_url="https://provider.example/v1",
            model_name="snapshot-model",
        ),
        calls=[],
    )
    captured = {}

    def build_adapter(llm_settings, *, external_ai_settings):
        captured["llm_settings"] = llm_settings
        captured["external_ai_settings"] = external_ai_settings
        return adapter

    artifact, configuration = run_configured_provider_smoke(
        settings,
        adapter_builder=build_adapter,
    )

    assert artifact["passed"] is True
    assert len(adapter.calls) == 5
    assert configuration["model"] == "snapshot-model"
    assert captured["llm_settings"].api_key == "test-provider-key"
    assert captured["external_ai_settings"].application_environment == settings.APP_ENV


def test_cli_repository_runtime_uses_the_supplied_settings_snapshot(monkeypatch):
    settings = _settings_snapshot()
    closed = []

    @dataclass(frozen=True)
    class FakeDatabaseRuntime:
        engine: object

        def close(self):
            closed.append("sql")

    database = FakeDatabaseRuntime(engine=object())
    monkeypatch.setattr(
        maintenance_runtime.SqlSettings,
        "from_settings",
        lambda value: value,
    )
    monkeypatch.setattr(
        maintenance_runtime,
        "build_database_runtime",
        lambda value: database if value is settings else None,
    )

    with maintenance_runtime.configured_repository_runtime(
        settings,
        include_qdrant=False,
    ):
        assert current_repository_engine() is database.engine

    assert closed == ["sql"]
    assert current_repository_engine() is None


def test_cli_repository_runtime_closes_all_owned_resources_when_body_raises(
    monkeypatch,
):
    settings = _settings_snapshot()
    closed = []

    @dataclass(frozen=True)
    class FakeDatabaseRuntime:
        engine: object

        def close(self):
            closed.append("sql")

    @dataclass(frozen=True)
    class FakeQdrantRuntime:
        client: object
        collection_name: str

        def close(self):
            closed.append("qdrant")

    database = FakeDatabaseRuntime(engine=object())
    qdrant = FakeQdrantRuntime(
        client=object(),
        collection_name="KnowledgeBase",
    )
    monkeypatch.setattr(
        maintenance_runtime.SqlSettings,
        "from_settings",
        lambda value: value,
    )
    monkeypatch.setattr(
        maintenance_runtime.QdrantSettings,
        "from_settings",
        lambda value: value,
    )
    monkeypatch.setattr(
        maintenance_runtime,
        "build_database_runtime",
        lambda value: database if value is settings else None,
    )
    monkeypatch.setattr(
        maintenance_runtime,
        "build_qdrant_admin_runtime",
        lambda value: qdrant if value is settings else None,
    )

    with pytest.raises(RuntimeError, match="smoke body failed"):
        with maintenance_runtime.configured_repository_runtime(
            settings,
            include_qdrant=True,
        ):
            assert current_repository_engine() is database.engine
            assert current_qdrant_runtime() == (
                qdrant.client,
                "KnowledgeBase",
            )
            raise RuntimeError("smoke body failed")

    assert closed == ["qdrant", "sql"]
    assert current_repository_engine() is None
    assert current_qdrant_runtime() == (None, None)


def test_cli_repository_runtime_closes_sql_when_qdrant_close_raises(
    monkeypatch,
):
    settings = _settings_snapshot()
    closed = []

    @dataclass(frozen=True)
    class FakeDatabaseRuntime:
        engine: object

        def close(self):
            closed.append("sql")

    @dataclass(frozen=True)
    class FakeQdrantRuntime:
        client: object
        collection_name: str

        def close(self):
            closed.append("qdrant")
            raise RuntimeError("qdrant close failed")

    database = FakeDatabaseRuntime(engine=object())
    qdrant = FakeQdrantRuntime(
        client=object(),
        collection_name="KnowledgeBase",
    )
    monkeypatch.setattr(
        maintenance_runtime.SqlSettings,
        "from_settings",
        lambda value: value,
    )
    monkeypatch.setattr(
        maintenance_runtime.QdrantSettings,
        "from_settings",
        lambda value: value,
    )
    monkeypatch.setattr(
        maintenance_runtime,
        "build_database_runtime",
        lambda value: database if value is settings else None,
    )
    monkeypatch.setattr(
        maintenance_runtime,
        "build_qdrant_admin_runtime",
        lambda value: qdrant if value is settings else None,
    )

    with pytest.raises(RuntimeError, match="qdrant close failed"):
        with maintenance_runtime.configured_repository_runtime(
            settings,
            include_qdrant=True,
        ):
            pass

    assert closed == ["qdrant", "sql"]
    assert current_repository_engine() is None
    assert current_qdrant_runtime() == (None, None)


def test_decorated_cli_reuses_bound_runtime_without_reloading_settings(monkeypatch):
    engine = object()
    monkeypatch.setattr(
        maintenance_runtime,
        "load_settings",
        lambda: (_ for _ in ()).throw(
            AssertionError("bound CLI runtime must not reload settings")
        ),
    )

    @maintenance_runtime.with_configured_repository_runtime(
        include_qdrant=False
    )
    def operation():
        return current_repository_engine()

    with bind_repository_runtime(db_engine=engine):
        assert operation() is engine
