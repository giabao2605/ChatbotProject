import hashlib
import json

import pytest

from scripts.decomposition_eval import human_review_pack as review_pack_module
from scripts.decomposition_eval.human_review_pack import (
    HUMAN_REVIEW_TEMPLATE,
    build_locked_review_pack,
    evaluate_human_review,
    load_bound_run,
    review_contract_sha256,
    write_locked_review_pack,
)


SOURCE_COMMIT = "7" * 40
DISPOSITION_SHA256 = "d" * 64
MANIFEST_SHA256 = "m" * 64


def _cases():
    return [
        {
            "id": f"case-{index:02d}",
            "evaluation_group": "complex" if index <= 10 else "simple",
            "question": f"raw question {index}",
            "expected_claims": [f"raw expected claim {index}"],
        }
        for index in range(1, 14)
    ]


def _evaluation(label, pair_index):
    return {
        "schema": "rag-labeled-eval-v4",
        "run_label": label,
        "git_sha": SOURCE_COMMIT,
        "manifest_sha256s": [MANIFEST_SHA256],
        "snapshot_fingerprint": "s" * 64,
        "provider_configuration_sha256": "p" * 64,
        "governance_scope_sha256": "g" * 64,
        "collection": "MechChatbot_CRAG_Eval_v1",
        "benchmark_concurrency": 1,
        "execution_context": "evaluation",
        "cases": [
            {
                "id": case["id"],
                "evaluation_group": case["evaluation_group"],
                "answer_metadata": {
                    "sha256": hashlib.sha256(
                        f"raw {label} answer {pair_index} {case['id']}".encode()
                    ).hexdigest(),
                    "char_count": len(
                        f"raw {label} answer {pair_index} {case['id']}"
                    ),
                },
                "passed": True,
                "actual_outcome": "full_answer",
                "leaked": False,
                "claim_evaluation": {"passed": True},
                "citation_evaluation": {"passed": True},
                "decomposition_evaluation": {"passed": True},
            }
            for case in _cases()
        ],
    }


def _capture(label, pair_index):
    return [
        {
            "schema": "query-decomposition-local-review-content-v1",
            "run_label": label,
            "case_id": case["id"],
            "question": case["question"],
            "answer": f"raw {label} answer {pair_index} {case['id']}",
            "answer_sha256": hashlib.sha256(
                f"raw {label} answer {pair_index} {case['id']}".encode()
            ).hexdigest(),
            "answer_char_count": len(
                f"raw {label} answer {pair_index} {case['id']}"
            ),
        }
        for case in _cases()
    ]


def _disposition():
    return {
        "schema": "query-decomposition-formal-window-disposition-v2",
        "run_id": "query-formal-78160aa-20260825-01",
        "source_commit": SOURCE_COMMIT,
        "status": "completed_technical_eligible_pending_human_review",
        "tombstoned": False,
        "terminal": True,
        "consumed": True,
        "immutable": True,
        "evidence_eligibility": {
            "formal_evidence": True,
            "rollout_evidence": True,
            "provider_health_passed": True,
            "query_quality_evaluated": True,
            "zero_retry_formal_path_exercised": True,
            "three_pair_gate_passed": True,
            "full_window_contract_passed": True,
            "technical_eligible": True,
            "production_eligible": False,
            "decision_status": "pending_human_review",
            "reuse_authorized": False,
            "carry_forward_authorized": False,
        },
        "governance": {
            "provider_smoke_rerun_authorized": False,
            "retry_or_catch_up_authorized": False,
            "same_root_reuse_authorized": False,
            "additional_formal_pairs_authorized": False,
            "dependency_install_or_download_authorized": False,
            "shared_environment_upgrade_authorized": False,
            "pilot_authorized": False,
            "feature_activation_authorized": False,
            "default_rollout_authorized": False,
            "push_authorized": False,
            "merge_authorized": False,
            "query_decomposition_remains_off": True,
        },
        "next_required_gate": [
            "independent_human_review_of_this_exact_commit_bound_three_pair_evidence_bundle"
        ],
    }


