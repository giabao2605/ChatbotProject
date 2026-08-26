from dataclasses import FrozenInstanceError
from datetime import datetime, timedelta
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from mech_chatbot.config.settings import Settings
from mech_chatbot.rag.execution import (
    AccessScope,
    RagCompleted,
    RagInvocation,
    RagPrepared,
    RagRequest,
    RagToken,
)


pytestmark = pytest.mark.unit


def test_build_rag_runtime_keeps_settings_and_executes_through_public_seam():
    from mech_chatbot.composition.rag_runtime import build_rag_runtime

    settings = SimpleNamespace(profile="phase-4-test")
    observed = []
    retrieval_calls = []
    provider_calls = []

    def execute_pipeline(state):
        observed.append((state.request.question, state.trace_id))
        state.retrieve(query="bearing")
        state.invoke_provider("prompt", surface="phase-4-test")
        return state.prepared((iter(["answer"]), "refs", [], ["P-1"], {}))

    retrieval = SimpleNamespace(
        retrieve=lambda **kwargs: retrieval_calls.append(kwargs) or (),
    )
    provider = SimpleNamespace(
        invoke=lambda *args, **kwargs: provider_calls.append((args, kwargs)),
    )
    runtime = build_rag_runtime(
        settings,
        execute_pipeline=execute_pipeline,
        retrieval=retrieval,
        provider=provider,
    )

    events = list(
        runtime.executor.run(
            RagRequest("question", AccessScope()),
            RagInvocation(trace_id="phase-4-runtime", mode="test"),
        )
    )

    assert runtime.settings is settings
    assert runtime.retrieval is retrieval
    assert runtime.provider is provider
    assert observed == [("question", "phase-4-runtime")]
    assert retrieval_calls == [{"query": "bearing"}]
    assert provider_calls == [(("prompt",), {"surface": "phase-4-test"})]
    assert [type(event) for event in events] == [RagPrepared, RagToken, RagCompleted]
    assert events[0].ref_text == "refs"
    assert events[1].text == "answer"


def test_rag_runtime_bundle_is_frozen():
    from mech_chatbot.composition.rag_runtime import build_rag_runtime

    runtime = build_rag_runtime(
        object(),
        execute_pipeline=lambda state: state.prepared((iter(()), "", [], [], {})),
        retrieval=object(),
        provider=object(),
    )

    with pytest.raises(FrozenInstanceError):
        runtime.settings = object()


def test_rag_runtime_owns_process_concurrency_until_close():
    from mech_chatbot.composition.rag_runtime import build_rag_runtime

    shutdown_calls = []

    class RecordingPool:
        def shutdown(self, *, wait, cancel_futures):
            shutdown_calls.append((wait, cancel_futures))

    semaphore = object()
    worker_counts = []
    pool = RecordingPool()
    runtime = build_rag_runtime(
        Settings(MAX_CONCURRENT_RAG=3),
        execute_pipeline=lambda state: state.prepared((iter(()), "", [], [], {})),
        retrieval=object(),
        provider=object(),
        semaphore_factory=lambda permits: (permits, semaphore),
        thread_pool_factory=lambda workers: worker_counts.append(workers) or pool,
    )

    assert runtime.process_settings.max_concurrent_requests == 3
    assert runtime.semaphore == (3, semaphore)
    assert runtime.thread_pool is pool
    assert worker_counts == [3]

    runtime.close()

    assert shutdown_calls == [(True, True)]


def test_rag_runtime_projects_health_contract_from_one_settings_snapshot():
    from mech_chatbot.composition.rag_runtime import build_rag_runtime

    runtime = build_rag_runtime(
        Settings(
            RAG_EXECUTION_CONTEXT="evaluation",
            EVALUATION_FORCE_AMBIGUOUS=True,
            RAG_REQUEST_DEADLINE_SECONDS=90.0,
        ),
        execute_pipeline=lambda state: state.prepared((iter(()), "", [], [], {})),
        retrieval=object(),
        provider=object(),
    )

    assert runtime.runtime_contract.to_dict() == {
        "execution_context": "evaluation",
        "evaluation_force_ambiguous": True,
        "request_deadline_seconds": 90.0,
    }


