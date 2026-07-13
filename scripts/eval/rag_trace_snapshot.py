"""Create reproducible refusal snapshots from the RAG JSONL trace."""

from __future__ import annotations

import argparse
import json
import subprocess
from collections import Counter
from datetime import datetime
from pathlib import Path


DEFAULT_EXCLUDED_REASONS = {"client_cancelled"}


def _parse_timestamp(value: str | None):
    return datetime.fromisoformat(value.replace("Z", "+00:00")) if value else None


def _git_sha() -> str | None:
    try:
        return subprocess.check_output(
            ["git", "rev-parse", "HEAD"], text=True, stderr=subprocess.DEVNULL
        ).strip()
    except (OSError, subprocess.CalledProcessError):
        return None


def build_snapshot(
    path: Path,
    *,
    start: str | None = None,
    end: str | None = None,
    execution_contexts: set[str] | None = None,
    excluded_reasons: set[str] | None = None,
) -> dict:
    path = Path(path)
    contexts = execution_contexts or {"production"}
    excluded = excluded_reasons if excluded_reasons is not None else DEFAULT_EXCLUDED_REASONS
    start_at = _parse_timestamp(start)
    end_at = _parse_timestamp(end)
    counts: Counter[str] = Counter()
    included_timestamps: list[str] = []
    parse_errors = 0
    legacy_reason_events = 0
    for raw in path.read_text(encoding="utf-8").splitlines():
        try:
            event = json.loads(raw)
        except json.JSONDecodeError:
            parse_errors += 1
            continue
        if event.get("event") != "rag_end" or not event.get("refusal"):
            continue
        if str(event.get("execution_context") or "production") not in contexts:
            continue
        timestamp = _parse_timestamp(event.get("ts"))
        if start_at and (timestamp is None or timestamp < start_at):
            continue
        if end_at and (timestamp is None or timestamp > end_at):
            continue
        reason = event.get("refusal_reason")
        if not reason and event.get("reason"):
            reason = event.get("reason")
            legacy_reason_events += 1
        if not reason or reason in excluded:
            continue
        counts[str(reason)] += 1
        if event.get("ts"):
            included_timestamps.append(event["ts"])
    return {
        "schema": "rag-refusal-snapshot-v1",
        "source": {"path": str(path.resolve()), "git_sha": _git_sha()},
        "filters": {
            "start": start,
            "end": end,
            "execution_contexts": sorted(contexts),
            "excluded_reasons": sorted(excluded),
            "exclude_empty_reason": True,
        },
        "observed_range": {
            "first": min(included_timestamps, default=None),
            "last": max(included_timestamps, default=None),
        },
        "denominator": sum(counts.values()),
        "refusal_reasons": dict(sorted(counts.items())),
        "parse_errors": parse_errors,
        "legacy_reason_events": legacy_reason_events,
    }


def render_markdown(report: dict) -> str:
    lines = [
        "# RAG refusal snapshot",
        "",
        f"- Commit: `{report['source']['git_sha']}`",
        f"- Source: `{report['source']['path']}`",
        f"- Denominator: {report['denominator']}",
        f"- Filters: `{json.dumps(report['filters'], ensure_ascii=False)}`",
        "",
        "| Refusal reason | Count |",
        "|---|---:|",
    ]
    lines.extend(
        f"| {reason} | {count} |"
        for reason, count in report["refusal_reasons"].items()
    )
    return "\n".join(lines) + "\n"


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("path", type=Path)
    parser.add_argument("--start")
    parser.add_argument("--end")
    parser.add_argument("--context", action="append", dest="contexts")
    parser.add_argument("--json-output", type=Path)
    parser.add_argument("--markdown-output", type=Path)
    args = parser.parse_args()
    report = build_snapshot(
        args.path,
        start=args.start,
        end=args.end,
        execution_contexts=set(args.contexts or ["production"]),
    )
    payload = json.dumps(report, ensure_ascii=False, indent=2) + "\n"
    if args.json_output:
        args.json_output.write_text(payload, encoding="utf-8")
    else:
        print(payload, end="")
    if args.markdown_output:
        args.markdown_output.write_text(render_markdown(report), encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