def _pair_inputs():
    return [
        {
            "pair_id": f"formal-pair-{index:02d}",
            "baseline_eval": _evaluation("baseline", index),
            "candidate_eval": _evaluation("candidate", index),
            "baseline_eval_path": f"formal-pair-{index:02d}/baseline/eval.json",
            "candidate_eval_path": f"formal-pair-{index:02d}/candidate/eval.json",
            "baseline_eval_sha256": str(index) * 64,
            "candidate_eval_sha256": str(index + 3) * 64,
            "baseline_review_capture": _capture("baseline", index),
            "candidate_review_capture": _capture("candidate", index),
            "baseline_review_capture_path": (
                f"formal-pair-{index:02d}/baseline/review-content.jsonl"
            ),
            "candidate_review_capture_path": (
                f"formal-pair-{index:02d}/candidate/review-content.jsonl"
            ),
            "baseline_review_capture_sha256": str(index + 6) * 64,
            "candidate_review_capture_sha256": str(index + 9) * 64,
        }
        for index in range(1, 4)
    ]


def _artifact_bindings():
    names = {
        "window_disposition",
        "owner_declaration",
        "manifest",
        "offline_preflight",
        "offline_rollback",
        "provider_boundary_preflight",
        "provider_boundary_rollback",
        "provider_smoke",
    }
    for pair_id in ("formal-pair-01", "formal-pair-02", "formal-pair-03"):
        names.update(
            f"{pair_id}_{suffix}"
            for suffix in (
                "root_trace",
                "run",
                "rollout_pair",
                "gate",
                "baseline_eval",
                "baseline_trace",
                "baseline_review_capture",
                "candidate_eval",
                "candidate_trace",
                "candidate_review_capture",
            )
        )
    bindings = {
        name: {
            "base": "source_root" if name == "manifest" else "run_root",
            "path": f"{name}.json",
            "sha256": "a" * 64,
        }
        for name in names
    }
    bindings["window_disposition"]["sha256"] = DISPOSITION_SHA256
    return bindings


def _build():
    return build_locked_review_pack(
        disposition=_disposition(),
        disposition_sha256=DISPOSITION_SHA256,
        manifest_rows=_cases(),
        manifest_sha256=MANIFEST_SHA256,
        pair_inputs=_pair_inputs(),
        artifact_bindings=_artifact_bindings(),
        source_owner="bao.nguyen",
    )


def test_build_locked_review_pack_is_metadata_only_and_covers_three_pairs():
    pack, rows = _build()

    assert pack["schema"] == "query-decomposition-human-review-pack-v2"
    assert pack["status"] == "locked_unreviewed"
    assert pack["source_commit"] == SOURCE_COMMIT
    assert pack["disposition_sha256"] == DISPOSITION_SHA256
    assert pack["case_count"] == 13
    assert pack["output_instance_count"] == 39
    assert pack["review_mode"] == "independent_human"
    assert pack["pilot_authorized"] is False
    assert pack["feature_activation_authorized"] is False
    assert pack["default_rollout_authorized"] is False
    assert len(rows) == 13
    assert all(len(row["pair_instances"]) == 3 for row in rows)
    assert all(
        instance["human_review"] == HUMAN_REVIEW_TEMPLATE
        for row in rows
        for instance in row["pair_instances"]
    )
    assert rows[0]["pair_instances"][0]["candidate_review_capture_path"].endswith(
        "candidate/review-content.jsonl"
    )

    serialized = json.dumps([pack, rows])
    for forbidden in (
        "raw question",
        "raw baseline answer",
        "raw candidate answer",
        "raw reference",
        '"question"',
        '"answer"',
        '"reference_answer"',
    ):
        assert forbidden not in serialized


def test_build_rejects_fail_open_governance_and_pair_case_drift():
    disposition = _disposition()
    disposition["governance"]["pilot_authorized"] = True
    with pytest.raises(ValueError, match="governance_not_fail_closed"):
        build_locked_review_pack(
            disposition=disposition,
            disposition_sha256=DISPOSITION_SHA256,
            manifest_rows=_cases(),
            manifest_sha256=MANIFEST_SHA256,
            pair_inputs=_pair_inputs(),
            artifact_bindings=_artifact_bindings(),
            source_owner="bao.nguyen",
        )

    pair_inputs = _pair_inputs()
    pair_inputs[2]["candidate_eval"]["cases"].pop()
    with pytest.raises(ValueError, match="pair_case_set_mismatch"):
        build_locked_review_pack(
            disposition=_disposition(),
            disposition_sha256=DISPOSITION_SHA256,
            manifest_rows=_cases(),
            manifest_sha256=MANIFEST_SHA256,
            pair_inputs=pair_inputs,
            artifact_bindings=_artifact_bindings(),
            source_owner="bao.nguyen",
        )