def test_rag_runtime_binds_trace_events_to_the_live_runtime_identity():
    from mech_chatbot.composition.rag_runtime import build_rag_runtime
    from mech_chatbot.config.logging import runtime_identity_sha256
    from mech_chatbot.governance.feature_activation import FEATURE_FLAGS
    from mech_chatbot.governance.provider_smoke import (
        provider_configuration_sha256_for_settings,
    )

    settings = Settings(
        RAG_DEPLOYMENT_GIT_SHA="abc123",
        RAG_DEPLOYMENT_ID="math-pilot",
        RAG_ACTIVATION_PROFILE="selective",
        RAG_GROUNDED_MATH_ENABLED=True,
        RAG_SNAPSHOT_FINGERPRINT="snapshot-1",
        RAG_ACTIVATION_BUNDLE_SHA256="a" * 64,
        RAG_RESTORE_EVIDENCE_SHA256="b" * 64,
        RAG_REQUEST_DEADLINE_SECONDS=90.0,
        SQL_DATABASE="pilot-sql",
        QDRANT_COLLECTION="configured-collection",
        QDRANT_SEARCH_TIMEOUT_SECONDS=11,
        LLM_BASE_URL="https://llm.invalid/v1",
    )
    retrieval = SimpleNamespace(collection_name="live-collection")
    runtime = build_rag_runtime(
        settings,
        execute_pipeline=lambda state: state.prepared((iter(()), "", [], [], {})),
        retrieval=retrieval,
        provider=object(),
        trace_persist=lambda *_args: None,
    )

    assert runtime.trace_runtime.runtime_identity_sha256 == runtime_identity_sha256({
        "git_sha": "abc123",
        "deployment_id": "math-pilot",
        "activation_profile": "selective",
        "feature_flags": {
            name: bool(getattr(settings, name)) for name in FEATURE_FLAGS
        },
        "snapshot_fingerprint": "snapshot-1",
        "provider_configuration_sha256": (
            provider_configuration_sha256_for_settings(settings)
        ),
        "qdrant_collection": "live-collection",
        "qdrant_search_timeout_seconds": 11,
        "sql_database": "pilot-sql",
        "activation_bundle_sha256": "a" * 64,
        "restore_evidence_sha256": "b" * 64,
        "request_deadline_seconds": 90.0,
    })


def test_rag_runtime_composes_provider_vector_and_vision_adapters_explicitly():
    from mech_chatbot.composition.rag_runtime import build_rag_runtime

    qdrant_client = SimpleNamespace(close=lambda: None)
    vectorstore = SimpleNamespace(embeddings=object())
    provider = SimpleNamespace(invoke=lambda *_args, **_kwargs: None)
    vision_model = object()
    observed = {}
    credentials = {
        "QDRANT_" + "API_KEY": "qdrant-runtime-value",
        "LLM_" + "API_KEY": "llm-runtime-value",
    }
    settings = Settings(
        QDRANT_URL="https://qdrant.invalid",
        USE_VOYAGE_RERANK=False,
        **credentials,
    )

    def build_qdrant(projected):
        observed["qdrant"] = projected
        return SimpleNamespace(
            qdrant_client=qdrant_client,
            vector_store=vectorstore,
            collection_name=projected.collection,
        )

    def build_llm(projected):
        observed["llm"] = projected
        return provider

    def build_vision(projected):
        observed["vision"] = projected
        return vision_model

    runtime = build_rag_runtime(
        settings,
        execute_pipeline=lambda state: state.prepared((iter(()), "", [], [], {})),
        qdrant_builder=build_qdrant,
        llm_builder=build_llm,
        vision_builder=build_vision,
    )

    assert runtime.retrieval.client is qdrant_client
    assert runtime.retrieval.vectorstore is vectorstore
    assert runtime.retrieval.vision_model is vision_model
    assert runtime.retrieval.collection_name == settings.QDRANT_COLLECTION
    assert runtime.retrieval.late_query_encoder is None
    assert runtime.provider is provider
    assert set(observed) == {"qdrant", "llm", "vision"}

    runtime.close()


