import pytest
from pathlib import Path

from mech_chatbot.governance.provider_smoke import provider_configuration_sha256


def test_process_environment_preserves_controlled_overlay_and_binds_provider():
    from scripts.integrated_eval.math_query_worker import build_matrix_process_environment

    base = {"PROXYLLM_BASE_URL": "https://synthetic.invalid/v1", "GPT_MODEL_NAME": "fake-model",
            "PROXYLLM_API_KEY": "synthetic-not-a-credential", "MAX_CONCURRENT_RAG": "1",
            "RAG_QUERY_DECOMPOSITION_ENABLED": "true",
            "RAG_EVAL_PROVIDER_CONFIGURATION_SHA256": "untrusted"}
    overlay = {"RAG_QUERY_DECOMPOSITION_ENABLED": "false", "RAG_EXECUTION_CONTEXT": "evaluation"}
    digest = provider_configuration_sha256({"endpoint": "https://synthetic.invalid/v1",
        "model": "fake-model", "max_concurrent_rag": 1})
    result = build_matrix_process_environment(base, overlay, expected_provider_sha256=digest)
    assert result["RAG_QUERY_DECOMPOSITION_ENABLED"] == "false"
    assert result["RAG_EVAL_PROVIDER_CONFIGURATION_SHA256"] == digest
    assert base["RAG_QUERY_DECOMPOSITION_ENABLED"] == "true"
    with pytest.raises(ValueError, match="matrix_provider_environment_invalid"):
        build_matrix_process_environment(base, {**overlay, "GPT_MODEL_NAME": "other"},
                                         expected_provider_sha256=digest)


def test_planned_process_arm_rejects_provider_drift_before_start(tmp_path):
    from scripts.integrated_eval.math_query_worker import run_planned_quality_arm

    arm = {"row": "math_only", "label": "baseline", "environment": {},
           "command": ["unused", "-m", "scripts.eval.run_eval", "--manifest", "missing",
                       "--output-dir", str(tmp_path / "output"), "--run-label", "baseline"]}
    with pytest.raises(ValueError, match="matrix_provider_environment_invalid"):
        run_planned_quality_arm(arm, source_root=tmp_path, python=Path("missing-python"),
            base_environment={}, expected_provider_sha256="a" * 64, timeout_seconds=10,
            expected_cases=[], expected_preflight={})
    assert not (tmp_path / "output").exists()
