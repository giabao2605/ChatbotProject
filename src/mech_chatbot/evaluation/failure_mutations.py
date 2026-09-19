"""Deterministic compiler for reviewed failure-family mutation matrices."""

from __future__ import annotations

from copy import deepcopy
import hashlib
import json
from typing import Any, Mapping, Sequence

from mech_chatbot.evaluation.failure_families import validate_failure_contract


MUTATION_AXES = frozenset({
    "paraphrase",
    "intent_order",
    "negation",
    "number_format",
    "unit_format",
    "operand_presence",
    "duplicate_row",
    "division_by_zero",
    "mixed_version",
    "intent_count",
    "rbac_scope",
    "lifecycle",
    "graph_relation",
    "retrieval_noise",
    "empty_retrieval",
})

# These axes can change the correct answer policy. The compiler must never infer
# the new label; a reviewer/fixture author has to provide it explicitly.
POLICY_CHANGING_AXES = frozenset({
    "negation",
    "operand_presence",
    "division_by_zero",
    "mixed_version",
    "rbac_scope",
    "lifecycle",
    "graph_relation",
    "empty_retrieval",
})

_PROTECTED_PATCH_FIELDS = frozenset({
    "id",
    "failure_family",
    "seed_case_id",
    "invariants",
    "mutation_axes",
    "holdout",
    "expected_policy",
})


class MutationMatrixError(ValueError):
    pass


def _canonical_sha256(value: Any) -> str:
    payload = json.dumps(
        value,
        sort_keys=True,
        ensure_ascii=False,
        separators=(",", ":"),
        default=str,
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _apply_replacements(question: str, replacements: Mapping) -> str:
    value = str(question)
    for old, new in sorted(
        ((str(old), str(new)) for old, new in replacements.items()),
        key=lambda item: item[0],
    ):
        if not old or old not in value:
            raise MutationMatrixError(f"replacement source is absent: {old!r}")
        value = value.replace(old, new)
    return value


def _compile_variant(seed: dict, recipe: Mapping) -> dict:
    variant_id = str(recipe.get("id") or "").strip()
    if not variant_id or variant_id == seed["id"]:
        raise MutationMatrixError("each mutation requires a unique non-seed id")
    axes = recipe.get("axes")
    if not isinstance(axes, list) or not axes:
        raise MutationMatrixError(f"{variant_id}: axes must be a non-empty list")
    unknown_axes = sorted(set(axes) - MUTATION_AXES)
    if unknown_axes:
        raise MutationMatrixError(
            f"{variant_id}: unsupported mutation axes: {', '.join(unknown_axes)}"
        )
    if not isinstance(recipe.get("holdout"), bool):
        raise MutationMatrixError(f"{variant_id}: holdout must be boolean")
    if POLICY_CHANGING_AXES.intersection(axes) and not isinstance(
        recipe.get("expected_policy"), dict
    ):
        raise MutationMatrixError(
            f"{variant_id}: policy-changing axes require explicit expected_policy"
        )

    patch = recipe.get("case_patch") or {}
    if not isinstance(patch, dict):
        raise MutationMatrixError(f"{variant_id}: case_patch must be an object")
    protected = sorted(_PROTECTED_PATCH_FIELDS.intersection(patch))
    if protected:
        raise MutationMatrixError(
            f"{variant_id}: protected case_patch fields: {', '.join(protected)}"
        )

    variant = deepcopy(seed)
    variant.update(deepcopy(patch))
    variant["id"] = variant_id
    variant["seed_case_id"] = seed["id"]
    variant["mutation_axes"] = sorted(set(str(axis) for axis in axes))
    variant["holdout"] = bool(recipe["holdout"])
    if "question" in recipe:
        question = str(recipe.get("question") or "").strip()
        if not question:
            raise MutationMatrixError(f"{variant_id}: question cannot be empty")
        variant["question"] = question
    replacements = recipe.get("replacements") or {}
    if not isinstance(replacements, dict):
        raise MutationMatrixError(f"{variant_id}: replacements must be an object")
    if replacements:
        variant["question"] = _apply_replacements(
            str(variant.get("question") or ""), replacements
        )
    if isinstance(recipe.get("expected_policy"), dict):
        variant["expected_policy"] = deepcopy(recipe["expected_policy"])
    policy_outcome = (variant.get("expected_policy") or {}).get("outcome")
    if variant.get("expected_outcome") and variant["expected_outcome"] != policy_outcome:
        raise MutationMatrixError(
            f"{variant_id}: expected_outcome must match expected_policy.outcome"
        )
    validate_failure_contract(variant)
    return variant


def compile_failure_mutation_matrix(
    seed: Mapping,
    recipes: Sequence[Mapping],
) -> dict:
    """Compile one immutable seed and its reviewed deterministic variants."""
    seed_case = deepcopy(dict(seed or {}))
    seed_id = str(seed_case.get("id") or "").strip()
    if not seed_id:
        raise MutationMatrixError("seed id is required")
    if str(seed_case.get("seed_case_id") or "") != seed_id:
        raise MutationMatrixError("seed_case_id must equal seed id")
    validate_failure_contract(seed_case)

    recipe_rows = [dict(item or {}) for item in recipes or ()]
    ids = [str(item.get("id") or "").strip() for item in recipe_rows]
    if len(ids) != len(set(ids)):
        raise MutationMatrixError("mutation ids must be unique")
    development_count = sum(item.get("holdout") is False for item in recipe_rows)
    holdout_count = sum(item.get("holdout") is True for item in recipe_rows)
    if development_count < 4:
        raise MutationMatrixError("at least 4 development variants are required")
    if holdout_count < 2:
        raise MutationMatrixError("at least 2 holdout variants are required")

    variants = [
        _compile_variant(seed_case, recipe)
        for recipe in sorted(recipe_rows, key=lambda item: str(item.get("id") or ""))
    ]
    cases = [seed_case, *variants]
    return {
        "schema": "failure-mutation-matrix-v1",
        "failure_family": seed_case["failure_family"],
        "seed_case_id": seed_id,
        "seed_sha256": _canonical_sha256(seed_case),
        "matrix_sha256": _canonical_sha256(cases),
        "seed_count": 1,
        "development_variant_count": development_count,
        "holdout_variant_count": holdout_count,
        "mutation_axes": sorted({
            axis for recipe in recipe_rows for axis in recipe.get("axes") or ()
        }),
        "cases": cases,
    }


__all__ = [
    "MUTATION_AXES",
    "MutationMatrixError",
    "POLICY_CHANGING_AXES",
    "compile_failure_mutation_matrix",
]