def test_rag_runtime_binds_configured_qdrant_timeout_to_batch_retrieval():
    from qdrant_client import models

    from mech_chatbot.composition.rag_runtime import build_rag_runtime

    calls = []

    class Client:
        def query_batch_points(self, **kwargs):
            calls.append(kwargs)
            return [SimpleNamespace(points=[]) for _ in kwargs["requests"]]

        def close(self):
            return None

    vectorstore = SimpleNamespace(
        embeddings=SimpleNamespace(embed_query=lambda _text: [0.1, 0.2]),
        sparse_embeddings=SimpleNamespace(
            embed_query=lambda _text: SimpleNamespace(
                indices=[1],
                values=[1.0],
            )
        ),
        vector_name="",
        sparse_vector_name="sparse",
        content_payload_key="page_content",
        metadata_payload_key="metadata",
    )
    runtime = build_rag_runtime(
        Settings(
            QDRANT_SEARCH_TIMEOUT_SECONDS=11,
            RERANK_PROVIDER="local_fusion",
            USE_VOYAGE_RERANK=False,
        ),
        execute_pipeline=lambda state: state.prepared((iter(()), "", [], [], {})),
        qdrant_builder=lambda settings: SimpleNamespace(
            qdrant_client=Client(),
            vector_store=vectorstore,
            collection_name=settings.collection,
        ),
        llm_builder=lambda _settings: SimpleNamespace(
            invoke=lambda *_args, **_kwargs: None
        ),
        vision_builder=lambda _settings: object(),
        intent_runtime_builder=lambda **_kwargs: None,
    )

    runtime.retrieval.retrieve_many(
        (
            {
                "new_part_ids": [],
                "strict_filter": models.Filter(),
                "broad_filter": models.Filter(),
                "is_bom_query": False,
                "query_to_search": "query 1",
                "rbac_filter": models.Filter(),
                "trace_id": "runtime-timeout-trace",
            },
        )
    )

    assert [call["timeout"] for call in calls] == [11, 11]

    runtime.close()


def test_rag_runtime_owns_one_semantic_router_prototype_index():
    from mech_chatbot.composition.rag_runtime import build_rag_runtime
    from mech_chatbot.rag import interaction_router, route_config

    calls = []

    def embed(text):
        calls.append(text)
        return [1.0, 0.0]

    runtime = build_rag_runtime(
        Settings(
            RERANK_PROVIDER="local_fusion",
            USE_VOYAGE_RERANK=False,
        ),
        execute_pipeline=lambda state: state.prepared((iter(()), "", [], [], {})),
        qdrant_builder=lambda settings: SimpleNamespace(
            qdrant_client=SimpleNamespace(close=lambda: None),
            vector_store=SimpleNamespace(
                embeddings=SimpleNamespace(embed_query=embed),
            ),
            collection_name=settings.collection,
        ),
        llm_builder=lambda _settings: SimpleNamespace(
            invoke=lambda *_args, **_kwargs: None
        ),
        vision_builder=lambda _settings: object(),
        intent_runtime_builder=lambda **_kwargs: None,
    )

    prototype_count = sum(len(items) for items in route_config.ROUTE_PROTOTYPES.values())
    assert len(calls) == prototype_count

    for question in ("ambiguous alpha", "ambiguous beta"):
        interaction_router.classify(
            question,
            embedder=embed,
            llm_classifier=lambda *_args: None,
            semantic_router=runtime.retrieval.semantic_router,
        )

    assert len(calls) == prototype_count + 2

    runtime.close()


