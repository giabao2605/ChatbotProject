"""Compile reviewed deterministic failure mutations into an eval manifest."""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[2]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from mech_chatbot.evaluation.failure_mutations import (
    MutationMatrixError,
    compile_failure_mutation_matrix,
)


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _commit() -> str:
    return subprocess.check_output(
        ["git", "rev-parse", "HEAD"], text=True
    ).strip()


def _worktree_provenance() -> dict:
    try:
        diff = subprocess.check_output(
            ["git", "diff", "HEAD", "--binary"], cwd=ROOT,
            stderr=subprocess.DEVNULL,
        )
    except (OSError, subprocess.CalledProcessError):
        diff = b""
    return {
        "source_commit": _commit(),
        "source_worktree_dirty": bool(diff),
        "source_worktree_diff_sha256": (
            hashlib.sha256(diff).hexdigest() if diff else None
        ),
    }


def _load_seed(path: Path, seed_id: str | None) -> dict:
    raw = path.read_text(encoding="utf-8")
    if seed_id:
        try:
            rows = [json.loads(line) for line in raw.splitlines() if line.strip()]
        except json.JSONDecodeError as exc:
            raise ValueError(f"invalid seed JSONL: {exc}") from exc
        matches = [row for row in rows if str(row.get("id") or "") == seed_id]
        if len(matches) != 1:
            raise ValueError(f"seed id must match exactly one JSONL row: {seed_id}")
        return matches[0]
    try:
        seed = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise ValueError("--seed-id is required when --seed is JSONL") from exc
    if not isinstance(seed, dict):
        raise ValueError("seed must be a JSON object")
    return seed


def _markdown(matrix: dict) -> str:
    return "\n".join([
        "# Failure mutation matrix",
        "",
        f"- Family: `{matrix['failure_family']}`",
        f"- Seed: `{matrix['seed_case_id']}`",
        f"- Development variants: `{matrix['development_variant_count']}`",
        f"- Holdout variants: `{matrix['holdout_variant_count']}`",
        f"- Axes: `{', '.join(matrix['mutation_axes'])}`",
        f"- Matrix SHA-256: `{matrix['matrix_sha256']}`",
        "",
    ])


def main(argv=None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--seed", type=Path, required=True)
    parser.add_argument("--seed-id")
    parser.add_argument("--contract", type=Path)
    parser.add_argument("--recipes", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--source-commit", default=None)
    args = parser.parse_args(argv)
    if args.output_dir.exists():
        parser.error(f"output already exists: {args.output_dir}")
    try:
        seed = _load_seed(args.seed, args.seed_id)
        recipes = json.loads(args.recipes.read_text(encoding="utf-8"))
        contract = (
            json.loads(args.contract.read_text(encoding="utf-8"))
            if args.contract else {}
        )
    except (OSError, UnicodeDecodeError, json.JSONDecodeError, ValueError) as exc:
        parser.error(f"invalid input artifact: {exc}")
    if not isinstance(seed, dict) or not isinstance(recipes, list) or not isinstance(contract, dict):
        parser.error("seed/contract must be objects and recipes must be an array")
    if "id" in contract and contract["id"] != seed.get("id"):
        parser.error("contract cannot change seed id")
    seed = {**seed, **contract}
    try:
        matrix = compile_failure_mutation_matrix(seed, recipes)
    except MutationMatrixError as exc:
        parser.error(str(exc))

    args.output_dir.mkdir(parents=True, exist_ok=False)
    matrix["run_metadata"] = {
        **_worktree_provenance(),
        "source_commit": str(args.source_commit or _commit()),
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "seed_path": str(args.seed),
        "seed_sha256": _sha256(args.seed),
        "recipes_path": str(args.recipes),
        "recipes_sha256": _sha256(args.recipes),
        "contract_path": str(args.contract) if args.contract else None,
        "contract_sha256": _sha256(args.contract) if args.contract else None,
        "generator": "deterministic-reviewed-recipes-v1",
    }
    (args.output_dir / "mutation-matrix.json").write_text(
        json.dumps(matrix, ensure_ascii=False, indent=2, default=str) + "\n",
        encoding="utf-8",
    )
    (args.output_dir / "manifest.jsonl").write_text(
        "".join(
            json.dumps(case, ensure_ascii=False, separators=(",", ":")) + "\n"
            for case in matrix["cases"]
        ),
        encoding="utf-8",
    )
    (args.output_dir / "mutation-matrix.md").write_text(
        _markdown(matrix), encoding="utf-8"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
