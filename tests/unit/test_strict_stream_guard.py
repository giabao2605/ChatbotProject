from contextlib import contextmanager
import importlib.util
from pathlib import Path
import sys
import threading
import time
from types import ModuleType, SimpleNamespace

import pytest

from mech_chatbot.llm.external_ai import ExternalAICallCancelled


pytestmark = pytest.mark.unit


PIPELINE_STEPS = Path(__file__).resolve().parents[2] / "src" / "mech_chatbot" / "rag" / "pipeline_steps.py"


def _load_pipeline_steps_without_rag_bootstrap(monkeypatch):
    """Load the generation function with a tiny bootstrap stub, not real models."""
    bootstrap = ModuleType("mech_chatbot.rag.bootstrap")
    bootstrap.STRICT_ANSWER_MODE = True
    bootstrap.vectorstore = object()
    monkeypatch.setitem(sys.modules, "mech_chatbot.rag.bootstrap", bootstrap)
    spec = importlib.util.spec_from_file_location("strict_stream_guard_module", PIPELINE_STEPS)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


class _FakeChain:
    def __init__(self, chunks, before_yield=None):
        self.chunks = chunks
        self.before_yield = before_yield

    def __or__(self, _other):
        return self

    def stream(self, _payload):
        for chunk in self.chunks:
            if self.before_yield:
                self.before_yield()
            yield chunk


@contextmanager
def _no_network_audit(**_kwargs):
    yield None


def _generate(module, *, cancel_event=None, question="Gia tri la bao nhieu?"):
    docs = [SimpleNamespace(metadata={"doc_id": 7, "security_level": "internal"})]
    return module._generate(
        context_text="Tai lieu chi ghi gia tri 10.",
        user_question=question,
        chat_history_str="",
        retrieved_docs=docs,
        new_part_ids=[],
        response_language="vi",
        trace_id="strict-stream-test",
        t_start=time.time(),
        user_department="Technical",
        user_roles=["viewer"],
        effective_question=question,
        intent_data={},
        base_k=5,
        retrieval_mode="general:explicit_dense_bm25_rrf",
        cancel_event=cancel_event,
    )


def _prepare(module, monkeypatch, chain):
    monkeypatch.setattr(module, "_build_prompt_template", lambda *_args, **_kwargs: chain)
    monkeypatch.setattr(module, "get_cohere_llm", lambda: object())
    monkeypatch.setattr(module, "StrOutputParser", lambda: object())
    monkeypatch.setattr(module, "audited_external_call", _no_network_audit)
    monkeypatch.setattr(module, "get_llm_model_name", lambda: "test-model")
    monkeypatch.setattr(module, "get_llm_endpoint", lambda: "https://example.invalid/v1")
    monkeypatch.setattr(module, "_context_is_mechanical", lambda *_args: False)
    monkeypatch.setattr(module, "strict_realtime_streaming_enabled", lambda *_args: False)
    monkeypatch.setattr(module, "has_unsupported_numbers", lambda *_args, **_kwargs: True)
    monkeypatch.setattr(module, "make_insufficient_evidence_message", lambda *_args, **_kwargs: "REFUSAL")


def test_strict_buffered_stream_never_yields_unsupported_factual_token(monkeypatch):
    module = _load_pipeline_steps_without_rag_bootstrap(monkeypatch)
    _prepare(module, monkeypatch, _FakeChain(["Gia tri la 999."]))

    emitted = list(_generate(module))

    assert emitted == ["REFUSAL"]
    assert "999" not in "".join(emitted)


def test_cancelled_stream_raises_before_any_provider_chunk_is_emitted(monkeypatch):
    module = _load_pipeline_steps_without_rag_bootstrap(monkeypatch)
    cancelled = threading.Event()
    _prepare(module, monkeypatch, _FakeChain(["Gia tri la 999."], before_yield=cancelled.set))

    with pytest.raises(ExternalAICallCancelled):
        list(_generate(module, cancel_event=cancelled))


def test_normal_policy_question_does_not_apply_global_numeric_holdback(monkeypatch):
    module = _load_pipeline_steps_without_rag_bootstrap(monkeypatch)
    seen_strict_values = []
    _prepare(module, monkeypatch, _FakeChain(["Quy định là 20."]))
    monkeypatch.setattr(
        module,
        "has_unsupported_numbers",
        lambda *_args, **kwargs: seen_strict_values.append(kwargs.get("strict_mode")) or False,
    )

    assert list(_generate(module, question="Quy định hiện hành là gì?")) == ["Quy định là 20."]
    assert seen_strict_values == [False]
