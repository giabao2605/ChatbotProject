"""Run clean migration verification and persist a machine-readable artifact."""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

from scripts.graph_eval.constants import ROOT


def _now():
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def verify(database, output, *, server=None):
    command = [
        sys.executable, str(ROOT / "scripts" / "migrations" / "verify_clean_migration.py"),
        "--database", database, "--recreate",
    ]
    if server:
        command.extend(["--server", server])
    started = _now()
    result = subprocess.run(command, cwd=ROOT, capture_output=True, text=True, check=False)
    completed = _now()
    git_sha = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip()
    artifact = {
        "schema": "graph-clean-migration-v1", "passed": result.returncode == 0,
        "git_sha": git_sha, "database": database, "started_at": started,
        "completed_at": completed, "exit_code": result.returncode,
        "stdout": result.stdout[-12000:], "stderr": result.stderr[-12000:],
        "required_versions": ["V0033", "V0034"],
    }
    output = Path(output)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(artifact, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return artifact


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--database", required=True)
    parser.add_argument("--server", default=os.getenv("SQL_SERVER"))
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    artifact = verify(args.database, args.output, server=args.server)
    print(json.dumps(artifact, ensure_ascii=False, indent=2))
    return 0 if artifact["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
