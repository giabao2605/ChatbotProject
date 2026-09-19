"""Synthetic rollback evidence; no rollback command is executed."""
import hashlib
import json

import pytest

from scripts.integrated_eval import math_query_dispatch as dispatch
from scripts.eval.verify_failure_family_rollback import (
    MATH_QUERY_ROLLBACK_PROFILE,
    ROLLBACK_TEST_PROFILES,
)


def test_combined_rollback_profile_requires_the_public_transition_test():
    flags = frozenset({"RAG_GROUNDED_MATH_ENABLED", "RAG_QUERY_DECOMPOSITION_ENABLED"})
    command = ("-m", "pytest",
               "tests/unit/test_rag_pipeline_public_characterization.py::test_math_query_flags_do_not_retain_decomposition_after_disable",
               "-q")
    assert MATH_QUERY_ROLLBACK_PROFILE[flags] == command
    assert flags not in ROLLBACK_TEST_PROFILES
    artifact = {"schema": "rollback-test-evidence-v1", "git_sha": "a" * 40,
                "flags": sorted(flags), "verified_flag_state": {flag: False for flag in flags},
                "passed": True, "tested_at": "2026-09-07T00:00:00Z",
                "command": list(command), "exit_code": 0,
                "stdout_tail": "1 passed [100%]", "stderr_tail": ""}
    raw = json.dumps(artifact).encode()
    assert dispatch.validate_matrix_rollback(
        raw, expected_sha256=hashlib.sha256(raw).hexdigest(), source_commit="a" * 40,
        row="math_query") is None


@pytest.mark.parametrize("change", [{}, {"exit_code": False},
    {"verified_flag_state": {"RAG_GROUNDED_MATH_ENABLED": 0}},
    {"git_sha": "b" * 40}, {"command": ["unapproved"]}, {"passed": False}])
def test_math_rollback_requires_bound_exact_profile_and_disabled_state(change):
    flags = frozenset({"RAG_GROUNDED_MATH_ENABLED"})
    artifact = {"schema": "rollback-test-evidence-v1", "git_sha": "a" * 40,
                "flags": sorted(flags), "verified_flag_state": {flag: False for flag in flags},
                "passed": True, "tested_at": "2026-09-07T00:00:00Z",
                "command": list(ROLLBACK_TEST_PROFILES[flags]), "exit_code": 0,
                "stdout_tail": "2 passed [100%]", "stderr_tail": "", **change}
    raw = json.dumps(artifact).encode()
    kwargs = {"expected_sha256": hashlib.sha256(raw).hexdigest(),
              "source_commit": "a" * 40, "row": "math_only"}
    if change:
        with pytest.raises(ValueError, match="matrix_rollback_invalid"):
            dispatch.validate_matrix_rollback(raw, **kwargs)
    else:
        assert dispatch.validate_matrix_rollback(raw, **kwargs) is None
        with pytest.raises(ValueError, match="matrix_rollback_invalid"):
            dispatch.validate_matrix_rollback(raw + b" ", **kwargs)
        with pytest.raises(ValueError, match="matrix_rollback_invalid"):
            dispatch.validate_matrix_rollback(raw, **{**kwargs, "row": "math_query"})
