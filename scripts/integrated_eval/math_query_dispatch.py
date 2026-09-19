"""Matrix dispatcher guards under construction; no dispatch or authorization CLI."""
from __future__ import annotations

import hashlib
import json
import os
from datetime import datetime, timedelta
from pathlib import Path, PureWindowsPath
import re
import stat
import subprocess
import sys

from scripts.integrated_eval.contracts import assert_clean_worktree


MATRIX_TOOL_PATHS = (
    "scripts/integrated_eval/math_query_dispatch.py",
    "scripts/integrated_eval/math_query_matrix.py",
    "scripts/integrated_eval/math_query_worker.py",
    "scripts/integrated_eval/math_query_quality.py",
    "scripts/integrated_eval/math_query_evidence.py",
    "scripts/integrated_eval/compose_gate_metadata.py",
    "scripts/integrated_eval/results.py",
    "scripts/integrated_eval/contracts.py",
    "scripts/eval/run_eval.py",
    "scripts/eval/provider_smoke.py",
)


def _redirected(path: Path) -> bool:
    # Python 3.11 has no Path.is_junction; lstat exposes Windows reparse points.
    try:
        info = path.lstat()
    except FileNotFoundError:
        return False
    return bool(stat.S_ISLNK(info.st_mode) or (
        getattr(info, "st_file_attributes", 0) & stat.FILE_ATTRIBUTE_REPARSE_POINT))


def validate_matrix_process_identity(source_root: Path, *, expected_python: Path) -> None:
    """Check loaded entry points, not source cleanliness or dependency integrity."""
    from scripts.integrated_eval import math_query_worker

    try:
        root = Path(source_root).resolve(strict=True)
        if Path(sys.executable).resolve(strict=True) != Path(expected_python).resolve(strict=True):
            raise ValueError("interpreter mismatch")
        entries = (
            (__file__, "scripts/integrated_eval/math_query_dispatch.py"),
            (math_query_worker.__file__, "scripts/integrated_eval/math_query_worker.py"),
            (math_query_worker.run_eval.__file__, "scripts/eval/run_eval.py"),
        )
        for loaded, relative in entries:
            path = Path(loaded)
            if (any(_redirected(part) for part in (path, *path.parents))
                    or path.resolve(strict=True) != root / relative):
                raise ValueError("loaded module mismatch")
    except (OSError, TypeError, ValueError):
        raise ValueError("matrix_process_identity_invalid") from None


def validate_matrix_source(source_root: Path, *, source_commit: str, tool_hashes: dict) -> None:
    """Check frozen source inputs; this does not prove approval or process identity."""
    if not isinstance(source_commit, str) or not re.fullmatch(r"[0-9a-f]{40}", source_commit):
        raise ValueError("matrix_source_commit_invalid")
    if (not isinstance(tool_hashes, dict) or set(tool_hashes) != set(MATRIX_TOOL_PATHS)
            or any(not isinstance(value, str) or not re.fullmatch(r"[0-9a-f]{64}", value)
                   for value in tool_hashes.values())):
        raise ValueError("matrix_tool_inventory_invalid")
    original = Path(source_root)
    if any(_redirected(path) for path in (original, *original.parents)):
        raise ValueError("matrix_source_path_invalid")
    root = original.resolve(strict=True)
    git_root = subprocess.check_output(
        ["git", "rev-parse", "--show-toplevel"], cwd=root, text=True).strip()
    if Path(git_root).resolve() != root:
        raise ValueError("matrix_source_root_mismatch")
    observed_commit = subprocess.check_output(
        ["git", "rev-parse", "HEAD"], cwd=root, text=True).strip()
    if observed_commit != source_commit:
        raise ValueError("matrix_source_commit_mismatch")
    assert_clean_worktree(root)
    for relative in MATRIX_TOOL_PATHS:
        path = root / relative
        if any(_redirected(part) for part in (path, *path.parents)):
            raise ValueError("matrix_tool_path_invalid")
        if hashlib.sha256(path.read_bytes()).hexdigest() != tool_hashes[relative]:
            raise ValueError("matrix_tool_hash_mismatch")


