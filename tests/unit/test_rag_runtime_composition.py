from dataclasses import FrozenInstanceError
from types import SimpleNamespace

import pytest

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

    def execute_pipeline(state):
        observed.append((state.request.question, state.trace_id))
        return state.prepared((iter(["answer"]), "refs", [], ["P-1"], {}))

    retrieval = SimpleNamespace(retrieve=lambda **_kwargs: ())
    provider = SimpleNamespace(invoke=lambda *_args, **_kwargs: None)
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


def test_rag_server_opens_requests_through_composed_runtime(monkeypatch):
    from mech_chatbot.api import rag_server

    sentinel = object()
    observed = []

    class ScriptedExecutor:
        def run(self, request, invocation, cancellation):
            observed.append((request.question, invocation.trace_id, cancellation))
            return sentinel

    monkeypatch.setattr(
        rag_server,
        "_rag_runtime",
        SimpleNamespace(executor=ScriptedExecutor()),
        raising=False,
    )
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
    )

    assert result is sentinel
    assert observed == [("runtime question", "runtime-trace", cancellation)]
