from contextlib import contextmanager
from decimal import Decimal
import importlib.util
from pathlib import Path
import sys
import threading
import time
from types import ModuleType, SimpleNamespace

import pytest

from mech_chatbot.llm.external_ai import ExternalAICallCancelled, ExternalProcessingDenied
from mech_chatbot.rag.grounded_math import (
    CalculationPlan,
    GroundedFact,
    derive_claim,
    make_calculation_provenance,
)
from mech_chatbot.rag.execution import (
    AccessScope,
    DefaultRagExecutor,
    RagCompleted,
    RagInvocation,
    RagRequest,
    RagToken,
    RequestBudgetExceeded,
    RequestBudgetLedger,
    RequestBudgetLimits,
)


pytestmark = pytest.mark.unit


PIPELINE_STEPS = Path(__file__).resolve().parents[2] / "src" / "mech_chatbot" / "rag" / "pipeline_steps.py"
_MISSING = object()


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


def _run_generation(
    module,
    *,
    cancel_event=None,
    question="Gia tri la bao nhieu?",
    docs=None,
    **overrides,
):
    docs = docs or [SimpleNamespace(metadata={"doc_id": 7, "security_level": "internal"})]
    plan_fields = {
        "context_text": "Tai lieu chi ghi gia tri 10.",
        "user_question": question,
        "chat_history_str": "",
        "retrieved_docs": docs,
        "new_part_ids": [],
        "response_language": "vi",
        "trace_id": "strict-stream-test",
        "started_at": time.time(),
        "user_department": "Technical",
        "user_roles": ["viewer"],
        "effective_question": question,
        "intent_data": {},
        "base_k": 5,
        "retrieval_mode": "general:explicit_dense_bm25_rrf",
    }
    plan_fields.update(overrides)
    return module.generate_answer(
        module.GenerationPlan(
            turn=module.GenerationTurn(
                user_question=plan_fields["user_question"],
                effective_question=plan_fields["effective_question"],
                chat_history_str=plan_fields["chat_history_str"],
                new_part_ids=plan_fields["new_part_ids"],
                response_language=plan_fields["response_language"],
                user_department=plan_fields["user_department"],
                user_roles=plan_fields["user_roles"],
            ),
            evidence=module.GenerationEvidence(
                context_text=plan_fields["context_text"],
                retrieved_docs=plan_fields["retrieved_docs"],
                intent_data=plan_fields["intent_data"],
                base_k=plan_fields["base_k"],
                retrieval_mode=plan_fields["retrieval_mode"],
                has_active_filter=plan_fields.get("has_active_filter", False),
                active_filter=plan_fields.get("active_filter"),
            ),
            control=module.GenerationControl(
                trace_id=plan_fields["trace_id"],
                started_at=plan_fields["started_at"],
                outcome=plan_fields.get("outcome", module.GenerationOutcome()),
            ),
            explicit_negative_answer=plan_fields.get("explicit_negative_answer", ""),
        ),
        cancel_event=cancel_event,
    )


def _run_through_executor(monkeypatch, module, **generation_kwargs):
    rag_package = sys.modules["mech_chatbot.rag"]
    module_names = (
        "mech_chatbot.rag.pipeline",
        "mech_chatbot.rag.pipeline_steps",
    )
    previous_modules = {
        name: sys.modules.get(name, _MISSING) for name in module_names
    }
    previous_attributes = {
        name.rsplit(".", 1)[-1]: getattr(
            rag_package, name.rsplit(".", 1)[-1], _MISSING
        )
        for name in module_names
    }
    outcome = module.GenerationOutcome()

    def scripted_pipeline(state):
        state.bind_generation(outcome)
        stream = _run_generation(module, outcome=outcome, **generation_kwargs)
        return state.prepared((stream, "", [], [], {}))

    try:
        return list(
            DefaultRagExecutor(execute_pipeline=scripted_pipeline).run(
                RagRequest("generation contract", AccessScope()),
                RagInvocation(trace_id="strict-generation-contract", mode="test"),
            )
        )
    finally:
        for name, previous in previous_modules.items():
            if previous is _MISSING:
                sys.modules.pop(name, None)
            else:
                sys.modules[name] = previous
        for attribute, previous in previous_attributes.items():
            if previous is _MISSING:
                if hasattr(rag_package, attribute):
                    delattr(rag_package, attribute)
            else:
                setattr(rag_package, attribute, previous)


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
    events = _run_through_executor(monkeypatch, module)
    emitted = [event.text for event in events if isinstance(event, RagToken)]

    assert emitted == ["REFUSAL"]
    assert "999" not in "".join(emitted)
    assert isinstance(events[-1], RagCompleted)
    assert events[-1].outcome == "refused"
    assert events[-1].refusal_reason == "post_check_numbers"