def _matrix_root_path(source_root: Path, relative_root: str) -> Path:
    """Read-only freshness check; dispatcher must claim exclusively after approval.

    This check cannot close filesystem races or authorize creation by itself.
    """
    if not isinstance(relative_root, str) or not relative_root.startswith(".local/"):
        raise ValueError("matrix_run_root_invalid")
    parts = relative_root.split("/")
    if any(not re.fullmatch(r"[A-Za-z0-9_-][A-Za-z0-9_.-]{0,127}", part)
           or part.endswith(".") or PureWindowsPath(part).is_reserved() for part in parts[1:]):
        raise ValueError("matrix_run_root_invalid")
    original = Path(source_root)
    target = original.joinpath(*parts)
    if any(_redirected(path) for path in (target, *target.parents)):
        raise ValueError("matrix_run_root_redirected")
    root = original.resolve(strict=True)
    resolved = target.resolve()
    if not resolved.is_relative_to(root / ".local") or resolved == root / ".local":
        raise ValueError("matrix_run_root_invalid")
    return resolved


def validate_fresh_matrix_root(source_root: Path, relative_root: str) -> Path:
    """Validate containment and reject every previously claimed root."""
    resolved = _matrix_root_path(source_root, relative_root)
    if resolved.exists():
        raise ValueError("matrix_run_root_not_fresh")
    return resolved


def claim_matrix_run_root(source_root: Path, relative_root: str) -> Path:
    """Create the validated matrix run root with an exclusive final mkdir."""
    run_root = validate_fresh_matrix_root(source_root, relative_root)
    local_root = Path(source_root).resolve(strict=True) / ".local"
    try:
        local_root.mkdir(mode=0o700, exist_ok=True)
        if any(_redirected(path) for path in (run_root.parent, *run_root.parent.parents)):
            raise ValueError("matrix_run_root_redirected")
        run_root.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        if any(_redirected(path) for path in (run_root.parent, *run_root.parent.parents)):
            raise ValueError("matrix_run_root_redirected")
        run_root.mkdir(mode=0o700, exist_ok=False)
        if (run_root.resolve(strict=True) != run_root
                or any(_redirected(path) for path in (run_root, *run_root.parents))):
            raise ValueError("matrix_run_root_redirected")
    except FileExistsError as exc:
        raise ValueError("matrix_run_root_not_fresh") from exc
    except OSError as exc:
        raise ValueError("matrix_run_root_not_fresh") from exc
    return run_root


def _unique_object(pairs):
    result = dict(pairs)
    if len(result) != len(pairs):
        raise ValueError("duplicate JSON key")
    return result


def validate_matrix_launch_inputs(
    draft_bytes: bytes, approval_bytes: bytes, *, source_root: Path,
    expected_approval_sha256: str, expected_owner: str, now: datetime,
    rollback_artifacts: dict[str, bytes], smoke_artifacts: dict[str, dict[str, bytes]],
) -> dict:
    """Validate approved inputs and proofs now; recheck smoke at each actual arm start."""
    if not isinstance(draft_bytes, bytes) or not isinstance(approval_bytes, bytes):
        raise ValueError("matrix_launch_bytes_required")
    approval = validate_matrix_approval_binding(
        draft_bytes, approval_bytes, expected_approval_sha256=expected_approval_sha256,
        expected_owner=expected_owner, now=now)
    declaration = json.loads(draft_bytes, object_pairs_hook=_unique_object, parse_constant=_reject_nonfinite)
    validated = validate_matrix_declaration(declaration, source_root=source_root)
    rows = set(declaration["rollback_sha256s"])
    if (not isinstance(rollback_artifacts, dict) or set(rollback_artifacts) != rows
            or not isinstance(smoke_artifacts, dict) or set(smoke_artifacts) != rows
            or any(not isinstance(arms, dict) or set(arms) != {"baseline", "candidate"}
                   for arms in smoke_artifacts.values())):
        raise ValueError("matrix_proof_inventory_invalid")
    for row in declaration["rollback_sha256s"]:
        validate_matrix_rollback(
            rollback_artifacts[row], expected_sha256=declaration["rollback_sha256s"][row],
            source_commit=declaration["source_commit"], row=row)
        for arm in ("baseline", "candidate"):
            validate_matrix_arm_smoke(
                smoke_artifacts[row][arm], expected_sha256=declaration["smoke_sha256s"][row][arm],
                expected_provider_sha256=declaration["conditions"][row]["provider_configuration_sha256"],
                arm_started_at=now)
    return {**approval, **validated, "proofs_validated": True, "dispatch_authorized": False}


