from __future__ import annotations

import pytest
import json
from pathlib import Path

from mech_chatbot.evaluation.failure_mutations import (
    MutationMatrixError,
    compile_failure_mutation_matrix,
)


def _seed():
    return {
        "id": "evidence-seed",
        "question": "Tài liệu quy định 1,500 chi tiết phải không?",
        "expected_outcome": "full_answer",
        "failure_family": "EVIDENCE_POLICY_ERROR",
        "seed_case_id": "evidence-seed",
        "expected_policy": {
            "outcome": "full_answer",
            "evidence_state": "SUFFICIENT",
            "correction_allowed": False,
        },
        "invariants": ["leakage_zero", "governance_unchanged"],
        "mutation_axes": [],
        "holdout": False,
    }


def _recipes():
    return [
        {
            "id": "dev-prefix",
            "axes": ["paraphrase"],
            "question": "Vui lòng xác nhận tài liệu quy định 1,500 chi tiết.",
            "holdout": False,
        },
        {
            "id": "dev-number-a",
            "axes": ["number_format"],
            "replacements": {"1,500": "1500"},
            "holdout": False,
        },
        {
            "id": "dev-number-b",
            "axes": ["number_format"],
            "replacements": {"1,500": "1.500"},
            "holdout": False,
        },
        {
            "id": "dev-suffix",
            "axes": ["paraphrase"],
            "question": "Theo tài liệu, số lượng có phải 1,500 chi tiết không?",
            "holdout": False,
        },
        {
            "id": "holdout-one",
            "axes": ["number_format", "paraphrase"],
            "question": "Con số được nêu có phải là 1500 chi tiết?",
            "holdout": True,
        },
        {
            "id": "holdout-two",
            "axes": ["paraphrase"],
            "question": "Hãy kiểm tra số lượng 1,500 trong tài liệu.",
            "holdout": True,
        },
    ]


def test_mutation_matrix_is_deterministic_and_keeps_seed_immutable():
    seed = _seed()
    original = dict(seed)

    first = compile_failure_mutation_matrix(seed, _recipes())
    second = compile_failure_mutation_matrix(seed, list(reversed(_recipes())))

    assert seed == original
    assert first == second
    assert first["schema"] == "failure-mutation-matrix-v1"
    assert first["seed_count"] == 1
    assert first["development_variant_count"] == 4
    assert first["holdout_variant_count"] == 2
    assert [case["id"] for case in first["cases"]] == [
        "evidence-seed",
        "dev-number-a",
        "dev-number-b",
        "dev-prefix",
        "dev-suffix",
        "holdout-one",
        "holdout-two",
    ]
    assert first["cases"][1]["question"].endswith("1500 chi tiết phải không?")


def test_mutation_matrix_rejects_under_sized_family():
    with pytest.raises(MutationMatrixError, match="at least 4 development"):
        compile_failure_mutation_matrix(_seed(), _recipes()[:3])


def test_policy_changing_axis_requires_explicit_human_policy_label():
    recipes = _recipes()
    recipes[0] = {
        "id": "dev-access",
        "axes": ["rbac_scope"],
        "case_patch": {"allowed_departments": ["HR"]},
        "holdout": False,
    }

    with pytest.raises(MutationMatrixError, match="explicit expected_policy"):
        compile_failure_mutation_matrix(_seed(), recipes)


def test_policy_changing_axis_accepts_explicit_label_and_case_patch():
    recipes = _recipes()
    recipes[0] = {
        "id": "dev-access",
        "axes": ["rbac_scope"],
        "case_patch": {
            "allowed_departments": ["HR"],
            "expected_outcome": "access_denied",
        },
        "expected_policy": {
            "outcome": "access_denied",
            "evidence_state": "INSUFFICIENT",
            "correction_allowed": False,
        },
        "holdout": False,
    }

    matrix = compile_failure_mutation_matrix(_seed(), recipes)
    access = next(case for case in matrix["cases"] if case["id"] == "dev-access")

    assert access["allowed_departments"] == ["HR"]
    assert access["expected_policy"]["outcome"] == "access_denied"


