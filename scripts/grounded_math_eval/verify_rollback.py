"""Create commit-pinned evidence that disabling grounded math restores the normal path."""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def verify(output: Path) -> dict:
    git_sha = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip()
    command = [
        sys.executable, "-m", "pytest",
        "tests/unit/test_strict_stream_guard.py::test_grounded_math_flag_defaults_to_normal_generation_path",
        "tests/unit/test_grounded_math_eval_fixture.py::test_grounded_math_rollout_toggles_only_math_between_arms",
        "-q",
    ]
    result = subprocess.run(command, cwd=ROOT, check=False, capture_output=True, text=True)
    report = {
        "schema": "rollback-test-evidence-v1", "git_sha": git_sha,
        "flags": ["RAG_GROUNDED_MATH_ENABLED"], "passed": result.returncode == 0,
        "tested_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        "command": command[1:], "exit_code": result.returncode,
        "stdout_tail": result.stdout[-2000:], "stderr_tail": result.stderr[-2000:],
    }
    output = Path(output)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return report


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    report = verify(args.output)
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if report["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
