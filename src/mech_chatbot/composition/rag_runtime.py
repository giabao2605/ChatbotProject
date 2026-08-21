"""Composition root for one RAG server process."""

from __future__ import annotations

import asyncio
import time
from collections.abc import Callable, Mapping
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from functools import partial
from types import MappingProxyType
from typing import Any, Protocol

from mech_chatbot.config.settings import RagProcessSettings, Settings, SqlSettings
from mech_chatbot.config.logging import TraceRuntime, runtime_identity_sha256
from mech_chatbot.rag.execution import (
    DefaultRagExecutor,
    RagExecutor,
    RequestBudgetLimits,
)
from mech_chatbot.rag.execution_contracts import RagRuntimeContract


class RagRetrievalAdapter(Protocol):
    def retrieve(self, **kwargs: Any) -> Any: ...

    def retrieve_many(
        self,
        requests: tuple[Mapping[str, Any], ...],
        *,
        deadline_monotonic: float | None = None,
    ) -> Any: ...


class RagProviderAdapter(Protocol):
    def invoke(self, *args: Any, **kwargs: Any) -> Any: ...


class RagDatabaseRuntime(Protocol):
    engine: Any

    def close(self) -> None: ...


def _serial_retrieve_many(
    retrieve: Callable[..., Any],
    requests: tuple[Mapping[str, Any], ...],
    deadline_monotonic: float | None,
) -> tuple[Any, ...]:
    results = []
    for request in requests:
        if (
            deadline_monotonic is not None
            and time.monotonic() >= deadline_monotonic
        ):
            raise TimeoutError("RAG request deadline reached before serial retrieval")
        results.append(retrieve(**request))
    return tuple(results)


def build_rag_database_runtime(settings: SqlSettings) -> RagDatabaseRuntime:
    """Build the SQL lifecycle dependency owned by one RAG process."""

    from mech_chatbot.db.engine import build_database_runtime

    return build_database_runtime(settings)


@dataclass(frozen=True, slots=True)
class FunctionRetrievalAdapter:
    retrieve_function: Callable[..., Any]

    def retrieve(self, **kwargs: Any) -> Any:
        return self.retrieve_function(**kwargs)

    def retrieve_many(
        self,
        requests: tuple[Mapping[str, Any], ...],
        *,
        deadline_monotonic: float | None = None,
    ) -> Any:
        return _serial_retrieve_many(
            self.retrieve,
            requests,
            deadline_monotonic,
        )


@dataclass(frozen=True, slots=True)
class RagRetrievalRuntime:
    """Qdrant, embeddings and retrieval policy for one RAG process."""

    retrieve_function: Callable[..., Any]
    retrieve_many_function: Callable[..., Any] | None = field(repr=False)
    client: Any = field(repr=False)
    vectorstore: Any = field(repr=False)
    vision_model: Any = field(default=None, repr=False)
    collection_name: str = "TaiLieuKyThuat_v2"
    strict_answer_mode: bool = True
    strict_realtime_streaming: bool = False
    rerank_per_part: int = 8
    rerank_top_n_cap: int = 20
    hyde_enabled: bool = True
    query_rewrite_enabled: bool = True
    query_decomposition_enabled: bool = False
    strict_site_filter: bool = True
    planner_version: str = "planner-v1"
    community_serving_epoch: str = "community-v1"
    graph_fingerprint: str | None = None
    evaluation_force_ambiguous: bool = False
    crag_enabled: bool = False
    graph_retrieval_enabled: bool = False
    community_summaries_enabled: bool = False
    claim_repair_enabled: bool = False
    evidence_verifier_enabled: bool = False
    grounded_math_enabled: bool = False
    auto_source_cards: bool = True
    stream_max_attempts: int = 3
    strict_streaming_holdback_chars: int = 160
    eval_draft_override: str | None = None
    history_budget: int = 4000
    history_summary_enabled: bool = False
    conversation_state_enabled: bool = False
    parent_context_enabled: bool = True
    parent_context_max_workers: int = 4
    parent_context_max_sections: int = 8
    parent_context_max_chunks: int = 6
    citation_max_sources: int = 5
    bom_citation_max_sources: int = 3
    semantic_cache_enabled: bool = True
    semantic_cache_sim_threshold: float = 0.93
    semantic_cache_ttl_hours: float = 24.0
    semantic_cache_environment: Any = field(default=None, repr=False)
    glossary_cache_ttl: float = 60.0
    llm_router_min_confidence: float = 0.5
    llm_router_enabled: bool = True
    semantic_router_enabled: bool = True
    semantic_router_threshold: float = 0.62
    semantic_router_margin: float = 0.04
    safety_block_enabled: bool = True
    safety_extra_injection: tuple[str, ...] = ()
    safety_extra_abuse: tuple[str, ...] = ()
    voyage_runtime: Any = field(default=None, repr=False)
    voyage_enabled: bool = True
    voyage_timeout_seconds: float = 15.0
    rerank_provider: str = "jina"
    rerank_runtime: Any = field(default=None, repr=False)
    rerank_enabled: bool = True
    rerank_timeout_seconds: float = 15.0
    rerank_max_chunks_per_document: int = 4
    rerank_max_chunks_per_section: int = 1
    rerank_candidate_cap: int = 20
    late_interaction_config: Any = field(default=None, repr=False)
    late_query_encoder: Any = field(default=None, repr=False)
    intent_runtime: Any = field(default=None, repr=False)

    def retrieve(self, **kwargs: Any) -> Any:
        return self.retrieve_function(**kwargs)

    def retrieve_many(
        self,
        requests: tuple[Mapping[str, Any], ...],
        *,
        deadline_monotonic: float | None = None,
    ) -> Any:
        if self.retrieve_many_function is None:
            return _serial_retrieve_many(
                self.retrieve,
                requests,
                deadline_monotonic,
            )
        return self.retrieve_many_function(
            requests,
            deadline_monotonic=deadline_monotonic,
        )


