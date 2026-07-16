"""Validate human review files and emit a metadata-only fail-closed summary."""

from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
from datetime import datetime, timezone
from pathlib import Path

from scripts.controlled_demo_eval.review_pack import (
    HUMAN_REVIEW_TEMPLATE,
    review_contract_sha256,
)


CONTROLLED_DECISIONS = {"accepted", "rejected", "needs_discussion"}
GRAPH_MUTABLE_FIELDS = {"reviewer", "expected_correct", "review_note"}
ROOT = Path(__file__).resolve().parents[2]


def _rate(numerator: int, denominator: int) -> float | None:
    return numerator / denominator if denominator else None


def pack_contract_sha256(pack: dict) -> str:
    raw = json.dumps(
        pack, ensure_ascii=False, sort_keys=True, separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(raw).hexdigest()


def evaluate_controlled_review(pack: dict, rows, *, anchor: dict) -> dict:
    rows = list(rows or [])
    case_ids = [str(row.get("case_id") or "") for row in rows]
    expected_count = int(pack.get("case_count") or 0)
    anchor_matches = all((
        anchor.get("pack_id") == pack.get("pack_id"),
        anchor.get("pack_sha256") == pack_contract_sha256(pack),
        anchor.get("review_contract_sha256") == pack.get("review_contract_sha256"),
        anchor.get("case_count") == expected_count,
    ))
    contract_matches = (
        len(str(pack.get("review_contract_sha256") or "")) == 64
        and review_contract_sha256(rows) == pack.get("review_contract_sha256")
    )
    pack_valid = all((
        pack.get("schema") == "controlled-demo-human-review-pack-v1",
        pack.get("scope") == "controlled_demo",
        pack.get("local_only") is True,
        expected_count > 0,
        pack.get("source_commit") == (pack.get("pair_provenance") or {}).get("git_sha"),
    ))
    invalid_case_ids = []
    accepted = rejected = discussion = 0
    answer_correct = citation_correct = safety_correct = 0
    reviewers = set()
    for case_id, row in zip(case_ids, rows):
        review = row.get("human_review") or {}
        reviewer = str(review.get("reviewer") or "").strip()
        decision = str(review.get("decision") or "").strip()
        note = str(review.get("note") or "").strip()
        labels = tuple(review.get(field) for field in (
            "answer_correct", "citation_correct", "safety_correct",
        ))
        labels_valid = all(isinstance(value, bool) for value in labels)
        fields_valid = set(review) == set(HUMAN_REVIEW_TEMPLATE)
        decision_valid = decision in CONTROLLED_DECISIONS
        consistent = (
            (decision == "accepted" and labels == (True, True, True))
            or (decision == "rejected" and labels_valid and not all(labels))
            or decision == "needs_discussion"
        )
        if not all((
            case_id, reviewer, note, labels_valid, fields_valid,
            decision_valid, consistent,
        )):
            invalid_case_ids.append(case_id)
            continue
        reviewers.add(reviewer)
        answer_correct += int(labels[0])
        citation_correct += int(labels[1])
        safety_correct += int(labels[2])
        accepted += int(decision == "accepted")
        rejected += int(decision == "rejected")
        discussion += int(decision == "needs_discussion")
    ids_valid = (
        len(case_ids) == expected_count
        and len(set(case_ids)) == len(case_ids)
        and all(case_ids)
    )
    validation_passed = (
        anchor_matches and pack_valid and contract_matches
        and ids_valid and not invalid_case_ids
    )
    review_complete = validation_passed and discussion == 0
    return {
        "schema": "controlled-demo-review-result-v1",
        "pack_id": str(pack.get("pack_id") or ""),
        "source_commit": str(pack.get("source_commit") or ""),
        "validation_passed": validation_passed,
        "anchor_matches": anchor_matches,
        "review_contract_matches": contract_matches,
        "review_complete": review_complete,
        "quality_passed": review_complete and accepted == expected_count,
        "case_count": len(rows),
        "expected_case_count": expected_count,
        "accepted_count": accepted,
        "rejected_count": rejected,
        "needs_discussion_count": discussion,
        "answer_correct_rate": _rate(answer_correct, expected_count),
        "citation_correct_rate": _rate(citation_correct, expected_count),
        "safety_correct_rate": _rate(safety_correct, expected_count),
        "reviewer_count": len(reviewers),
        "invalid_case_ids": sorted(set(invalid_case_ids)),
    }


def _graph_by_id(rows) -> tuple[dict, bool, bool]:
    result = {}
    duplicate = False
    blank = False
    for row in rows or []:
        edge_id = row.get("edge_id")
        if edge_id is None or str(edge_id).strip() == "":
            blank = True
        if edge_id in result:
            duplicate = True
        result[edge_id] = row
    return result, duplicate, blank


def evaluate_graph_review(
    source_rows, reviewed_rows, *, anchor: dict, source_sha256: str,
    minimum_sample=20, minimum_precision=0.95,
) -> dict:
    source, source_duplicates, source_blank = _graph_by_id(source_rows)
    reviewed, reviewed_duplicates, reviewed_blank = _graph_by_id(reviewed_rows)
    anchor_matches = all((
        anchor.get("source_sha256") == source_sha256,
        anchor.get("edge_count") == len(source),
    ))
    invalid_edge_ids = []
    mismatch_edge_ids = []
    correct = 0
    reviewers = set()
    for edge_id, row in reviewed.items():
        original = source.get(edge_id)
        immutable_matches = (
            original is not None
            and set(row) == set(original)
            and all(
                row.get(field) == value
                for field, value in original.items()
                if field not in GRAPH_MUTABLE_FIELDS
            )
        )
        if not immutable_matches:
            mismatch_edge_ids.append(edge_id)
        reviewer = str(row.get("reviewer") or "").strip()
        review_note = str(row.get("review_note") or "").strip()
        expected_correct = row.get("expected_correct")
        row_valid = all((
            immutable_matches,
            reviewer,
            review_note,
            row.get("review_source") == "independent",
            isinstance(expected_correct, bool),
        ))
        if not row_valid:
            invalid_edge_ids.append(edge_id)
            continue
        reviewers.add(reviewer)
        correct += int(expected_correct)
    sample_count = len(reviewed)
    precision = _rate(correct, sample_count)
    validation_passed = all((
        anchor_matches, bool(source), bool(reviewed), not source_duplicates,
        not reviewed_duplicates, not source_blank, not reviewed_blank,
        not invalid_edge_ids,
    ))
    review_complete = validation_passed and sample_count >= int(minimum_sample)
    return {
        "schema": "controlled-demo-graph-review-result-v1",
        "validation_passed": validation_passed,
        "anchor_matches": anchor_matches,
        "review_complete": review_complete,
        "review_sample_count": sample_count,
        "minimum_review_sample": int(minimum_sample),
        "reviewed_edge_precision": precision,
        "minimum_reviewed_edge_precision": float(minimum_precision),
        "ready_for_graph_quality_gate": (
            review_complete
            and precision is not None
            and precision >= float(minimum_precision)
        ),
        "reviewer_count": len(reviewers),
        "invalid_edge_ids": sorted(set(invalid_edge_ids)),
        "immutable_mismatch_edge_ids": sorted(set(mismatch_edge_ids)),
    }


def build_finalization_report(
    *, git_sha: str, crag_review: dict, grounded_math_review: dict,
    graph_review: dict,
) -> dict:
    all_complete = all((
        crag_review.get("review_complete") is True,
        grounded_math_review.get("review_complete") is True,
        graph_review.get("review_complete") is True,
    ))
    graph_ready = graph_review.get("ready_for_graph_quality_gate") is True
    if not crag_review.get("review_complete") or not grounded_math_review.get("review_complete"):
        next_action = "complete_controlled_review_packs"
    elif not graph_review.get("review_complete"):
        next_action = "complete_graph_review"
    elif not graph_ready:
        next_action = "reject_graph_retrieval"
    else:
        next_action = "run_graph_quality_gate"
    return {
        "schema": "controlled-demo-human-review-finalization-v1",
        "scope": "controlled_demo",
        "git_sha": git_sha,
        "all_review_inputs_complete": all_complete,
        "graph_review_ready": graph_ready,
        "community_generation_unlocked": False,
        "next_action": next_action,
        "crag_review": crag_review,
        "grounded_math_review": grounded_math_review,
        "graph_review": graph_review,
    }


def _json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def _jsonl(path: Path) -> list[dict]:
    return [
        json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


def _reference(path: Path, *, schema=None, format=None) -> dict:
    payload = {"path": str(path.resolve()), "sha256": hashlib.sha256(path.read_bytes()).hexdigest()}
    if schema:
        payload["schema"] = schema
    if format:
        payload["format"] = format
    return payload


def main(argv=None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--crag-pack", type=Path, required=True)
    parser.add_argument("--crag-review", type=Path, required=True)
    parser.add_argument("--grounded-math-pack", type=Path, required=True)
    parser.add_argument("--grounded-math-review", type=Path, required=True)
    parser.add_argument("--graph-source", type=Path, required=True)
    parser.add_argument("--graph-review", type=Path, required=True)
    parser.add_argument("--review-anchor", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args(argv)
    if args.output.exists():
        raise FileExistsError(f"finalization artifact already exists: {args.output}")
    crag_pack = _json(args.crag_pack)
    math_pack = _json(args.grounded_math_pack)
    anchor = _json(args.review_anchor)
    if anchor.get("schema") != "controlled-demo-human-review-anchor-v1":
        raise ValueError("review anchor schema must be controlled-demo-human-review-anchor-v1")
    pack_anchors = anchor.get("controlled_packs") or {}
    graph_anchor = anchor.get("graph_queue") or {}
    report = build_finalization_report(
        git_sha=subprocess.check_output(
            ["git", "rev-parse", "HEAD"], cwd=ROOT, text=True,
        ).strip(),
        crag_review=evaluate_controlled_review(
            crag_pack, _jsonl(args.crag_review),
            anchor=pack_anchors.get(str(crag_pack.get("pack_id") or "")) or {},
        ),
        grounded_math_review=evaluate_controlled_review(
            math_pack, _jsonl(args.grounded_math_review),
            anchor=pack_anchors.get(str(math_pack.get("pack_id") or "")) or {},
        ),
        graph_review=evaluate_graph_review(
            _jsonl(args.graph_source), _jsonl(args.graph_review),
            anchor=graph_anchor,
            source_sha256=hashlib.sha256(args.graph_source.read_bytes()).hexdigest(),
        ),
    )
    report["generated_at"] = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
    report["source_artifacts"] = [
        _reference(args.crag_pack, schema=crag_pack.get("schema")),
        _reference(args.crag_review, format="jsonl"),
        _reference(args.grounded_math_pack, schema=math_pack.get("schema")),
        _reference(args.grounded_math_review, format="jsonl"),
        _reference(args.graph_source, format="jsonl"),
        _reference(args.graph_review, format="jsonl"),
        _reference(args.review_anchor, schema=anchor.get("schema")),
    ]
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if report["all_review_inputs_complete"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
