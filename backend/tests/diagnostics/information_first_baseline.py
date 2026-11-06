"""Explicit GD1 diagnostic: real control flow, isolated data/model boundaries.

Not named test_*: known target failures must not silently become normal CI.
Run explicitly with pytest. No application implementation is changed here.
"""
from __future__ import annotations

from datetime import date
import hashlib
import importlib.metadata
import json
import logging
import os
from pathlib import Path
import platform
import re
import socket
import subprocess
import time
from types import SimpleNamespace

import pytest
from langchain_core.documents import Document
from langchain_core.runnables import RunnableLambda

from tests.unit.rag.test_rag_pipeline_public_characterization import (
    offline_pipeline, _run, _answer, _trace_events,
)

ROOT = Path(__file__).resolve().parents[3]
PACK = ROOT / "reports/rag-information-first-gd1"
DATA = json.loads((PACK / "development.json").read_text(encoding="utf-8"))
SOURCES = {source["id"]: source for source in DATA["documents"]}
pytestmark = pytest.mark.unit


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


@pytest.fixture(autouse=True)
def isolated_environment(monkeypatch):
    def deny_network(*_args, **_kwargs):
        raise AssertionError("GD1 forbids external connections")

    monkeypatch.setattr(socket.socket, "connect", deny_network)
    monkeypatch.setattr(socket.socket, "connect_ex", deny_network)
    monkeypatch.setattr(socket, "create_connection", deny_network)
    from mech_chatbot.domain import serving_state

    class FrozenDate(date):
        @classmethod
        def today(cls):
            return cls.fromisoformat(DATA["as_of"])

    monkeypatch.setattr(serving_state, "date", FrozenDate)


@pytest.fixture(scope="module")
def journal():
    lock = json.loads((PACK / "lock.json").read_text(encoding="utf-8"))
    for filename, expected in lock["files"].items():
        assert digest(ROOT / filename) == expected, f"Snapshot drift: {filename}"
    head = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip()
    assert head == lock["source_commit"], "Baseline must bind the inspected commit"
    assert not subprocess.check_output(
        ["git", "diff", "HEAD", "--", "backend/src", "frontend/src"], cwd=ROOT,
    ), "Application sources must be unchanged for this baseline"
    rows = []
    yield rows
    output = os.getenv("GD1_OUTPUT")
    if output:
        target = Path(output).resolve()
        assert target.is_relative_to(ROOT), "Keep diagnostics in this workspace"
        report = {
            "schema": "gd1-observed-baseline-v1", "source_commit": head,
            "lock_sha256": digest(PACK / "lock.json"),
            "as_of": DATA["as_of"], "scope": "offline_control_flow_diagnostic",
            "boundaries": {
                "executor_policy_source_checks": "production_code",
                "retrieval_ranking": "supplied_candidates_not_live_search",
                "generation": "scripted_drafts_including_adversarial_drafts",
                "sql_qdrant_provider_network": "disabled",
                "ocr": "manually_verified_transcript_not_fresh_ocr",
            },
            "configuration": {
                "cache": False, "parent_context": False, "rerank_provider": False,
                "crag": False, "query_decomposition": False,
                "evidence_verifier": False, "strict_answer_mode": True,
                "protected_file_actor": DATA["actor"], "fixture_retrieval_k": 5,
                "executor_scope": "HR/viewer/internal/HCM; no account identity in reused _run helper",
                "python": platform.python_version(),
                "packages": {name: importlib.metadata.version(name) for name in (
                    "pytest", "langchain-core", "langchain-qdrant", "qdrant-client", "pydantic",
                )},
            },
            "cases": rows,
            "summary": {
                "executed": len(rows),
                "target_met": sum(r.get("target_met") is True for r in rows),
                "target_not_met": sum(r.get("target_met") is False for r in rows),
                "harness_errors": sum("harness_error" in r for r in rows),
                "holdout_executed": 0,
                "live_recall": None, "live_p95": None, "live_cost": None,
                "business_accepted": False,
            },
        }
        # Do not overwrite a frozen baseline or an earlier attempted run.
        target.parent.mkdir(parents=True, exist_ok=True)
        with target.open("x", encoding="utf-8") as stream:
            stream.write(json.dumps(report, ensure_ascii=False, indent=2) + "\n")


def documents_for(case):
    return [Document(page_content=SOURCES[key]["text"], metadata={
        **SOURCES[key]["metadata"],
        "source_hash": hashlib.sha256(SOURCES[key]["text"].encode()).hexdigest(),
        "snapshot_id": "gd1-20261031",
    }) for key in case["sources"]]