@dataclass(frozen=True, slots=True)
class FunctionProviderAdapter:
    invoke_function: Callable[..., Any]

    def invoke(self, *args: Any, **kwargs: Any) -> Any:
        return self.invoke_function(*args, **kwargs)


@dataclass(frozen=True, slots=True)
class RagRuntime:
    """Frozen dependencies and process-owned concurrency for one RAG server."""

    settings: Any = field(repr=False)
    process_settings: RagProcessSettings
    runtime_contract: RagRuntimeContract
    trace_runtime: TraceRuntime
    executor: RagExecutor
    retrieval: RagRetrievalAdapter
    provider: RagProviderAdapter
    semaphore: Any = field(repr=False)
    thread_pool: Any = field(repr=False)

    def close(self) -> None:
        """Release resources owned by this process runtime."""

        shutdown = getattr(self.thread_pool, "shutdown", None)
        if callable(shutdown):
            shutdown(wait=True, cancel_futures=True)
        for dependency in (
            getattr(self.retrieval, "intent_runtime", None),
            self.retrieval,
            self.provider,
        ):
            if dependency is None:
                continue
            target = getattr(dependency, "client", dependency)
            close = getattr(target, "close", None)
            if callable(close):
                close()


def _process_settings(value: Any) -> RagProcessSettings:
    if isinstance(value, RagProcessSettings):
        return value
    if isinstance(value, Settings):
        return RagProcessSettings.from_settings(value)
    # Phase 4 tests passed small settings sentinels through this seam. Keep
    # those callers working while production always supplies typed Settings.
    return RagProcessSettings.from_settings(Settings())


def _runtime_contract(settings: RagProcessSettings) -> RagRuntimeContract:
    return RagRuntimeContract.from_mapping(
        {
            "execution_context": settings.execution_context,
            "evaluation_force_ambiguous": settings.evaluation_force_ambiguous,
            "request_deadline_seconds": settings.request_deadline_seconds,
        }
    )


