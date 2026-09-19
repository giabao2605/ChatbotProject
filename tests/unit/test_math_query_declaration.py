"""Declared traffic scope must match code-owned isolation rows exactly."""
from copy import deepcopy
from pathlib import Path
import hashlib
import json
import sys
import subprocess
from datetime import datetime, timezone

import pytest

from scripts.integrated_eval import math_query_dispatch as dispatch
from scripts.integrated_eval.math_query_matrix import build_draft
from scripts.integrated_eval.math_query_matrix import ROWS
from tests.unit.test_math_query_dispatch_source import _git


@pytest.fixture
def traffic():
    draft = build_draft(Path(__file__).resolve().parents[2])
    return {"rows": draft["rows"], "concurrency": 1, "provider_retries": 0,
            "replacement_requests": 0, "catch_up_requests": 0,
            "arm_order": ["baseline", "candidate"]}


def test_declared_traffic_matches_three_rows_without_granting_dispatch(traffic):
    assert dispatch.validate_matrix_traffic_contract(traffic) is None


@pytest.mark.parametrize("mutation", ["extra_row", "missing_row", "math_off", "extra_flag",
                                    "wrong_manifest", "wrong_count", "false_as_zero",
                                    "parallel", "retry", "reverse_arms", "extra_field"])
def test_declared_traffic_rejects_scope_drift(traffic, mutation):
    changed = deepcopy(traffic)
    if mutation == "extra_row":
        changed["rows"].append(deepcopy(changed["rows"][0]))
    elif mutation == "missing_row":
        changed["rows"].pop()
    elif mutation == "math_off":
        changed["rows"][2]["candidate_flags"]["RAG_GROUNDED_MATH_ENABLED"] = False
    elif mutation == "extra_flag":
        changed["rows"][0]["candidate_flags"]["RAG_GRAPH_RETRIEVAL_ENABLED"] = True
    elif mutation == "wrong_manifest":
        changed["rows"][0]["manifest"]["sha256"] = "0" * 64
    elif mutation == "wrong_count":
        changed["rows"][2]["case_count"] = 13
    elif mutation == "false_as_zero":
        changed["provider_retries"] = False
    elif mutation == "parallel":
        changed["concurrency"] = 5
    elif mutation == "retry":
        changed["provider_retries"] = 1
    elif mutation == "reverse_arms":
        changed["arm_order"].reverse()
    else:
        changed["extra"] = True
    with pytest.raises(ValueError, match="matrix_traffic_contract_invalid"):
        dispatch.validate_matrix_traffic_contract(changed)


@pytest.fixture
def declaration(tmp_path, traffic):
    from scripts.crag_eval.run_rollout import governance_scope_sha256
    from mech_chatbot.governance.feature_activation import VERSION_FIELDS

    source = tmp_path / "source"
    source.mkdir()
    _git(source, "init", "-q")
    real = Path(__file__).resolve().parents[2]
    hashes = {}
    for relative in (*dispatch.MATRIX_TOOL_PATHS, *(row[1] for row in ROWS)):
        path = source / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes((real / relative).read_bytes())
        if relative in dispatch.MATRIX_TOOL_PATHS:
            hashes[relative] = hashlib.sha256(path.read_bytes()).hexdigest()
    _git(source, "add", ".")
    _git(source, "-c", "user.name=Offline Test", "-c", "user.email=offline@example.invalid",
         "commit", "-qm", "synthetic declaration source")
    commit = _git(source, "rev-parse", "HEAD")
    conditions, preflights = {}, {}
    for name, relative, count, digest, _ in ROWS:
        collection = "MechChatbot_GroundedMath_Eval_v1" if name == "math_only" else "MechChatbot_CRAG_Eval_v1"
        conditions[name] = {"git_sha": commit, "manifest_sha256s": [digest],
                            "snapshot_fingerprint": "a" * 64, "provider_configuration_sha256": "b" * 64,
                            "governance_scope_sha256": governance_scope_sha256(source / relative),
                            "benchmark_concurrency": 1, "collection": collection,
                            "execution_context": "evaluation"}
        preflights[name] = {"schema": "grounded-math-fixture-preflight-v1" if name == "math_only"
                           else "decomposition-fixture-preflight-v1", "passed": True,
                           "checked_cases": count, "collection": collection,
                           "batch": "grounded-math-eval-v1" if name == "math_only" else "crag-eval-v1",
                           "failures": [], "fixture_fingerprint": "a" * 64, "case_resolutions": {}}
    return source, {"schema": "math-query-window-declaration-v1", "owner": "synthetic-owner",
                    "source_commit": commit, "run_root": ".local/matrix-test", "tool_hashes": hashes,
                    "traffic": traffic, "conditions": conditions, "preflights": preflights,
                    "rollback_sha256s": {name: "e" * 64 for name, *_ in ROWS},
                    "smoke_sha256s": {name: {"baseline": "d" * 64, "candidate": "d" * 64}
                                      for name, *_ in ROWS},
                    "versions": {key: "synthetic-v1" for key in VERSION_FIELDS}}


