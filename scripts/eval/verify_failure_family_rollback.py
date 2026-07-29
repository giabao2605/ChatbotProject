"""Compose commit-pinned runtime rollback evidence for failure-family gates."""

from __future__ import annotations

import argparse
import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path
import subprocess


ROOT = Path(__file__).resolve().parents[2]

ROLLBACK_TEST_PROFILES = {
    frozenset({"RAG_CRAG_ENABLED", "RAG_CLAIM_REPAIR_ENABLED"}): (
        "-m", "pytest",
        "tests/unit/test_corrective_retrieval.py::test_crag_rollback_flag_disables_correction_runtime",
        "tests/unit/test_claim_repair.py::test_claim_repair_rollback_flag_disables_runtime",
        "-q",
    ),
    frozenset({"RAG_GROUNDED_MATH_ENABLED"}): (
        "-m", "pytest",
        "tests/unit/test_strict_stream_guard.py::test_grounded_math_disabled_uses_normal_generation_path",
        "tests/unit/test_grounded_math_eval_fixture.py::test_grounded_math_rollout_toggles_only_math_between_arms",
        "-q",
    ),
    frozenset({"RAG_QUERY_DECOMPOSITION_ENABLED"}): (
        "-m", "pytest", "tests/unit/test_query_decomposition.py",
        "tests/unit/test_decomposition_evaluation.py", "-q",
    ),
    frozenset({"RAG_GRAPH_RETRIEVAL_ENABLED"}): (
        "-m", "pytest", "tests/unit/test_graph_rag.py",
        "tests/unit/test_graph_evaluation.py", "-q",
    ),
}


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def clean_git_sha(root: Path = ROOT) -> str:
    worktree_changes = subprocess.check_output(
        ["git", "status", "--porcelain", "--untracked-files=normal"],
        cwd=root,
        text=True,
    ).strip()
    if worktree_changes:
        raise RuntimeError("worktree has changes; commit before rollback verification")
    return subprocess.check_output(
        ["git", "rev-parse", "HEAD"], cwd=root, text=True,
    ).strip()


def validate_rollback_evidence(artifact: dict, *, git_sha: str) -> frozenset[str]:
    if not isinstance(artifact, dict):
        raise ValueError("unsupported rollback evidence schema")
    if artifact.get("schema") != "rollback-test-evidence-v1":
        raise ValueError("unsupported rollback evidence schema")
    if artifact.get("passed") is not True:
        raise ValueError("failed rollback evidence")
    if artifact.get("git_sha") != git_sha:
        raise ValueError("rollback evidence commit does not match")
    raw_flags = artifact.get("flags")
    if not isinstance(raw_flags, list) or not all(
        isinstance(flag, str) and flag for flag in raw_flags
    ) or len(raw_flags) != len(set(raw_flags)):
        raise ValueError("rollback evidence has invalid flags")
    flag_group = frozenset(raw_flags)
    expected_command = ROLLBACK_TEST_PROFILES.get(flag_group)
    if expected_command is None:
        raise ValueError("unsupported rollback flag group")
    required_values = (
        artifact.get("tested_at"), artifact.get("command"),
        artifact.get("stdout_tail"), artifact.get("stderr_tail"),
        artifact.get("verified_flag_state"),
    )
    if (
        not isinstance(required_values[0], str)
        or not isinstance(required_values[1], list)
        or not isinstance(required_values[2], str)
        or not isinstance(required_values[3], str)
        or not isinstance(required_values[4], dict)
        or artifact.get("exit_code") != 0
        or "[100%]" not in required_values[2]
    ):
        raise ValueError("rollback test evidence is incomplete")
    if tuple(required_values[1]) != expected_command:
        raise ValueError("unexpected rollback test command")
    if required_values[4] != {flag: False for flag in flag_group}:
        raise ValueError("rollback evidence does not verify disabled flag state")
    try:
        tested_at = datetime.fromisoformat(required_values[0].replace("Z", "+00:00"))
    except ValueError as exc:
        raise ValueError("rollback evidence tested_at is invalid") from exc
    if tested_at.tzinfo is None:
        raise ValueError("rollback evidence tested_at is invalid")
    return flag_group


def compose_verification(paths, *, git_sha: str) -> dict:
    flags: set[str] = set()
    profiles = set()
    references = []
    for raw_path in paths:
        path = Path(raw_path)
        artifact = json.loads(path.read_text(encoding="utf-8"))
        try:
            profile = validate_rollback_evidence(artifact, git_sha=git_sha)
        except ValueError as exc:
            raise ValueError(f"{exc}: {path}") from exc
        if profile in profiles:
            raise ValueError(f"duplicate rollback flag group: {path}")
        profiles.add(profile)
        artifact_flags = artifact["flags"]
        flags.update(artifact_flags)
        references.append({
            "path": str(path.resolve()),
            "sha256": _sha256(path),
            "schema": artifact["schema"],
            "git_sha": artifact["git_sha"],
            "flags": sorted(artifact_flags),
        })
    if not references:
        raise ValueError("at least one rollback evidence artifact is required")
    if profiles != set(ROLLBACK_TEST_PROFILES):
        raise ValueError("rollback evidence does not cover every required flag group")
    return {
        "schema": "feature-rollback-verification-v1",
        "git_sha": git_sha,
        "passed": True,
        "flags": sorted(flags),
        "verified_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        "source_artifacts": references,
    }


def main(argv=None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--evidence", type=Path, nargs="+", required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args(argv)
    if args.output.exists():
        parser.error(f"output already exists: {args.output}")
    try:
        git_sha = clean_git_sha(ROOT)
        report = compose_verification(args.evidence, git_sha=git_sha)
    except (
        OSError,
        RuntimeError,
        UnicodeDecodeError,
        json.JSONDecodeError,
        ValueError,
    ) as exc:
        parser.error(str(exc))
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