def _runtime_identity(
    settings: Settings,
    process: RagProcessSettings,
    retrieval: RagRetrievalAdapter,
) -> str:
    from mech_chatbot.governance.feature_activation import FEATURE_FLAGS
    from mech_chatbot.governance.provider_smoke import (
        provider_configuration_sha256_for_settings,
    )

    return runtime_identity_sha256({
        "git_sha": process.deployment_git_sha,
        "deployment_id": process.deployment_id,
        "activation_profile": str(
            settings.RAG_ACTIVATION_PROFILE or "all_off"
        ).strip().casefold(),
        "feature_flags": {
            name: bool(getattr(settings, name)) for name in FEATURE_FLAGS
        },
        "snapshot_fingerprint": process.snapshot_fingerprint,
        "provider_configuration_sha256": (
            provider_configuration_sha256_for_settings(settings)
        ),
        "qdrant_collection": getattr(
            retrieval,
            "collection_name",
            settings.QDRANT_COLLECTION,
        ),
        "sql_database": settings.SQL_DATABASE,
        "activation_bundle_sha256": settings.RAG_ACTIVATION_BUNDLE_SHA256,
        "restore_evidence_sha256": getattr(
            settings,
            "RAG_RESTORE_EVIDENCE_SHA256",
            None,
        ),
        "request_deadline_seconds": process.request_deadline_seconds,
    })


def _build_external_stack(settings, qdrant_builder, llm_builder, vision_builder):
    from mech_chatbot.adapters.qdrant_runtime import build_qdrant_runtime
    from mech_chatbot.config.settings import LlmSettings, QdrantSettings, VisionSettings
    from mech_chatbot.llm.llm_client import build_llm_adapter
    from mech_chatbot.llm.vision_client import build_vision_model

    vector_runtime = (qdrant_builder or build_qdrant_runtime)(
        QdrantSettings.from_settings(settings)
    )
    provider = (llm_builder or build_llm_adapter)(
        LlmSettings.from_settings(settings)
    )
    vision_model = (vision_builder or build_vision_model)(
        VisionSettings.from_settings(settings)
    )
    return vector_runtime, provider, vision_model


def _build_late_dependencies(settings, process, encoder_builder):
    from mech_chatbot.rag.late_interaction import (
        LateInteractionConfig,
        build_encoder,
        encode_query,
    )

    config = LateInteractionConfig(
        interaction_enabled=process.late_interaction_enabled,
        encoder_ready=process.late_encoder_ready,
        model_name=process.late_model_name,
        use_fp16=process.late_use_fp16,
        query_max_length=process.late_query_max_length,
        document_max_length=process.late_document_max_length,
        collection_name=process.late_collection,
        index_version=process.late_index_version,
    )
    if not (config.interaction_enabled and config.encoder_ready):
        return config, None
    encoder = (encoder_builder or build_encoder)(config)
    return config, partial(
        encode_query,
        encoder=encoder,
        max_length=config.query_max_length,
    )


def _build_intent_dependency(settings, process, builder):
    from mech_chatbot.rag.intent import build_intent_runtime

    return (builder or build_intent_runtime)(
        max_workers=settings.INTENT_MAX_WORKERS,
        intent_timeout=settings.INTENT_TIMEOUT,
        context_timeout=settings.CONTEXT_TIMEOUT,
        query_rewrite_enabled=process.query_rewrite_enabled,
    )


def _build_voyage_dependency(settings, builder):
    if (
        settings.RERANK_PROVIDER not in {"voyage", "jina"}
        or not settings.USE_VOYAGE_RERANK
    ):
        return None
    from mech_chatbot.config.settings import ExternalAiSettings
    from mech_chatbot.llm.external_ai import (
        ExternalProcessingDenied,
        get_provider_runtime,
    )

    try:
        return (builder or get_provider_runtime)(
            "voyage",
            fallback_endpoint="https://api.voyageai.com/v1",
            fallback_model=settings.VOYAGE_RERANK_MODEL,
            settings=ExternalAiSettings.from_settings(settings),
            resolved_secrets={"VOYAGE_API_KEY": settings.VOYAGE_API_KEY},
        )
    except ExternalProcessingDenied:
        return None


def _build_jina_dependency(settings, builder):
    if settings.RERANK_PROVIDER != "jina":
        return None
    from mech_chatbot.config.settings import ExternalAiSettings
    from mech_chatbot.llm.external_ai import (
        ExternalProcessingDenied,
        get_provider_runtime,
        make_external_call_spec,
    )

    try:
        external_settings = ExternalAiSettings.from_settings(settings)
        runtime = (builder or get_provider_runtime)(
            "jina",
            fallback_endpoint="https://api.jina.ai/v1",
            fallback_model=settings.JINA_RERANK_MODEL,
            settings=external_settings,
            resolved_secrets={"JINA_API_KEY": settings.JINA_API_KEY},
        )
        if getattr(runtime, "profile", None) is not None:
            make_external_call_spec(
                provider="jina",
                model=getattr(runtime, "model", settings.JINA_RERANK_MODEL),
                endpoint=getattr(runtime, "endpoint", None),
                surface="reranking",
                policies=("all_external",),
                profile=runtime.profile,
                settings=external_settings,
            )
        return runtime
    except ExternalProcessingDenied:
        return None