def prepare_matrix_run(
    draft_bytes: bytes, approval_bytes: bytes, *, source_root: Path, expected_python: Path,
    expected_approval_sha256: str, expected_owner: str, now: datetime,
    rollback_artifacts: dict[str, bytes], smoke_artifacts: dict[str, dict[str, bytes]],
) -> dict:
    """Validate then consume a fresh root; does not launch a worker or grant traffic."""
    validated = validate_matrix_launch_inputs(
        draft_bytes, approval_bytes, source_root=source_root,
        expected_approval_sha256=expected_approval_sha256, expected_owner=expected_owner,
        now=now, rollback_artifacts=rollback_artifacts, smoke_artifacts=smoke_artifacts)
    validate_matrix_process_identity(source_root, expected_python=expected_python)
    declaration = json.loads(draft_bytes, object_pairs_hook=_unique_object, parse_constant=_reject_nonfinite)
    run_root = claim_matrix_run_root(source_root, declaration["run_root"])
    receipt = {"schema": "math-query-root-consumed-v1",
               "source_commit": declaration["source_commit"],
               "draft_sha256": validated["draft_sha256"],
               "approval_sha256": validated["approval_sha256"],
               "consumed": True, "dispatch_authorized": False}
    # A failed write leaves the claimed directory consumed; never remove or retry it.
    with (run_root / "consumed.json").open("xb") as stream:
        stream.write(json.dumps(receipt, sort_keys=True, allow_nan=False).encode("utf-8"))
        stream.flush()
        os.fsync(stream.fileno())
    return {**validated, "run_root": str(run_root), "root_claimed": True,
            "process_identity_validated": True, "dispatch_authorized": False}


def execute_matrix_processes(
    draft_bytes: bytes, approval_bytes: bytes, *, source_root: Path, expected_python: Path,
    expected_approval_sha256: str, expected_owner: str, rollback_artifacts: dict,
    smoke_artifacts: dict, base_environment: dict, timeout_seconds, clock,
) -> dict:
    """Bind explicit process settings before consuming the declared matrix root."""
    import math
    from scripts.integrated_eval.math_query_matrix import build_arm_plan
    from scripts.integrated_eval.math_query_worker import (
        build_matrix_process_environment, run_planned_quality_arm,
    )

    if (not callable(clock) or type(timeout_seconds) not in (int, float)
            or not math.isfinite(timeout_seconds) or timeout_seconds <= 0):
        raise ValueError("matrix_process_options_invalid")
    options = dict(source_root=source_root, expected_approval_sha256=expected_approval_sha256,
                   expected_owner=expected_owner, rollback_artifacts=rollback_artifacts,
                   smoke_artifacts=smoke_artifacts)
    validate_matrix_launch_inputs(draft_bytes, approval_bytes, **options, now=clock())
    declaration = json.loads(draft_bytes)
    base = dict(base_environment)
    plan = build_arm_plan(source_root, _matrix_root_path(source_root, declaration["run_root"]))
    for arm in plan["arms"]:
        build_matrix_process_environment(base, arm["environment"], expected_provider_sha256=
            declaration["conditions"][arm["row"]]["provider_configuration_sha256"])

    def run_arm(arm, *, expected_cases, expected_preflight):
        return run_planned_quality_arm(arm, source_root=source_root, python=expected_python,
            base_environment=base, expected_provider_sha256=
                declaration["conditions"][arm["row"]]["provider_configuration_sha256"],
            timeout_seconds=timeout_seconds, expected_cases=expected_cases,
            expected_preflight=expected_preflight)

    return execute_matrix_arms(draft_bytes, approval_bytes, **options,
                              expected_python=expected_python, run_arm=run_arm, clock=clock)


def _write_matrix_arm_receipt(source_root, declaration, terminal, arm, result, index):
    """Persist one completed arm exclusively before advancing the coordinator."""
    result_bytes = json.dumps(result, sort_keys=True, allow_nan=False).encode("utf-8")
    stem = f"{index:02d}-{arm['row']}-{arm['label']}"
    receipt = {"schema": "math-query-arm-receipt-v1", "row": arm["row"],
               "arm": arm["label"], "source_commit": declaration["source_commit"],
               "draft_sha256": terminal["draft_sha256"],
               "worker_result_sha256": hashlib.sha256(result_bytes).hexdigest(),
               "worker_result_file": stem + ".result.json",
               "reported_cases_bound": True, "observation_coverage_complete": True,
               "matrix_accepted": False}
    receipt_root = _matrix_root_path(source_root, declaration["run_root"]) / "arm-receipts"
    if _redirected(receipt_root):
        raise ValueError("redirected receipt directory")
    receipt_root.mkdir(exist_ok=True)
    with (receipt_root / receipt["worker_result_file"]).open("xb") as stream:
        stream.write(result_bytes)
        stream.flush()
        os.fsync(stream.fileno())
    receipt_path = receipt_root / (stem + ".receipt.json")
    with receipt_path.open("xb") as stream:
        stream.write(json.dumps(receipt, sort_keys=True).encode("utf-8"))
        stream.flush()
        os.fsync(stream.fileno())
    return json.loads(result_bytes)


