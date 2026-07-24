from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from datetime import date, timedelta
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient
from langchain_core.documents import Document

from mech_chatbot.api import rag_server
from mech_chatbot.config.settings import Settings
from mech_chatbot.llm.external_ai import ExternalAICallCancelled
from mech_chatbot.rag.context_builders import (
    build_common_metadata_context,
    format_docs,
    hydrate_parent_context,
)
from mech_chatbot.rag.execution import (
    RagCancelled,
    RagCitation,
    RagCompleted,
    RagDiagnostics,
    RagFailed,
    RagInvocation,
    RagPrepared,
    RagRuntimeContract,
    RagToken,
    RequestBudgetExceeded,
    RequestBudgetLedger,
    RequestBudgetLimits,
    collect_rag_events,
    consume_rag_events,
)


pytestmark = pytest.mark.unit

SERVICE_HEADERS = {"X-RAG-Service-Token": "wave5-service-token"}


@pytest.fixture
def rag_client(monkeypatch):
    from mech_chatbot.auth import core

    executor = ThreadPoolExecutor(max_workers=2)
    application = rag_server.create_rag_app(
        Settings(
            RAG_REQUIRE_SERVICE_AUTH=True,
            RAG_SERVICE_TOKEN="wave5-service-token",
        )
    )
    state = application.state.rag_server
    application.state.rag_server = replace(
        state,
        runtime=SimpleNamespace(
            executor=object(),
            thread_pool=executor,
            semaphore=rag_server.asyncio.Semaphore(2),
            runtime_contract=RagRuntimeContract("production", False, 120.0),
        ),
        ready=True,
    )
    monkeypatch.setattr(
        core,
        "load_user_profile",
        lambda user_id=None, username=None: {
            "user_id": user_id or 17,
            "username": username or "wave5-viewer",
            "department": "Technical",
            "roles": ["viewer"],
            "allowed_departments": ["Technical"],
            "max_security_level": "internal",
            "allowed_sites": ["HQ"],
        },
    )

    client = TestClient(application)
    try:
        yield client
    finally:
        client.close()
        executor.shutdown(wait=True)


@pytest.mark.parametrize(
    "value",
    [
        None,
        {
            "execution_context": "unknown",
            "evaluation_force_ambiguous": False,
            "request_deadline_seconds": 1,
        },
        {
            "execution_context": "production",
            "evaluation_force_ambiguous": "false",
            "request_deadline_seconds": 1,
        },
        {
            "execution_context": "production",
            "evaluation_force_ambiguous": False,
            "request_deadline_seconds": True,
        },
        {
            "execution_context": "production",
            "evaluation_force_ambiguous": False,
            "request_deadline_seconds": "never",
        },
        {
            "execution_context": "production",
            "evaluation_force_ambiguous": False,
            "request_deadline_seconds": 0,
        },
        {
            "execution_context": "production",
            "evaluation_force_ambiguous": False,
            "request_deadline_seconds": float("inf"),
        },
    ],
)
def test_runtime_contract_rejects_ambiguous_or_unsafe_process_settings(value):
    with pytest.raises(ValueError):
        RagRuntimeContract.from_mapping(value)


def test_runtime_contract_accepts_an_existing_contract_without_reinterpreting_it():
    contract = RagRuntimeContract("production", False, 120.0)

    assert RagRuntimeContract.from_mapping(contract) is contract
    assert contract.is_controlled_demo is True


def test_typed_invocation_rejects_an_unknown_execution_mode():
    with pytest.raises(ValueError, match="Unsupported RAG execution mode"):
        RagInvocation(trace_id="wave5-invalid-mode", mode="unknown")