def _pipeline_environment(settings):
    from mech_chatbot.governance.feature_activation import (
        FEATURE_FLAGS,
        VERSION_FIELDS,
    )

    def serialize(value):
        if isinstance(value, bool):
            return "true" if value else "false"
        return str(value)

    return MappingProxyType(
        {
            name: serialize(getattr(settings, name))
            for name in (*FEATURE_FLAGS, *VERSION_FIELDS)
        }
    )


def _retrieval_feature_settings(settings, process):
    return {
        "strict_answer_mode": settings.STRICT_ANSWER_MODE,
        "strict_realtime_streaming": settings.STRICT_REALTIME_STREAMING,
        "hyde_enabled": process.hyde_enabled,
        "query_rewrite_enabled": process.query_rewrite_enabled,
        "query_decomposition_enabled": settings.RAG_QUERY_DECOMPOSITION_ENABLED,
        "strict_site_filter": process.strict_site_filter,
        "evaluation_force_ambiguous": process.evaluation_force_ambiguous,
        "crag_enabled": settings.RAG_CRAG_ENABLED,
        "graph_retrieval_enabled": settings.RAG_GRAPH_RETRIEVAL_ENABLED,
        "community_summaries_enabled": settings.RAG_GRAPH_COMMUNITY_SUMMARIES_ENABLED,
        "claim_repair_enabled": settings.RAG_CLAIM_REPAIR_ENABLED,
        "evidence_verifier_enabled": process.evidence_verifier_enabled,
        "grounded_math_enabled": settings.RAG_GROUNDED_MATH_ENABLED,
        "auto_source_cards": process.auto_source_cards,
        "history_summary_enabled": process.history_summary_enabled,
        "conversation_state_enabled": process.conversation_state_enabled,
    }


def _retrieval_limit_settings(settings, process):
    return {
        "rerank_per_part": settings.RERANK_PER_PART,
        "rerank_top_n_cap": settings.RERANK_TOP_N_CAP,
        "stream_max_attempts": process.stream_max_attempts,
        "strict_streaming_holdback_chars": process.streaming_holdback_chars,
        "history_budget": settings.HISTORY_BUDGET,
        "parent_context_enabled": process.parent_context_enabled,
        "parent_context_max_workers": settings.PARENT_CONTEXT_MAX_WORKERS,
        "parent_context_max_sections": process.parent_context_max_sections,
        "parent_context_max_chunks": process.parent_context_max_chunks,
        "citation_max_sources": process.citation_max_sources,
        "bom_citation_max_sources": process.bom_citation_max_sources,
        "rerank_max_chunks_per_document": process.rerank_max_chunks_per_document,
        "rerank_max_chunks_per_section": process.rerank_max_chunks_per_section,
        "rerank_candidate_cap": process.rerank_candidate_cap,
    }


