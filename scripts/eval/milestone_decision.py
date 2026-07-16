"""Build or verify immutable, scope-aware roadmap decision artifacts."""

from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SRC = ROOT / "src"
for value in (ROOT, SRC):
    if str(value) not in sys.path:
        sys.path.insert(0, str(value))

from mech_chatbot.evaluation.milestone_decisions import (
    validate_milestone_decision,
    verify_milestone_decision,
)


def build_decision_artifact(
    *, milestone: str, scope: str, decision: str, source_commit: str,
    evidence_paths, reason: str, reviewer: str, signed_at: str,
) -> dict:
    evidence = []
    for path_value in evidence_paths:
        path = Path(path_value)
        raw = path.read_bytes()
        artifact = json.loads(raw.decode("utf-8"))
        evidence.append({
            "path": str(path),
            "sha256": hashlib.sha256(raw).hexdigest(),
            "schema": str(artifact.get("schema") or ""),
            "source_commit": source_commit,
        })
    payload = {
        "schema": "milestone-decision-v2",
        "milestone": milestone,
        "scope": scope,
        "decision": decision,
        "source_commit": source_commit,
        "evidence": evidence,
        "reason": reason,
        "reviewer_signoff": {"reviewer": reviewer, "signed_at": signed_at},
    }
    validation = validate_milestone_decision(payload)
    if not validation["passed"]:
        failed = [name for name, passed in validation["checks"].items() if not passed]
        raise ValueError(f"invalid milestone decision: {', '.join(failed)}")
    return payload


def main(argv=None):
    parser = argparse.ArgumentParser()
    subparsers = parser.add_subparsers(dest="command", required=True)
    build = subparsers.add_parser("build")
    build.add_argument("--milestone", required=True)
    build.add_argument("--scope", choices=("controlled_demo", "default_rollout"), required=True)
    build.add_argument("--decision", choices=("accepted", "rejected", "inconclusive"), required=True)
    build.add_argument("--source-commit")
    build.add_argument("--evidence", type=Path, action="append", required=True)
    build.add_argument("--reason", required=True)
    build.add_argument("--reviewer", required=True)
    build.add_argument("--signed-at")
    build.add_argument("--output", type=Path, required=True)
    verify = subparsers.add_parser("verify")
    verify.add_argument("--decision-artifact", type=Path, required=True)
    args = parser.parse_args(argv)
    current_commit = subprocess.check_output(
        ["git", "rev-parse", "HEAD"], cwd=ROOT, text=True,
    ).strip()
    if args.command == "build":
        payload = build_decision_artifact(
            milestone=args.milestone,
            scope=args.scope,
            decision=args.decision,
            source_commit=args.source_commit or current_commit,
            evidence_paths=args.evidence,
            reason=args.reason,
            reviewer=args.reviewer,
            signed_at=args.signed_at or datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        )
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        report = verify_milestone_decision(payload, root=ROOT, current_commit=current_commit)
    else:
        payload = json.loads(args.decision_artifact.read_text(encoding="utf-8"))
        report = verify_milestone_decision(payload, root=ROOT, current_commit=current_commit)
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if report["passed"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