def _write_matrix_terminal(source_root, declaration, terminal):
    root = _matrix_root_path(source_root, declaration["run_root"])
    with (root / "terminal.json").open("xb") as stream:
        stream.write(json.dumps(terminal, sort_keys=True, allow_nan=False).encode("utf-8"))
        stream.flush()
        os.fsync(stream.fileno())


def execute_matrix_arms(
    draft_bytes: bytes, approval_bytes: bytes, *, source_root: Path, expected_python: Path,
    expected_approval_sha256: str, expected_owner: str,
    rollback_artifacts: dict[str, bytes], smoke_artifacts: dict[str, dict[str, bytes]],
    run_arm, clock,
) -> dict:
    """Coordinate coverage, not acceptance, using an explicit trusted isolated transport."""
    from scripts.integrated_eval.math_query_matrix import build_arm_plan
    from scripts.eval.run_eval import load_manifest_files

    if not callable(run_arm) or not callable(clock):
        raise ValueError("matrix_transport_required")
    options = dict(source_root=source_root, expected_python=expected_python,
        expected_approval_sha256=expected_approval_sha256, expected_owner=expected_owner,
        rollback_artifacts=rollback_artifacts, smoke_artifacts=smoke_artifacts)
    # Validate immutable approved bytes before using their run path for planning.
    validate_matrix_launch_inputs(draft_bytes, approval_bytes,
        **{key: value for key, value in options.items() if key != "expected_python"}, now=clock())
    declaration = json.loads(draft_bytes)
    plan = build_arm_plan(source_root, _matrix_root_path(source_root, declaration["run_root"]))
    prepare_matrix_run(draft_bytes, approval_bytes, **options, now=clock())
    results = []
    terminal = {"schema": "math-query-terminal-v1", "status": "failed",
                "source_commit": declaration["source_commit"],
                "draft_sha256": hashlib.sha256(draft_bytes).hexdigest(),
                "completed_arm_count": 0, "matrix_accepted": False, "dispatch_authorized": False}
    try:
        for arm in plan["arms"]:
            terminal = {**terminal, "row": arm["row"], "arm": arm["label"]}
            validate_matrix_arm_start(draft_bytes, approval_bytes, **options,
                                      now=clock(), row=arm["row"], arm=arm["label"])
            command = arm["command"]
            cases = load_manifest_files([Path(command[command.index("--manifest") + 1])])
            result = run_arm(json.loads(json.dumps(arm)), expected_cases=cases,
                expected_preflight=json.loads(json.dumps(declaration["preflights"][arm["row"]])))
            validate_matrix_worker_result(result)
            results.append(_write_matrix_arm_receipt(
                source_root, declaration, terminal, arm, result, len(results)))
            terminal = {**terminal, "completed_arm_count": len(results)}
        terminal = {**terminal, "status": "completed"}
    except Exception:
        raise RuntimeError("matrix_worker_failed") from None
    finally:
        _write_matrix_terminal(source_root, declaration, terminal)
    return {"completed_arm_count": len(results), "arms": results,
            "matrix_accepted": False, "dispatch_authorized": False}


def validate_matrix_worker_result(result: dict) -> None:
    """Reject worker output that claims authority beyond per-arm quality evidence."""
    if (not isinstance(result, dict)
            or result.get("schema") != "math-query-quality-worker-result-v1"
            or type(result.get("exit_code")) is not int
            or result["exit_code"] not in (0, 2)
            or not isinstance(result.get("quality"), dict)
            or result.get("quality", {}).get("reported_cases_bound") is not True
            or result.get("quality", {}).get("observation_coverage_complete") is not True
            or result.get("matrix_accepted") is not False
            or result.get("dispatch_authorized") is not False
            or any(payload.get(flag, False) is not False
                   for payload in (result, result["quality"])
                   for flag in ("matrix_accepted", "dispatch_authorized", "default_rollout_authorized"))):
        raise ValueError("matrix_worker_result_invalid")


