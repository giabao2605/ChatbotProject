"""Remaining behavior branches for the legacy RAG step orchestration seam.

The layered-refactor plan keeps these as temporary characterization tests until
``RagExecutor.run`` owns the same contracts.  Only provider, vector-store, time,
and audit persistence boundaries are replaced with deterministic fakes.
"""

from __future__ import annotations

from contextlib import contextmanager
import importlib
import sys
import threading
import time
from types import ModuleType, SimpleNamespace

from langchain_core.runnables import Runnable
import pytest

pytestmark = pytest.mark.unit


PIPELINE_STEPS_MODULE = "mech_chatbot.rag.pipeline_steps"
_MISSING = object()


@pytest.fixture
def load_steps(monkeypatch):
    """Import the canonical module with a deterministic bootstrap boundary."""
    rag_package = importlib.import_module("mech_chatbot.rag")
    original_module = sys.modules.get(PIPELINE_STEPS_MODULE, _MISSING)
    original_package_attribute = getattr(rag_package, "pipeline_steps", _MISSING)

    def load(*, strict=True):
        bootstrap = ModuleType("mech_chatbot.rag.bootstrap")
        bootstrap.STRICT_ANSWER_MODE = strict
        bootstrap.client = object()
        bootstrap.vectorstore = SimpleNamespace()
        monkeypatch.setitem(sys.modules, "mech_chatbot.rag.bootstrap", bootstrap)
        sys.modules.pop(PIPELINE_STEPS_MODULE, None)
        module = importlib.import_module(PIPELINE_STEPS_MODULE)
        assert module.__name__ == PIPELINE_STEPS_MODULE
        module._test_strict_answer_mode = strict
        return module

    yield load

    if original_module is _MISSING:
        sys.modules.pop(PIPELINE_STEPS_MODULE, None)
    else:
        sys.modules[PIPELINE_STEPS_MODULE] = original_module
    if original_package_attribute is _MISSING:
        if hasattr(rag_package, "pipeline_steps"):
            delattr(rag_package, "pipeline_steps")
    else:
        rag_package.pipeline_steps = original_package_attribute


def _doc(content="Approved policy.", **metadata):
    values = {
        "doc_id": 77,
        "trang_so": 4,
        "file_goc": "policy.pdf",
        "version_no": 2,
        "domain": "generic",
        "security_level": "internal",
        "external_processing_policy": "all_external",
        "servable": True,
        "publication_state": "published",
        "lifecycle_status": "published",
        "review_status": "approved",
        "effective_status": "active",
        "is_current": True,
    }
    values.update(metadata)
    return SimpleNamespace(page_content=content, metadata=values)


class _Provider(Runnable):
    """Deterministic fake for the external streaming provider boundary."""

    def __init__(self, scripts):
        self.scripts = list(scripts)

    def invoke(self, _input, config=None, **_kwargs):
        del config
        script = self.scripts.pop(0)
        if isinstance(script, BaseException):
            raise script
        return "".join(script)

    def stream(self, _input, config=None, **_kwargs):
        del config
        script = self.scripts.pop(0)
        if isinstance(script, BaseException):
            raise script
        yield from script


class _Retriever:
    def __init__(self, documents):
        self.documents = list(documents)

    def invoke(self, _query):
        return list(self.documents)


class _FallbackVectorStore:
    embeddings = None
    sparse_embeddings = None

    def __init__(self, documents, *, documents_by_filter=None):
        self.documents = list(documents)
        self.documents_by_filter = {
            key: list(values) for key, values in (documents_by_filter or {}).items()
        }

    def as_retriever(self, **kwargs):
        if not self.documents_by_filter:
            return _Retriever(self.documents)
        payload_filter = kwargs["search_kwargs"]["filter"]
        return _Retriever(self.documents_by_filter[payload_filter])


class _Lifecycle:
    def __init__(self):
        self.refusal_reason = None

    def refuse(self, reason):
        self.refusal_reason = reason