def _retrieval_policy_settings(
    settings,
    process,
    voyage_runtime,
    jina_runtime,
):
    rerank_provider = settings.RERANK_PROVIDER
    if (
        rerank_provider == "jina"
        and not getattr(jina_runtime, "api_key", None)
        and settings.USE_VOYAGE_RERANK
        and getattr(voyage_runtime, "api_key", None)
    ):
        rerank_provider = "voyage"
    rerank_runtime = (
        jina_runtime
        if rerank_provider == "jina"
        else voyage_runtime
    )
    rerank_enabled = (
        rerank_provider == "jina" and jina_runtime is not None
    ) or (
        rerank_provider == "voyage"
        and settings.USE_VOYAGE_RERANK
        and voyage_runtime is not None
    )
    rerank_timeout = (
        settings.JINA_RERANK_TIMEOUT_SECONDS
        if rerank_provider == "jina"
        else settings.VOYAGE_RERANK_TIMEOUT_SECONDS
    )
    return {
        "planner_version": settings.RAG_PLANNER_VERSION,
        "community_serving_epoch": settings.RAG_COMMUNITY_SERVING_EPOCH,
        "graph_fingerprint": process.graph_fingerprint,
        "eval_draft_override": process.eval_draft_override,
        "semantic_cache_enabled": settings.SEMANTIC_CACHE_ENABLED,
        "semantic_cache_sim_threshold": settings.SEMANTIC_CACHE_SIM_THRESHOLD,
        "semantic_cache_ttl_hours": settings.SEMANTIC_CACHE_TTL_HOURS,
        "semantic_cache_environment": _pipeline_environment(settings),
        "glossary_cache_ttl": settings.GLOSSARY_CACHE_TTL,
        "llm_router_min_confidence": settings.LLM_ROUTER_MIN_CONFIDENCE,
        "llm_router_enabled": process.llm_router_enabled,
        "semantic_router_enabled": process.semantic_router_enabled,
        "semantic_router_threshold": process.semantic_router_threshold,
        "semantic_router_margin": process.semantic_router_margin,
        "safety_block_enabled": process.safety_block_enabled,
        "safety_extra_injection": process.safety_extra_injection,
        "safety_extra_abuse": process.safety_extra_abuse,
        "voyage_runtime": voyage_runtime,
        "voyage_enabled": settings.USE_VOYAGE_RERANK,
        "voyage_timeout_seconds": settings.VOYAGE_RERANK_TIMEOUT_SECONDS,
        "rerank_provider": rerank_provider,
        "rerank_runtime": rerank_runtime,
        "rerank_enabled": rerank_enabled,
        "rerank_timeout_seconds": rerank_timeout,
    }


def _build_default_adapters(
    settings: Settings,
    retrieve_function: Callable[..., Any],
    retrieve_many_function: Callable[..., Any],
    *,
    qdrant_builder: Callable[[Any], Any] | None = None,
    llm_builder: Callable[[Any], Any] | None = None,
    vision_builder: Callable[[Any], Any] | None = None,
    late_encoder_builder: Callable[[Any], Any] | None = None,
    intent_runtime_builder: Callable[..., Any] | None = None,
    voyage_runtime_builder: Callable[..., Any] | None = None,
    jina_runtime_builder: Callable[..., Any] | None = None,
) -> tuple[RagRetrievalRuntime, RagProviderAdapter]:
    """Construct external adapters only when the composition root is called."""
    process = RagProcessSettings.from_settings(settings)
    vector_runtime, provider, vision_model = _build_external_stack(
        settings, qdrant_builder, llm_builder, vision_builder
    )
    late_config, late_query_encoder = _build_late_dependencies(
        settings, process, late_encoder_builder
    )
    intent_runtime = _build_intent_dependency(
        settings, process, intent_runtime_builder
    )
    voyage_runtime = _build_voyage_dependency(settings, voyage_runtime_builder)
    jina_runtime = _build_jina_dependency(settings, jina_runtime_builder)
    composed_retrieve = partial(
        retrieve_function,
        vectorstore=vector_runtime.vector_store,
        client=vector_runtime.qdrant_client,
        collection_name=vector_runtime.collection_name,
    )
    composed_retrieve_many = partial(
        retrieve_many_function,
        vectorstore=vector_runtime.vector_store,
        client=vector_runtime.qdrant_client,
        collection_name=vector_runtime.collection_name,
    )
    return (
        RagRetrievalRuntime(
            retrieve_function=composed_retrieve,
            retrieve_many_function=composed_retrieve_many,
            client=vector_runtime.qdrant_client,
            vectorstore=vector_runtime.vector_store,
            vision_model=vision_model,
            collection_name=vector_runtime.collection_name,
            late_interaction_config=late_config,
            late_query_encoder=late_query_encoder,
            intent_runtime=intent_runtime,
            **_retrieval_feature_settings(settings, process),
            **_retrieval_limit_settings(settings, process),
            **_retrieval_policy_settings(
                settings,
                process,
                voyage_runtime,
                jina_runtime,
            ),
        ),
        provider,
    )