def test_cancelled_stream_raises_before_any_provider_chunk_is_emitted(monkeypatch):
    module = _load_pipeline_steps_without_rag_bootstrap(monkeypatch)
    cancelled = threading.Event()
    _prepare(module, monkeypatch, _FakeChain(["Gia tri la 999."], before_yield=cancelled.set))

    with pytest.raises(ExternalAICallCancelled):
        list(_run_generation(module, cancel_event=cancelled))


def test_normal_policy_question_does_not_apply_global_numeric_holdback(monkeypatch):
    module = _load_pipeline_steps_without_rag_bootstrap(monkeypatch)
    seen_strict_values = []
    _prepare(module, monkeypatch, _FakeChain(["Quy định là 20."]))
    monkeypatch.setattr(
        module,
        "has_unsupported_numbers",
        lambda *_args, **kwargs: seen_strict_values.append(kwargs.get("strict_mode")) or False,
    )

    assert list(_run_generation(module, question="Quy định hiện hành là gì?")) == ["Quy định là 20."]
    assert seen_strict_values == [False]


def test_explicit_negative_evidence_skips_provider_and_claim_repair(monkeypatch):
    module = _load_pipeline_steps_without_rag_bootstrap(monkeypatch)
    monkeypatch.setattr(
        module,
        "_build_prompt_template",
        lambda *_args, **_kwargs: pytest.fail("provider chain must not be built"),
    )
    trace_events = []
    monkeypatch.setattr(
        module,
        "log_trace",
        lambda event, _trace_id, **fields: trace_events.append((event, fields)),
    )
    docs = [
        SimpleNamespace(
            metadata={"doc_id": 73, "trang_so": 3, "security_level": "internal"}
        )
    ]

    events = _run_through_executor(
        monkeypatch,
        module,
        question="Đơn giá là bao nhiêu?",
        docs=docs,
        explicit_negative_answer=(
            "Theo tài liệu, thông tin được nêu rõ: “Không có trường đơn giá "
            "trong BOM này.” [SRC:D73P3]"
        ),
    )
    emitted = [event.text for event in events if isinstance(event, RagToken)]

    assert emitted == [
        "Theo tài liệu, thông tin được nêu rõ: “Không có trường đơn giá "
        "trong BOM này.” [SRC:D73P3]"
    ]
    deterministic = dict(trace_events)["deterministic_generation"]
    assert deterministic["claim_repair_skipped_reason"] == "explicit_negative_evidence"
    assert events[-1].outcome == "answered"


@pytest.mark.parametrize("policy", ["internal_only", None])
def test_claim_repair_forwards_document_policy_and_fails_closed(monkeypatch, policy):
    module = _load_pipeline_steps_without_rag_bootstrap(monkeypatch)
    metadata = {
        "doc_id": 7,
        "trang_so": 3,
        "version_no": 1,
        "security_level": "confidential",
    }
    if policy is not None:
        metadata["external_processing_policy"] = policy
    document = SimpleNamespace(
        metadata=metadata
    )

    with pytest.raises(ExternalProcessingDenied, match="internal_only"):
        module._attempt_number_claim_repair(
            "Chi phí 2500 USD.",
            context_text="Chi phí 1500 USD.",
            user_question="Chi phí bao nhiêu?",
            retrieved_docs=[document],
            trace_id="claim-repair-policy-test",
            enabled=True,
        )