def validate_matrix_arm_start(
    draft_bytes: bytes, approval_bytes: bytes, *, source_root: Path, expected_python: Path,
    expected_approval_sha256: str, expected_owner: str, now: datetime, row: str, arm: str,
    rollback_artifacts: dict[str, bytes], smoke_artifacts: dict[str, dict[str, bytes]],
) -> dict:
    """Recheck a prepared arm's inputs; not a dispatch or sequencing authorization."""
    approval = validate_matrix_approval_binding(
        draft_bytes, approval_bytes, expected_approval_sha256=expected_approval_sha256,
        expected_owner=expected_owner, now=now)
    declaration = json.loads(draft_bytes, object_pairs_hook=_unique_object, parse_constant=_reject_nonfinite)
    validate_matrix_source(source_root, source_commit=declaration["source_commit"],
                           tool_hashes=declaration["tool_hashes"])
    validate_matrix_process_identity(source_root, expected_python=expected_python)
    try:
        if row not in {"math_only", "query_only", "math_query"} or arm not in {"baseline", "candidate"}:
            raise ValueError("invalid arm")
        root = _matrix_root_path(source_root, declaration["run_root"])
        receipt_path = root / "consumed.json"
        if _redirected(receipt_path):
            raise ValueError("redirected receipt")
        receipt = json.loads(receipt_path.read_bytes(), object_pairs_hook=_unique_object,
                             parse_constant=_reject_nonfinite)
        expected = {"schema": "math-query-root-consumed-v1",
                    "source_commit": declaration["source_commit"],
                    "draft_sha256": approval["draft_sha256"],
                    "approval_sha256": approval["approval_sha256"],
                    "consumed": True, "dispatch_authorized": False}
        if json.dumps(receipt, sort_keys=True) != json.dumps(expected, sort_keys=True):
            raise ValueError("receipt mismatch")
    except (ValueError, TypeError, KeyError, OSError):
        raise ValueError("matrix_arm_receipt_invalid") from None
    validate_matrix_rollback(rollback_artifacts[row],
        expected_sha256=declaration["rollback_sha256s"][row],
        source_commit=declaration["source_commit"], row=row)
    validate_matrix_arm_smoke(smoke_artifacts[row][arm],
        expected_sha256=declaration["smoke_sha256s"][row][arm],
        expected_provider_sha256=declaration["conditions"][row]["provider_configuration_sha256"],
        arm_started_at=now)
    return {"arm_inputs_validated": True, "dispatch_authorized": False}


