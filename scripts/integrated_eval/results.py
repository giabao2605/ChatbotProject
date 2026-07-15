"""Aggregate metadata-only budget and security results for integrated gates."""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SRC = ROOT / "src"
for value in (ROOT, SRC):
    if str(value) not in sys.path:
        sys.path.insert(0, str(value))

from mech_chatbot.evaluation.integrated_hardening import (
    evaluate_request_budgets,
    evaluate_security_results,
)


def build_results(
    eval_reports, security_cases, *, source_eval_sha256s=None,
    security_results_sha256=None,
) -> dict:
    budget_cases = []
    eval_reports = list(eval_reports or ())
    eval_schemas_valid = bool(eval_reports) and all(
        report.get("schema") == "rag-labeled-eval-v4" for report in eval_reports
    )
    for report in eval_reports:
        if report.get("schema") != "rag-labeled-eval-v4":
            continue
        for case in report.get("cases") or ():
            budget_cases.append({
                field: case.get(field)
                for field in (
                    "id", "combination_id", "planner_count", "subquery_count",
                    "correction_count", "repair_count", "calculation_count",
                    "graph_edge_count", "provider_retries",
                    "final_generation_count", "deadline_exceeded",
                )
            })
    budget = evaluate_request_budgets(budget_cases)
    security = evaluate_security_results(security_cases)
    return {
        "schema": "integrated-hardening-results-v1",
        "passed": eval_schemas_valid and budget["passed"] and security["passed"],
        "eval_schemas_valid": eval_schemas_valid,
        "budget_report": budget,
        "security_report": security,
        "source_eval_sha256s": list(source_eval_sha256s or []),
        "security_results_sha256": security_results_sha256,
    }


def main(argv=None):
    parser = argparse.ArgumentParser()
    parser.add_argument("--eval", type=Path, action="append", required=True)
    parser.add_argument("--security-results", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args(argv)
    reports = [json.loads(path.read_text(encoding="utf-8")) for path in args.eval]
    eval_hashes = [hashlib.sha256(path.read_bytes()).hexdigest() for path in args.eval]
    security = [
        json.loads(line)
        for line in args.security_results.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    artifact = build_results(
        reports, security, source_eval_sha256s=eval_hashes,
        security_results_sha256=hashlib.sha256(
            args.security_results.read_bytes()
        ).hexdigest(),
    )
    artifact["generated_at"] = datetime.now(timezone.utc).isoformat().replace(
        "+00:00", "Z"
    )
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(artifact, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(json.dumps(artifact, ensure_ascii=False, indent=2))
    return 0 if artifact["passed"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
