"""Build and verify a metadata-only review pack for a Query formal window."""

from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import subprocess
import tempfile
from collections import Counter
from datetime import datetime
from pathlib import Path

from scripts.controlled_demo_eval.review_pack import pair_provenance


PACK_SCHEMA = "query-decomposition-human-review-pack-v1"
RESULT_SCHEMA = "query-decomposition-human-review-result-v1"
PAIR_IDS = tuple(f"formal-pair-{index:02d}" for index in range(1, 4))
REASON_CODES = (
    "pass",
    "answer_incorrect",
    "citation_incorrect",
    "safety_incorrect",
    "insufficient_evidence_handling_incorrect",
    "needs_discussion",
)
HUMAN_REVIEW_TEMPLATE = {
    "reviewer": "",
    "reviewed_at": None,
    "answer_correct": None,
    "citation_correct": None,
    "safety_correct": None,
    "decision": "",
    "reason_code": "",
}
FAIL_CLOSED_FIELDS = (
    "provider_smoke_rerun_authorized",
    "retry_or_catch_up_authorized",
    "same_root_reuse_authorized",
    "additional_formal_pairs_authorized",
    "dependency_install_or_download_authorized",
    "shared_environment_upgrade_authorized",
    "pilot_authorized",
    "feature_activation_authorized",
    "default_rollout_authorized",
    "push_authorized",
    "merge_authorized",
)
REQUIRED_ARTIFACT_BINDINGS = {
    "window_disposition",
    "owner_declaration",
    "manifest",
    "offline_preflight",
    "offline_rollback",
    "provider_boundary_preflight",
    "provider_boundary_rollback",
    "provider_smoke",
    *(
        f"{pair_id}_{suffix}"
        for pair_id in PAIR_IDS
        for suffix in (
            "root_trace",
            "run",
            "rollout_pair",
            "gate",
            "baseline_eval",
            "baseline_trace",
            "candidate_eval",
            "candidate_trace",
        )
    ),
}


def _canonical_bytes(value: object) -> bytes:
    return json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")


def _canonical_sha256(value: object) -> str:
    return hashlib.sha256(_canonical_bytes(value)).hexdigest()


def _text_sha256(value: object) -> str:
    return hashlib.sha256(str(value or "").encode("utf-8")).hexdigest()


def _file_sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _is_sha256(value: object) -> bool:
    text = str(value or "")
    return len(text) == 64 and all(character in "0123456789abcdef" for character in text)


def _timestamp_valid(value: object) -> bool:
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return False
    return parsed.tzinfo is not None


def _reviewer_id(value: object) -> str:
    return " ".join(str(value or "").split()).casefold()


def review_contract_sha256(rows) -> str:
    normalized = []
    for row in rows:
        normalized.append(
            {
                **{key: value for key, value in row.items() if key != "pair_instances"},
                "pair_instances": [
                    {
                        **{
                            key: value
                            for key, value in instance.items()
                            if key != "human_review"
                        },
                        "human_review": dict(HUMAN_REVIEW_TEMPLATE),
                    }
                    for instance in row.get("pair_instances") or []
                ],
            }
        )
    return _canonical_sha256(normalized)


def pack_sha256(pack: dict) -> str:
    return _canonical_sha256(pack)


def _validate_disposition(disposition: dict) -> None:
    eligibility = disposition.get("evidence_eligibility") or {}
    governance = disposition.get("governance") or {}
    required_true = (
        "formal_evidence",
        "rollout_evidence",
        "provider_health_passed",
        "query_quality_evaluated",
        "zero_retry_formal_path_exercised",
        "three_pair_gate_passed",
        "full_window_contract_passed",
        "technical_eligible",
    )
    disposition_valid = all(
        (
            disposition.get("schema")
            == "query-decomposition-formal-window-disposition-v2",
            disposition.get("status")
            == "completed_technical_eligible_pending_human_review",
            disposition.get("tombstoned") is False,
            disposition.get("terminal") is True,
            disposition.get("consumed") is True,
            disposition.get("immutable") is True,
            all(eligibility.get(field) is True for field in required_true),
            eligibility.get("production_eligible") is False,
            eligibility.get("decision_status") == "pending_human_review",
            eligibility.get("reuse_authorized") is False,
            eligibility.get("carry_forward_authorized") is False,
            governance.get("query_decomposition_remains_off") is True,
            "independent_human_review_of_this_exact_commit_bound_three_pair_evidence_bundle"
            in (disposition.get("next_required_gate") or []),
        )
    )
    if not disposition_valid:
        raise ValueError("disposition_not_review_eligible")
    if any(governance.get(field) is not False for field in FAIL_CLOSED_FIELDS):
        raise ValueError("governance_not_fail_closed")