def test_build_rejects_incomplete_artifact_binding_set():
    bindings = _artifact_bindings()
    bindings.pop("formal-pair-03_candidate_trace")

    with pytest.raises(ValueError, match="artifact_bindings_invalid"):
        build_locked_review_pack(
            disposition=_disposition(),
            disposition_sha256=DISPOSITION_SHA256,
            manifest_rows=_cases(),
            manifest_sha256=MANIFEST_SHA256,
            pair_inputs=_pair_inputs(),
            artifact_bindings=bindings,
            source_owner="bao.nguyen",
        )


def test_review_contract_hash_allows_labels_only():
    _, rows = _build()
    original = review_contract_sha256(rows)
    reviewed = json.loads(json.dumps(rows))
    reviewed[0]["pair_instances"][0]["human_review"] = {
        "reviewer": "tran.nghi",
        "reviewed_at": "2026-08-25T12:00:00+07:00",
        "answer_correct": True,
        "citation_correct": True,
        "safety_correct": True,
        "decision": "accepted",
        "reason_code": "pass",
    }

    assert review_contract_sha256(reviewed) == original
    reviewed[0]["pair_instances"][0]["candidate_case_sha256"] = "0" * 64
    assert review_contract_sha256(reviewed) != original


def test_human_review_requires_one_independent_human_and_all_outputs():
    pack, rows = _build()
    reviewed = json.loads(json.dumps(rows))
    for row in reviewed:
        for instance in row["pair_instances"]:
            instance["human_review"] = {
                "reviewer": "tran.nghi",
                "reviewed_at": "2026-08-25T12:00:00+07:00",
                "answer_correct": True,
                "citation_correct": True,
                "safety_correct": True,
                "decision": "accepted",
                "reason_code": "pass",
            }

    result = evaluate_human_review(
        pack,
        reviewed,
        expected_pack_sha256=review_pack_module.pack_sha256(pack),
        evaluated_at="2026-08-25T05:05:00Z",
    )

    assert result["validation_passed"] is True
    assert result["review_complete"] is True
    assert result["quality_passed"] is True
    assert result["reviewer"] == "tran.nghi"
    assert result["reviewed_output_count"] == 39
    assert result["pilot_authorized"] is False
    assert result["default_rollout_authorized"] is False

    reviewed[0]["pair_instances"][0]["human_review"]["reviewer"] = "bao.nguyen"
    rejected = evaluate_human_review(
        pack,
        reviewed,
        expected_pack_sha256=review_pack_module.pack_sha256(pack),
        evaluated_at="2026-08-25T05:05:00Z",
    )
    assert rejected["validation_passed"] is False
    assert rejected["quality_passed"] is False

    tampered_pack = {**pack, "source_owner": "someone.else"}
    owner_review = json.loads(json.dumps(rows))
    for row in owner_review:
        for instance in row["pair_instances"]:
            instance["human_review"] = {
                **instance["human_review"],
                "reviewer": "bao.nguyen",
                "reviewed_at": "2026-08-25T12:00:00+07:00",
                "answer_correct": True,
                "citation_correct": True,
                "safety_correct": True,
                "decision": "accepted",
                "reason_code": "pass",
            }
    tampered = evaluate_human_review(
        tampered_pack,
        owner_review,
        expected_pack_sha256=review_pack_module.pack_sha256(pack),
        evaluated_at="2026-08-25T05:05:00Z",
    )
    assert tampered["pack_hash_matches"] is False
    assert tampered["validation_passed"] is False
    assert tampered["quality_passed"] is False


def test_write_locked_pack_does_not_overwrite(tmp_path):
    pack, rows = _build()
    output = tmp_path / "query-review"

    write_locked_review_pack(output, pack, rows)

    assert (output / "pack.json").is_file()
    assert (output / "review.jsonl").is_file()
    assert (output / "README.md").is_file()
    with pytest.raises(FileExistsError, match="already_exists"):
        write_locked_review_pack(output, pack, rows)


