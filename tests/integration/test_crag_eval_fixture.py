"""Opt-in end-to-end staging fixture verification.

Requires RUN_CRAG_EVAL_FIXTURE=1, RUN_DB_TESTS=1, RUN_QDRANT_TESTS=1 and the
staging QDRANT_COLLECTION. It writes and then removes only crag-eval-v1 data.
"""

import os

import pytest


pytestmark = [pytest.mark.integration, pytest.mark.security, pytest.mark.slow]


@pytest.mark.skipif(
    not all(os.getenv(name) == "1" for name in ("RUN_CRAG_EVAL_FIXTURE", "RUN_DB_TESTS", "RUN_QDRANT_TESTS")),
    reason="explicit CRAG fixture, SQL and Qdrant opt-ins are required",
)
def test_crag_fixture_ingest_publish_retrieval_and_denial(tmp_path):
    from scripts.crag_eval.cleanup_fixture import cleanup_fixture
    from scripts.crag_eval.generate_fixture import generate_fixture
    from scripts.crag_eval.ingest_fixture import ingest_fixture
    from scripts.crag_eval.preflight import run_live_preflight
    from scripts.eval.run_eval import load_manifest_files
    from mech_chatbot.rag.service import chat_with_rag

    # The live scripts intentionally use the fixed workspace asset root so cleanup can prove scope.
    from scripts.crag_eval.constants import DEFAULT_OUTPUT
    generate_fixture(DEFAULT_OUTPUT)
    try:
        result = ingest_fixture(DEFAULT_OUTPUT)
        assert result["completed"] + result["skipped"] == 5
        cases = load_manifest_files([DEFAULT_OUTPUT / "eval_manifest.jsonl"])
        assert run_live_preflight(cases)["passed"] is True

        allowed = next(case for case in cases if case["id"] == "crag-number-thousands")
        stream, *_ = chat_with_rag(
            allowed["question"], None, [], [], allowed["user_department"], allowed["user_roles"],
            allowed["allowed_departments"], allowed["max_security_level"], allowed["allowed_sites"],
        )
        assert "1,500" in "".join(stream)

        denied = next(case for case in cases if case["id"] == "crag-restricted-denial")
        stream, _, _, _, debug = chat_with_rag(
            denied["question"], None, [], [], denied["user_department"], denied["user_roles"],
            denied["allowed_departments"], denied["max_security_level"], denied["allowed_sites"],
        )
        answer = "".join(stream)
        assert "CRAG-EVAL-SECRET-RED" not in answer
        assert all("crag_eval_restricted_v1.md" not in str(doc.get("file_goc", "")).lower() for doc in debug.get("retrieved_docs", []))
    finally:
        cleanup_fixture(DEFAULT_OUTPUT)