def test_rag_runtime_resolves_explicit_jina_provider_without_exposing_key():
    from mech_chatbot.composition.rag_runtime import build_rag_runtime

    jina_secret = "jina-" + "runtime-value"
    voyage_secret = "voyage-" + "runtime-value"
    observed = {}
    qdrant_runtime = SimpleNamespace(
        qdrant_client=SimpleNamespace(close=lambda: None),
        vector_store=object(),
        collection_name="knowledge",
    )
    settings = Settings.from_env(
        {
            "QDRANT_URL": "https://qdrant.invalid",
            "QDRANT_API_KEY": "qdrant-value",
            "LLM_BASE_URL": "https://llm.invalid",
            "LLM_API_KEY": "llm-value",
            "RERANK_PROVIDER": "jina",
            "JINA_API_KEY": jina_secret,
            "VOYAGE_API_KEY": voyage_secret,
            "USE_VOYAGE_RERANK": "true",
            "EXTERNAL_PROCESSING_POLICY": "all_external",
        }
    )

    def build_jina(provider_name, **kwargs):
        observed["jina"] = {"provider_name": provider_name, **kwargs}
        return SimpleNamespace(
            api_key=kwargs["resolved_secrets"]["JINA_API_KEY"],
            model=kwargs["fallback_model"],
            endpoint=kwargs["fallback_endpoint"],
            settings=kwargs["settings"],
        )

    def build_voyage(provider_name, **kwargs):
        observed["voyage"] = {"provider_name": provider_name, **kwargs}
        return SimpleNamespace(
            api_key=kwargs["resolved_secrets"]["VOYAGE_API_KEY"],
            model=kwargs["fallback_model"],
            endpoint=kwargs["fallback_endpoint"],
            settings=kwargs["settings"],
        )

    runtime = build_rag_runtime(
        settings,
        execute_pipeline=lambda state: state.prepared((iter(()), "", [], [], {})),
        qdrant_builder=lambda _settings: qdrant_runtime,
        llm_builder=lambda _settings: SimpleNamespace(
            invoke=lambda *_args, **_kwargs: None
        ),
        vision_builder=lambda _settings: object(),
        intent_runtime_builder=lambda **_kwargs: None,
        voyage_runtime_builder=build_voyage,
        jina_runtime_builder=build_jina,
    )

    assert observed["jina"]["provider_name"] == "jina"
    assert observed["jina"]["fallback_endpoint"] == "https://api.jina.ai/v1"
    assert observed["jina"]["fallback_model"] == "jina-reranker-v3"
    assert observed["jina"]["resolved_secrets"] == {"JINA_API_KEY": jina_secret}
    assert observed["voyage"]["provider_name"] == "voyage"
    assert observed["voyage"]["fallback_endpoint"] == "https://api.voyageai.com/v1"
    assert observed["voyage"]["fallback_model"] == "rerank-2.5-lite"
    assert observed["voyage"]["resolved_secrets"] == {
        "VOYAGE_API_KEY": voyage_secret
    }
    assert runtime.retrieval.rerank_provider == "jina"
    assert runtime.retrieval.rerank_runtime.api_key == jina_secret
    assert runtime.retrieval.voyage_runtime.api_key == voyage_secret
    assert runtime.retrieval.rerank_runtime.settings.processing_policy == "all_external"
    assert jina_secret not in repr(runtime)
    assert voyage_secret not in repr(runtime)
    assert jina_secret not in repr(runtime.retrieval)
    assert voyage_secret not in repr(runtime.retrieval)

    runtime.close()