def validate_matrix_declaration(declaration: dict, *, source_root: Path) -> dict:
    """Check frozen declaration consistency, not approval, live readiness or dispatch."""
    from scripts.integrated_eval.math_query_evidence import validate_matrix_conditions
    from scripts.integrated_eval.math_query_matrix import ROWS, build_draft

    fields = {"schema", "owner", "source_commit", "run_root", "tool_hashes",
              "traffic", "conditions", "preflights", "versions", "smoke_sha256s",
              "rollback_sha256s"}
    try:
        if (not isinstance(declaration, dict) or set(declaration) != fields
                or declaration["schema"] != "math-query-window-declaration-v1"
                or not isinstance(declaration["owner"], str) or not declaration["owner"].strip()):
            raise ValueError("invalid declaration shape")
        validate_matrix_traffic_contract(declaration["traffic"])
        validate_matrix_source(source_root, source_commit=declaration["source_commit"],
                               tool_hashes=declaration["tool_hashes"])
        run_root = validate_fresh_matrix_root(source_root, declaration["run_root"])
        build_draft(source_root)  # Verify actual manifest bytes, not only declared digests.
        conditions = declaration["conditions"]
        validate_matrix_conditions(conditions, declaration["versions"], source_root)
        preflights = declaration["preflights"]
        rollbacks = declaration["rollback_sha256s"]
        if (not isinstance(rollbacks, dict) or set(rollbacks) != {row[0] for row in ROWS}
                or any(not isinstance(digest, str) or re.fullmatch(r"[0-9a-f]{64}", digest) is None
                       for digest in rollbacks.values())):
            raise ValueError("invalid rollback inventory")
        smokes = declaration["smoke_sha256s"]
        if not isinstance(smokes, dict) or set(smokes) != {row[0] for row in ROWS} or any(
            not isinstance(arms, dict) or set(arms) != {"baseline", "candidate"}
            or any(not isinstance(digest, str) or re.fullmatch(r"[0-9a-f]{64}", digest) is None
                   for digest in arms.values()) for arms in smokes.values()
        ):
            raise ValueError("invalid smoke inventory")
        if not isinstance(preflights, dict) or set(preflights) != {row[0] for row in ROWS}:
            raise ValueError("invalid preflight inventory")
        for name, relative, count, _, _ in ROWS:
            preflight = preflights[name]
            expected_schema = ("grounded-math-fixture-preflight-v1" if name == "math_only"
                               else "decomposition-fixture-preflight-v1")
            expected_batch = "grounded-math-eval-v1" if name == "math_only" else "crag-eval-v1"
            case_ids = {json.loads(line)["id"] for line in (source_root / relative).read_bytes().splitlines()
                        if line.strip()}
            resolutions = preflight.get("case_resolutions")
            if not all((
                conditions[name]["git_sha"] == declaration["source_commit"],
                preflight.get("schema") == expected_schema, preflight.get("batch") == expected_batch,
                preflight.get("passed") is True, preflight.get("failures") == [],
                type(preflight.get("checked_cases")) is int, preflight.get("checked_cases") == count,
                preflight.get("collection") == conditions[name]["collection"],
                preflight.get("fixture_fingerprint") == conditions[name]["snapshot_fingerprint"],
                isinstance(resolutions, dict),
            )):
                raise ValueError("invalid frozen preflight")
            if not set(resolutions) <= case_ids or any(
                not isinstance(value, dict) or not set(value) <= {
                    "expected_calculation", "expected_citations", "expected_branches", "expected_claims"}
                for value in resolutions.values()
            ):
                raise ValueError("invalid frozen resolution")
    except (ValueError, TypeError, KeyError, AttributeError, OSError, RuntimeError,
            subprocess.CalledProcessError):
        raise ValueError("matrix_declaration_invalid") from None
    return {"declaration_validated": True, "run_root": str(run_root), "dispatch_authorized": False}


def validate_matrix_traffic_contract(traffic: dict) -> None:
    """Validate declared traffic only; no artifact reads, claim or dispatch."""
    from mech_chatbot.governance.feature_activation import FEATURE_FLAGS
    from scripts.integrated_eval.math_query_matrix import ROWS

    expected = {
        "rows": [{"row": name, "case_count": count,
                  "manifest": {"path": relative, "sha256": digest},
                  "baseline_flags": {flag: False for flag in FEATURE_FLAGS},
                  "candidate_flags": {flag: flag in enabled for flag in FEATURE_FLAGS}}
                 for name, relative, count, digest, enabled in ROWS],
        "concurrency": 1, "provider_retries": 0, "replacement_requests": 0,
        "catch_up_requests": 0, "arm_order": ["baseline", "candidate"],
    }
    try:
        if json.dumps(traffic, sort_keys=True, allow_nan=False) == json.dumps(expected, sort_keys=True):
            return
    except (TypeError, ValueError):
        pass
    raise ValueError("matrix_traffic_contract_invalid")


def _reject_nonfinite(_value):
    raise ValueError("nonfinite JSON number")


def validate_matrix_rollback(
    rollback_bytes: bytes, *, expected_sha256: str, source_commit: str, row: str,
) -> None:
    """Verify the exact commit-bound rollback profile for the declared row."""
    from scripts.eval.verify_failure_family_rollback import validate_rollback_evidence
    from scripts.integrated_eval.math_query_matrix import ROWS

    try:
        expected_flags = {name: flags for name, _, _, _, flags in ROWS}[row]
        if (not isinstance(rollback_bytes, bytes)
                or hashlib.sha256(rollback_bytes).hexdigest() != expected_sha256
                or not isinstance(source_commit, str)
                or re.fullmatch(r"[0-9a-f]{40}", source_commit) is None):
            raise ValueError("rollback binding invalid")
        artifact = json.loads(rollback_bytes, object_pairs_hook=_unique_object, parse_constant=_reject_nonfinite)
        if (not isinstance(artifact, dict) or type(artifact.get("exit_code")) is not int
                or not isinstance(artifact.get("verified_flag_state"), dict)
                or any(value is not False for value in artifact["verified_flag_state"].values())):
            raise ValueError("rollback types invalid")
        if validate_rollback_evidence(artifact, git_sha=source_commit) != expected_flags:
            raise ValueError("rollback scope invalid")
    except (ValueError, TypeError, KeyError, OverflowError):
        raise ValueError("matrix_rollback_invalid") from None