def _plan(
    steps,
    *,
    docs=None,
    answer="",
    outcome=None,
    budget=None,
    provider=None,
    runtime_overrides=None,
    invocation=None,
):
    documents = list(docs or [_doc()])
    provider = provider or _Provider([["unused provider response"]])
    retrieval_config = {
        "strict_answer_mode": bool(
            getattr(steps, "_test_strict_answer_mode", True)
        ),
        "strict_realtime_streaming": False,
        "claim_repair_enabled": False,
        "grounded_math_enabled": False,
        "stream_max_attempts": 3,
        "auto_source_cards": True,
    }
    retrieval_config.update(runtime_overrides or {})
    return steps.GenerationPlan(
        turn=steps.GenerationTurn(
            user_question="Quy định hiện hành là gì?",
            effective_question="Quy định hiện hành là gì?",
            chat_history_str="",
            new_part_ids=[],
            response_language="vi",
            user_department="HR",
            user_roles=["viewer"],
        ),
        evidence=steps.GenerationEvidence(
            context_text="Quy định yêu cầu nhân viên nộp đề nghị.",
            retrieved_docs=documents,
            intent_data={"version_policy": "current_only"},
            base_k=5,
            retrieval_mode="general:fake",
        ),
        control=steps.GenerationControl(
            trace_id="remaining-branch-contract",
            started_at=time.time(),
            outcome=outcome or steps.GenerationOutcome(),
            budget=budget,
        ),
        explicit_negative_answer=answer,
        runtime=SimpleNamespace(
            invocation=invocation or SimpleNamespace(
                evaluation_draft_override=None,
            ),
            retrieval_adapter=SimpleNamespace(**retrieval_config),
            provider_adapter=SimpleNamespace(
                client=provider,
                settings=SimpleNamespace(
                    model_name="test-model",
                    base_url="https://example.invalid/v1",
                ),
                invoke=lambda messages, **kwargs: SimpleNamespace(
                    content=provider.invoke(messages, **kwargs)
                ),
            ),
        ),
    )


@contextmanager
def _allow_external_call(**_kwargs):
    yield None


def _prepare_provider(steps, monkeypatch, scripts):
    provider = _Provider(scripts)
    monkeypatch.setattr(steps, "audited_external_call", _allow_external_call)
    monkeypatch.setattr(steps.time, "sleep", lambda _seconds: None)
    return provider


def _disambiguate(steps, docs, question, *, intent=None):
    lifecycle = _Lifecycle()
    terminal, returned = steps._disambiguate(
        retrieved_docs=docs,
        user_question=question,
        new_part_ids=[],
        intent_data=intent or {"version_policy": "current_only"},
        response_language="vi",
        current_part_ids=[],
        trace_id="remaining-disambiguation",
        t_start=time.time(),
        make_debug_info=lambda values: {"document_count": len(values)},
        lifecycle=lifecycle,
    )
    return lifecycle, terminal, returned


def test_history_uses_default_budget_and_truncates_long_roles(load_steps, monkeypatch):
    steps = load_steps()
    bot = "Sentence. " * 70
    user = "x" * 1300

    rendered, summary, covered = steps._prepare_history(
        [{"role": "assistant", "content": bot}, {"role": "user", "content": user}],
        {},
        "vi",
        history_budget=4000,
        history_summary_enabled=False,
    )

    assert rendered.startswith("Bot: ")
    assert " [...]\nKhach: " in rendered
    assert rendered.endswith("x" * 1200 + " [...]\n")
    assert summary is None
    assert covered is None


def test_history_reuses_previous_summary_when_refresh_is_not_due(load_steps, monkeypatch):
    steps = load_steps()

    rendered, summary, covered = steps._prepare_history(
        [{"role": "user", "content": f"turn {index}"} for index in range(13)],
        {"history_summary": "- Approved earlier decision", "summary_covered": 99},
        "en-US",
        history_summary_enabled=True,
    )

    assert rendered.startswith(
        "=== EARLIER CONVERSATION SUMMARY ===\n- Approved earlier decision"
    )
    assert summary == "- Approved earlier decision"
    assert covered == 99


def test_general_retrieval_recovers_invalid_top_k_through_vectorstore_fallback(
    load_steps,
    monkeypatch,
):
    steps = load_steps()
    document = _doc("General policy fallback")
    vectorstore = _FallbackVectorStore([document])

    docs, top_k, mode, _started, active_filter = steps._retrieve(
        new_part_ids=[],
        strict_filter=None,
        broad_filter=None,
        is_bom_query=False,
        query_to_search="leave policy",
        rbac_filter=None,
        trace_id="general-fallback",
        vectorstore=vectorstore,
        client=object(),
        collection_name="test",
    )

    assert docs == [document]
    assert top_k == 30
    assert mode == "general:hybrid_fallback"
    assert active_filter is not steps._RETRIEVE_UNSET