def test_rag_runtime_uses_voyage_when_jina_runtime_has_no_key():
    from mech_chatbot.composition.rag_runtime import build_rag_runtime

    voyage_runtime = SimpleNamespace(api_key="configured")
    runtime = build_rag_runtime(
        Settings(
            RERANK_PROVIDER="jina",
            JINA_API_KEY="configured",
            VOYAGE_API_KEY="configured",
            USE_VOYAGE_RERANK=True,
        ),
        execute_pipeline=lambda state: state.prepared((iter(()), "", [], [], {})),
        qdrant_builder=lambda settings: SimpleNamespace(
            qdrant_client=SimpleNamespace(close=lambda: None),
            vector_store=object(),
            collection_name=settings.collection,
        ),
        llm_builder=lambda _settings: object(),
        vision_builder=lambda _settings: object(),
        intent_runtime_builder=lambda **_kwargs: None,
        jina_runtime_builder=lambda *_args, **_kwargs: SimpleNamespace(api_key=None),
        voyage_runtime_builder=lambda *_args, **_kwargs: voyage_runtime,
    )

    assert runtime.retrieval.rerank_provider == "voyage"
    assert runtime.retrieval.rerank_runtime is voyage_runtime
    assert runtime.retrieval.rerank_enabled is True

    runtime.close()


def test_rag_runtime_uses_voyage_for_evaluation_only_jina_in_production():
    from mech_chatbot.composition.rag_runtime import build_rag_runtime

    voyage_runtime = SimpleNamespace(api_key="configured")

    def build_jina(_provider_name, **kwargs):
        return SimpleNamespace(
            api_key="configured",
            endpoint="https://api.jina.ai/v1",
            model="jina-reranker-v3",
            profile=SimpleNamespace(
                provider="jina",
                is_active=True,
                policy_version="evaluation-only-v1",
                review_expires_at=datetime.now() + timedelta(days=1),
                allowed_surfaces=("reranking",),
            ),
            settings=kwargs["settings"],
        )

    runtime = build_rag_runtime(
        Settings(
            RAG_EXECUTION_CONTEXT="production",
            RERANK_PROVIDER="jina",
            JINA_API_KEY="configured",
            VOYAGE_API_KEY="configured",
            USE_VOYAGE_RERANK=True,
        ),
        execute_pipeline=lambda state: state.prepared((iter(()), "", [], [], {})),
        qdrant_builder=lambda settings: SimpleNamespace(
            qdrant_client=SimpleNamespace(close=lambda: None),
            vector_store=object(),
            collection_name=settings.collection,
        ),
        llm_builder=lambda _settings: object(),
        vision_builder=lambda _settings: object(),
        intent_runtime_builder=lambda **_kwargs: None,
        jina_runtime_builder=build_jina,
        voyage_runtime_builder=lambda *_args, **_kwargs: voyage_runtime,
    )

    assert runtime.retrieval.rerank_provider == "voyage"
    assert runtime.retrieval.rerank_runtime is voyage_runtime

    runtime.close()


def test_rag_runtime_uses_production_authorized_jina_as_primary():
    from mech_chatbot.composition.rag_runtime import build_rag_runtime

    def build_jina(_provider_name, **kwargs):
        return SimpleNamespace(
            api_key="configured",
            endpoint="https://api.jina.ai/v1",
            model="jina-reranker-v3",
            profile=SimpleNamespace(
                provider="jina",
                is_active=True,
                policy_version="risk-accepted-v1-jina-production",
                review_expires_at=datetime.now() + timedelta(days=90),
                allowed_surfaces=("reranking",),
                retention_mode="provider_default_no_training",
                risk_acceptance_ref=(
                    "owner-decision:2026-07-31:jina-primary-voyage-fallback"
                ),
            ),
            settings=kwargs["settings"],
        )

    runtime = build_rag_runtime(
        Settings(
            RAG_EXECUTION_CONTEXT="production",
            RERANK_PROVIDER="jina",
            JINA_API_KEY="configured",
            VOYAGE_API_KEY="configured",
            USE_VOYAGE_RERANK=True,
        ),
        execute_pipeline=lambda state: state.prepared((iter(()), "", [], [], {})),
        qdrant_builder=lambda settings: SimpleNamespace(
            qdrant_client=SimpleNamespace(close=lambda: None),
            vector_store=object(),
            collection_name=settings.collection,
        ),
        llm_builder=lambda _settings: object(),
        vision_builder=lambda _settings: object(),
        intent_runtime_builder=lambda **_kwargs: None,
        jina_runtime_builder=build_jina,
        voyage_runtime_builder=lambda *_args, **_kwargs: SimpleNamespace(
            api_key="configured"
        ),
    )

    assert runtime.retrieval.rerank_provider == "jina"
    assert runtime.retrieval.rerank_runtime.model == "jina-reranker-v3"

    runtime.close()