def _case_index(evaluation: dict) -> dict[str, dict]:
    result = {}
    for case in evaluation.get("cases") or []:
        case_id = str(case.get("id") or "")
        if not case_id or case_id in result:
            raise ValueError("evaluation_case_ids_invalid")
        result[case_id] = case
    return result


def _answer_metadata(case: dict) -> dict:
    answer = str(case.get("answer") or "")
    return {
        "case_sha256": _canonical_sha256(case),
        "answer_sha256": _text_sha256(answer),
        "answer_char_count": len(answer),
        "passed": case.get("passed") is True,
        "leaked": case.get("leaked") is True,
        "actual_outcome_sha256": _text_sha256(case.get("actual_outcome")),
        "claim_evaluation_sha256": _canonical_sha256(
            case.get("claim_evaluation") or {}
        ),
        "citation_evaluation_sha256": _canonical_sha256(
            case.get("citation_evaluation") or {}
        ),
        "decomposition_evaluation_sha256": _canonical_sha256(
            case.get("decomposition_evaluation") or {}
        ),
    }


def _validate_artifact_bindings(
    artifact_bindings: dict, *, disposition_sha256: str
) -> None:
    valid = (
        set(artifact_bindings) == REQUIRED_ARTIFACT_BINDINGS
        and all(
            isinstance(reference, dict)
            and set(reference) == {"base", "path", "sha256"}
            and reference.get("base")
            == ("source_root" if name == "manifest" else "run_root")
            and bool(str(reference.get("path") or ""))
            and _is_sha256(reference.get("sha256"))
            for name, reference in artifact_bindings.items()
        )
        and artifact_bindings["window_disposition"]["sha256"]
        == disposition_sha256
    )
    if not valid:
        raise ValueError("artifact_bindings_invalid")


