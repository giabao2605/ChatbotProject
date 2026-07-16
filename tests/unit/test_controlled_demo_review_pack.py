import hashlib
import json

import pytest

from scripts.controlled_demo_eval.review_pack import (
    build_review_rows,
    load_bound_manifest,
    write_review_pack,
)


def _manifest_case(case_id="case-1", group="grounded_math"):
    return {
        "manifest_schema": "rag-eval-manifest-v2",
        "id": case_id,
        "question": "Tong BOM la bao nhieu?",
        "evaluation_group": group,
        "expected_outcome": "full_answer",
        "expected_claims": [{"claim_id": "total", "value": "3"}],
        "expected_citations": [{"document": "bom.md", "page": 1}],
    }


def _eval(case_id="case-1", answer="Ba chi tiet."):
    return {
        "schema": "rag-labeled-eval-v4",
        "run_label": "baseline" if answer == "Baseline" else "candidate",
        "git_sha": "a" * 40,
        "manifest_sha256s": ["b" * 64],
        "snapshot_fingerprint": "c" * 64,
        "provider_configuration_sha256": "d" * 64,
        "governance_scope_sha256": "e" * 64,
        "collection": "TaiLieuKyThuat_v2",
        "benchmark_concurrency": 1,
        "execution_context": "evaluation",
        "cases": [{
            "id": case_id,
            "answer": answer,
            "passed": False,
            "actual_outcome": "full_answer",
            "claim_evaluation": {"claim_precision": 0.5},
            "citation_evaluation": {"citation_accuracy": 1.0},
            "calculation_evaluation": {"passed": False},
        }],
    }


def test_build_review_rows_keeps_evidence_and_blank_human_labels():
    rows = build_review_rows(
        [_manifest_case()], _eval(answer="Baseline"), _eval(answer="Candidate"),
        allowed_groups={"grounded_math"},
    )

    assert len(rows) == 1
    row = rows[0]
    assert row["question"] == "Tong BOM la bao nhieu?"
    assert row["baseline"]["answer"] == "Baseline"
    assert row["candidate"]["answer"] == "Candidate"
    assert row["expected_claims"][0]["claim_id"] == "total"
    assert row["human_review"] == {
        "reviewer": "",
        "answer_correct": None,
        "citation_correct": None,
        "safety_correct": None,
        "decision": "",
        "note": "",
    }


def test_build_review_rows_filters_groups_and_rejects_unbound_cases():
    baseline = _eval("math", "Baseline")
    baseline["cases"] = [_eval("math", "Baseline")["cases"][0], _eval("risk", "Baseline")["cases"][0]]
    candidate = _eval("math", "Candidate")
    candidate["cases"] = [_eval("math", "Candidate")["cases"][0], _eval("risk", "Candidate")["cases"][0]]
    rows = build_review_rows(
        [_manifest_case("math", "grounded_math"), _manifest_case("risk", "access_denied")],
        baseline,
        candidate,
        allowed_groups={"access_denied"},
    )
    assert [row["case_id"] for row in rows] == ["risk"]

    with pytest.raises(ValueError, match="missing evaluation result"):
        build_review_rows(
            [_manifest_case("missing")], _eval("other", "Baseline"), _eval("other", "Candidate"),
            allowed_groups={"grounded_math"},
        )


def test_review_pack_rejects_mismatched_pair_provenance():
    baseline = _eval(answer="Baseline")
    candidate = _eval(answer="Candidate")
    candidate["snapshot_fingerprint"] = "different"

    with pytest.raises(ValueError, match="snapshot_fingerprint"):
        build_review_rows(
            [_manifest_case()], baseline, candidate,
            allowed_groups={"grounded_math"},
        )


def test_manifest_hash_must_be_bound_to_pair_provenance(tmp_path):
    path = tmp_path / "manifest.jsonl"
    path.write_text(json.dumps(_manifest_case()) + "\n", encoding="utf-8")
    digest = hashlib.sha256(path.read_bytes()).hexdigest()
    baseline = _eval(answer="Baseline")
    candidate = _eval(answer="Candidate")
    baseline["manifest_sha256s"] = [digest]
    candidate["manifest_sha256s"] = [digest]

    rows, reference = load_bound_manifest(path, baseline, candidate)

    assert rows[0]["id"] == "case-1"
    assert reference["sha256"] == digest
    assert reference["schemas"] == ["rag-eval-manifest-v2"]

    candidate["manifest_sha256s"] = ["f" * 64]
    with pytest.raises(ValueError, match="manifest_sha256s"):
        load_bound_manifest(path, baseline, candidate)

    candidate["manifest_sha256s"] = ["0" * 64]
    baseline["manifest_sha256s"] = ["0" * 64]
    with pytest.raises(ValueError, match="manifest sha256"):
        load_bound_manifest(path, baseline, candidate)


def test_write_review_pack_is_local_only_and_does_not_overwrite(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    reports = tmp_path / "reports" / "review"
    payload = write_review_pack(
        rows=build_review_rows(
            [_manifest_case()], _eval(answer="Baseline"), _eval(answer="Candidate"),
            allowed_groups={"grounded_math"},
        ),
        baseline_eval=_eval(answer="Baseline"),
        candidate_eval=_eval(answer="Candidate"),
        output_dir=reports,
        pack_id="math-review",
        source_artifacts=[{"path": "baseline.json", "sha256": "a" * 64}],
    )

    assert payload["schema"] == "controlled-demo-human-review-pack-v1"
    assert payload["local_only"] is True
    assert payload["reviewed_cases"] == 0
    assert payload["source_artifacts"][0]["path"] == "baseline.json"
    persisted = json.loads((reports / "pack.json").read_text(encoding="utf-8"))
    assert persisted == payload
    lines = (reports / "review.jsonl").read_text(encoding="utf-8").splitlines()
    assert json.loads(lines[0])["human_review"]["answer_correct"] is None

    with pytest.raises(FileExistsError):
        write_review_pack(
            rows=[], baseline_eval=_eval(answer="Baseline"), candidate_eval=_eval(answer="Candidate"),
            output_dir=reports, pack_id="math-review",
        )

    with pytest.raises(ValueError, match="reports"):
        write_review_pack(
            rows=[], baseline_eval=_eval(answer="Baseline"), candidate_eval=_eval(answer="Candidate"),
            output_dir=tmp_path / "data" / "tracked", pack_id="bad",
        )