def test_declaration_binds_source_conditions_and_preflight_without_creating_root(declaration):
    source, value = declaration
    result = dispatch.validate_matrix_declaration(value, source_root=source)
    assert result["declaration_validated"] is True
    assert result["dispatch_authorized"] is False
    assert not (source / ".local").exists()


def test_launch_inputs_reject_mutable_bytes_before_source_access(tmp_path):
    with pytest.raises(ValueError, match="matrix_launch_bytes_required"):
        dispatch.validate_matrix_launch_inputs(
            bytearray(b"{}"), b"{}", source_root=tmp_path / "missing",
            rollback_artifacts={}, smoke_artifacts={},
            expected_approval_sha256="0" * 64, expected_owner="synthetic-owner",
            now=datetime.now(timezone.utc))


@pytest.mark.parametrize("change", ["snapshot", "commit", "count", "failed", "missing", "extra",
                                    "missing_smoke", "wrong_smoke", "extra_smoke",
                                    "missing_rollback", "wrong_rollback", "extra_rollback"])
def test_declaration_rejects_inconsistent_frozen_runtime(declaration, change):
    source, value = declaration
    changed = deepcopy(value)
    if change == "snapshot":
        changed["preflights"]["query_only"]["fixture_fingerprint"] = "c" * 64
    elif change == "commit":
        for row in changed["conditions"].values():
            row["git_sha"] = "c" * 40
    elif change == "count":
        changed["preflights"]["math_query"]["checked_cases"] = True
    elif change == "failed":
        changed["preflights"]["math_only"]["failures"] = ["fixture drift"]
    elif change == "missing":
        del changed["preflights"]["query_only"]
    elif change == "missing_smoke":
        del changed["smoke_sha256s"]["query_only"]["candidate"]
    elif change == "wrong_smoke":
        changed["smoke_sha256s"]["math_only"]["baseline"] = "not-a-hash"
    elif change == "extra_smoke":
        changed["smoke_sha256s"]["extra"] = {"baseline": "d" * 64, "candidate": "d" * 64}
    elif change == "missing_rollback":
        del changed["rollback_sha256s"]["math_query"]
    elif change == "wrong_rollback":
        changed["rollback_sha256s"]["math_query"] = False
    elif change == "extra_rollback":
        changed["rollback_sha256s"]["extra"] = "e" * 64
    else:
        changed["extra"] = True
    with pytest.raises(ValueError, match="matrix_declaration_invalid"):
        dispatch.validate_matrix_declaration(changed, source_root=source)


