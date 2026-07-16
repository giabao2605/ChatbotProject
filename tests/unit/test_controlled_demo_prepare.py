import json

import pytest


pytestmark = pytest.mark.unit


def _case(case_id, group):
    return {
        "manifest_schema": "rag-eval-manifest-v2",
        "id": case_id,
        "evaluation_group": group,
        "question": f"question-{case_id}",
    }


def test_prepare_writes_group_manifests_and_crag_scope(tmp_path):
    from scripts.controlled_demo_eval.prepare import prepare_manifests

    cases = [
        _case("factual", "factual"),
        _case("refusal", "insufficient_evidence"),
        _case("denied", "access_denied"),
        _case("math", "grounded_math"),
        _case("complex", "complex"),
    ]

    report = prepare_manifests(cases, tmp_path)

    assert report["schema"] == "controlled-demo-manifest-inventory-v1"
    assert report["source_case_count"] == 5
    assert report["groups"]["grounded_math"]["case_count"] == 1
    assert report["milestones"]["crag"]["case_count"] == 3
    crag_rows = [
        json.loads(line)
        for line in (tmp_path / "crag.jsonl").read_text(encoding="utf-8").splitlines()
    ]
    assert [row["id"] for row in crag_rows] == ["factual", "refusal", "denied"]
    assert report["milestones"]["query_decomposition"]["minimum_met"] is False


def test_prepare_refuses_to_overwrite_existing_artifacts(tmp_path):
    from scripts.controlled_demo_eval.prepare import prepare_manifests

    (tmp_path / "inventory.json").write_text("existing", encoding="utf-8")

    with pytest.raises(ValueError, match="refusing to overwrite"):
        prepare_manifests([_case("one", "factual")], tmp_path)


def test_prepare_rejects_unknown_or_duplicate_cases(tmp_path):
    from scripts.controlled_demo_eval.prepare import prepare_manifests

    with pytest.raises(ValueError, match="unsupported evaluation_group"):
        prepare_manifests([_case("one", "unknown")], tmp_path)

    with pytest.raises(ValueError, match="duplicate case id"):
        prepare_manifests(
            [_case("same", "factual"), _case("same", "grounded_math")],
            tmp_path,
        )


def test_prepare_strict_contract_records_source_hash_and_rejects_wrong_distribution(
    tmp_path,
):
    from scripts.controlled_demo_eval.prepare import prepare_manifests

    source = tmp_path / "source.jsonl"
    source.write_text(json.dumps(_case("one", "factual")) + "\n", encoding="utf-8")

    with pytest.raises(ValueError, match="group distribution"):
        prepare_manifests(
            [_case("one", "factual")],
            tmp_path / "bad",
            source_manifests=[source],
            expected_group_counts={"factual": 2},
        )

    report = prepare_manifests(
        [_case("one", "factual")],
        tmp_path / "good",
        source_manifests=[source],
        expected_group_counts={"factual": 1},
    )

    assert report["source_manifests"][0]["sha256"]
    assert report["source_manifests"][0]["path"] == str(source.resolve())