def test_diagnostics_normalize_malformed_numeric_metrics_without_raising():
    diagnostics = RagDiagnostics.from_mapping(
        {
            "planner_count": object(),
            "generation_metrics": {
                "input_tokens": "invalid",
                "estimated_cost": "invalid",
            },
        }
    )

    assert diagnostics.budget.planners == 0
    assert diagnostics.generation.input_tokens == 0
    assert diagnostics.generation.estimated_cost == 0.0
    assert diagnostics != object()


def test_request_budget_rejects_unknown_fields_and_cumulative_overuse():
    ledger = RequestBudgetLedger(
        limits=RequestBudgetLimits(provider_retries=1),
        started_monotonic=10.0,
    )

    with pytest.raises(ValueError, match="Unknown request budget field"):
        ledger.record("unknown", 1)

    ledger.record("provider_retries", -5)
    assert ledger.provider_retries == 0
    ledger.consume_provider_retry()
    with pytest.raises(RequestBudgetExceeded, match="provider_retries"):
        ledger.consume_provider_retry()


@pytest.mark.parametrize(
    "events, message",
    [
        ([RagToken("early")], "RagToken before RagPrepared"),
        ([RagCitation({"doc_id": 1})], "RagCitation before RagPrepared"),
        ([RagCompleted("answered", "trace", {})], "completed before RagPrepared"),
        ([RagPrepared("", (), (), {}), RagPrepared("", (), (), {})], "more than once"),
        ([RagPrepared("", (), (), {})], "without RagCompleted"),
        ([], "without RagPrepared"),
        (
            [
                RagPrepared("", (), (), {}),
                RagCompleted("answered", "trace", {}),
                RagToken("late"),
            ],
            "event after completion",
        ),
    ],
)
def test_typed_event_consumer_rejects_invalid_lifecycle_sequences(events, message):
    with pytest.raises(RuntimeError, match=message):
        consume_rag_events(iter(events))


@pytest.mark.parametrize(
    "terminal",
    [
        RagCancelled("cancelled"),
        RagCancelled("cancelled", cause=TimeoutError("provider timeout")),
    ],
)
def test_legacy_event_collection_preserves_typed_cancellation(terminal):
    if terminal.cause is None:
        with pytest.raises(ExternalAICallCancelled, match="cancelled"):
            collect_rag_events(iter([RagPrepared("", (), (), {}), terminal]))
    else:
        with pytest.raises(TimeoutError) as exc_info:
            collect_rag_events(iter([RagPrepared("", (), (), {}), terminal]))
        assert exc_info.value is terminal.cause


def test_chat_without_attributed_sources_clears_reference_material(rag_client, monkeypatch):
    class SourceFreeExecutor:
        def run(self, _request, invocation, cancellation):
            yield RagPrepared("untrusted reference", ("unused.png",), (), {})
            yield RagToken("Answer without a source attribution")
            yield RagCompleted("answered", invocation.trace_id, {})

    state = rag_client.app.state.rag_server
    rag_client.app.state.rag_server = replace(
        state,
        runtime=SimpleNamespace(
            executor=SourceFreeExecutor(),
            thread_pool=state.runtime.thread_pool,
            semaphore=state.runtime.semaphore,
            runtime_contract=state.runtime.runtime_contract,
        ),
    )

    response = rag_client.post(
        "/chat",
        headers=SERVICE_HEADERS,
        json={"username": "wave5-viewer", "user_question": "How?"},
    )

    assert response.status_code == 200
    assert response.json()["response"] == "Answer without a source attribution"
    assert response.json()["ref_text"] == ""
    assert response.json()["ref_images"] == []


def test_stream_rejects_requests_before_runtime_is_ready(rag_client, monkeypatch):
    state = rag_client.app.state.rag_server
    rag_client.app.state.rag_server = replace(state, ready=False)

    response = rag_client.post(
        "/chat/stream",
        headers=SERVICE_HEADERS,
        json={"username": "wave5-viewer", "user_question": "How?"},
    )

    assert response.status_code == 503
    assert response.json() == {"detail": "RAG system is not loaded yet."}