@pytest.fixture
def launch_evidence(declaration):
    from scripts.eval.verify_failure_family_rollback import (
        MATH_QUERY_ROLLBACK_PROFILE, ROLLBACK_TEST_PROFILES,
    )

    source, original = declaration
    value = deepcopy(original)
    profiles = {**ROLLBACK_TEST_PROFILES, **MATH_QUERY_ROLLBACK_PROFILE}
    rollbacks, smokes = {}, {}
    for name, _, _, _, flags in ROWS:
        rollback = {"schema": "rollback-test-evidence-v1", "git_sha": value["source_commit"],
                    "flags": sorted(flags), "verified_flag_state": {flag: False for flag in flags},
                    "passed": True, "tested_at": "2026-09-07T00:00:00Z",
                    "command": list(profiles[frozenset(flags)]), "exit_code": 0,
                    "stdout_tail": "1 passed [100%]", "stderr_tail": ""}
        rollbacks[name] = json.dumps(rollback).encode()
        value["rollback_sha256s"][name] = hashlib.sha256(rollbacks[name]).hexdigest()
        smoke = json.dumps({"schema": "provider-smoke-v1", "passed": True,
                            "request_count": 5, "successful_requests": 5, "failed_requests": 0,
                            "provider_retries": 0, "provider_configuration_sha256": "b" * 64,
                            "provider_outcome": {"provider_blocked": False},
                            "completed_at": "2026-09-07T00:00:00Z"}).encode()
        smokes[name] = {arm: smoke for arm in ("baseline", "candidate")}
        value["smoke_sha256s"][name] = {arm: hashlib.sha256(smoke).hexdigest()
                                       for arm in ("baseline", "candidate")}
    return source, value, rollbacks, smokes


@pytest.mark.parametrize("drift", [False, "root", "rollback", "rollback_bytes", "smoke_bytes"])
def test_launch_inputs_bind_approval_to_the_exact_validated_declaration(launch_evidence, drift):
    from tests.unit.test_math_query_approval import _packet

    source, value, rollbacks, smokes = launch_evidence
    raw = json.dumps(value).encode()
    _, approval = _packet()
    approval = {**approval, "draft_sha256": hashlib.sha256(raw).hexdigest()}
    approval_raw = json.dumps(approval).encode()
    if drift == "root":
        raw = json.dumps({**value, "run_root": ".local/different-root"}).encode()
    elif drift == "rollback":
        raw = json.dumps({**value, "rollback_sha256s": {
            **value["rollback_sha256s"], "math_query": "f" * 64}}).encode()
    arguments = {"source_root": source, "expected_owner": "synthetic-owner",
                 "rollback_artifacts": rollbacks, "smoke_artifacts": smokes,
                 "expected_approval_sha256": hashlib.sha256(approval_raw).hexdigest(),
                 "now": datetime(2026, 9, 7, 0, 30, tzinfo=timezone.utc)}
    if drift in {"rollback_bytes", "smoke_bytes"}:
        if drift == "rollback_bytes":
            rollbacks["math_query"] += b" "
        else:
            smokes["query_only"]["candidate"] += b" "
        with pytest.raises(ValueError, match="matrix_(rollback|smoke)_invalid"):
            dispatch.validate_matrix_launch_inputs(raw, approval_raw, **arguments)
    elif drift:
        with pytest.raises(ValueError, match="matrix_approval_invalid"):
            dispatch.validate_matrix_launch_inputs(raw, approval_raw, **arguments)
    else:
        result = dispatch.validate_matrix_launch_inputs(raw, approval_raw, **arguments)
        assert result["approval_bound"] is True
        assert result["declaration_validated"] is True
        assert result["proofs_validated"] is True
        assert result["dispatch_authorized"] is False
        assert result["draft_sha256"] == hashlib.sha256(raw).hexdigest()
        assert result["run_root"] == str(source / ".local/matrix-test")
    assert not (source / ".local").exists()


@pytest.mark.parametrize("fault", ["failed_rollback", "stale_smoke", "missing_row", "extra_arm"])
def test_launch_rejects_invalid_proofs_even_with_valid_approval(launch_evidence, fault):
    from tests.unit.test_math_query_approval import _packet

    source, value, rollbacks, smokes = launch_evidence
    if fault == "failed_rollback":
        artifact = {**json.loads(rollbacks["math_query"]), "passed": False}
        rollbacks["math_query"] = json.dumps(artifact).encode()
        value["rollback_sha256s"]["math_query"] = hashlib.sha256(rollbacks["math_query"]).hexdigest()
    elif fault == "stale_smoke":
        artifact = {**json.loads(smokes["math_only"]["baseline"]),
                    "completed_at": "2026-09-06T23:59:59Z"}
        smokes["math_only"]["baseline"] = json.dumps(artifact).encode()
        value["smoke_sha256s"]["math_only"]["baseline"] = hashlib.sha256(
            smokes["math_only"]["baseline"]).hexdigest()
    elif fault == "missing_row":
        del rollbacks["query_only"]
    else:
        smokes["query_only"]["extra"] = b"{}"
    raw = json.dumps(value).encode()
    _, approval = _packet()
    approval_raw = json.dumps({**approval, "draft_sha256": hashlib.sha256(raw).hexdigest()}).encode()
    with pytest.raises(ValueError, match="matrix_(rollback_invalid|smoke_invalid|proof_inventory_invalid)"):
        dispatch.validate_matrix_launch_inputs(
            raw, approval_raw, source_root=source, expected_owner="synthetic-owner",
            expected_approval_sha256=hashlib.sha256(approval_raw).hexdigest(),
            now=datetime(2026, 9, 7, 0, 30, tzinfo=timezone.utc),
            rollback_artifacts=rollbacks, smoke_artifacts=smokes)
    assert not (source / ".local").exists()