def test_rag_runtime_rerank_provider_defaults_to_jina_and_can_disable_external():
    assert Settings().RERANK_PROVIDER == "jina"

    local_settings = Settings(
        RERANK_PROVIDER="local_fusion",
        VOYAGE_API_KEY="unused",
        JINA_API_KEY="unused",
    )

    assert local_settings.RERANK_PROVIDER == "local_fusion"


def test_rag_runtime_loads_late_encoder_only_when_both_activation_flags_are_on():
    from mech_chatbot.composition.rag_runtime import build_rag_runtime

    encoder = SimpleNamespace(
        encode=lambda *_args, **_kwargs: {"colbert_vecs": [[[1.0, 2.0]]]},
    )
    build_calls = []
    qdrant_runtime = SimpleNamespace(
        qdrant_client=SimpleNamespace(close=lambda: None),
        vector_store=object(),
        collection_name="knowledge",
    )
    provider = SimpleNamespace(invoke=lambda *_args, **_kwargs: None)
    builders = {
        "qdrant_builder": lambda _settings: qdrant_runtime,
        "llm_builder": lambda _settings: provider,
        "vision_builder": lambda _settings: object(),
        "late_encoder_builder": lambda config: build_calls.append(config) or encoder,
    }

    disabled = build_rag_runtime(
        Settings(USE_VOYAGE_RERANK=False),
        execute_pipeline=lambda state: state.prepared((iter(()), "", [], [], {})),
        **builders,
    )
    enabled = build_rag_runtime(
        Settings(
            RAG_LATE_INTERACTION_ENABLED=True,
            RAG_LATE_ENCODER_READY=True,
            USE_VOYAGE_RERANK=False,
        ),
        execute_pipeline=lambda state: state.prepared((iter(()), "", [], [], {})),
        **builders,
    )

    assert disabled.retrieval.late_query_encoder is None
    assert enabled.retrieval.late_query_encoder("bearing") is not None
    assert len(build_calls) == 1

    disabled.close()
    enabled.close()


def test_rag_server_opens_requests_through_composed_runtime(monkeypatch):
    from mech_chatbot.api import rag_server

    sentinel = object()
    observed = []

    class ScriptedExecutor:
        def run(self, request, invocation, cancellation):
            observed.append((request.question, invocation.trace_id, cancellation))
            return sentinel

    runtime = SimpleNamespace(executor=ScriptedExecutor())
    cancellation = object()
    result = rag_server._open_rag_events(
        rag_server.ChatRequest(user_question="runtime question"),
        {
            "department": "Technical",
            "roles": ["viewer"],
            "allowed_departments": ["Technical"],
            "max_security_level": "internal",
            "allowed_sites": ["HCM"],
        },
        "runtime-trace",
        cancellation,
        mode="test",
        runtime=runtime,
    )

    assert result is sentinel
    assert observed == [("runtime question", "runtime-trace", cancellation)]