def build_locked_review_pack(
    *,
    disposition: dict,
    disposition_sha256: str,
    manifest_rows: list[dict],
    manifest_sha256: str,
    pair_inputs: list[dict],
    artifact_bindings: dict,
    source_owner: str,
) -> tuple[dict, list[dict]]:
    """Return a locked 13-case/39-output pack without raw review content."""
    _validate_disposition(disposition)
    source_commit = str(disposition.get("source_commit") or "")
    run_id = str(disposition.get("run_id") or "")
    if not source_commit or not run_id or not source_owner:
        raise ValueError("source_identity_missing")
    _validate_artifact_bindings(
        artifact_bindings, disposition_sha256=disposition_sha256
    )

    manifest_by_id = {}
    groups = Counter()
    for case in manifest_rows:
        case_id = str(case.get("id") or "")
        group = str(case.get("evaluation_group") or "")
        if not case_id or case_id in manifest_by_id or group not in {"complex", "simple"}:
            raise ValueError("manifest_case_contract_invalid")
        manifest_by_id[case_id] = case
        groups[group] += 1
    if len(manifest_by_id) != 13 or groups != {"complex": 10, "simple": 3}:
        raise ValueError("manifest_case_contract_invalid")
    manifest_ids = set(manifest_by_id)

    if [pair.get("pair_id") for pair in pair_inputs] != list(PAIR_IDS):
        raise ValueError("formal_pair_set_invalid")
    pair_indexes = []
    for pair in pair_inputs:
        baseline = pair["baseline_eval"]
        candidate = pair["candidate_eval"]
        provenance = pair_provenance(baseline, candidate)
        if (
            provenance.get("git_sha") != source_commit
            or manifest_sha256 not in set(provenance.get("manifest_sha256s") or [])
        ):
            raise ValueError("pair_provenance_mismatch")
        baseline_by_id = _case_index(baseline)
        candidate_by_id = _case_index(candidate)
        if set(baseline_by_id) != manifest_ids or set(candidate_by_id) != manifest_ids:
            raise ValueError("pair_case_set_mismatch")
        pair_indexes.append((pair, baseline_by_id, candidate_by_id))

    rows = []
    for case_id in manifest_by_id:
        manifest_case = manifest_by_id[case_id]
        instances = []
        for pair, baseline_by_id, candidate_by_id in pair_indexes:
            instances.append(
                {
                    "pair_id": pair["pair_id"],
                    "baseline_eval_path": pair["baseline_eval_path"],
                    "baseline_eval_sha256": pair["baseline_eval_sha256"],
                    "candidate_eval_path": pair["candidate_eval_path"],
                    "candidate_eval_sha256": pair["candidate_eval_sha256"],
                    "baseline": _answer_metadata(baseline_by_id[case_id]),
                    "candidate": _answer_metadata(candidate_by_id[case_id]),
                    "human_review": dict(HUMAN_REVIEW_TEMPLATE),
                }
            )
        rows.append(
            {
                "case_id": case_id,
                "evaluation_group": manifest_case["evaluation_group"],
                "manifest_case_sha256": _canonical_sha256(manifest_case),
                "pair_instances": instances,
            }
        )

    governance = disposition["governance"]
    pack = {
        "schema": PACK_SCHEMA,
        "status": "locked_unreviewed",
        "scope": "controlled_demo_quality_review",
        "capability": "query_decomposition",
        "source_commit": source_commit,
        "source_owner": source_owner,
        "run_id": run_id,
        "disposition_sha256": disposition_sha256,
        "manifest_sha256": manifest_sha256,
        "case_count": len(rows),
        "output_instance_count": sum(
            len(row["pair_instances"]) for row in rows
        ),
        "group_counts": dict(sorted(groups.items())),
        "review_mode": "independent_human",
        "minimum_reviewers": 1,
        "source_owner_may_review": False,
        "codex_may_review": False,
        "all_cases_required": True,
        "all_pairs_required": True,
        "allowed_reason_codes": list(REASON_CODES),
        "review_contract_sha256": review_contract_sha256(rows),
        "artifact_bindings": dict(sorted(artifact_bindings.items())),
        **{field: governance[field] for field in FAIL_CLOSED_FIELDS},
        "query_decomposition_remains_off": True,
        "production_eligible": False,
    }
    return pack, rows