def test_explicit_hybrid_search_skips_revalidating_process_owned_runtime(
    load_steps,
    monkeypatch,
):
    steps = load_steps()
    document = _doc("Approved sparse result")
    constructor_calls = []

    class Store:
        def __init__(self, **kwargs):
            constructor_calls.append(kwargs)

        def similarity_search(self, *_args, **_kwargs):
            return [document]

    monkeypatch.setattr("langchain_qdrant.QdrantVectorStore", Store)
    vectorstore = SimpleNamespace(
        embeddings=object(),
        sparse_embeddings=object(),
    )

    documents, mode = steps._explicit_hybrid_rrf(
        "approved query",
        payload_filter=object(),
        dense_top_k=5,
        sparse_top_k=5,
        result_cap=5,
        vectorstore=vectorstore,
        client=object(),
        collection_name="test",
    )

    assert documents == [document]
    assert mode == "explicit_dense_bm25_rrf"
    assert len(constructor_calls) == 2
    assert all(
        call["validate_embeddings"] is False
        and call["validate_collection_config"] is False
        for call in constructor_calls
    )


def test_slow_bm25_search_uses_bounded_dense_fallback(
    load_steps,
    monkeypatch,
):
    steps = load_steps()
    dense_document = _doc("Dense result")
    sparse_calls = []
    trace_events = []

    class Store:
        def __init__(self, **kwargs):
            self.mode = getattr(
                kwargs["retrieval_mode"],
                "value",
                kwargs["retrieval_mode"],
            )

        def similarity_search(self, *_args, **kwargs):
            if self.mode == "sparse":
                sparse_calls.append(kwargs)
                raise TimeoutError("simulated sparse tail latency")
            return [dense_document]

    class VectorStore:
        embeddings = object()
        sparse_embeddings = object()

        def as_retriever(self, **_kwargs):
            pytest.fail("BM25 timeout must not retry sparse through HYBRID")

    monkeypatch.setattr("langchain_qdrant.QdrantVectorStore", Store)
    monkeypatch.setattr(
        steps,
        "log_trace",
        lambda event, trace_id, **fields: trace_events.append(
            {"event": event, "trace_id": trace_id, **fields}
        ),
    )

    documents, mode = steps._explicit_hybrid_rrf(
        "approved query",
        payload_filter=object(),
        dense_top_k=5,
        sparse_top_k=5,
        result_cap=5,
        trace_id="bm25-timeout",
        vectorstore=VectorStore(),
        client=object(),
        collection_name="test",
    )

    assert documents == [dense_document]
    assert mode == "explicit_dense_fallback"
    assert sparse_calls[0]["timeout"] == 3
    bm25_event = next(
        event for event in trace_events if event["event"] == "bm25_retrieval"
    )
    assert bm25_event["fallback"] == "dense"
    assert bm25_event["retry_attempted"] is False
    assert bm25_event["error"] == "TimeoutError"


def test_empty_exact_retrieval_broadens_without_exposing_unservable_documents(
    load_steps,
):
    steps = load_steps()
    published = _doc("Broad published match", doc_id=81)
    expired = _doc("Broad expired match", doc_id=82, effective_status="expired")
    vectorstore = _FallbackVectorStore(
        [],
        documents_by_filter={
            "strict": [],
            "broad": [published, expired],
        },
    )

    docs, top_k, mode, _started, active_filter = steps._retrieve(
        new_part_ids=["PART-81"],
        strict_filter="strict",
        broad_filter="broad",
        is_bom_query=False,
        query_to_search="PART-81",
        rbac_filter=None,
        trace_id=None,
        vectorstore=vectorstore,
        client=object(),
        collection_name="test",
    )

    assert docs == [published]
    assert top_k == 15
    assert mode == "broad_fallback:hybrid_fallback"
    assert active_filter == "broad"


def test_pending_description_selection_rewrites_query_without_forcing_part_id(
    load_steps,
    monkeypatch,
):
    steps = load_steps()
    monkeypatch.setattr(
        steps,
        "analyze_context",
        lambda *_args, **_kwargs: {
            "context_action": "continue",
            "standalone_question": None,
            "llm_resolved": True,
        },
    )
    pending = [
        {"index": 1, "key": "policy-A", "product_name": "Leave policy 2026"}
    ]

    result = steps._rewrite_and_anchor(
        user_question="chọn số 1",
        chat_history=[],
        current_part_ids=[],
        conversation_context={"pending_candidates": pending},
        user_department="HR",
        user_roles=["viewer"],
        allowed_departments=["HR"],
        max_security_level="internal",
        allowed_sites=[],
        trace_id="description-selection",
        t_intent=time.time(),
        runtime=SimpleNamespace(
            conversation_state_enabled=True,
            strict_site_filter=True,
            intent_runtime=None,
        ),
    )

    assert result[0] == "Leave policy 2026"
    assert result[1] == []
    assert result[-1] is False


