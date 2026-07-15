"""Opt-in ingest, preflight and deterministic calculation on isolated staging."""

import os

import pytest


pytestmark = [pytest.mark.integration, pytest.mark.security, pytest.mark.slow]


@pytest.mark.skipif(
    not all(os.getenv(name) == "1" for name in (
        "RUN_GROUNDED_MATH_EVAL_FIXTURE", "RUN_DB_TESTS", "RUN_QDRANT_TESTS",
    )),
    reason="explicit grounded-math fixture, SQL and Qdrant opt-ins are required",
)
def test_grounded_math_fixture_ingest_preflight_calculate_and_cleanup():
    from scripts.eval.run_eval import load_manifest_files
    from scripts.grounded_math_eval.cleanup_fixture import cleanup_fixture
    from scripts.grounded_math_eval.constants import DEFAULT_OUTPUT
    from scripts.grounded_math_eval.generate_fixture import generate_fixture
    from scripts.grounded_math_eval.ingest_fixture import ingest_fixture
    from scripts.grounded_math_eval.preflight import run_live_preflight
    from mech_chatbot.rag.service import chat_with_rag

    generate_fixture(DEFAULT_OUTPUT)
    try:
        result = ingest_fixture(DEFAULT_OUTPUT)
        assert result["completed"] + result["skipped"] == 3
        cases = load_manifest_files([DEFAULT_OUTPUT / "eval_manifest.jsonl"])
        preflight = run_live_preflight(cases)
        assert preflight["passed"] is True
        case = next(item for item in cases if item["id"] == "math-add")
        stream, _, _, _, debug = chat_with_rag(
            case["question"], None, [], [], case["user_department"], case["user_roles"],
            case["allowed_departments"], case["max_security_level"], case["allowed_sites"],
        )
        answer = "".join(stream)
        assert "2 + 4 = 6 kg" in answer
        assert len(debug.get("calculation_provenance") or []) == 1
    finally:
        cleanup = cleanup_fixture(DEFAULT_OUTPUT)
        assert cleanup["assets_deleted"] is True