def _sha256(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _write_bound_run(tmp_path):
    source = tmp_path / "source"
    run_root = source / ".local" / "query-formal-78160aa-20260825-01"
    run_root.mkdir(parents=True)
    manifest = source / "data" / "decomposition_eval_v1" / "eval_manifest.jsonl"
    manifest.parent.mkdir(parents=True)
    manifest.write_text(
        "".join(json.dumps(case) + "\n" for case in _cases()), encoding="utf-8"
    )
    owner = {
        "owner": "bao.nguyen",
        "scope": {
            "manifest_path": "data/decomposition_eval_v1/eval_manifest.jsonl",
            "manifest_sha256": _sha256(manifest),
        },
    }
    owner_path = run_root / "owner-declaration.json"
    owner_path.write_text(json.dumps(owner), encoding="utf-8")

    disposition = _disposition()
    disposition["owner_declaration"] = {
        "path": owner_path.name,
        "sha256": _sha256(owner_path),
    }
    for section_name, prefix in (
        ("offline_evidence", "offline"),
        ("provider_boundary_evidence", "provider-boundary"),
    ):
        section = {}
        for kind in ("preflight", "rollback"):
            path = run_root / f"{prefix}-{kind}.json"
            path.write_text(json.dumps({"passed": True}), encoding="utf-8")
            section[f"{kind}_path"] = path.name
            section[f"{kind}_sha256"] = _sha256(path)
        disposition[section_name] = section
    smoke_path = run_root / "provider-smoke.json"
    smoke_path.write_text(json.dumps({"passed": True}), encoding="utf-8")
    disposition["provider_smoke"] = {
        "path": smoke_path.name,
        "sha256": _sha256(smoke_path),
    }

    for index, pair_id in enumerate(("formal-pair-01", "formal-pair-02", "formal-pair-03"), start=1):
        pair_root = run_root / pair_id
        (pair_root / "baseline").mkdir(parents=True)
        (pair_root / "candidate").mkdir()
        section = {"output_path": pair_id}
        for key, relative in (
            ("trace", f"rag-trace-pair-{index:02d}.jsonl"),
            ("run", f"{pair_id}/run.json"),
            ("rollout_pair", f"{pair_id}/rollout_pair.json"),
            ("gate", f"{pair_id}/gate.json"),
        ):
            path = run_root / relative
            path.write_text("{}\n", encoding="utf-8")
            section[f"{key}_path"] = relative
            section[f"{key}_sha256"] = _sha256(path)
        for arm in ("baseline", "candidate"):
            eval_path = pair_root / arm / "eval.json"
            evaluation = _evaluation(arm, index)
            evaluation["manifest_sha256s"] = [_sha256(manifest)]
            eval_path.write_text(
                json.dumps(evaluation), encoding="utf-8"
            )
            trace_path = pair_root / arm / "trace.json"
            trace_path.write_text("{}\n", encoding="utf-8")
            capture_path = pair_root / arm / "review-content.jsonl"
            capture_path.write_text(
                "".join(json.dumps(row) + "\n" for row in _capture(arm, index)),
                encoding="utf-8",
            )
            section[f"{arm}_eval_sha256"] = _sha256(eval_path)
            section[f"{arm}_trace_sha256"] = _sha256(trace_path)
            section[f"{arm}_review_capture_path"] = (
                f"{pair_id}/{arm}/review-content.jsonl"
            )
            section[f"{arm}_review_capture_sha256"] = _sha256(capture_path)
        disposition[f"formal_pair_{index:02d}"] = section

    disposition_path = run_root / "window-disposition.json"
    disposition_path.write_text(json.dumps(disposition), encoding="utf-8")
    return run_root, _sha256(disposition_path)


def test_load_bound_run_hashes_and_parses_same_artifacts(tmp_path, monkeypatch):
    run_root, disposition_sha256 = _write_bound_run(tmp_path)

    def clean_git(_root, *args):
        return SOURCE_COMMIT if args == ("rev-parse", "HEAD") else ""

    monkeypatch.setattr(review_pack_module, "_git_text", clean_git)
    pack, rows = load_bound_run(
        run_root,
        expected_source_commit=SOURCE_COMMIT,
        expected_disposition_sha256=disposition_sha256,
    )
    assert pack["case_count"] == 13
    assert len(rows) == 13

    candidate = run_root / "formal-pair-03" / "candidate" / "eval.json"
    candidate.write_text("{}", encoding="utf-8")
    with pytest.raises(ValueError, match="artifact_hash_mismatch"):
        load_bound_run(
            run_root,
            expected_source_commit=SOURCE_COMMIT,
            expected_disposition_sha256=disposition_sha256,
        )

    with pytest.raises(ValueError, match="disposition_sha256_mismatch"):
        load_bound_run(
            run_root,
            expected_source_commit=SOURCE_COMMIT,
            expected_disposition_sha256="0" * 64,
        )

    def dirty_git(_root, *args):
        return SOURCE_COMMIT if args == ("rev-parse", "HEAD") else " M tracked.py"

    monkeypatch.setattr(review_pack_module, "_git_text", dirty_git)
    with pytest.raises(ValueError, match="source_worktree_not_clean"):
        load_bound_run(
            run_root,
            expected_source_commit=SOURCE_COMMIT,
            expected_disposition_sha256=disposition_sha256,
        )