def test_rag_app_lifespan_builds_reports_and_closes_one_runtime(
    monkeypatch, tmp_path
):
    from mech_chatbot.api import rag_server
    from mech_chatbot.config.logging import LoggingConfig
    from mech_chatbot.config.repository_runtime import current_repository_engine
    from mech_chatbot.rag import rerank
    from mech_chatbot.rag.execution_contracts import RagRuntimeContract

    closed = []
    database_closed = []
    observed_settings = []
    observed_sql_settings = []
    observed_logging = []
    database_engine = object()
    runtime_identity_hash = "c" * 64
    credentials = {
        "QDRANT_" + "API_KEY": "qdrant-lifespan-value",
        "LLM_" + "API_KEY": "llm-lifespan-value",
    }
    settings = Settings(
        QDRANT_URL="https://qdrant.invalid",
        LLM_BASE_URL="https://llm.invalid",
        RAG_REQUIRE_SERVICE_AUTH=False,
        RAG_TRACE_LOG_FILE=str(tmp_path / "runtime-trace.jsonl"),
        SQL_DATABASE="math_pilot_evidence",
        **credentials,
    )
    runtime = SimpleNamespace(
        semaphore=SimpleNamespace(_value=2),
        runtime_contract=RagRuntimeContract("production", False, 120.0),
        trace_runtime=SimpleNamespace(
            runtime_identity_sha256=runtime_identity_hash,
        ),
        close=lambda: closed.append(True),
    )
    monkeypatch.setattr(rerank, "tokenize_cached", lambda _text: "tokens")
    monkeypatch.setattr(
        rag_server,
        "configure_logging",
        lambda config: observed_logging.append(config),
        raising=False,
    )

    def build_runtime(snapshot):
        assert current_repository_engine() is database_engine
        observed_settings.append(snapshot)
        return runtime

    application = rag_server.create_rag_app(
        settings,
        runtime_builder=build_runtime,
        database_builder=lambda sql_settings: (
            observed_sql_settings.append(sql_settings)
            or SimpleNamespace(
                engine=database_engine,
                close=lambda: database_closed.append(True),
            )
        ),
    )

    @application.get("/_phase6/repository-binding")
    def repository_binding():
        return {"bound": current_repository_engine() is database_engine}

    with TestClient(application) as client:
        response = client.get("/health")
        binding_response = client.get("/_phase6/repository-binding")

    assert response.status_code == 200
    assert response.json()["status"] == "ok"
    assert response.json()["rag_loaded"] is True
    assert response.json()["runtime_identity_sha256"] == runtime_identity_hash
    assert response.json()["sql_database"] == "math_pilot_evidence"
    assert {
        "sql_server",
        "sql_driver",
        "sql_username",
        "sql_password",
    }.isdisjoint(response.json())
    assert len(response.json()["provider_configuration_sha256"]) == 64
    assert binding_response.json() == {"bound": True}
    assert observed_settings == [settings]
    assert len(observed_logging) == 1
    assert (
        observed_logging[0].system_log_file
        == LoggingConfig.from_settings(settings).system_log_file
    )
    assert observed_logging[0].trace_log_file == tmp_path / "runtime-trace.jsonl"
    assert len(observed_sql_settings) == 1
    assert observed_sql_settings[0].database == response.json()["sql_database"]
    assert closed == [True]
    assert database_closed == [True]
    assert application.state.rag_server.ready is False


def test_rag_health_reports_evidence_hashes_without_paths_or_credentials():
    from mech_chatbot.api import rag_server

    activation_hash = "a" * 64
    restore_hash = "b" * 64
    application = rag_server.create_rag_app(
        Settings.from_env(
            {
                "RAG_ACTIVATION_BUNDLE_PATH": "private/activation-bundle.json",
                "RAG_ACTIVATION_BUNDLE_SHA256": activation_hash,
                "RAG_RESTORE_EVIDENCE_SHA256": restore_hash,
                "SQL_USERNAME": "private-user",
                "SQL_PASSWORD": "private-password",
                "LLM_API_KEY": "private-llm-key",
                "RAG_SERVICE_TOKEN": "health-test-token",
            }
        )
    )

    client = TestClient(application)
    assert client.get("/health").status_code == 401
    response = client.get(
        "/health",
        headers={"X-RAG-Service-Token": "health-test-token"},
    )
    payload = response.json()

    assert response.status_code == 200
    assert payload["activation_bundle_sha256"] == activation_hash
    assert payload["restore_evidence_sha256"] == restore_hash
    assert {
        "activation_bundle_path",
        "restore_evidence_path",
        "sql_server",
        "sql_driver",
        "sql_username",
        "sql_password",
        "llm_api_key",
    }.isdisjoint(payload)


