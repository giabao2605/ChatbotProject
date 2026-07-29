from __future__ import annotations

import json

import pytest

from scripts.eval.crag_cohort_hash import build_cohort_hashes


pytestmark = pytest.mark.unit


def test_cohort_hash_output_contains_only_hashed_actor_identity():
    report = build_cohort_hashes(
        ["user-7", "user-9"],
        experiment_id="crag-demo-v1",
        assignment_salt="test-only-secret",
    )

    assert report["schema"] == "crag-controlled-demo-cohort-v1"
    assert report["actor_count"] == 2
    assert len(report["actor_hashes"]) == 2
    assert all(len(value) == 64 for value in report["actor_hashes"])
    serialized = json.dumps(report)
    assert "user-7" not in serialized
    assert "user-9" not in serialized
    assert "test-only-secret" not in serialized
