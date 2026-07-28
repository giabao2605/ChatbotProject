from __future__ import annotations

import json
from types import SimpleNamespace

import pytest

import scripts.decomposition_eval.verify_rollback as decomposition_rollback
import scripts.eval.verify_failure_family_rollback as rollback_verifier
import scripts.graph_eval.verify_rollback as graph_rollback
import scripts.grounded_math_eval.verify_rollback as grounded_math_rollback
from scripts.eval.verify_failure_family_rollback import (
    clean_git_sha,
    compose_verification,
)


def _evidence(*flags: str, passed: bool = True, git_sha: str = "a" * 40):
    commands = {
        frozenset({"RAG_CRAG_ENABLED", "RAG_CLAIM_REPAIR_ENABLED"}): [
            "-m", "pytest",
            "tests/unit/test_corrective_retrieval.py::test_crag_rollback_flag_disables_correction_runtime",
            "tests/unit/test_claim_repair.py::test_claim_repair_rollback_flag_disables_runtime",
            "-q",
        ],
        frozenset({"RAG_GROUNDED_MATH_ENABLED"}): [
            "-m", "pytest",
            "tests/unit/test_strict_stream_guard.py::test_grounded_math_disabled_uses_normal_generation_path",
            "tests/unit/test_grounded_math_eval_fixture.py::test_grounded_math_rollout_toggles_only_math_between_arms",
            "-q",
        ],
        frozenset({"RAG_QUERY_DECOMPOSITION_ENABLED"}): [
            "-m", "pytest", "tests/unit/test_query_decomposition.py",
            "tests/unit/test_decomposition_evaluation.py", "-q",
        ],
        frozenset({"RAG_GRAPH_RETRIEVAL_ENABLED"}): [
            "-m", "pytest", "tests/unit/test_graph_rag.py",
            "tests/unit/test_graph_evaluation.py", "-q",
        ],
    }
    return {
        "schema": "rollback-test-evidence-v1",
        "git_sha": git_sha,
        "flags": list(flags),
        "verified_flag_state": {flag: False for flag in flags},
        "passed": passed,
        "tested_at": "2026-07-17T00:00:00Z",
        "command": commands.get(frozenset(flags), ["-m", "pytest", "-q"]),
        "exit_code": 0 if passed else 1,
        "stdout_tail": ". [100%]\n" if passed else "F [100%]\n",
        "stderr_tail": "",
    }


def test_composes_commit_pinned_feature_rollback_verification(tmp_path):
    paths = []
    for index, evidence in enumerate((
        _evidence("RAG_CRAG_ENABLED", "RAG_CLAIM_REPAIR_ENABLED"),
        _evidence("RAG_GROUNDED_MATH_ENABLED"),
        _evidence("RAG_QUERY_DECOMPOSITION_ENABLED"),
        _evidence("RAG_GRAPH_RETRIEVAL_ENABLED"),
    )):
        path = tmp_path / f"rollback-{index}.json"
        path.write_text(json.dumps(evidence), encoding="utf-8")
        paths.append(path)

    report = compose_verification(paths, git_sha="a" * 40)

    assert report["schema"] == "feature-rollback-verification-v1"
    assert report["passed"] is True
    assert report["flags"] == [
        "RAG_CLAIM_REPAIR_ENABLED",
        "RAG_CRAG_ENABLED",
        "RAG_GRAPH_RETRIEVAL_ENABLED",
        "RAG_GROUNDED_MATH_ENABLED",
        "RAG_QUERY_DECOMPOSITION_ENABLED",
    ]
    assert all(item["sha256"] for item in report["source_artifacts"])


def test_rejects_failed_or_wrong_commit_rollback_evidence(tmp_path):
    failed = tmp_path / "failed.json"
    failed.write_text(json.dumps(_evidence("RAG_CRAG_ENABLED", passed=False)), encoding="utf-8")

    with pytest.raises(ValueError, match="failed rollback evidence"):
        compose_verification([failed], git_sha="a" * 40)

    stale = tmp_path / "stale.json"
    stale.write_text(json.dumps(_evidence("RAG_CRAG_ENABLED", git_sha="b" * 40)), encoding="utf-8")

    with pytest.raises(ValueError, match="commit does not match"):
        compose_verification([stale], git_sha="a" * 40)


def test_rejects_self_declared_or_wrong_command_rollback_evidence(tmp_path):
    self_declared = tmp_path / "self-declared.json"
    self_declared.write_text(json.dumps({
        "schema": "rollback-test-evidence-v1",
        "git_sha": "a" * 40,
        "flags": ["RAG_GROUNDED_MATH_ENABLED"],
        "passed": True,
    }), encoding="utf-8")
    with pytest.raises(ValueError, match="test evidence is incomplete"):
        compose_verification([self_declared], git_sha="a" * 40)

    wrong_command = tmp_path / "wrong-command.json"
    evidence = _evidence("RAG_GROUNDED_MATH_ENABLED")
    evidence["command"] = ["-m", "pytest", "tests/unit/test_unrelated.py", "-q"]
    wrong_command.write_text(json.dumps(evidence), encoding="utf-8")
    with pytest.raises(ValueError, match="unexpected rollback test command"):
        compose_verification([wrong_command], git_sha="a" * 40)

    duplicate_flag = tmp_path / "duplicate-flag.json"
    evidence = _evidence("RAG_GROUNDED_MATH_ENABLED")
    evidence["flags"].append("RAG_GROUNDED_MATH_ENABLED")
    duplicate_flag.write_text(json.dumps(evidence), encoding="utf-8")
    with pytest.raises(ValueError, match="invalid flags"):
        compose_verification([duplicate_flag], git_sha="a" * 40)


def test_clean_git_sha_rejects_uncommitted_changes(monkeypatch, tmp_path):
    def dirty_output(command, **_kwargs):
        if command[1] == "status":
            return " M scripts/eval/failure_family_gate.py\n"
        return "a" * 40 + "\n"

    monkeypatch.setattr(rollback_verifier.subprocess, "check_output", dirty_output)

    with pytest.raises(RuntimeError, match="worktree has changes"):
        clean_git_sha(tmp_path)

    monkeypatch.setattr(
        rollback_verifier.subprocess,
        "check_output",
        lambda command, **_kwargs: "" if command[1] == "status" else "a" * 40,
    )
    assert clean_git_sha(tmp_path) == "a" * 40


@pytest.mark.parametrize(
    ("module", "flag"),
    [
        (grounded_math_rollback, "RAG_GROUNDED_MATH_ENABLED"),
        (decomposition_rollback, "RAG_QUERY_DECOMPOSITION_ENABLED"),
        (graph_rollback, "RAG_GRAPH_RETRIEVAL_ENABLED"),
    ],
)
def test_single_flag_verifiers_force_disabled_environment(
    monkeypatch, tmp_path, module, flag,
):
    environments = []

    monkeypatch.setattr(module, "clean_git_sha", lambda _root: "a" * 40)

    def fake_run(*_args, **kwargs):
        environments.append(kwargs["env"])
        return SimpleNamespace(returncode=0, stdout=". [100%]\n", stderr="")

    monkeypatch.setattr(module.subprocess, "run", fake_run)

    report = module.verify(tmp_path / f"{flag}.json")

    assert environments[0][flag] == "false"
    assert report["verified_flag_state"] == {flag: False}