def evaluate_human_review(
    pack: dict,
    rows: list[dict],
    *,
    expected_pack_sha256: str,
    evaluated_at: str,
) -> dict:
    """Validate all 39 independent-human judgments and emit metadata only."""
    actual_pack_sha256 = pack_sha256(pack)
    pack_hash_matches = actual_pack_sha256 == expected_pack_sha256
    contract_matches = review_contract_sha256(rows) == pack.get(
        "review_contract_sha256"
    )
    pack_valid = all(
        (
            pack.get("schema") == PACK_SCHEMA,
            pack.get("status") == "locked_unreviewed",
            pack.get("scope") == "controlled_demo_quality_review",
            pack.get("review_mode") == "independent_human",
            pack.get("minimum_reviewers") == 1,
            pack.get("source_owner_may_review") is False,
            pack.get("codex_may_review") is False,
            pack.get("case_count") == len(rows) == 13,
            pack.get("output_instance_count") == 39,
            pack.get("production_eligible") is False,
            pack.get("query_decomposition_remains_off") is True,
            all(pack.get(field) is False for field in FAIL_CLOSED_FIELDS),
            pack_hash_matches,
            _timestamp_valid(evaluated_at),
        )
    )
    invalid_instances = []
    reviewers = set()
    accepted = rejected = discussion = 0
    for row in rows:
        case_id = str(row.get("case_id") or "")
        instances = row.get("pair_instances") or []
        if len(instances) != 3:
            invalid_instances.append(f"{case_id}:pair_count")
        for instance in instances:
            pair_id = str(instance.get("pair_id") or "")
            review = instance.get("human_review") or {}
            reviewer = _reviewer_id(review.get("reviewer"))
            labels = tuple(
                review.get(field)
                for field in (
                    "answer_correct",
                    "citation_correct",
                    "safety_correct",
                )
            )
            decision = str(review.get("decision") or "")
            reason = str(review.get("reason_code") or "")
            labels_valid = all(isinstance(value, bool) for value in labels)
            consistent = (
                (decision == "accepted" and labels == (True, True, True) and reason == "pass")
                or (
                    decision == "rejected"
                    and labels_valid
                    and not all(labels)
                    and reason in set(REASON_CODES) - {"pass", "needs_discussion"}
                )
                or (decision == "needs_discussion" and reason == "needs_discussion")
            )
            valid = all(
                (
                    case_id,
                    pair_id in PAIR_IDS,
                    set(review) == set(HUMAN_REVIEW_TEMPLATE),
                    reviewer,
                    reviewer not in {_reviewer_id(pack.get("source_owner")), "codex"},
                    _timestamp_valid(review.get("reviewed_at")),
                    labels_valid,
                    consistent,
                )
            )
            if not valid:
                invalid_instances.append(f"{case_id}:{pair_id}")
                continue
            reviewers.add(reviewer)
            accepted += int(decision == "accepted")
            rejected += int(decision == "rejected")
            discussion += int(decision == "needs_discussion")
    validation_passed = all(
        (
            pack_valid,
            contract_matches,
            not invalid_instances,
            len(reviewers) == 1,
            accepted + rejected + discussion == 39,
        )
    )
    review_complete = validation_passed and discussion == 0
    quality_passed = review_complete and accepted == 39
    return {
        "schema": RESULT_SCHEMA,
        "scope": "controlled_demo_quality_review",
        "capability": "query_decomposition",
        "source_commit": pack.get("source_commit"),
        "run_id": pack.get("run_id"),
        "disposition_sha256": pack.get("disposition_sha256"),
        "review_mode": pack.get("review_mode"),
        "reviewer": next(iter(reviewers), None),
        "evaluated_at": evaluated_at,
        "pack_sha256": actual_pack_sha256,
        "expected_pack_sha256": expected_pack_sha256,
        "pack_hash_matches": pack_hash_matches,
        "review_contract_sha256": pack.get("review_contract_sha256"),
        "reviewed_rows_sha256": _canonical_sha256(rows),
        "validation_passed": validation_passed,
        "review_complete": review_complete,
        "quality_passed": quality_passed,
        "case_count": len(rows),
        "reviewed_output_count": accepted + rejected + discussion,
        "accepted_count": accepted,
        "rejected_count": rejected,
        "needs_discussion_count": discussion,
        "invalid_instances": sorted(set(invalid_instances)),
        "production_eligible": False,
        "pilot_authorized": False,
        "feature_activation_authorized": False,
        "default_rollout_authorized": False,
        "push_authorized": False,
        "merge_authorized": False,
        "query_decomposition_remains_off": True,
    }


def _readme(pack: dict) -> str:
    return "\n".join(
        (
            f"# Query human review: {pack['run_id']}",
            "",
            "Pack metadata-only da khoa cho 13 case va ca 3 formal pair.",
            "Chi sua object `human_review` trong tung `pair_instances` cua `review.jsonl`.",
            "Reviewer phai la mot con nguoi doc lap, khong phai source owner va khong phai Codex.",
            "Mo artifact eval theo path/hash trong pack de xem noi dung; khong chep raw question, answer hoac document text vao pack.",
            "Review accepted chi khi answer, citation va safety deu true; needs_discussion khong qua quality gate.",
            "Pack nay khong authorize pilot, activation, default rollout, push, merge hoac provider traffic.",
            "",
        )
    )


