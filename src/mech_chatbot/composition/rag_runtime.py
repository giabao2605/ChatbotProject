"""Composition root for one RAG server process."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any, Protocol

from mech_chatbot.rag.execution import DefaultRagExecutor, RagExecutor


class RagRetrievalAdapter(Protocol):
    def retrieve(self, **kwargs: Any) -> Any: ...


class RagProviderAdapter(Protocol):
    def invoke(self, *args: Any, **kwargs: Any) -> Any: ...


@dataclass(frozen=True, slots=True)
class FunctionRetrievalAdapter:
    retrieve_function: Callable[..., Any]

    def retrieve(self, **kwargs: Any) -> Any:
        return self.retrieve_function(**kwargs)


@dataclass(frozen=True, slots=True)
class FunctionProviderAdapter:
    invoke_function: Callable[..., Any]

    def invoke(self, *args: Any, **kwargs: Any) -> Any:
        return self.invoke_function(*args, **kwargs)


@dataclass(frozen=True, slots=True)
class RagRuntime:
    """Frozen process dependencies; callers use only ``executor``."""

    settings: Any
    executor: RagExecutor
    retrieval: RagRetrievalAdapter
    provider: RagProviderAdapter


def build_rag_runtime(
    existing_settings: Any,
    *,
    execute_pipeline: Callable[[Any], Any] | None = None,
    retrieval: RagRetrievalAdapter | None = None,
    provider: RagProviderAdapter | None = None,
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

    resolved_retrieval = retrieval
    if resolved_retrieval is None:
        from mech_chatbot.rag.pipeline_steps import _retrieve

        resolved_retrieval = FunctionRetrievalAdapter(_retrieve)

    resolved_provider = provider
    if resolved_provider is None:
        from mech_chatbot.llm.llm_client import cohere_invoke

        resolved_provider = FunctionProviderAdapter(cohere_invoke)

    return RagRuntime(
        settings=existing_settings,
        executor=DefaultRagExecutor(
            execute_pipeline=resolved_execute,
            retrieval_adapter=resolved_retrieval,
            provider_adapter=resolved_provider,
        ),
        retrieval=resolved_retrieval,
        provider=resolved_provider,
    )


__all__ = [
    "FunctionProviderAdapter",
    "FunctionRetrievalAdapter",
    "RagProviderAdapter",
    "RagRetrievalAdapter",
    "RagRuntime",
    "build_rag_runtime",
]
