"""Explicit composition root for the browser-chat application surface.

The composition module only assembles application-owned contracts. Concrete
HTTP, SQL, audit, and pilot implementations are supplied by the caller so
importing this module never creates external clients or process-wide state.
"""

from __future__ import annotations

from dataclasses import dataclass
from collections.abc import Callable, Mapping
from typing import Any

from mech_chatbot.adapters.chat_runtime import (
    HttpRagStreamAdapter,
    PilotExperimentAdapter,
    RepositoryAuditSink,
    RepositoryChatStore,
)
from mech_chatbot.application.chat_turn import (
    AuditSink,
    ChatStore,
    ChatTurnRunner,
    CitationResolver,
    PilotExperimentPort,
    RagStreamPort,
)


@dataclass(frozen=True, slots=True)
class AppRuntime:
    """Immutable dependency bundle owned by one app-server process."""

    settings: Any
    rag_stream: RagStreamPort
    chat_store: ChatStore
    audit_sink: AuditSink
    pilot_experiments: PilotExperimentPort
    citation_resolver: CitationResolver
    chat_turn_runner: ChatTurnRunner


def build_app_runtime(
    existing_settings: Any,
    *,
    rag_stream: RagStreamPort,
    chat_store: ChatStore,
    audit_sink: AuditSink,
    pilot_experiments: PilotExperimentPort,
    citation_resolver: CitationResolver,
) -> AppRuntime:
    """Build an immutable app runtime from explicitly supplied adapters.

    ``existing_settings`` is retained by identity. This factory deliberately
    does not read environment variables, resolve globals, or instantiate
    infrastructure: the process bootstrap owns those decisions and passes
    already-configured ports here.
    """

    runner = ChatTurnRunner(
        rag_stream=rag_stream,
        chat_store=chat_store,
        audit_sink=audit_sink,
        pilot_experiments=pilot_experiments,
        citation_resolver=citation_resolver,
    )
    return AppRuntime(
        settings=existing_settings,
        rag_stream=rag_stream,
        chat_store=chat_store,
        audit_sink=audit_sink,
        pilot_experiments=pilot_experiments,
        citation_resolver=citation_resolver,
        chat_turn_runner=runner,
    )


def build_default_app_runtime(
    existing_settings: Any,
    *,
    post: Callable[..., Any],
    base_url: str | Callable[[], str],
    headers: Mapping[str, str] | Callable[[], Mapping[str, str]],
    timeout: Any | Callable[[], Any],
    save_chat_history: Callable[..., int | None],
    save_answer_evidence: Callable[..., Any],
    save_answer_sources: Callable[..., Any],
    write_audit_log: Callable[..., Any],
    load_pilot_config: Callable[[], object | None],
    assign_pilot_route: Callable[..., object],
    pilot_outcome: Callable[..., Mapping[str, Any]],
    schedule_pilot_replay: Callable[..., bool],
    citation_resolver: CitationResolver,
) -> AppRuntime:
    """Compose production adapters without exposing them to the API router."""

    return build_app_runtime(
        existing_settings,
        rag_stream=HttpRagStreamAdapter(
            post=post,
            base_url=base_url,
            headers=headers,
            timeout=timeout,
        ),
        chat_store=RepositoryChatStore(
            save_chat_history=save_chat_history,
            save_answer_evidence=save_answer_evidence,
            save_answer_sources=save_answer_sources,
        ),
        audit_sink=RepositoryAuditSink(write_audit_log=write_audit_log),
        pilot_experiments=PilotExperimentAdapter(
            load_config=load_pilot_config,
            assign_route=assign_pilot_route,
            outcome=pilot_outcome,
            schedule_replay=schedule_pilot_replay,
        ),
        citation_resolver=citation_resolver,
    )


__all__ = ["AppRuntime", "build_app_runtime", "build_default_app_runtime"]
