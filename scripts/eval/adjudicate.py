"""Resolve independent evaluation labels into a reproducible artifact."""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SRC = ROOT / "src"
for candidate in (ROOT, SRC):
    if str(candidate) not in sys.path:
        sys.path.insert(0, str(candidate))

from mech_chatbot.evaluation.adjudication import resolve_case_reviews
from mech_chatbot.evaluation.schema import EVALUATOR_MODELS, EVALUATOR_VERSION


def _read_reviews(path: Path) -> dict[str, list[dict]]:
    grouped: dict[str, list[dict]] = defaultdict(list)
    for line_number, raw in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        if not raw.strip():
            continue
        try:
            review = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise ValueError(f"{path}:{line_number}: invalid JSON: {exc.msg}") from exc
        case_id = str(review.pop("case_id", "")).strip()
        if not case_id:
            raise ValueError(f"{path}:{line_number}: case_id is required")
        grouped[case_id].append(review)
    if not grouped:
        raise ValueError("at least one adjudication case is required")
    return grouped


def build_artifact(path: Path) -> dict:
    records = [
        resolve_case_reviews(case_id, reviews)
        for case_id, reviews in sorted(_read_reviews(path).items())
    ]
    return {
        "schema": "evaluation-adjudication-artifact-v1",
        "evaluator_version": EVALUATOR_VERSION,
        "evaluator_models": EVALUATOR_MODELS,
        "source_sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
        "protocol": {
            "independent_reviewers": 2,
            "third_reviewer_on_disagreement": True,
            "reason_code_required": True,
            "raw_prompt_recorded": False,
        },
        "cases": len(records),
        "disagreements": sum(record["disagreement"] for record in records),
        "records": records,
    }


def render_markdown(report: dict) -> str:
    lines = [
        "# Evaluation adjudication", "",
        f"- Schema: `{report['schema']}`",
        f"- Evaluator: `{report['evaluator_version']}`",
        f"- Source SHA-256: `{report['source_sha256']}`",
        f"- Cases: {report['cases']}",
        f"- Disagreements: {report['disagreements']}", "",
        "## Resolved labels", "",
    ]
    for record in report["records"]:
        lines.append(
            f"- `{record['case_id']}`: outcome={record['outcome_label']}; "
            f"answer_correct={str(record['answer_correct']).lower()}; "
            f"citation_correct={str(record['citation_correct']).lower()}; "
            f"resolved_by={record['resolved_by']}"
        )
    return "\n".join(lines) + "\n"


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("reviews", type=Path)
    parser.add_argument("--json-output", type=Path, required=True)
    parser.add_argument("--markdown-output", type=Path, required=True)
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    report = build_artifact(args.reviews)
    args.json_output.parent.mkdir(parents=True, exist_ok=True)
    args.markdown_output.parent.mkdir(parents=True, exist_ok=True)
    args.json_output.write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    args.markdown_output.write_text(render_markdown(report), encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