def test_grounded_math_generation_streams_verified_answer_without_llm(monkeypatch):
    module = _load_pipeline_steps_without_rag_bootstrap(monkeypatch)
    monkeypatch.setenv("RAG_GROUNDED_MATH_ENABLED", "true")
    monkeypatch.setattr(
        module,
        "get_cohere_llm",
        lambda: (_ for _ in ()).throw(AssertionError("grounded math must not initialize LLM")),
    )
    plan = CalculationPlan(
        "add",
        (
            GroundedFact(Decimal("2"), "cái", 41, 3, 12, "BOM-1", "PART-A"),
            GroundedFact(Decimal("5"), "cái", 41, 4, 12, "BOM-2", "PART-B"),
        ),
    )
    docs = [
        SimpleNamespace(metadata={
            "doc_id": 41,
            "trang_so": 3,
            "version_no": 12,
            "file_goc": "bom-v12.pdf",
            "security_level": "internal",
            "calculation_provenance": make_calculation_provenance(plan, derive_claim(plan)),
        }),
        SimpleNamespace(metadata={
            "doc_id": 41,
            "trang_so": 4,
            "version_no": 12,
            "file_goc": "bom-v12.pdf",
            "security_level": "internal",
        }),
    ]

    events = _run_through_executor(
        monkeypatch,
        module,
        question="Cộng PART-A và PART-B",
        docs=docs,
    )
    emitted = [event.text for event in events if isinstance(event, RagToken)]

    assert emitted == [
        "Kết quả tính có kiểm soát: 7 cái; công thức: 2 + 5 = 7 cái. "
        "[Nguồn: bom-v12.pdf, Trang 3, Version 12, SourceID D41P3] "
        "[Nguồn: bom-v12.pdf, Trang 4, Version 12, SourceID D41P4]"
    ]
    assert events[-1].outcome == "answered"

    cancelled = threading.Event()
    cancelled.set()
    with pytest.raises(ExternalAICallCancelled):
        list(_run_generation(
            module,
            question="Cộng PART-A và PART-B",
            docs=docs,
            cancel_event=cancelled,
        ))


def test_grounded_math_flag_defaults_to_normal_generation_path(monkeypatch):
    module = _load_pipeline_steps_without_rag_bootstrap(monkeypatch)
    monkeypatch.delenv("RAG_GROUNDED_MATH_ENABLED", raising=False)
    initialized = []
    _prepare(module, monkeypatch, _FakeChain(["Normal generation path."]))
    monkeypatch.setattr(
        module,
        "get_cohere_llm",
        lambda: initialized.append(True) or object(),
    )
    monkeypatch.setattr(
        module,
        "has_unsupported_numbers",
        lambda *_args, **_kwargs: False,
    )
    plan = CalculationPlan(
        "sum",
        (GroundedFact(Decimal("2"), "cái", 41, 3, 12, "BOM-1", "PART-A"),),
    )
    docs = [SimpleNamespace(metadata={
        "doc_id": 41,
        "trang_so": 3,
        "version_no": 12,
        "file_goc": "bom-v12.pdf",
        "security_level": "internal",
        "calculation_provenance": make_calculation_provenance(plan, derive_claim(plan)),
    })]

    emitted = list(_run_generation(module, question="Tổng BOM là bao nhiêu?", docs=docs))

    assert emitted == ["Normal generation path."]
    assert initialized == [True]


def test_vision_retries_share_the_request_wide_provider_budget(monkeypatch):
    from tenacity import wait_none

    from mech_chatbot.llm import vision_client

    module = _load_pipeline_steps_without_rag_bootstrap(monkeypatch)
    attempts = []

    class FailingVision:
        def generate_content(self, _payload):
            attempts.append("call")
            raise RuntimeError("temporary vision failure")

    budget = RequestBudgetLedger(RequestBudgetLimits(), started_monotonic=0.0)
    monkeypatch.setattr(vision_client, "build_vision_model", lambda: FailingVision())
    monkeypatch.setattr(module.Image, "open", lambda _path: object())
    monkeypatch.setattr(module, "is_retryable_error", lambda _error: True)
    monkeypatch.setattr(module, "wait_exponential", lambda **_kwargs: wait_none())

    with pytest.raises(RequestBudgetExceeded, match="provider_retries"):
        module._analyze_image(
            "image.png",
            "what is this?",
            "vision-budget-test",
            retry_budget=budget,
        )
    assert len(attempts) == 3
    assert budget.provider_retries == 2