def test_mutation_cli_writes_isolated_manifest_and_provenance_artifact(tmp_path):
    from scripts.eval.generate_failure_mutations import main

    seed_path = tmp_path / "seed.json"
    recipes_path = tmp_path / "recipes.json"
    output = tmp_path / "run"
    seed_path.write_text(json.dumps(_seed()), encoding="utf-8")
    recipes_path.write_text(json.dumps(_recipes()), encoding="utf-8")

    assert main([
        "--seed", str(seed_path),
        "--recipes", str(recipes_path),
        "--output-dir", str(output),
        "--source-commit", "a" * 40,
    ]) == 0

    artifact = json.loads((output / "mutation-matrix.json").read_text(encoding="utf-8"))
    rows = [
        json.loads(line)
        for line in (output / "manifest.jsonl").read_text(encoding="utf-8").splitlines()
    ]
    assert artifact["run_metadata"]["source_commit"] == "a" * 40
    assert artifact["run_metadata"]["seed_sha256"]
    assert artifact["run_metadata"]["recipes_sha256"]
    assert len(rows) == 7

    with pytest.raises(SystemExit):
        main([
            "--seed", str(seed_path),
            "--recipes", str(recipes_path),
            "--output-dir", str(output),
            "--source-commit", "a" * 40,
        ])


def test_mutation_cli_can_select_seed_from_existing_jsonl_and_overlay_contract(tmp_path):
    from scripts.eval.generate_failure_mutations import main

    source = tmp_path / "source.jsonl"
    source.write_text(
        json.dumps({"id": "other", "question": "other"}) + "\n"
        + json.dumps({
            "id": "selected",
            "question": "Tài liệu quy định 1,500 chi tiết phải không?",
            "expected_outcome": "full_answer",
        }) + "\n",
        encoding="utf-8",
    )
    contract = tmp_path / "contract.json"
    contract.write_text(json.dumps({
        "failure_family": "EVIDENCE_POLICY_ERROR",
        "seed_case_id": "selected",
        "expected_policy": {
            "outcome": "full_answer",
            "evidence_state": "SUFFICIENT",
            "correction_allowed": False,
        },
        "invariants": ["leakage_zero"],
        "mutation_axes": [],
        "holdout": False,
    }), encoding="utf-8")
    recipes = tmp_path / "recipes.json"
    recipes.write_text(json.dumps(_recipes()), encoding="utf-8")

    assert main([
        "--seed", str(source),
        "--seed-id", "selected",
        "--contract", str(contract),
        "--recipes", str(recipes),
        "--output-dir", str(tmp_path / "run"),
        "--source-commit", "a" * 40,
    ]) == 0

    artifact = json.loads(
        (tmp_path / "run" / "mutation-matrix.json").read_text(encoding="utf-8")
    )
    assert artifact["seed_case_id"] == "selected"
    assert artifact["run_metadata"]["contract_sha256"]


@pytest.mark.parametrize(("name", "seed_id", "family"), [
    ("evidence-policy", "demo-case-037", "EVIDENCE_POLICY_ERROR"),
    ("grounded-math", "demo-case-013", "CALCULATION_PLAN_ERROR"),
    ("multi-intent", "demo-case-023", "MULTI_INTENT_COVERAGE_ERROR"),
    ("graph-relation", "demo-case-032", "GRAPH_RELATION_ERROR"),
])
def test_checked_in_failure_family_recipes_compile(name, seed_id, family):
    root = Path("data/failure_family_eval_v1")
    seeds = [
        json.loads(line)
        for line in (root / "seeds.jsonl").read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    seed = next(row for row in seeds if row["id"] == seed_id)
    contract = json.loads((root / f"{name}.contract.json").read_text(encoding="utf-8"))
    recipes = json.loads((root / f"{name}.recipes.json").read_text(encoding="utf-8"))

    matrix = compile_failure_mutation_matrix({**seed, **contract}, recipes)

    assert matrix["failure_family"] == family
    assert matrix["development_variant_count"] == 4
    assert matrix["holdout_variant_count"] == 2