def test_continuation_anchors_the_active_document(load_steps, monkeypatch):
    steps = load_steps()
    monkeypatch.setattr(
        steps,
        "analyze_context",
        lambda *_args, **_kwargs: {
            "context_action": "continue",
            "standalone_question": None,
            "llm_resolved": True,
        },
    )

    result = steps._rewrite_and_anchor(
        user_question="còn kích thước thì sao?",
        chat_history=[{"role": "user", "content": "Tra PART-9"}],
        current_part_ids=[],
        conversation_context={"active_doc_refs": ["PART-9"]},
        user_department="Technical",
        user_roles=["viewer"],
        allowed_departments=["Technical"],
        max_security_level="internal",
        allowed_sites=[],
        trace_id="continuation-anchor",
        t_intent=time.time(),
        runtime=SimpleNamespace(
            conversation_state_enabled=True,
            strict_site_filter=True,
            intent_runtime=None,
        ),
    )

    assert result[1] == ["PART-9"]
    assert result[-1] is True


def test_disambiguation_selects_the_single_matching_dimension(load_steps):
    steps = load_steps()
    docs = [
        _doc(
            "Bracket 100x200",
            domain="mechanical",
            base_code="PART-9",
            variant_code="A",
            kich_thuoc_tong_the="100x200",
        ),
        _doc(
            "Bracket 300x400",
            domain="mechanical",
            base_code="PART-9",
            variant_code="B",
            kich_thuoc_tong_the="300x400",
        ),
    ]

    lifecycle, terminal, returned = _disambiguate(
        steps,
        docs,
        "Bản vẽ kích thước 100x200",
        intent={"version_policy": "current_only", "dimensions": ["100x200"]},
    )

    assert terminal is None
    assert lifecycle.refusal_reason is None
    assert returned == [docs[0]]


def test_disambiguation_requests_more_detail_when_no_candidate_is_confident(load_steps):
    steps = load_steps()
    docs = [
        _doc(
            "SUS304 bracket",
            domain="mechanical",
            base_code="PART-9",
            variant_code="A",
            vat_lieu="SUS304",
        ),
        _doc(
            "SS400 bracket",
            domain="mechanical",
            base_code="PART-9",
            variant_code="B",
            vat_lieu="SS400",
        ),
    ]

    lifecycle, terminal, returned = _disambiguate(
        steps,
        docs,
        "Bản vẽ vật liệu AL6061",
        intent={"version_policy": "current_only", "materials": ["AL6061"]},
    )

    assert lifecycle.refusal_reason == "no_confident_candidate"
    assert "cung cấp thêm mã bản vẽ" in "".join(terminal[0])
    assert returned == docs


def test_administrative_pages_are_not_misclassified_as_selectable_variants(load_steps):
    steps = load_steps()
    docs = [
        _doc("HR page A", domain="generic", base_code="POLICY", variant_code="A"),
        _doc("HR page B", domain="generic", base_code="POLICY", variant_code="B"),
    ]

    lifecycle, terminal, returned = _disambiguate(
        steps,
        docs,
        "Chính sách nhân sự bằng vật liệu SUS304",
        intent={"version_policy": "current_only", "materials": ["SUS304"]},
    )

    assert terminal is None
    assert lifecycle.refusal_reason is None
    assert returned == docs


def test_grounded_math_malformed_provenance_fails_closed_without_provider(
    load_steps,
    monkeypatch,
):
    steps = load_steps()
    outcome = steps.GenerationOutcome()
    document = _doc(calculation_provenance={"operation": "sum", "references": [{}]})

    answer = "".join(
        steps.generate_answer(
            _plan(
                steps,
                docs=[document],
                outcome=outcome,
                provider=pytest.fail,
                runtime_overrides={"grounded_math_enabled": True},
            )
        )
    )

    assert "thông tin đủ để trả lời" in answer.lower()
    assert outcome.refusal_reason == "grounded_math_post_check"