def pipeline_observation(case, docs, offline, monkeypatch, caplog):
    from mech_chatbot.domain.source_ids import extract_source_ids
    from mech_chatbot.rag.execution import RagCompleted
    from mech_chatbot.rag import pipeline_steps
    documents, calls, executor, runtime = offline
    documents.extend(docs)
    provider_inputs = []

    def respond(prompt, **_kwargs):
        provider_inputs.append(str(prompt))
        return case.get("draft", "unexpected generation")

    executor._provider_adapter.client = RunnableLambda(respond)
    if case["probe"] == "timeout":
        from mech_chatbot.rag.execution import RequestDeadlineExceeded

        def deadline(*_args, **_kwargs):
            raise RequestDeadlineExceeded("controlled GD1 deadline")

        monkeypatch.setattr(pipeline_steps, "_explicit_hybrid_rrf", deadline)
    trace_id = "gd1-" + case["id"]
    with caplog.at_level(logging.INFO, logger="RagTrace"):
        events = _run(case["question"], trace_id=trace_id, executor=executor)
    final = events[-1]
    answer = _answer(events)
    diagnostics = dict(final.diagnostics) if isinstance(final, RagCompleted) else {}
    # Only safe fields from the existing trace are exported.
    fields = {"event", "phase", "status", "step", "reason", "refusal_reason", "docs_count"}
    trace = [{k: v for k, v in row.items() if k in fields}
             for row in _trace_events(caplog, trace_id)]
    return {
        "entrypoint": "DefaultRagExecutor.run",
        "terminal_event": type(final).__name__,
        "error_code": getattr(final, "code", None),
        "outcome": getattr(final, "outcome", None),
        "reason": getattr(final, "refusal_reason", None),
        "answer": answer,
        "source_ids": sorted({int(re.match(r"D(\d+)", source).group(1))
                              for source in extract_source_ids(answer)}),
        "citation_candidate_ids": sorted({int(d["doc_id"]) for d in diagnostics.get("citation_docs", [])
                              if d.get("doc_id") is not None}),
        "generation_calls": len(provider_inputs),
        "generation_context_sources": sorted({d.metadata["doc_id"] for d in docs
            if any(f'D{d.metadata["doc_id"]}P1' in value for value in provider_inputs)}),
        "retrieval_boundary_calls": len(calls), "trace": trace,
    }


def boundary_observation(case, docs, monkeypatch):
    from mech_chatbot.rag import pipeline_steps as steps
    probe = case["probe"]
    if probe == "merge":
        calls = []

        def retrieve(_q, _f, **kwargs):
            calls.append(kwargs["phase"])
            return ([docs[0]] if kwargs["phase"] == "strict_exact" else docs), "offline_rrf"

        monkeypatch.setattr(steps, "_explicit_hybrid_rrf", retrieve)
        result = steps._retrieve(
            new_part_ids=["9.3.03844"], strict_filter="strict", broad_filter="broad",
            is_bom_query=True, query_to_search=case["question"], rbac_filter=None,
            vectorstore=object(), client=object(), collection_name="gd1-offline",
        )
        return {"entrypoint": "shared_retrieval", "source_ids": [d.metadata["doc_id"] for d in result[0]],
                "retrieval_phases": calls, "mode": result[2]}
    if probe == "resolver":
        lifecycle = SimpleNamespace(refuse=lambda _reason: None)
        terminal, returned = steps._disambiguate(
            retrieved_docs=docs, user_question=case["question"], new_part_ids=[],
            intent_data={"version_policy": "current_only", "query_scope": "general_policy",
                         "need_disambiguation": False}, response_language="vi", current_part_ids=[],
            trace_id="gd1-" + case["id"], t_start=time.time(),
            make_debug_info=lambda _: {}, lifecycle=lifecycle,
        )
        return {"entrypoint": "shared_disambiguation", "terminal": terminal is not None,
                "answer": "".join(terminal[0]) if terminal else "",
                "source_ids": [d.metadata["doc_id"] for d in returned]}
    if probe == "citation":
        from mech_chatbot.application.chat_citations import build_citation_list
        cards = build_citation_list(
            [{**docs[0].metadata, "version_no": v} for v in (1, 2)],
            page_has_vision=lambda *_: False,
        )
        return {"entrypoint": "build_citation_list", "versions": [c["version_no"] for c in cards],
                "scope_limit": "same DocID/page with two revisions; current DB identity invariant not assumed"}
    if probe == "access":
        from mech_chatbot.application.protected_files import (
            DocumentAccessRecord, ProtectedFileActor, evaluate_document_access,
        )
        md = docs[0].metadata
        profile = {**DATA["actor"], "roles": case["roles"],
                   "allowed_departments": case.get("allowed_departments", ["HR"])}
        record = DocumentAccessRecord(
            doc_id=md["doc_id"], ten_file=md["file_goc"], file_path=None, thu_muc="HR",
            security_level=md["security_level"], site=md["site"],
            lifecycle_status=md["lifecycle_status"], review_status=md["review_status"],
            departments=("HR",), servable=md["servable"], is_current=md["is_current"],
            effective_status=md["effective_status"], version_no=md["version_no"],
        )
        decision = evaluate_document_access(
            ProtectedFileActor.from_profile(profile), record, strict_site_filter=True,
        )
        return {"entrypoint": "evaluate_document_access", "allowed": decision.allowed,
                "reason": decision.reason}
    if probe == "lifecycle":
        from mech_chatbot.domain.serving_state import is_currently_servable
        changes = {
            "current": {}, "expired": {"expiry_date": "2026-10-30"},
            "future": {"effective_date": "2026-11-01"},
            "draft": {"lifecycle_status": "draft"}, "revoked": {"servable": False},
            "invalid_date": {"expiry_date": "invalid"},
            "missing_business_dates": {"effective_date": None, "expiry_date": None},
        }
        accepted = [name for name, values in changes.items() if is_currently_servable(
            {**docs[0].metadata, **values}, today=date.fromisoformat(DATA["as_of"]), require_current=True)]
        return {"entrypoint": "is_currently_servable", "allowed_states": accepted,
                "scope_limit": "document serving is not proof that an activity is ongoing"}
    if probe == "schema":
        from mech_chatbot.api.routers.chat import ChatMessageRequest
        return {"entrypoint": "ChatMessageRequest", "request_fields": sorted(ChatMessageRequest.model_fields)}
    raise ValueError(f"Unknown diagnostic probe: {probe}")