@pytest.mark.parametrize("result", [
    {"schema": "math-query-quality-worker-result-v1", "exit_code": 0,
     "quality": {"reported_cases_bound": True, "observation_coverage_complete": True},
     "matrix_accepted": True, "dispatch_authorized": False},
    {"schema": "math-query-quality-worker-result-v1", "exit_code": 0,
     "quality": {"reported_cases_bound": True, "observation_coverage_complete": True},
     "matrix_accepted": False, "dispatch_authorized": True},
    {"exit_code": 0,
     "quality": {"reported_cases_bound": True, "observation_coverage_complete": True},
     "matrix_accepted": False, "dispatch_authorized": False},
])
def test_worker_result_cannot_claim_matrix_authority(result):
    with pytest.raises(ValueError, match="matrix_worker_result_invalid"):
        dispatch.validate_matrix_worker_result(result)


@pytest.mark.parametrize("container", ["result", "quality"])
@pytest.mark.parametrize("flag", ["matrix_accepted", "dispatch_authorized", "default_rollout_authorized"])
def test_worker_result_rejects_nested_and_default_authority(container, flag):
    quality = {"reported_cases_bound": True, "observation_coverage_complete": True}
    result = {"schema": "math-query-quality-worker-result-v1", "exit_code": 0,
              "quality": quality, "matrix_accepted": False, "dispatch_authorized": False}
    result = ({**result, flag: True} if container == "result" else
              {**result, "quality": {**quality, flag: True}})
    with pytest.raises(ValueError, match="matrix_worker_result_invalid"):
        dispatch.validate_matrix_worker_result(result)


def test_prepare_run_rejects_valid_declared_source_not_executing_here(launch_evidence):
    from tests.unit.test_math_query_approval import _packet

    source, value, rollbacks, smokes = launch_evidence
    raw = json.dumps(value).encode()
    _, approval = _packet()
    approval_raw = json.dumps({**approval, "draft_sha256": hashlib.sha256(raw).hexdigest()}).encode()
    with pytest.raises(ValueError, match="matrix_process_identity_invalid"):
        dispatch.prepare_matrix_run(
            raw, approval_raw, source_root=source, expected_python=Path(sys.executable),
            expected_owner="synthetic-owner",
            expected_approval_sha256=hashlib.sha256(approval_raw).hexdigest(),
            now=datetime(2026, 9, 7, 0, 30, tzinfo=timezone.utc),
            rollback_artifacts=rollbacks, smoke_artifacts=smokes)
    assert not (source / ".local").exists()


def test_process_matrix_rejects_missing_provider_before_claim(launch_evidence):
    from tests.unit.test_math_query_approval import _packet

    source, value, rollbacks, smokes = launch_evidence
    raw = json.dumps(value).encode()
    _, approval = _packet()
    approval_raw = json.dumps({**approval, "draft_sha256": hashlib.sha256(raw).hexdigest()}).encode()
    with pytest.raises(ValueError, match="matrix_provider_environment_invalid"):
        dispatch.execute_matrix_processes(raw, approval_raw, source_root=source,
            expected_python=Path(sys.executable), expected_owner="synthetic-owner",
            expected_approval_sha256=hashlib.sha256(approval_raw).hexdigest(),
            rollback_artifacts=rollbacks, smoke_artifacts=smokes, base_environment={},
            timeout_seconds=30, clock=lambda: datetime(2026, 9, 7, 0, 30, tzinfo=timezone.utc))
    assert not (source / ".local").exists()