def test_stream_rejects_a_non_numeric_replay_expiry(rag_client, monkeypatch):
    from mech_chatbot.evaluation import crag_pilot

    verification = {}

    def reject_signature(**kwargs):
        verification.update(kwargs)
        return False

    monkeypatch.setattr(
        crag_pilot,
        "canonical_payload_sha256",
        lambda _payload: "payload-hash",
    )
    monkeypatch.setattr(crag_pilot, "verify_replay_signature", reject_signature)

    response = rag_client.post(
        "/chat/stream",
        headers={
            **SERVICE_HEADERS,
            "X-RAG-Pilot-Replay": "true",
            "X-RAG-Pilot-Experiment-ID": "experiment-1",
            "X-RAG-Matched-Pair-ID": "pair-1",
            "X-RAG-Original-Trace-ID": "trace-original",
            "X-RAG-Assigned-Arm": "candidate",
            "X-RAG-Pilot-Replay-Expires": "not-a-number",
            "X-RAG-Pilot-Payload-SHA256": "payload-hash",
            "X-RAG-Pilot-Replay-Signature": "invalid-signature",
        },
        json={"username": "wave5-viewer", "user_question": "How?"},
    )

    assert response.status_code == 403
    assert response.json() == {"detail": "Invalid CRAG pilot replay signature"}
    assert verification["expires_at"] == 0
    assert verification["signature"] == "invalid-signature"


def test_common_metadata_with_future_expiry_does_not_emit_a_warning(monkeypatch):
    from mech_chatbot.db import repository

    expiry = date.today() + timedelta(days=1)
    monkeypatch.setattr(
        repository,
        "get_common_metadata_for_rag",
        lambda _doc_ids: {
            9: {
                "title": "Current procedure",
                "expiry_date": expiry.isoformat(),
            }
        },
    )

    rendered = build_common_metadata_context(
        [Document(page_content="evidence", metadata={"doc_id": 9})]
    )

    assert "Current procedure" in rendered
    assert "[CANH BAO:" not in rendered


def test_parent_hydration_preserves_opted_out_and_unkeyed_documents():
    opted_out = Document(
        page_content="local context",
        metadata={
            "doc_id": 7,
            "parent_section": "A",
            "parent_context_enabled": False,
        },
    )
    unkeyed = Document(page_content="standalone context", metadata={})

    assert hydrate_parent_context([opted_out, unkeyed]) == [opted_out, unkeyed]


def test_parent_hydration_fails_closed_when_vector_storage_is_unavailable(monkeypatch):
    class UnavailableVectorStore:
        def scroll(self, **_kwargs):
            raise RuntimeError("vector store unavailable")

    selected = Document(
        page_content="selected evidence",
        metadata={
            "doc_id": 73,
            "parent_page": 3,
            "site": "HQ",
            "phong_ban_quyen": ["Technical"],
            "security_level": "internal",
            "servable": True,
            "publication_state": "published",
            "lifecycle_status": "published",
            "review_status": "approved",
            "is_current": True,
        },
    )

    assert hydrate_parent_context(
        [selected],
        max_workers=1,
        client=UnavailableVectorStore(),
        collection_name="test-knowledge",
    ) == [selected]


def test_document_formatter_supports_list_material_codes_and_string_btp_codes():
    rendered = format_docs(
        [
            Document(
                page_content="evidence",
                metadata={
                    "doc_id": 5,
                    "trang_so": 2,
                    "ma_doi_tuong": ["OBJ-5"],
                    "ma_btp": "BTP-5",
                    "ma_vat_tu": ["SUS304", "AL6061"],
                    "is_current": False,
                    "is_archived": False,
                },
            )
        ]
    )

    assert "| MA: OBJ-5" in rendered
    assert "| BTP: BTP-5" in rendered
    assert "| VAT TU: SUS304, AL6061" in rendered
    assert "| SOURCE_ID: D5P2" in rendered
