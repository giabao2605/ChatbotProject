"""Compose commit-pinned runtime rollback evidence for failure-family gates."""

from __future__ import annotations

import argparse
import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path
import subprocess


ROOT = Path(__file__).resolve().parents[2]


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def compose_verification(paths, *, git_sha: str) -> dict:
    flags: set[str] = set()
    references = []
    for raw_path in paths:
        path = Path(raw_path)
        artifact = json.loads(path.read_text(encoding="utf-8"))
        if artifact.get("schema") != "rollback-test-evidence-v1":
            raise ValueError(f"unsupported rollback evidence schema: {path}")
        if artifact.get("passed") is not True:
            raise ValueError(f"failed rollback evidence: {path}")
        if artifact.get("git_sha") != git_sha:
            raise ValueError(f"rollback evidence commit does not match: {path}")
        artifact_flags = artifact.get("flags") or []
        if not artifact_flags or not all(isinstance(flag, str) and flag for flag in artifact_flags):
            raise ValueError(f"rollback evidence has invalid flags: {path}")
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
    git_sha = subprocess.check_output(
        ["git", "rev-parse", "HEAD"], cwd=ROOT, text=True
    ).strip()
    try:
        report = compose_verification(args.evidence, git_sha=git_sha)
    except (OSError, UnicodeDecodeError, json.JSONDecodeError, ValueError) as exc:
        parser.error(str(exc))
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
