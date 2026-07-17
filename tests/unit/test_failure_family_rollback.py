from __future__ import annotations

import json

import pytest

from scripts.eval.verify_failure_family_rollback import compose_verification


def _evidence(*flags: str, passed: bool = True, git_sha: str = "a" * 40):
    return {
        "schema": "rollback-test-evidence-v1",
        "git_sha": git_sha,
        "flags": list(flags),
        "passed": passed,
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