def test_normal_streaming_returns_provider_chunks_when_strict_mode_is_disabled(
    load_steps,
    monkeypatch,
):
    steps = load_steps(strict=False)
    provider = _prepare_provider(steps, monkeypatch, [["Approved ", "policy."]])

    answer = list(steps.generate_answer(_plan(steps, provider=provider)))

    assert answer == ["Approved ", "policy."]


def test_normal_streaming_retries_rate_limit_before_emitting(load_steps, monkeypatch):
    steps = load_steps(strict=False)
    provider = _prepare_provider(
        steps,
        monkeypatch,
        [RuntimeError("rate limit"), ["Recovered answer."]],
    )
    metrics = {}

    answer = list(
        steps.generate_answer(_plan(steps, provider=provider), metrics=metrics)
    )

    assert answer == ["Recovered answer."]
    assert metrics["provider_retries"] == 1


def test_normal_streaming_does_not_retry_non_retryable_provider_failure(
    load_steps,
    monkeypatch,
):
    steps = load_steps(strict=False)
    provider = _prepare_provider(
        steps,
        monkeypatch,
        [RuntimeError(), ["unexpected retry"]],
    )

    with pytest.raises(RuntimeError):
        list(steps.generate_answer(_plan(steps, provider=provider)))
    assert provider.scripts == [["unexpected retry"]]


def test_normal_streaming_honors_cancellation_before_provider_call(load_steps, monkeypatch):
    from mech_chatbot.llm.external_ai import ExternalAICallCancelled

    steps = load_steps(strict=False)
    provider = _prepare_provider(steps, monkeypatch, [["must not escape"]])
    cancelled = threading.Event()
    cancelled.set()

    with pytest.raises(ExternalAICallCancelled):
        list(
            steps.generate_answer(
                _plan(steps, provider=provider),
                cancel_event=cancelled,
            )
        )
    assert provider.scripts == [["must not escape"]]


def test_guarded_generation_requires_inline_citation_when_source_cards_are_disabled(
    load_steps,
    monkeypatch,
):
    steps = load_steps()
    provider = _prepare_provider(steps, monkeypatch, [["Nhân viên nộp đề nghị."]])
    outcome = steps.GenerationOutcome()

    answer = "".join(
        steps.generate_answer(
            _plan(
                steps,
                outcome=outcome,
                provider=provider,
                runtime_overrides={"auto_source_cards": False},
            )
        )
    )

    assert answer != "Nhân viên nộp đề nghị."
    assert outcome.refusal_reason == "missing_source_page_version"


def test_claim_repair_releases_only_the_grounded_rewrite(load_steps, monkeypatch):
    steps = load_steps()
    provider = _prepare_provider(
        steps,
        monkeypatch,
        [["Chi phí là 999 USD."], ["Chi phí là 10 USD."]],
    )
    plan = _plan(
        steps,
        provider=provider,
        runtime_overrides={"claim_repair_enabled": True},
    )
    plan = steps.GenerationPlan(
        turn=steps.GenerationTurn(
            user_question="Chi phí là bao nhiêu?",
            effective_question="Chi phí là bao nhiêu?",
            chat_history_str="",
            new_part_ids=[],
            response_language="vi",
            user_department="HR",
            user_roles=["viewer"],
        ),
        evidence=steps.GenerationEvidence(
            context_text="Chi phí là 10 USD.",
            retrieved_docs=plan.evidence.retrieved_docs,
            intent_data=plan.evidence.intent_data,
            base_k=plan.evidence.base_k,
            retrieval_mode=plan.evidence.retrieval_mode,
        ),
        control=plan.control,
        runtime=plan.runtime,
    )
    metrics = {}

    answer = "".join(steps.generate_answer(plan, metrics=metrics))

    assert answer == "Chi phí là 10 USD."
    assert metrics["repair_count"] == 1


def test_evaluation_draft_override_is_request_local(load_steps, monkeypatch):
    from mech_chatbot.rag.execution import RagInvocation

    steps = load_steps()
    provider = _prepare_provider(steps, monkeypatch, [["ignored provider draft"]])
    controlled_draft = "Quy định yêu cầu nhân viên nộp đề nghị."
    plan = _plan(
        steps,
        provider=provider,
        invocation=RagInvocation(
            trace_id="remaining-branch-contract",
            mode="evaluation",
            evaluation_draft_override=controlled_draft,
        ),
    )

    assert "".join(steps.generate_answer(plan)) == controlled_draft
