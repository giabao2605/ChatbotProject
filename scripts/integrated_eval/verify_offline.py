"""Create commit-pinned offline evidence for cache, streaming and rollback."""

from __future__ import annotations

import argparse
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

from mech_chatbot.config.settings import Settings
from mech_chatbot.evaluation.integrated_hardening import FEATURE_FLAGS
from mech_chatbot.rag.semantic_cache import pipeline_namespace
from scripts.integrated_eval.contracts import assert_clean_worktree


def verify(matrix_path, output):
    assert_clean_worktree(ROOT)
    matrix = json.loads(Path(matrix_path).read_text(encoding="utf-8"))
    git_sha = subprocess.check_output(
        ["git", "rev-parse", "HEAD"], cwd=ROOT, text=True
    ).strip()
    command = [
        sys.executable, "-m", "pytest",
        "tests/unit/test_integrated_hardening.py",
        "tests/unit/test_semantic_cache_stream_lifecycle.py",
        "tests/unit/test_strict_stream_guard.py",
        "tests/unit/test_rollout_guardrails.py", "-q",
    ]
    result = subprocess.run(
        command, cwd=ROOT, check=False, capture_output=True, text=True
    )
    defaults = {
        name: Settings.model_fields[name].default
        for name in FEATURE_FLAGS
    }
    namespaces = [
        pipeline_namespace({**item["flags"], **item["versions"]})
        for item in matrix.get("combinations") or []
    ]
    report = {
        "schema": "integrated-offline-verification-v1",
        "git_sha": git_sha,
        "tested_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        "passed": (
            result.returncode == 0
            and set(defaults.values()) == {False}
            and len(namespaces) == len(set(namespaces))
        ),
        "feature_flags_default_disabled": set(defaults.values()) == {False},
        "cache_isolation_passed": len(namespaces) == len(set(namespaces)),
        "strict_stream_passed": result.returncode == 0,
        "rollback_passed": result.returncode == 0 and set(defaults.values()) == {False},
        "flags": list(FEATURE_FLAGS),
        "command": command[1:],
        "exit_code": result.returncode,
        "stdout_tail": result.stdout[-3000:],
        "stderr_tail": result.stderr[-3000:],
    }
    output = Path(output)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    return report


def main(argv=None):
    parser = argparse.ArgumentParser()
    parser.add_argument("--matrix", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args(argv)
    report = verify(args.matrix, args.output)
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if report["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