@pytest.mark.parametrize("fail_fsync", [False, True])
def test_prepare_run_in_clean_subprocess_claims_once_without_dispatch(launch_evidence, tmp_path, fail_fsync):
    from tests.unit.test_math_query_approval import _packet

    source, value, rollbacks, smokes = launch_evidence
    real = Path(__file__).resolve().parents[2]
    for folder in ("scripts", "src"):
        for original in (real / folder).rglob("*.py"):
            destination = source / original.relative_to(real)
            destination.parent.mkdir(parents=True, exist_ok=True)
            destination.write_bytes(original.read_bytes())
    (source / ".gitignore").write_text(".local/\n__pycache__/\n", encoding="utf-8")
    # Synthetic leaf worker only; retain the real transport and coordinator.
    worker_path = source / "scripts/integrated_eval/math_query_worker.py"
    worker_path.write_text(worker_path.read_text(encoding="utf-8") + '''

def evaluate_quality_arm(manifest_paths, output_dir, label, *, expected_cases, expected_preflight):
    import os
    assert expected_cases and expected_preflight["passed"] is True
    row = os.environ["RAG_EVAL_COMBINATION_ID"]
    assert os.environ["RAG_EXECUTION_CONTEXT"] == "evaluation"
    assert os.environ["RAG_CRAG_ENABLED"] == "false"
    assert os.environ["RAG_GROUNDED_MATH_ENABLED"] == str(label == "candidate" and row != "query_only").lower()
    assert os.environ["RAG_QUERY_DECOMPOSITION_ENABLED"] == str(label == "candidate" and row != "math_only").lower()
    assert os.environ["QDRANT_COLLECTION"] == expected_preflight["collection"]
    assert len(os.environ["RAG_EVAL_PROVIDER_CONFIGURATION_SHA256"]) == 64
    report = Path(output_dir) / label / "eval.json"
    report.parent.mkdir(parents=True)
    from mech_chatbot.evaluation.grounded_math import evaluate_grounded_calculation
    from mech_chatbot.evaluation.decomposition import evaluate_decomposition_case
    ledger = QualityObservationLedger.create(expected_cases, label=label)
    reports = []
    for case in expected_cases:
        answer, debug = "", {}
        reported = {"id": case["id"], "trace_id": "eval:" + label + ":" + case["id"],
                    "answer_metadata": {"sha256": hashlib.sha256(b"").hexdigest(), "char_count": 0},
                    "calculation_evaluation": evaluate_grounded_calculation(case.get("expected_calculation"), [], answer=answer),
                    "decomposition_evaluation": evaluate_decomposition_case(case, debug, answer=answer)}
        ledger = ledger.record(case, reported, {"case_id": case["id"], "trace_id": reported["trace_id"],
                                               "answer": answer, "debug": debug})
        reports.append(reported)
    raw = json.dumps({"cases": reports}).encode()
    report.write_bytes(raw)
    return {"schema": "math-query-quality-worker-result-v1", "exit_code": 2 if label == "baseline" else 0,
            "eval_sha256": hashlib.sha256(raw).hexdigest(),
            "quality": ledger.finalize(reports),
            "matrix_accepted": False, "dispatch_authorized": False}
''', encoding="utf-8")
    value["tool_hashes"]["scripts/integrated_eval/math_query_worker.py"] = hashlib.sha256(worker_path.read_bytes()).hexdigest()
    _git(source, "add", ".")
    _git(source, "-c", "user.name=Offline Test", "-c", "user.email=offline@example.invalid",
         "commit", "-qm", "complete isolated Python source")
    commit = _git(source, "rev-parse", "HEAD")
    value["source_commit"] = commit
    for row in value["conditions"]:
        value["conditions"][row]["git_sha"] = commit
        rollbacks[row] = json.dumps({**json.loads(rollbacks[row]), "git_sha": commit}).encode()
        value["rollback_sha256s"][row] = hashlib.sha256(rollbacks[row]).hexdigest()
    from mech_chatbot.governance.provider_smoke import provider_configuration_sha256
    provider_hash = provider_configuration_sha256({"endpoint": "https://synthetic.invalid/v1",
                                                  "model": "fake-model", "max_concurrent_rag": 1})
    for row in value["conditions"]:
        value["conditions"][row]["provider_configuration_sha256"] = provider_hash
        for arm in smokes[row]:
            smokes[row][arm] = json.dumps({**json.loads(smokes[row][arm]),
                "provider_configuration_sha256": provider_hash}).encode()
            value["smoke_sha256s"][row][arm] = hashlib.sha256(smokes[row][arm]).hexdigest()
    raw = json.dumps(value).encode()
    _, approval = _packet()
    approval_raw = json.dumps({**approval, "draft_sha256": hashlib.sha256(raw).hexdigest()}).encode()
    packet = tmp_path / "synthetic-input.json"
    packet.write_text(json.dumps({"draft": raw.decode(), "approval": approval_raw.decode(),
        "rollbacks": {key: blob.decode() for key, blob in rollbacks.items()},
        "smokes": {key: {arm: blob.decode() for arm, blob in arms.items()}
                   for key, arms in smokes.items()}}), encoding="utf-8")
    code = '''
import sys, json, hashlib
from pathlib import Path
from datetime import datetime, timezone
root = Path.cwd()
sys.path[:0] = [str(root), str(root / "src")]
from scripts.integrated_eval.math_query_dispatch import prepare_matrix_run, validate_matrix_arm_start, execute_matrix_arms, execute_matrix_processes
packet = json.loads(Path(sys.argv[1]).read_text(encoding="utf-8"))
approval = packet["approval"].encode()
args = dict(source_root=root, expected_python=Path(sys.executable),
    expected_owner="synthetic-owner", expected_approval_sha256=hashlib.sha256(approval).hexdigest(),
    now=datetime(2026, 9, 7, 0, 30, tzinfo=timezone.utc),
    rollback_artifacts={key: text.encode() for key, text in packet["rollbacks"].items()},
    smoke_artifacts={key: {arm: text.encode() for arm, text in arms.items()}
                     for key, arms in packet["smokes"].items()})
if sys.argv[2] == "fail-fsync":
    from unittest.mock import patch
    with patch("os.fsync", side_effect=OSError("synthetic durability failure")):
        try:
            prepare_matrix_run(packet["draft"].encode(), approval, **args)
        except OSError as exc:
            assert str(exc) == "synthetic durability failure"
        else:
            raise AssertionError("durability failure reported success")
    assert (root / ".local/matrix-test").is_dir()
else:
    result = prepare_matrix_run(packet["draft"].encode(), approval, **args)
    assert result["root_claimed"] is True
    assert result["process_identity_validated"] is True
    assert result["dispatch_authorized"] is False
    arm = validate_matrix_arm_start(packet["draft"].encode(), approval,
                                    row="math_query", arm="candidate", **args)
    assert arm["arm_inputs_validated"] is True
    assert arm["dispatch_authorized"] is False
    try:
        validate_matrix_arm_start(packet["draft"].encode(), approval,
            row="math_query", arm="candidate",
            **{**args, "now": datetime(2026, 9, 7, 0, 31, tzinfo=timezone.utc)})
    except ValueError as exc:
        assert str(exc) == "matrix_smoke_invalid"
    else:
        raise AssertionError("expired smoke accepted after prepare")
    for fail_at in (None, 1):
        draft = {**json.loads(packet["draft"]), "run_root": ".local/sequence-" + str(fail_at)}
        draft_raw = json.dumps(draft).encode()
        approved = json.dumps({**json.loads(approval),
                               "draft_sha256": hashlib.sha256(draft_raw).hexdigest()}).encode()
        calls = []
        def worker(arm, *, expected_cases, expected_preflight):
            calls.append((arm["row"], arm["label"]))
            assert expected_cases and expected_preflight["passed"] is True
            if len(calls) - 1 == fail_at:
                raise RuntimeError("synthetic worker failure")
            return {"exit_code": 2 if arm["label"] == "baseline" else 0, "quality": {"reported_cases_bound": True,
                    "observation_coverage_complete": True},
                    "schema": "math-query-quality-worker-result-v1",
                    "matrix_accepted": False, "dispatch_authorized": False}
        options = {key: value for key, value in args.items() if key != "now"}
        options["expected_approval_sha256"] = hashlib.sha256(approved).hexdigest()
        if fail_at is None:
            result = execute_matrix_arms(draft_raw, approved, run_arm=worker,
                                        clock=lambda: args["now"], **options)
            assert result["completed_arm_count"] == 6
            assert result["matrix_accepted"] is False
        else:
            try:
                execute_matrix_arms(draft_raw, approved, run_arm=worker,
                                    clock=lambda: args["now"], **options)
            except RuntimeError as exc:
                assert str(exc) == "matrix_worker_failed"
            else:
                raise AssertionError("worker failure ignored")
        expected = [(row, arm) for row in ("math_only", "query_only", "math_query")
                    for arm in ("baseline", "candidate")]
        assert calls == (expected if fail_at is None else expected[:2])
        terminal = json.loads((root / draft["run_root"] / "terminal.json").read_text())
        assert terminal["status"] == ("completed" if fail_at is None else "failed")
        assert terminal["completed_arm_count"] == (6 if fail_at is None else 1)
        assert terminal["matrix_accepted"] is False
        assert "synthetic worker failure" not in json.dumps(terminal)
        receipts = list((root / draft["run_root"] / "arm-receipts").glob("*.receipt.json"))
        assert len(receipts) == (6 if fail_at is None else 1)
        first = json.loads(sorted(receipts)[0].read_text())
        assert first["row"] == "math_only" and first["arm"] == "baseline"
        assert first["draft_sha256"] == hashlib.sha256(draft_raw).hexdigest()
        assert len(first["worker_result_sha256"]) == 64
        result_path = sorted(receipts)[0].with_name(first["worker_result_file"])
        assert hashlib.sha256(result_path.read_bytes()).hexdigest() == first["worker_result_sha256"]
    draft = {**json.loads(packet["draft"]), "run_root": ".local/malicious-worker"}
    draft_raw = json.dumps(draft).encode()
    approved = json.dumps({**json.loads(approval),
                           "draft_sha256": hashlib.sha256(draft_raw).hexdigest()}).encode()
    options = {key: value for key, value in args.items() if key != "now"}
    options["expected_approval_sha256"] = hashlib.sha256(approved).hexdigest()
    def malicious_worker(arm, *, expected_cases, expected_preflight):
        return {"schema": "math-query-quality-worker-result-v1", "exit_code": 0,
                "quality": {"reported_cases_bound": True,
                            "observation_coverage_complete": True},
                "matrix_accepted": True, "dispatch_authorized": False}
    try:
        execute_matrix_arms(draft_raw, approved, run_arm=malicious_worker,
                            clock=lambda: args["now"], **options)
    except RuntimeError as exc:
        assert str(exc) == "matrix_worker_failed"
    else:
        raise AssertionError("worker authority claim accepted")
    terminal = json.loads((root / draft["run_root"] / "terminal.json").read_text())
    assert terminal["status"] == "failed"
    assert terminal["completed_arm_count"] == 0
    assert terminal["matrix_accepted"] is False
    assert not (root / draft["run_root"] / "arm-receipts").exists()
    import os
    draft = {**json.loads(packet["draft"]), "run_root": ".local/process-sequence"}
    draft_raw = json.dumps(draft).encode()
    approved = json.dumps({**json.loads(approval), "draft_sha256": hashlib.sha256(draft_raw).hexdigest()}).encode()
    environment = {key: os.environ[key] for key in ("SystemRoot", "TEMP", "TMP") if key in os.environ}
    environment = {**environment, "PROXYLLM_BASE_URL": "https://synthetic.invalid/v1",
                   "GPT_MODEL_NAME": "fake-model", "MAX_CONCURRENT_RAG": "1"}
    result = execute_matrix_processes(draft_raw, approved,
        **{**options, "expected_approval_sha256": hashlib.sha256(approved).hexdigest()},
        base_environment=environment, timeout_seconds=20, clock=lambda: args["now"])
    assert result["completed_arm_count"] == 6
    assert result["matrix_accepted"] is False
    from scripts.integrated_eval.math_query_evidence import reconcile_matrix_execution
    proof = reconcile_matrix_execution(root / draft["run_root"],
        expected_commit=draft["source_commit"], expected_draft_sha256=hashlib.sha256(draft_raw).hexdigest())
    assert proof["execution_complete"] is True and proof["matrix_accepted"] is False
    assert proof["quality_binding_verified"] is False
    from scripts.integrated_eval.math_query_matrix import ROWS
    from scripts.eval.run_eval import load_manifest_files
    frozen = {name: load_manifest_files([root / relative]) for name, relative, *_ in ROWS}
    bound = reconcile_matrix_execution(root / draft["run_root"], expected_commit=draft["source_commit"],
        expected_draft_sha256=hashlib.sha256(draft_raw).hexdigest(), expected_resolved_cases=frozen)
    assert bound["quality_binding_verified"] is True
    assert bound["matrix_accepted"] is False
    try:
        reconcile_matrix_execution(root / draft["run_root"], expected_commit=draft["source_commit"],
            expected_draft_sha256=hashlib.sha256(draft_raw).hexdigest(), expected_resolved_cases={})
    except ValueError:
        pass
    else:
        raise AssertionError("missing frozen case inventory accepted")
    eval_path = root / draft["run_root"] / "math_only/baseline/eval.json"
    original_eval = eval_path.read_bytes()
    eval_path.write_bytes(original_eval + b" ")
    try:
        reconcile_matrix_execution(root / draft["run_root"], expected_commit=draft["source_commit"],
                                   expected_draft_sha256=hashlib.sha256(draft_raw).hexdigest())
    except ValueError:
        pass
    else:
        raise AssertionError("changed eval accepted")
    eval_path.write_bytes(original_eval)
    altered = root / draft["run_root"] / "arm-receipts/00-math_only-baseline.result.json"
    receipt_path = root / draft["run_root"] / "arm-receipts/00-math_only-baseline.receipt.json"
    original_result = altered.read_bytes()
    original_receipt = receipt_path.read_bytes()
    malicious = {**json.loads(original_result), "matrix_accepted": True}
    altered.write_bytes(json.dumps(malicious, sort_keys=True).encode())
    receipt = {**json.loads(original_receipt),
               "worker_result_sha256": hashlib.sha256(altered.read_bytes()).hexdigest()}
    receipt_path.write_bytes(json.dumps(receipt, sort_keys=True).encode())
    try:
        reconcile_matrix_execution(root / draft["run_root"], expected_commit=draft["source_commit"],
                                   expected_draft_sha256=hashlib.sha256(draft_raw).hexdigest())
    except ValueError:
        pass
    else:
        raise AssertionError("worker authority claim accepted by reconciliation")
    altered.write_bytes(original_result)
    receipt_path.write_bytes(original_receipt)
    altered.write_bytes(altered.read_bytes() + b" ")
    try:
        reconcile_matrix_execution(root / draft["run_root"], expected_commit=draft["source_commit"],
                                   expected_draft_sha256=hashlib.sha256(draft_raw).hexdigest())
    except ValueError:
        pass
    else:
        raise AssertionError("changed result accepted")
try:
    prepare_matrix_run(packet["draft"].encode(), approval, **args)
except ValueError:
    pass
else:
    raise AssertionError("consumed root reused")
print("offline-prepare-claimed-once")
'''
    result = subprocess.run([sys.executable, "-I", "-B", "-c", code, str(packet),
                             "fail-fsync" if fail_fsync else "success"],
                            cwd=source, capture_output=True, text=True, timeout=60)
    assert result.returncode == 0, result.stderr
    assert "offline-prepare-claimed-once" in result.stdout
    assert (source / ".local/matrix-test").is_dir()
    receipt = json.loads((source / ".local/matrix-test/consumed.json").read_text(encoding="utf-8"))
    assert receipt == {"schema": "math-query-root-consumed-v1", "source_commit": commit,
                       "draft_sha256": hashlib.sha256(raw).hexdigest(),
                       "approval_sha256": hashlib.sha256(approval_raw).hexdigest(),
                       "consumed": True, "dispatch_authorized": False}
    assert _git(source, "status", "--porcelain") == ""
