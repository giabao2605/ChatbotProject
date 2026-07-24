from dataclasses import FrozenInstanceError
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


def test_rag_app_lifespan_builds_reports_and_closes_one_runtime(monkeypatch):
    from mech_chatbot.api import rag_server
    from mech_chatbot.rag import rerank
    from mech_chatbot.rag.execution_contracts import RagRuntimeContract

    closed = []
    observed_settings = []
    credentials = {
        "QDRANT_" + "API_KEY": "qdrant-lifespan-value",
        "LLM_" + "API_KEY": "llm-lifespan-value",
    }
    settings = Settings(
        QDRANT_URL="https://qdrant.invalid",
        LLM_BASE_URL="https://llm.invalid",
        RAG_REQUIRE_SERVICE_AUTH=False,
        **credentials,
    )
    runtime = SimpleNamespace(
        semaphore=SimpleNamespace(_value=2),
        runtime_contract=RagRuntimeContract("production", False, 120.0),
        close=lambda: closed.append(True),
    )
    monkeypatch.setattr(rerank, "tokenize_cached", lambda _text: "tokens")
    application = rag_server.create_rag_app(
        settings,
        runtime_builder=lambda snapshot: observed_settings.append(snapshot) or runtime,
    )

    with TestClient(application) as client:
        response = client.get("/health")

    assert response.status_code == 200
    assert response.json()["status"] == "ok"
    assert response.json()["rag_loaded"] is True
    assert observed_settings == [settings]
    assert closed == [True]
    assert application.state.rag_server.ready is False


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
