"""Validate multi-run rollout evidence against roadmap section 2.1."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SRC = ROOT / "src"
for candidate in (ROOT, SRC):
    if str(candidate) not in sys.path:
        sys.path.insert(0, str(candidate))

from mech_chatbot.evaluation.rollout_guardrails import evaluate_rollout_series


def _read(path: Path) -> dict:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"expected JSON object: {path}")
    return value


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--stage", required=True)
    parser.add_argument("--pair", type=Path, action="append", required=True)
    parser.add_argument("--decisions", type=Path)
    parser.add_argument("--minimum-pairs", type=int, default=3)
    parser.add_argument("--output", type=Path, required=True)
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    decisions = _read(args.decisions) if args.decisions else {}
    report = evaluate_rollout_series(
        args.stage,
        [_read(path) for path in args.pair],
        prior_decisions=decisions,
        minimum_pairs=args.minimum_pairs,
    )
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    return 0 if report["production_eligible"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
