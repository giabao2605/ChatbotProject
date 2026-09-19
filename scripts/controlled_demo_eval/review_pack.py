"""Build local-only human review packs from controlled-demo pair artifacts."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path


HUMAN_REVIEW_TEMPLATE = {
    "reviewer": "",
    "answer_correct": None,
    "citation_correct": None,
    "safety_correct": None,
    "decision": "",
    "note": "",
}
PAIR_PROVENANCE_FIELDS = (
    "git_sha", "manifest_sha256s", "snapshot_fingerprint",
    "provider_configuration_sha256", "governance_scope_sha256", "collection",
    "benchmark_concurrency", "execution_context",
)


def review_contract_sha256(rows) -> str:
    normalized = []
    for row in rows:
        value = dict(row)
        value["human_review"] = dict(HUMAN_REVIEW_TEMPLATE)
        normalized.append(value)
    raw = json.dumps(
        normalized, ensure_ascii=False, sort_keys=True, separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(raw).hexdigest()


def pair_provenance(baseline_eval: dict, candidate_eval: dict) -> dict:
    if baseline_eval.get("schema") != "rag-labeled-eval-v4":
        raise ValueError("baseline evaluation schema must be rag-labeled-eval-v4")
    if candidate_eval.get("schema") != "rag-labeled-eval-v4":
        raise ValueError("candidate evaluation schema must be rag-labeled-eval-v4")
    if baseline_eval.get("run_label") != "baseline":
        raise ValueError("baseline evaluation run_label must be baseline")
    if candidate_eval.get("run_label") != "candidate":
        raise ValueError("candidate evaluation run_label must be candidate")
    provenance = {}
    for field in PAIR_PROVENANCE_FIELDS:
        before = baseline_eval.get(field)
        after = candidate_eval.get(field)
        if before is None or before != after:
            raise ValueError(f"pair provenance mismatch: {field}")
        provenance[field] = before
    return provenance


def _by_id(evaluation: dict) -> dict[str, dict]:
    return {
        str(row.get("id") or ""): row
        for row in evaluation.get("cases") or []
        if str(row.get("id") or "").strip()
    }


def _arm(row: dict) -> dict:
    return {
        "answer": row.get("answer"),
        "passed": row.get("passed"),
        "actual_outcome": row.get("actual_outcome"),
        "retrieved_sources": list(row.get("retrieved_sources") or []),
        "claim_evaluation": row.get("claim_evaluation") or {},
        "citation_evaluation": row.get("citation_evaluation") or {},
        "calculation_evaluation": row.get("calculation_evaluation") or {},
        "decomposition_evaluation": row.get("decomposition_evaluation") or {},
        "leaked": bool(row.get("leaked")),
    }


def build_review_rows(
    manifest_cases, baseline_eval: dict, candidate_eval: dict, *, allowed_groups,
) -> list[dict]:
    """Join manifest expectations to both arms without adding human labels."""
    pair_provenance(baseline_eval, candidate_eval)
    baseline = _by_id(baseline_eval)
    candidate = _by_id(candidate_eval)
    groups = {str(value) for value in allowed_groups}
    rows = []
    for case in manifest_cases:
        if str(case.get("evaluation_group") or "") not in groups:
            continue
        case_id = str(case.get("id") or "")
        if case_id not in baseline or case_id not in candidate:
            raise ValueError(f"missing evaluation result for case {case_id}")
        rows.append({
            "case_id": case_id,
            "evaluation_group": case.get("evaluation_group"),
            "question": case.get("question"),
            "expected_outcome": case.get("expected_outcome"),
            "expected_claims": list(case.get("expected_claims") or []),
            "expected_citations": list(case.get("expected_citations") or []),
            "expected_calculation": case.get("expected_calculation") or {},
            "baseline": _arm(baseline[case_id]),
            "candidate": _arm(candidate[case_id]),
            "human_review": dict(HUMAN_REVIEW_TEMPLATE),
        })
    return rows


def _artifact_reference(path: Path, artifact: dict) -> dict:
    return {
        "path": str(path.resolve()),
        "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
        "schema": artifact.get("schema"),
    }


def load_bound_manifest(
    path: Path, baseline_eval: dict, candidate_eval: dict,
) -> tuple[list[dict], dict]:
    provenance = pair_provenance(baseline_eval, candidate_eval)
    raw = Path(path).read_bytes()
    digest = hashlib.sha256(raw).hexdigest()
    if digest not in set(provenance["manifest_sha256s"]):
        raise ValueError("manifest sha256 is not bound to the evaluation pair")
    rows = [
        json.loads(line) for line in raw.decode("utf-8").splitlines()
        if line.strip()
    ]
    schemas = sorted({
        str(row.get("manifest_schema") or row.get("schema") or "")
        for row in rows
    })
    return rows, {
        "path": str(Path(path).resolve()),
        "sha256": digest,
        "format": "jsonl",
        "schemas": schemas,
    }


def _markdown(rows: list[dict], pack_id: str) -> str:
    lines = [
        f"# Controlled demo review pack: {pack_id}",
        "",
        "File này chỉ dùng review local. Điền nhãn vào `review.jsonl`; không commit raw question hoặc answer.",
        "",
        "Chỉ sửa object `human_review` của từng dòng:",
        "",
        "- `reviewer`: tên hoặc mã reviewer, không để trống.",
        "- `answer_correct`, `citation_correct`, `safety_correct`: dùng JSON boolean `true`/`false`, không đặt trong dấu nháy.",
        "- `decision`: `accepted` chỉ khi cả ba boolean đều `true`; dùng `rejected` khi có ít nhất một giá trị `false`; dùng `needs_discussion` khi cần phân xử.",
        "- `note`: lý do ngắn, dựa trên expected claim/citation và nguồn.",
        "",
        "Không sửa các trường khác; finalizer sẽ so contract hash và từ chối file đã đổi nội dung ngoài `human_review`.",
        "",
    ]
    for row in rows:
        lines.extend([
            f"## {row['case_id']}",
            "",
            f"- Group: `{row['evaluation_group']}`",
            f"- Expected outcome: `{row['expected_outcome']}`",
            f"- Baseline outcome: `{row['baseline']['actual_outcome']}`",
            f"- Candidate outcome: `{row['candidate']['actual_outcome']}`",
            "",
            "Question:",
            "",
            str(row.get("question") or ""),
            "",
            "Candidate answer:",
            "",
            str(row["candidate"].get("answer") or ""),
            "",
            "Human review fields remain blank in `review.jsonl`.",
            "",
        ])
    return "\n".join(lines).rstrip() + "\n"


def write_review_pack(
    *, rows, baseline_eval: dict, candidate_eval: dict, output_dir: Path, pack_id: str,
    source_artifacts=None,
) -> dict:
    provenance = pair_provenance(baseline_eval, candidate_eval)
    output_dir = Path(output_dir).resolve()
    if "reports" not in {part.casefold() for part in output_dir.parts}:
        raise ValueError("human review packs must be written under reports")
    targets = (
        output_dir / "review.jsonl",
        output_dir / "README.md",
        output_dir / "pack.json",
    )
    if any(path.exists() for path in targets):
        raise FileExistsError(f"review pack already exists: {output_dir}")
    output_dir.mkdir(parents=True, exist_ok=True)
    rows = list(rows)
    targets[0].write_text(
        "".join(json.dumps(row, ensure_ascii=False) + "\n" for row in rows),
        encoding="utf-8",
    )
    targets[1].write_text(_markdown(rows, pack_id), encoding="utf-8")
    payload = {
        "schema": "controlled-demo-human-review-pack-v1",
        "scope": "controlled_demo",
        "pack_id": pack_id,
        "local_only": True,
        "case_count": len(rows),
        "reviewed_cases": 0,
        "review_contract_sha256": review_contract_sha256(rows),
        "source_commit": provenance["git_sha"],
        "pair_provenance": provenance,
        "baseline_schema": baseline_eval.get("schema"),
        "candidate_schema": candidate_eval.get("schema"),
        "instructions": {
            "reviewer_required": True,
            "allowed_decisions": ["accepted", "rejected", "needs_discussion"],
            "do_not_commit_raw_content": True,
        },
        "source_artifacts": list(source_artifacts or []),
    }
    targets[2].write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    return payload


def main(argv=None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--baseline-eval", type=Path, required=True)
    parser.add_argument("--candidate-eval", type=Path, required=True)
    parser.add_argument("--group", action="append", required=True)
    parser.add_argument("--pack-id", required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args(argv)
    baseline = json.loads(args.baseline_eval.read_text(encoding="utf-8"))
    candidate = json.loads(args.candidate_eval.read_text(encoding="utf-8"))
    manifest_cases, manifest_reference = load_bound_manifest(
        args.manifest, baseline, candidate,
    )
    rows = build_review_rows(
        manifest_cases, baseline, candidate,
        allowed_groups=set(args.group),
    )
    source_artifacts = [
        manifest_reference,
        _artifact_reference(args.baseline_eval, baseline),
        _artifact_reference(args.candidate_eval, candidate),
    ]
    payload = write_review_pack(
        rows=rows, baseline_eval=baseline, candidate_eval=candidate,
        output_dir=args.output_dir, pack_id=args.pack_id,
        source_artifacts=source_artifacts,
    )
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