def test_rag_thread_worker_rebinds_repository_runtime(monkeypatch):
    from mech_chatbot.api import rag_server
    from mech_chatbot.config.repository_runtime import current_repository_engine

    database_engine = object()
    state = SimpleNamespace(
        settings=Settings(),
        database_runtime=SimpleNamespace(engine=database_engine),
        runtime=object(),
    )
    monkeypatch.setattr(
        rag_server,
        "_run_rag_sync",
        lambda *_args: current_repository_engine(),
    )

    result = rag_server._run_rag_sync_with_repository(
        object(),
        {},
        state,
    )

    assert result is database_engine


def test_rag_lifespan_keeps_database_when_model_startup_fails():
    from mech_chatbot.api import rag_server

    database_closed = []
    database_runtime = SimpleNamespace(
        engine=object(),
        close=lambda: database_closed.append(True),
    )
    application = rag_server.create_rag_app(
        Settings(
            QDRANT_URL="https://qdrant.invalid",
            QDRANT_API_KEY="qdrant-value",
            LLM_BASE_URL="https://llm.invalid",
            LLM_API_KEY="llm-value",
            RAG_REQUIRE_SERVICE_AUTH=False,
        ),
        runtime_builder=lambda _settings: (_ for _ in ()).throw(
            RuntimeError("model startup failed")
        ),
        database_builder=lambda _settings: database_runtime,
    )

    with TestClient(application) as client:
        response = client.get("/health")
        assert response.status_code == 200
        assert response.json()["status"] == "degraded"
        assert application.state.rag_server.database_runtime is database_runtime

    assert database_closed == [True]
    assert application.state.rag_server.database_runtime is None


def test_rag_shutdown_resets_state_and_closes_database_when_runtime_close_fails(
    monkeypatch,
):
    from mech_chatbot.api import rag_server
    from mech_chatbot.rag import rerank
    from mech_chatbot.rag.execution_contracts import RagRuntimeContract

    database_closed = []
    runtime = SimpleNamespace(
        semaphore=SimpleNamespace(_value=2),
        runtime_contract=RagRuntimeContract("production", False, 120.0),
        close=lambda: (_ for _ in ()).throw(RuntimeError("close failed")),
    )
    monkeypatch.setattr(rerank, "tokenize_cached", lambda _text: "tokens")
    application = rag_server.create_rag_app(
        Settings(
            QDRANT_URL="https://qdrant.invalid",
            QDRANT_API_KEY="qdrant-value",
            LLM_BASE_URL="https://llm.invalid",
            LLM_API_KEY="llm-value",
            RAG_REQUIRE_SERVICE_AUTH=False,
        ),
        runtime_builder=lambda _settings: runtime,
        database_builder=lambda _settings: SimpleNamespace(
            engine=object(),
            close=lambda: database_closed.append(True),
        ),
    )

    with pytest.raises(RuntimeError, match="close failed"):
        with TestClient(application):
            assert application.state.rag_server.ready is True

    assert database_closed == [True]
    assert application.state.rag_server.ready is False
    assert application.state.rag_server.runtime is None
    assert application.state.rag_server.database_runtime is None


def test_rag_app_invalid_settings_fail_at_lifespan_without_secret_values():
    from mech_chatbot.api import rag_server
    from mech_chatbot.config.validate import ConfigError

    application = rag_server.create_rag_app(
        Settings(RAG_REQUIRE_SERVICE_AUTH=False),
        runtime_builder=lambda _settings: pytest.fail("runtime must not build"),
    )

    with pytest.raises(ConfigError) as exc_info:
        with TestClient(application):
            pass

    message = str(exc_info.value)
    assert "QDRANT_URL" in message
    assert "LLM API key" in message
    assert "qdrant-lifespan-value" not in message
