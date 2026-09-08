"""Per-arm observation worker; parent dispatcher owns authorization and isolation.

No CLI is exposed. The caller must verify source, permissions, environment and
fresh run root before calling, and stop the whole window on worker failure.
Expected inputs must come from the independent frozen contract, not live reads.
"""
from __future__ import annotations

import json
import hashlib
from pathlib import Path
import math
import subprocess

from scripts.eval import run_eval
from scripts.integrated_eval.math_query_quality import (
    QualityObservationLedger,
    validate_frozen_preflight,
    validate_stored_quality_binding,
)


def build_matrix_process_environment(base, overlay, *, expected_provider_sha256):
    """Bind parsed provider settings from explicit mappings, never ambient secrets."""
    from mech_chatbot.config.settings import Settings
    from mech_chatbot.governance.provider_smoke import provider_configuration_sha256_for_settings

    try:
        if not isinstance(base, dict) or not isinstance(overlay, dict):
            raise ValueError("invalid environment")
        environment = {**base, **overlay}
        if any(not isinstance(key, str) or not isinstance(value, str)
               for key, value in environment.items()):
            raise ValueError("invalid environment")
        if any(not environment.get(key, "").strip() for key in (
            "PROXYLLM_BASE_URL", "GPT_MODEL_NAME", "MAX_CONCURRENT_RAG")):
            raise ValueError("provider identity missing")
        settings = Settings.from_env(environment)
        if provider_configuration_sha256_for_settings(settings) != expected_provider_sha256:
            raise ValueError("provider identity mismatch")
        return {**environment, "RAG_EVAL_PROVIDER_CONFIGURATION_SHA256": expected_provider_sha256}
    except (TypeError, ValueError):
        raise ValueError("matrix_provider_environment_invalid") from None


def run_planned_quality_arm(arm, *, source_root, python, base_environment,
                            expected_provider_sha256, timeout_seconds,
                            expected_cases, expected_preflight):
    """Adapt a coordinator-owned arm plan to the isolated worker transport."""
    environment = build_matrix_process_environment(
        base_environment, arm["environment"], expected_provider_sha256=expected_provider_sha256)
    command = arm["command"]
    # The command is data from build_arm_plan, never executed as supplied text.
    manifest = Path(command[command.index("--manifest") + 1])
    output = Path(command[command.index("--output-dir") + 1])
    if command[command.index("--run-label") + 1] != arm["label"]:
        raise ValueError("matrix_arm_label_mismatch")
    return run_quality_arm_process(source_root=source_root, python=python,
        environment=environment, manifest_paths=[manifest], output_dir=output,
        label=arm["label"], expected_cases=expected_cases, expected_preflight=expected_preflight,
        timeout_seconds=timeout_seconds)


def run_quality_arm_process(*, source_root, python, environment, manifest_paths,
                            output_dir, label, expected_cases, expected_preflight,
                            timeout_seconds):
    """Explicit child transport; caller owns authorization and frozen environment."""
    root = Path(source_root).resolve(strict=True)
    # load_settings discovers .env upward from its config module. Reject that
    # implicit input rather than changing application-wide dotenv behavior.
    config_root = root / "src/mech_chatbot/config"
    if any((path / ".env").exists() or (path / ".env").is_symlink()
           for path in (config_root, *config_root.parents)):
        raise ValueError("matrix_worker_dotenv_forbidden")
    if (not isinstance(environment, dict) or any(
            not isinstance(key, str) or not isinstance(value, str)
            for key, value in environment.items())
            or type(timeout_seconds) not in (int, float)
            or not math.isfinite(timeout_seconds) or timeout_seconds <= 0):
        raise ValueError("matrix_worker_process_inputs_invalid")
    packet = json.dumps({"manifest_paths": [str(Path(path).resolve()) for path in manifest_paths],
        "output_dir": str(Path(output_dir).resolve()), "label": label,
        "expected_cases": expected_cases, "expected_preflight": expected_preflight}, allow_nan=False)
    code = '''
import sys, json, contextlib, os
from pathlib import Path
root = Path.cwd()
sys.path[:0] = [str(root), str(root / "src")]
from scripts.integrated_eval.math_query_worker import evaluate_quality_arm
packet = json.loads(sys.stdin.read())
with open(os.devnull, "w") as sink, contextlib.redirect_stdout(sink), contextlib.redirect_stderr(sink):
    result = evaluate_quality_arm(**packet)
sys.stdout.write(json.dumps(result, allow_nan=False))
'''
    try:
        completed = subprocess.run([str(python), "-I", "-B", "-c", code],
            cwd=Path(source_root).resolve(strict=True), env=dict(environment), input=packet,
            capture_output=True, text=True, encoding="utf-8", timeout=timeout_seconds,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        if completed.returncode != 0:
            raise ValueError("child failed")
        result = json.loads(completed.stdout)
        if not isinstance(result, dict) or result.get("schema") != "math-query-quality-worker-result-v1":
            raise ValueError("child output invalid")
        return result
    except (OSError, ValueError, subprocess.SubprocessError):
        raise RuntimeError("matrix_worker_process_failed") from None


def evaluate_quality_arm(manifest_paths, output_dir, label, *, expected_cases, expected_preflight):
    """Run one already-declared arm; return metadata, never matrix acceptance."""
    # JSON snapshots detach nested inputs and reject non-JSON/NaN contracts.
    cases = json.loads(json.dumps(expected_cases, allow_nan=False))
    preflight = json.loads(json.dumps(expected_preflight, allow_nan=False))
    if not validate_frozen_preflight(cases, preflight, expected_cases=cases, expected_preflight=preflight):
        raise ValueError("invalid frozen quality preflight")
    resolutions = preflight.get("case_resolutions", {})
    case_ids = {case["id"] for case in cases}
    permitted = {"expected_calculation", "expected_citations", "expected_branches", "expected_claims"}
    if not isinstance(resolutions, dict) or not set(resolutions) <= case_ids or any(
        not isinstance(value, dict) or not set(value) <= permitted for value in resolutions.values()
    ):
        raise ValueError("invalid frozen quality resolutions")
    resolved_cases = [{**case, **resolutions.get(case["id"], {})} for case in cases]
    ledger = QualityObservationLedger.create(resolved_cases, label=label)

    def validate(actual_cases, actual_preflight):
        return validate_frozen_preflight(
            actual_cases, actual_preflight, expected_cases=cases, expected_preflight=preflight)

    def observe(case, reported, observation):
        nonlocal ledger
        ledger = ledger.record(case, reported, observation)

    arguments = [value for path in manifest_paths for value in ("--manifest", str(path))]
    exit_code = run_eval.main([
        *arguments, "--output-dir", str(output_dir), "--run-label", label,
        "--maximum-provider-retries", "0", "--stop-on-provider-failure",
    ], quality_observer=observe, preflight_validator=validate)
    report_bytes = (Path(output_dir) / label / "eval.json").read_bytes()
    report = json.loads(report_bytes)
    quality = ledger.finalize(report["cases"])
    validate_stored_quality_binding(quality, report["cases"], resolved_cases, label=label)
    return {"schema": "math-query-quality-worker-result-v1", "exit_code": exit_code,
            "eval_sha256": hashlib.sha256(report_bytes).hexdigest(),
            "quality": quality, "matrix_accepted": False, "dispatch_authorized": False}