def target_errors(case, observed):
    failures = []
    for key, expected in case["expected"].items():
        if key == "contains":
            failures += [f"missing answer term: {term}" for term in expected
                         if term not in observed.get("answer", "")]
        elif key == "must_not_emit":
            failures += [f"unsupported draft emitted: {term}" for term in expected
                         if term in observed.get("answer", "")]
        elif key == "no_generation":
            if observed.get("generation_calls") != 0:
                failures.append("generation ran after injected deadline")
        elif key == "request_fields":
            failures += [f"missing contract: {field}" for field in expected
                         if field not in observed.get(key, [])]
        elif observed.get(key) != expected:
            failures.append(f"{key}: expected {expected!r}; observed {observed.get(key)!r}")
    return failures


@pytest.mark.parametrize("case", DATA["cases"], ids=lambda case: case["id"])
def test_development_target(case, offline_pipeline, monkeypatch, caplog, journal):
    started = time.perf_counter()
    docs = documents_for(case)
    row = {"case_id": case["id"], "probe": case["probe"],
           "input_source_ids": [d.metadata["doc_id"] for d in docs], "expected": case["expected"]}
    try:
        observed = (pipeline_observation(case, docs, offline_pipeline, monkeypatch, caplog)
                    if case["probe"] in {"pipeline", "timeout"}
                    else boundary_observation(case, docs, monkeypatch))
        failures = target_errors(case, observed)
        row.update(observed=observed, target_met=not failures, failures=failures)
    except Exception as error:
        row.update(harness_error=type(error).__name__)
        raise
    finally:
        row["elapsed_ms_local_only"] = round((time.perf_counter() - started) * 1000, 3)
        journal.append(row)
    assert not failures, "; ".join(failures)


def test_corpus_and_split_contract(journal):
    holdout = json.loads((PACK / "holdout.json").read_text(encoding="utf-8"))
    dev_ids = {d["metadata"]["doc_id"] for d in DATA["documents"]}
    hold_ids = {d["metadata"]["doc_id"] for d in holdout["documents"]}
    assert not dev_ids & hold_ids
    assert not ({d["text"] for d in DATA["documents"]} & {d["text"] for d in holdout["documents"]})
    for pack in (DATA, holdout):
        sources = {d["id"]: d for d in pack["documents"]}
        assert len(sources) == len(pack["documents"])
        assert len({c["id"] for c in pack["cases"]}) == len(pack["cases"])
        for case in pack["cases"]:
            assert set(case["sources"]) <= sources.keys()
            for evidence in case["evidence_quotes"]:
                assert evidence["quote"] in sources[evidence["source_id"]]["text"]
    assert holdout["execution_status"] == "not_executed_or_used_for_tuning"


def test_grader_rejects_missing_source_and_unsupported_draft():
    case = {"expected": {"source_ids": [101, 102], "must_not_emit": ["invented fact"]}}
    assert len(target_errors(case, {"source_ids": [101], "answer": "invented fact"})) == 2
    assert not target_errors(case, {"source_ids": [101, 102], "answer": "supported excerpt"})