def validate_matrix_arm_smoke(
    smoke_bytes: bytes, *, expected_sha256: str, expected_provider_sha256: str,
    arm_started_at: datetime,
) -> None:
    """Check frozen smoke bytes at this arm's start without performing a probe."""
    from mech_chatbot.governance.provider_smoke import (
        provider_smoke_artifact_valid, provider_smoke_fresh_for_arms,
    )

    try:
        if (not isinstance(smoke_bytes, bytes)
                or hashlib.sha256(smoke_bytes).hexdigest() != expected_sha256
                or not isinstance(expected_provider_sha256, str)
                or re.fullmatch(r"[0-9a-f]{64}", expected_provider_sha256) is None):
            raise ValueError("smoke binding invalid")
        artifact = json.loads(smoke_bytes, object_pairs_hook=_unique_object, parse_constant=_reject_nonfinite)
        if not isinstance(artifact, dict) or any(
            type(artifact.get(key)) is not int for key in (
                "request_count", "successful_requests", "failed_requests", "provider_retries")
        ):
            raise ValueError("smoke counts invalid")
        if not provider_smoke_artifact_valid(artifact, expected_provider_sha256=expected_provider_sha256):
            raise ValueError("smoke outcome invalid")
        if not provider_smoke_fresh_for_arms(artifact, arm_started_at=(arm_started_at,)):
            raise ValueError("smoke freshness invalid")
    except (ValueError, TypeError, KeyError, OverflowError):
        raise ValueError("matrix_smoke_invalid") from None


def validate_matrix_approval_binding(
    draft_bytes: bytes, approval_bytes: bytes, *, expected_approval_sha256: str,
    expected_owner: str, now: datetime,
) -> dict:
    """Bind an independently authenticated approval hash, not authorize dispatch.

    The launch caller must obtain the hash/owner through the owner approval
    channel, never calculate that trust anchor from an untrusted input file.
    Declaration content, source, runtime and single-use checks remain separate.
    """
    forbidden = ("default_rollout_authorized", "retry_authorized",
                 "replacement_authorized", "catch_up_authorized")
    fields = {"schema", "actor", "draft_sha256", "authorized_at", "expires_at", "scope", *forbidden}
    try:
        if (not isinstance(expected_owner, str) or not expected_owner.strip()
                or not isinstance(expected_approval_sha256, str)
                or re.fullmatch(r"[0-9a-f]{64}", expected_approval_sha256) is None
                or hashlib.sha256(approval_bytes).hexdigest() != expected_approval_sha256):
            raise ValueError("approval trust anchor mismatch")
        approval = json.loads(approval_bytes, object_pairs_hook=_unique_object, parse_constant=_reject_nonfinite)
        draft = json.loads(draft_bytes, object_pairs_hook=_unique_object, parse_constant=_reject_nonfinite)
        if not isinstance(approval, dict) or set(approval) != fields or not isinstance(draft, dict):
            raise ValueError("approval shape invalid")
        validate_matrix_traffic_contract(draft.get("traffic"))
        start = datetime.fromisoformat(approval["authorized_at"].replace("Z", "+00:00"))
        end = datetime.fromisoformat(approval["expires_at"].replace("Z", "+00:00"))
        if not all((
            approval["schema"] == "math-query-window-approval-v1",
            draft.get("schema") == "math-query-window-declaration-v1",
            approval["actor"] == draft.get("owner") == expected_owner,
            approval["scope"] == "math-query-evaluation-only",
            approval["draft_sha256"] == hashlib.sha256(draft_bytes).hexdigest(),
            all(approval[key] is False for key in forbidden),
            start.utcoffset() is not None, end.utcoffset() is not None, now.utcoffset() is not None,
        )):
            raise ValueError("approval binding invalid")
        if not (start <= now < end and timedelta(0) < end - start <= timedelta(minutes=60)):
            raise ValueError("approval window invalid")
    except (ValueError, TypeError, KeyError, AttributeError, OverflowError):
        raise ValueError("matrix_approval_invalid") from None
    return {"approval_bound": True, "approval_sha256": expected_approval_sha256,
            "draft_sha256": approval["draft_sha256"], "declaration_validated": False,
            "dispatch_authorized": False}