def write_locked_review_pack(output_dir: Path, pack: dict, rows: list[dict]) -> None:
    output = Path(output_dir)
    if output.exists():
        raise FileExistsError("review_pack_already_exists")
    output.parent.mkdir(parents=True, exist_ok=True)
    temporary = Path(tempfile.mkdtemp(prefix=f".{output.name}.tmp-", dir=output.parent))
    try:
        (temporary / "pack.json").write_text(
            json.dumps(pack, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )
        (temporary / "review.jsonl").write_text(
            "".join(json.dumps(row, ensure_ascii=False) + "\n" for row in rows),
            encoding="utf-8",
        )
        (temporary / "README.md").write_text(_readme(pack), encoding="utf-8")
        if output.exists():
            raise FileExistsError("review_pack_already_exists")
        temporary.replace(output)
    finally:
        if temporary.exists():
            shutil.rmtree(temporary)


def _json_from_bytes(raw: bytes, *, name: str) -> dict:
    value = json.loads(raw.decode("utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"json_object_required:{name}")
    return value


def _jsonl_from_bytes(raw: bytes) -> list[dict]:
    return [
        json.loads(line)
        for line in raw.decode("utf-8").splitlines()
        if line.strip()
    ]


def _git_text(source_root: Path, *args: str) -> str:
    result = subprocess.run(
        ["git", "-C", str(source_root), *args],
        check=True,
        capture_output=True,
        text=True,
        encoding="utf-8",
    )
    return result.stdout.strip()


def _bind(
    bindings: dict,
    *,
    name: str,
    path: Path,
    expected_sha256: str,
    base: str,
    root: Path,
) -> bytes:
    raw = path.read_bytes()
    actual = hashlib.sha256(raw).hexdigest()
    if actual != expected_sha256:
        raise ValueError(f"artifact_hash_mismatch:{name}")
    bindings[name] = {
        "base": base,
        "path": path.relative_to(root).as_posix(),
        "sha256": actual,
    }
    return raw


def load_bound_run(
    run_root: Path, *, expected_source_commit: str, expected_disposition_sha256: str
) -> tuple[dict, list[dict]]:
    """Load a clean exact run-root, rehash every bound artifact, then build."""
    run_root = Path(run_root).resolve()
    if run_root.parent.name != ".local":
        raise ValueError("run_root_must_be_under_dot_local")
    source_root = run_root.parent.parent
    if _git_text(source_root, "rev-parse", "HEAD") != expected_source_commit:
        raise ValueError("source_commit_mismatch")
    if _git_text(source_root, "status", "--porcelain", "--untracked-files=no"):
        raise ValueError("source_worktree_not_clean")

    disposition_path = run_root / "window-disposition.json"
    disposition_raw = disposition_path.read_bytes()
    disposition_sha256 = hashlib.sha256(disposition_raw).hexdigest()
    if disposition_sha256 != expected_disposition_sha256:
        raise ValueError("disposition_sha256_mismatch")
    disposition = _json_from_bytes(disposition_raw, name=disposition_path.name)
    _validate_disposition(disposition)
    if disposition.get("source_commit") != expected_source_commit:
        raise ValueError("source_commit_mismatch")

    bindings = {
        "window_disposition": {
            "base": "run_root",
            "path": "window-disposition.json",
            "sha256": disposition_sha256,
        }
    }
    owner_ref = disposition["owner_declaration"]
    owner_path = run_root / owner_ref["path"]
    owner_raw = _bind(
        bindings,
        name="owner_declaration",
        path=owner_path,
        expected_sha256=owner_ref["sha256"],
        base="run_root",
        root=run_root,
    )
    owner = _json_from_bytes(owner_raw, name=owner_path.name)
    scope = owner.get("scope") or {}
    manifest_path = source_root / str(scope.get("manifest_path") or "")
    manifest_sha256 = str(scope.get("manifest_sha256") or "")
    manifest_raw = manifest_path.read_bytes()
    actual_manifest_sha256 = hashlib.sha256(manifest_raw).hexdigest()
    if actual_manifest_sha256 != manifest_sha256:
        raise ValueError("manifest_hash_mismatch")
    bindings["manifest"] = {
        "base": "source_root",
        "path": manifest_path.relative_to(source_root).as_posix(),
        "sha256": actual_manifest_sha256,
    }

    for prefix, section_name in (
        ("offline", "offline_evidence"),
        ("provider_boundary", "provider_boundary_evidence"),
    ):
        section = disposition[section_name]
        for kind in ("preflight", "rollback"):
            _bind(
                bindings,
                name=f"{prefix}_{kind}",
                path=run_root / section[f"{kind}_path"],
                expected_sha256=section[f"{kind}_sha256"],
                base="run_root",
                root=run_root,
            )
    smoke = disposition["provider_smoke"]
    _bind(
        bindings,
        name="provider_smoke",
        path=run_root / smoke["path"],
        expected_sha256=smoke["sha256"],
        base="run_root",
        root=run_root,
    )

    pair_inputs = []
    for index, pair_id in enumerate(PAIR_IDS, start=1):
        section = disposition[f"formal_pair_{index:02d}"]
        for key, suffix in (
            ("root_trace", "trace"),
            ("run", "run"),
            ("rollout_pair", "rollout_pair"),
            ("gate", "gate"),
        ):
            path_key = "trace_path" if suffix == "trace" else f"{suffix}_path"
            hash_key = "trace_sha256" if suffix == "trace" else f"{suffix}_sha256"
            _bind(
                bindings,
                name=f"{pair_id}_{key}",
                path=run_root / section[path_key],
                expected_sha256=section[hash_key],
                base="run_root",
                root=run_root,
            )
        eval_paths = {
            "baseline": run_root / pair_id / "baseline" / "eval.json",
            "candidate": run_root / pair_id / "candidate" / "eval.json",
        }
        trace_paths = {
            "baseline": run_root / pair_id / "baseline" / "trace.json",
            "candidate": run_root / pair_id / "candidate" / "trace.json",
        }
        eval_raw = {}
        for arm in ("baseline", "candidate"):
            eval_raw[arm] = _bind(
                bindings,
                name=f"{pair_id}_{arm}_eval",
                path=eval_paths[arm],
                expected_sha256=section[f"{arm}_eval_sha256"],
                base="run_root",
                root=run_root,
            )
            _bind(
                bindings,
                name=f"{pair_id}_{arm}_trace",
                path=trace_paths[arm],
                expected_sha256=section[f"{arm}_trace_sha256"],
                base="run_root",
                root=run_root,
            )
        pair_inputs.append(
            {
                "pair_id": pair_id,
                "baseline_eval": _json_from_bytes(
                    eval_raw["baseline"], name=eval_paths["baseline"].name
                ),
                "candidate_eval": _json_from_bytes(
                    eval_raw["candidate"], name=eval_paths["candidate"].name
                ),
                "baseline_eval_path": eval_paths["baseline"].relative_to(run_root).as_posix(),
                "candidate_eval_path": eval_paths["candidate"].relative_to(run_root).as_posix(),
                "baseline_eval_sha256": section["baseline_eval_sha256"],
                "candidate_eval_sha256": section["candidate_eval_sha256"],
            }
        )

    return build_locked_review_pack(
        disposition=disposition,
        disposition_sha256=disposition_sha256,
        manifest_rows=_jsonl_from_bytes(manifest_raw),
        manifest_sha256=manifest_sha256,
        pair_inputs=pair_inputs,
        artifact_bindings=bindings,
        source_owner=str(owner.get("owner") or ""),
    )


def main(argv=None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--run-root", type=Path, required=True)
    parser.add_argument("--expected-source-commit", required=True)
    parser.add_argument("--expected-disposition-sha256", required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args(argv)
    pack, rows = load_bound_run(
        args.run_root,
        expected_source_commit=args.expected_source_commit,
        expected_disposition_sha256=args.expected_disposition_sha256,
    )
    write_locked_review_pack(args.output_dir.resolve(), pack, rows)
    print(json.dumps(pack, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
