"""Enforce independent line and branch thresholds from coverage.py JSON."""

from __future__ import annotations

import argparse
import json
import math
import sys
from pathlib import Path


class CoverageReportError(ValueError):
    """Raised when a coverage report cannot be safely evaluated."""


def _percentage(raw_value: str) -> float:
    try:
        value = float(raw_value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError("must be between 0 and 100") from exc
    if not math.isfinite(value) or not 0 <= value <= 100:
        raise argparse.ArgumentTypeError("must be between 0 and 100")
    return value


def _load_totals(report_path: Path) -> dict[str, object]:
    try:
        report = json.loads(report_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError) as exc:
        raise CoverageReportError("cannot read coverage report") from exc
    except json.JSONDecodeError as exc:
        raise CoverageReportError("coverage report is not valid JSON") from exc

    if not isinstance(report, dict) or not isinstance(report.get("totals"), dict):
        raise CoverageReportError("coverage report is missing object 'totals'")
    return report["totals"]


def _validated_counts(totals: dict[str, object]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for name in (
        "covered_lines",
        "num_statements",
        "covered_branches",
        "num_branches",
    ):
        if name not in totals:
            raise CoverageReportError(f"missing numeric total '{name}'")
        value = totals[name]
        if isinstance(value, bool) or not isinstance(value, int) or value < 0:
            raise CoverageReportError(
                f"total '{name}' must be a non-negative integer"
            )
        counts[name] = value

    if counts["num_statements"] == 0:
        raise CoverageReportError(
            "cannot evaluate line coverage because num_statements is zero"
        )
    if counts["num_branches"] == 0:
        raise CoverageReportError(
            "cannot evaluate branch coverage because num_branches is zero"
        )
    if counts["covered_lines"] > counts["num_statements"]:
        raise CoverageReportError("covered_lines exceeds num_statements")
    if counts["covered_branches"] > counts["num_branches"]:
        raise CoverageReportError("covered_branches exceeds num_branches")
    return counts


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("report", type=Path)
    parser.add_argument("--min-line", type=_percentage, required=True)
    parser.add_argument("--min-branch", type=_percentage, required=True)
    args = parser.parse_args()

    try:
        totals = _load_totals(args.report)
        counts = _validated_counts(totals)
    except CoverageReportError as exc:
        print(f"ERROR: {exc}.", file=sys.stderr)
        return 2
    line_percent = counts["covered_lines"] / counts["num_statements"] * 100
    branch_percent = counts["covered_branches"] / counts["num_branches"] * 100

    print(f"line coverage: {line_percent:.6f}%")
    print(f"branch coverage: {branch_percent:.6f}%")

    failed = False
    if line_percent < args.min_line:
        print(
            f"ERROR: line coverage {line_percent:.6f}% is below minimum "
            f"{args.min_line:.6f}%",
            file=sys.stderr,
        )
        failed = True
    if branch_percent < args.min_branch:
        print(
            f"ERROR: branch coverage {branch_percent:.6f}% is below minimum "
            f"{args.min_branch:.6f}%",
            file=sys.stderr,
        )
        failed = True
    return int(failed)


if __name__ == "__main__":
    raise SystemExit(main())