def build_rag_runtime(
    existing_settings: Any,
    *,
    execute_pipeline: Callable[[Any], Any] | None = None,
    retrieval: RagRetrievalAdapter | None = None,
    provider: RagProviderAdapter | None = None,
    semaphore_factory: Callable[[int], Any] = asyncio.Semaphore,
    thread_pool_factory: Callable[[int], Any] | None = None,
    qdrant_builder: Callable[[Any], Any] | None = None,
    llm_builder: Callable[[Any], Any] | None = None,
    vision_builder: Callable[[Any], Any] | None = None,
    late_encoder_builder: Callable[[Any], Any] | None = None,
    intent_runtime_builder: Callable[..., Any] | None = None,
    voyage_runtime_builder: Callable[..., Any] | None = None,
    jina_runtime_builder: Callable[..., Any] | None = None,
    trace_persist: Callable[[str, dict[str, Any]], None] | None = None,
) -> RagRuntime:
    """Build RAG dependencies without a singleton or service locator.

    Explicit adapters keep tests and future process wiring independent from
    import-time infrastructure. The default branch retains the current
    production implementation until Phase 4 private phases consume these
    adapters directly.
    """

    resolved_execute = execute_pipeline
    if resolved_execute is None:
        from mech_chatbot.rag import pipeline

        resolved_execute = pipeline.execute_pipeline

    retrieve_function: Callable[..., Any] | None = None
    retrieve_many_function: Callable[..., Any] | None = None
    if retrieval is None:
        from mech_chatbot.rag.pipeline_steps import _retrieve, _retrieve_many

        retrieve_function = _retrieve
        retrieve_many_function = _retrieve_many

    if retrieval is None or provider is None:
        if not isinstance(existing_settings, Settings):
            raise TypeError(
                "Default RAG adapters require the canonical Settings snapshot"
            )
        default_retrieval, default_provider = _build_default_adapters(
            existing_settings,
            retrieve_function or (lambda **_kwargs: ()),
            retrieve_many_function or (lambda _requests, **_kwargs: ()),
            qdrant_builder=qdrant_builder,
            llm_builder=llm_builder,
            vision_builder=vision_builder,
            late_encoder_builder=late_encoder_builder,
            intent_runtime_builder=intent_runtime_builder,
            voyage_runtime_builder=voyage_runtime_builder,
            jina_runtime_builder=jina_runtime_builder,
        )
        resolved_retrieval = retrieval or default_retrieval
        resolved_provider = provider or default_provider
    else:
        resolved_retrieval = retrieval
        resolved_provider = provider

    process_settings = _process_settings(existing_settings)
    identity_settings = (
        existing_settings if isinstance(existing_settings, Settings) else Settings()
    )
    if trace_persist is None:
        from mech_chatbot.db.repositories.analytics import save_rag_trace_summary

        trace_persist = save_rag_trace_summary
    from mech_chatbot.rag.execution import current_execution_context

    pool_builder = thread_pool_factory or (
        lambda workers: ThreadPoolExecutor(
            max_workers=workers,
            thread_name_prefix="rag",
        )
    )
    return RagRuntime(
        settings=existing_settings,
        process_settings=process_settings,
        runtime_contract=_runtime_contract(process_settings),
        trace_runtime=TraceRuntime(
            persist=trace_persist,
            execution_context=current_execution_context,
            runtime_identity_sha256=_runtime_identity(
                identity_settings,
                process_settings,
                resolved_retrieval,
            ),
        ),
        executor=DefaultRagExecutor(
            execute_pipeline=resolved_execute,
            retrieval_adapter=resolved_retrieval,
            provider_adapter=resolved_provider,
            budget_limits=RequestBudgetLimits(
                deadline_seconds=process_settings.request_deadline_seconds,
            ),
        ),
        retrieval=resolved_retrieval,
        provider=resolved_provider,
        semaphore=semaphore_factory(process_settings.max_concurrent_requests),
        thread_pool=pool_builder(process_settings.max_concurrent_requests),
    )


__all__ = [
    "FunctionProviderAdapter",
    "FunctionRetrievalAdapter",
    "RagProviderAdapter",
    "RagRetrievalAdapter",
    "RagRetrievalRuntime",
    "RagDatabaseRuntime",
    "RagRuntime",
    "build_rag_database_runtime",
    "build_rag_runtime",
]
