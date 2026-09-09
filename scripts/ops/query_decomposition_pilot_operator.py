"""Run the authorized Query Decomposition pilot without retry or catch-up."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import subprocess
import time

from mech_chatbot.governance.feature_activation import FEATURE_FLAGS
from mech_chatbot.governance.query_activation_contract import (
    runtime_consumption_authorization_status,
)
from scripts.ops.render_activation_profile import build_profile_environment
from scripts.ops.query_decomposition_pilot import (
    SEQUENTIAL_PILOT_CONTRACT_VERSION,
    _EVIDENCE_FIELDS,
    _authorization_and_schedule,
    _format,
    _source_commit,
    _timestamp,
    _wal_rows,
    pilot_evidence_valid,
    record_pilot_completion,
)
from scripts.ops.query_pilot_operator_support import (
    OperatorStopped,
    consolidated_binding_valid as _consolidated_binding_valid,
    ensure_port_free as _ensure_port_free,
    fetch_runtime_health,
    inside as _inside, loopback_url as _loopback_url,
    operator_outputs_fresh as _operator_outputs_fresh,
    pilot_run_paths as _pilot_run_paths,
    pilot_run_root as _pilot_run_root,
    send_query_sse,
    terminal_reason as _terminal_reason,
)
from scripts.ops.query_pilot_review_capture import (
    capture_answer,
    prepare_capture_directory,
    review_capture_cards,
    validate_capture_chain,
)
from scripts.ops.query_pilot_capture_lifecycle import cleanup_terminal_captures


_HEALTH_BINDINGS = (
    "deployment_id",
    "git_sha",
    "snapshot_fingerprint",
    "provider_configuration_sha256",
    "activation_bundle_sha256",
    "runtime_identity_sha256",
    "sql_database",
    "qdrant_collection",
    "feature_flags",
    "activation_scope",
    "activation_profile",
    "execution_context",
)
_QUERY_ONLY_FLAGS = {
    name: name == "RAG_QUERY_DECOMPOSITION_ENABLED" for name in FEATURE_FLAGS
}
def _sha256(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def _digest(value: object) -> bool:
    normalized = str(value or "").strip().casefold()
    return len(normalized) == 64 and not (
        set(normalized) - set("0123456789abcdef")
    )


def _exclusive_json(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    raw = json.dumps(value, ensure_ascii=False, indent=2) + "\n"
    try:
        with path.open("x", encoding="utf-8", newline="\n") as stream:
            stream.write(raw)
            stream.flush()
            os.fsync(stream.fileno())
    except FileExistsError:
        raise OperatorStopped("artifact_already_exists") from None


def validate_runtime_health(live: Mapping[str, object], frozen: Mapping[str, object]) -> None:
    if not all((
        live.get("status") == "ok",
        live.get("rag_loaded") is True,
        live.get("activation_valid") is True,
        live.get("live_authorized") is True,
        live.get("activation_scope") == "controlled_demo",
        live.get("activation_profile") == "selective",
        live.get("execution_context") == "production",
        live.get("feature_flags") == _QUERY_ONLY_FLAGS,
        all(live.get(name) == frozen.get(name) for name in _HEALTH_BINDINGS),
    )):
        raise OperatorStopped("runtime_health_drift")


def trace_evidence(trace_path: str | Path, trace_id: str) -> dict:
    matches = []
    try:
        lines = Path(trace_path).read_text(encoding="utf-8").splitlines()
        for line in lines:
            value = json.loads(line)
            if (
                isinstance(value, dict)
                and value.get("event") == "pilot_request_evidence"
                and value.get("trace_id") == trace_id
            ):
                matches.append({name: value.get(name) for name in _EVIDENCE_FIELDS})
    except (OSError, UnicodeDecodeError, json.JSONDecodeError):
        raise OperatorStopped("trace_evidence_invalid") from None
    if len(matches) != 1 or set(matches[0]) != _EVIDENCE_FIELDS:
        raise OperatorStopped("trace_evidence_invalid")
    return matches[0]


def _manifest_questions(path: Path, expected_sha: str) -> dict[str, str]:
    raw = path.read_bytes()
    if not _digest(expected_sha) or _sha256(raw) != expected_sha:
        raise OperatorStopped("manifest_drift")
    questions: dict[str, str] = {}
    try:
        for line in raw.decode("utf-8").splitlines():
            value = json.loads(line)
            if value.get("evaluation_group") != "complex":
                continue
            case_id = str(value.get("id") or "").strip()
            question = str(value.get("question") or "").strip()
            if not case_id or not question or case_id in questions:
                raise OperatorStopped("manifest_invalid")
            questions[case_id] = question
    except (UnicodeDecodeError, json.JSONDecodeError, AttributeError):
        raise OperatorStopped("manifest_invalid") from None
    if len(questions) != 10:
        raise OperatorStopped("manifest_invalid")
    return questions


def validate_manifest_routing(questions: Mapping[str, str]) -> None:
    """Fail closed unless every pilot case remains a technical request."""
    from mech_chatbot.rag import interaction_router, route_safety

    for question in questions.values():
        if route_safety.detect(question) is not None:
            raise OperatorStopped("manifest_routing_invalid")
        classifier_called = False

        def probabilistic_classifier(_text, _context=None):
            nonlocal classifier_called
            classifier_called = True
            return interaction_router.ROUTE_TECHNICAL, 1.0

        result = interaction_router.classify(
            question,
            semantic_enabled=False,
            llm_classifier=probabilistic_classifier,
        )
        if (
            classifier_called
            or result.route != interaction_router.ROUTE_TECHNICAL
            or result.layer != interaction_router.LAYER_RULE
        ):
            raise OperatorStopped("manifest_routing_invalid")


def validate_operator_inputs(
    *,
    source_root: Path,
    schedule_path: Path,
    authorization_path: Path,
    authorization_sha256: str,
    bundle_path: Path,
    bundle_sha256: str,
    manifest_path: Path,
    manifest_sha256: str,
    now: datetime,
) -> tuple[dict, dict, dict[str, str]]:
    paths = (schedule_path, authorization_path, bundle_path, manifest_path)
    if not all(_inside(path, source_root) for path in paths):
        raise OperatorStopped("operator_input_outside_source_root")
    authorization, auth_sha, schedule, _ = _authorization_and_schedule(
        authorization_path, schedule_path
    )
    bundle_raw = bundle_path.read_bytes()
    if not all((
        _source_commit(source_root) == authorization.get("source_commit"),
        _digest(authorization_sha256),
        _digest(bundle_sha256),
        _digest(manifest_sha256),
        auth_sha == authorization_sha256,
        _sha256(bundle_raw) == bundle_sha256,
        authorization.get("activation_bundle_sha256") == bundle_sha256,
        _consolidated_binding_valid(authorization, source_root),
        _digest(
            (authorization.get("consolidated_launch_draft") or {}).get(
                "sha256"
            )
        ),
        schedule.get("manifest", {}).get("sha256") == manifest_sha256,
        runtime_consumption_authorization_status(
            {
                "RAG_RUNTIME_CONSUMPTION_AUTHORIZATION_PATH": str(
                    authorization_path
                ),
                "RAG_RUNTIME_CONSUMPTION_AUTHORIZATION_SHA256": (
                    authorization_sha256
                ),
            },
            root=source_root,
            source_commit=authorization.get("source_commit"),
            activation_bundle_sha256=bundle_sha256,
            enabled_flags={"RAG_QUERY_DECOMPOSITION_ENABLED"},
            now=now,
        ) == "authorized",
    )):
        raise OperatorStopped("operator_authorization_invalid")
    questions = _manifest_questions(manifest_path, manifest_sha256)
    validate_manifest_routing(questions)
    review_capture_cards(schedule)
    for card in schedule.get("cards") or ():
        question = questions.get(card.get("case_id"))
        if question is None or _sha256(question.encode("utf-8")) != card.get(
            "request_sha256"
        ):
            raise OperatorStopped("schedule_manifest_mismatch")
    return authorization, schedule, questions


def _claim(
    claim_dir: Path,
    card: dict,
    *,
    attempted_at: datetime,
    authorization_sha256: str,
    schedule_sha256: str,
) -> None:
    _exclusive_json(claim_dir / f"{card['card_id']}.json", {
        "schema": "query-decomposition-pilot-attempt-claim-v1",
        "card_id": card["card_id"],
        "case_id": card["case_id"],
        "request_sha256": card["request_sha256"],
        "attempt_number": 1,
        "attempted_at": _format(attempted_at),
        "authorization_sha256": authorization_sha256,
        "schedule_sha256": schedule_sha256,
        "raw_question_persisted": False,
    })


def run_pilot(
    *,
    source_root: str | Path,
    schedule_path: str | Path,
    authorization_path: str | Path,
    authorization_sha256: str,
    bundle_path: str | Path,
    bundle_sha256: str,
    manifest_path: str | Path,
    manifest_sha256: str,
    runtime_url: str,
    frozen_health: Mapping[str, object],
    trace_path: str | Path,
    wal_path: str | Path,
    claim_dir: str | Path,
    service_token: str,
    clock: Callable[[], datetime] | None = None,
    sleeper: Callable[[float], None] = time.sleep,
    health: Callable[[], dict] | None = None,
    send: Callable[[str], str | tuple[str, bytearray | None]] | None = None,
    evidence_loader: Callable[[str], dict] | None = None,
    capture_writer: Callable[..., dict] = capture_answer,
    capture_directory_preparer: Callable[..., Path] = prepare_capture_directory,
) -> dict:
    root = Path(source_root).resolve()
    schedule_file = Path(schedule_path).resolve()
    authorization_file = Path(authorization_path).resolve()
    bundle_file = Path(bundle_path).resolve()
    manifest_file = Path(manifest_path).resolve()
    wal_file = Path(wal_path).resolve()
    claims = Path(claim_dir).resolve()
    trace_file = Path(trace_path).resolve()
    if not all(_inside(path, root / ".local") for path in (
        wal_file, claims, trace_file,
    )):
        raise OperatorStopped("pilot_output_outside_dot_local")
    now_fn = clock or (lambda: datetime.now(timezone.utc))
    authorization, schedule, questions = validate_operator_inputs(
        source_root=root,
        schedule_path=schedule_file,
        authorization_path=authorization_file,
        authorization_sha256=authorization_sha256,
        bundle_path=bundle_file,
        bundle_sha256=bundle_sha256,
        manifest_path=manifest_file,
        manifest_sha256=manifest_sha256,
        now=now_fn(),
    )
    cards = schedule.get("cards") or []
    sequential = schedule.get("pilot_contract_version") == SEQUENTIAL_PILOT_CONTRACT_VERSION
    expires = _timestamp(authorization.get("expires_at"))
    if _wal_rows(wal_file) or (claims.exists() and any(claims.iterdir())):
        raise OperatorStopped("pilot_root_not_fresh")
    schedule_sha = _sha256(schedule_file.read_bytes())
    selected_cards = (
        review_capture_cards(schedule)
        if "review_capture_card_ids" in schedule
        else frozenset()
    )
    captured_metadata: list[dict] = []
    capture_dir = wal_file.parent / "review-captures"
    if selected_cards:
        try:
            capture_dir = capture_directory_preparer(
                capture_dir, source_root=root,
            )
        except (OSError, RuntimeError, ValueError):
            raise OperatorStopped("review_capture_preflight_failed") from None
    health_fn = health or (
        lambda: fetch_runtime_health(runtime_url, service_token)
    )
    evidence_fn = evidence_loader or (
        lambda trace_id: trace_evidence(trace_file, trace_id)
    )
    for index, card in enumerate(cards):
        if selected_cards:
            try:
                validate_capture_chain(
                    capture_dir, expected=tuple(captured_metadata),
                )
            except (OSError, TypeError, ValueError):
                raise OperatorStopped("review_capture_chain_invalid") from None
        scheduled = _timestamp(card.get("scheduled_at"))
        next_scheduled = (
            _timestamp(cards[index + 1].get("scheduled_at"))
            if not sequential and index + 1 < len(cards)
            else _timestamp(authorization.get("expires_at"))
        )
        while now_fn().astimezone(timezone.utc) < scheduled:
            remaining = (scheduled - now_fn().astimezone(timezone.utc)).total_seconds()
            sleeper(min(max(remaining, 0.0), 1.0))
        attempted = now_fn().astimezone(timezone.utc)
        if attempted >= next_scheduled:
            raise OperatorStopped("scheduled_card_missed")
        live = health_fn()
        validate_runtime_health(live, frozen_health)
        attempted = now_fn().astimezone(timezone.utc)
        if attempted >= next_scheduled:
            raise OperatorStopped("scheduled_card_missed")
        _claim(
            claims,
            card,
            attempted_at=attempted,
            authorization_sha256=authorization_sha256,
            schedule_sha256=schedule_sha,
        )
        capture_required = card["card_id"] in selected_cards
        stream_result = (
            send(questions[card["case_id"]])
            if send is not None
            else send_query_sse(
                runtime_url,
                service_token,
                questions[card["case_id"]],
                capture_answer=capture_required,
            )
        )
        citations = ()
        if isinstance(stream_result, tuple) and len(stream_result) == 3:
            trace_id, answer, citations = stream_result
        elif isinstance(stream_result, tuple) and len(stream_result) == 2:
            trace_id, answer = stream_result
        elif isinstance(stream_result, str):
            trace_id, answer = stream_result, None
        else:
            raise OperatorStopped("rag_stream_result_invalid")
        if not isinstance(trace_id, str) or not trace_id:
            if isinstance(answer, bytearray):
                answer[:] = b"\0" * len(answer)
            raise OperatorStopped("rag_stream_result_invalid")
        try:
            evidence = evidence_fn(trace_id)
            retries = evidence.get("provider_retries")
            if type(retries) is not int or retries != 0:
                raise OperatorStopped("provider_retry_observed")
            completed = now_fn().astimezone(timezone.utc)
            if completed > next_scheduled:
                raise OperatorStopped("request_crossed_schedule_boundary")
            if not pilot_evidence_valid(evidence):
                raise OperatorStopped("per_request_evidence_invalid")
            if capture_required:
                if not isinstance(answer, bytearray):
                    raise OperatorStopped("review_capture_answer_missing")
                try:
                    captured = capture_writer(
                        capture_dir=capture_dir,
                        source_commit=str(
                            authorization.get("source_commit") or ""
                        ),
                        pilot_draft_sha256=str(
                            (authorization.get("pilot_draft") or {}).get(
                                "sha256"
                            ) or ""
                        ),
                        consolidated_launch_draft_sha256=str(
                            (
                                authorization.get(
                                    "consolidated_launch_draft"
                                ) or {}
                            ).get("sha256") or ""
                        ),
                        pilot_authorization_sha256=authorization_sha256,
                        schedule_sha256=schedule_sha,
                        card=card,
                        trace_id=trace_id,
                        answer=answer,
                        citations=citations,
                    )
                    captured_metadata.append(dict(captured))
                except (OSError, RuntimeError, ValueError):
                    raise OperatorStopped("review_capture_failed") from None
            record_pilot_completion(
                schedule_path=schedule_file,
                authorization_path=authorization_file,
                wal_path=wal_file,
                card_id=card["card_id"],
                attempted_at=_format(attempted),
                completed_at=_format(completed),
                trace_id=trace_id,
                runtime_identity_sha256=str(
                    frozen_health.get("runtime_identity_sha256") or ""
                ),
                evidence=evidence,
            )
        except OperatorStopped:
            raise
        except Exception:
            raise OperatorStopped("pilot_request_post_stream_failed") from None
        finally:
            if isinstance(answer, bytearray):
                answer[:] = b"\0" * len(answer)
            for citation in citations:
                if isinstance(citation, dict):
                    citation.clear()
    if selected_cards:
        try:
            validate_capture_chain(capture_dir, expected=tuple(captured_metadata))
        except (OSError, TypeError, ValueError):
            raise OperatorStopped("review_capture_chain_invalid") from None
    if len(captured_metadata) != len(selected_cards):
        raise OperatorStopped("review_capture_chain_invalid")
    return {
        "schema": "query-decomposition-pilot-operator-result-v1",
        "status": "completed",
        "completed_request_count": len(cards),
        "provider_retries": 0,
        "replacement_requests": 0,
        "catch_up_requests": 0,
        "review_capture_count": len(selected_cards),
        "runtime_identity_sha256": frozen_health.get(
            "runtime_identity_sha256"
        ),
    }


def build_candidate_environment(
    parent: Mapping[str, str],
    *,
    source_root: Path,
    bundle_path: Path,
    bundle_sha256: str,
    authorization_path: Path,
    authorization_sha256: str,
    snapshot_fingerprint: str,
    deployment_id: str,
    port: int,
    qdrant_collection: str,
    sql_database: str,
    trace_path: Path,
) -> dict[str, str]:
    if not all((
        0 < port <= 65535,
        _digest(snapshot_fingerprint),
        _digest(bundle_sha256),
        _digest(authorization_sha256),
        deployment_id.strip(),
        qdrant_collection.strip(),
        sql_database.strip(),
        _inside(bundle_path, source_root),
        _inside(authorization_path, source_root),
        _inside(trace_path, source_root / ".local"),
    )):
        raise OperatorStopped("runtime_environment_input_invalid")
    rendered = build_profile_environment(
        profile="selective",
        scope="controlled_demo",
        activation_bundle=bundle_path,
        activation_bundle_sha256=bundle_sha256,
        runtime_consumption_authorization=authorization_path,
        runtime_consumption_authorization_sha256=authorization_sha256,
    )
    enabled = {
        name: str(rendered.get(name) or "").casefold() == "true"
        for name in FEATURE_FLAGS
    }
    if enabled != _QUERY_ONLY_FLAGS:
        raise OperatorStopped("runtime_feature_scope_invalid")
    return {
        **{str(name): str(value) for name, value in parent.items()},
        **{str(name): str(value) for name, value in rendered.items()},
        "PYTHONPATH": f"{source_root}{os.pathsep}{source_root / 'src'}",
        "EXTERNAL_PROCESSING_POLICY": "all_external",
        "RAG_SNAPSHOT_FINGERPRINT": snapshot_fingerprint,
        "RAG_EVAL_FORCE_AMBIGUOUS": "false",
        "RAG_REQUEST_DEADLINE_SECONDS": "120",
        "GPT_STREAM_MAX_ATTEMPTS": "1",
        "RAG_PROVIDER_RETRY_LIMIT": "0",
        "PARENT_CONTEXT_MAX_WORKERS": "4",
        "QDRANT_COLLECTION": qdrant_collection,
        "RAG_EVAL_EXPECTED_COLLECTION": qdrant_collection,
        "SQL_DATABASE": sql_database,
        "RAG_SERVER_PORT": str(port),
        "RAG_DEPLOYMENT_ID": deployment_id,
        "RAG_DEPLOYMENT_GIT_SHA": _source_commit(source_root),
        "RAG_TRACE_LOG_FILE": str(trace_path),
    }


def _fixed_health_valid(
    value: Mapping[str, object],
    *,
    source_commit: str,
    deployment_id: str,
    snapshot_fingerprint: str,
    bundle_sha256: str,
    qdrant_collection: str,
    sql_database: str,
) -> bool:
    return all((
        value.get("status") == "ok",
        value.get("rag_loaded") is True,
        value.get("activation_valid") is True,
        value.get("live_authorized") is True,
        value.get("activation_scope") == "controlled_demo",
        value.get("activation_profile") == "selective",
        value.get("execution_context") == "production",
        value.get("feature_flags") == _QUERY_ONLY_FLAGS,
        value.get("git_sha") == source_commit,
        value.get("deployment_id") == deployment_id,
        value.get("snapshot_fingerprint") == snapshot_fingerprint,
        value.get("activation_bundle_sha256") == bundle_sha256,
        value.get("qdrant_collection") == qdrant_collection,
        value.get("sql_database") == sql_database,
        _digest(value.get("runtime_identity_sha256")),
        _digest(value.get("provider_configuration_sha256")),
    ))


def supervise_pilot(
    *,
    python_exe: str | Path,
    source_root: str | Path,
    schedule_path: str | Path,
    authorization_path: str | Path,
    authorization_sha256: str,
    bundle_path: str | Path,
    bundle_sha256: str,
    manifest_path: str | Path,
    manifest_sha256: str,
    snapshot_fingerprint: str,
    deployment_id: str,
    port: int,
    qdrant_collection: str,
    sql_database: str,
    trace_path: str | Path,
    wal_path: str | Path,
    claim_dir: str | Path,
    frozen_health_path: str | Path,
    runtime_state_path: str | Path,
    runtime_stop_path: str | Path,
    runtime_out_log: str | Path,
    runtime_err_log: str | Path,
    result_path: str | Path,
    terminal_path: str | Path,
    service_token: str,
    popen: Callable[..., object] = subprocess.Popen,
    health_fetcher: Callable[[], dict] | None = None,
    defer_capture_cleanup: bool = False,
    **pilot_injections,
) -> dict:
    root = Path(source_root).resolve()
    executable = Path(python_exe).resolve()
    schedule = Path(schedule_path).resolve()
    authorization = Path(authorization_path).resolve()
    bundle = Path(bundle_path).resolve()
    manifest = Path(manifest_path).resolve()
    trace = Path(trace_path).resolve()
    wal = Path(wal_path).resolve()
    claims = Path(claim_dir).resolve()
    captures = wal.parent / "review-captures"
    frozen_path = Path(frozen_health_path).resolve()
    state_path = Path(runtime_state_path).resolve()
    stop_path = Path(runtime_stop_path).resolve()
    out_path = Path(runtime_out_log).resolve()
    err_path = Path(runtime_err_log).resolve()
    result = Path(result_path).resolve()
    terminal = Path(terminal_path).resolve()
    if not all((
        executable.is_file(),
        all(_inside(path, root / ".local") for path in (
            trace, wal, claims, captures, frozen_path, state_path, stop_path,
            out_path, err_path, result, terminal,
        )),
    )):
        raise OperatorStopped("runtime_path_invalid")
    if not _operator_outputs_fresh((
        trace, wal, claims, captures, frozen_path, state_path, stop_path,
        out_path, err_path, result, terminal,
    )):
        raise OperatorStopped("runtime_output_not_fresh")
    now_fn = pilot_injections.get("clock") or (lambda: datetime.now(timezone.utc))
    authorization_value, _, _ = validate_operator_inputs(
        source_root=root,
        schedule_path=schedule,
        authorization_path=authorization,
        authorization_sha256=authorization_sha256,
        bundle_path=bundle,
        bundle_sha256=bundle_sha256,
        manifest_path=manifest,
        manifest_sha256=manifest_sha256,
        now=now_fn(),
    )
    run_root = _pilot_run_root(authorization_value, root)
    expected_paths = _pilot_run_paths(run_root)
    supplied_paths = {
        "trace": trace, "wal": wal, "claims": claims, "captures": captures,
        "frozen_health": frozen_path, "runtime_state": state_path,
        "runtime_stop": stop_path, "runtime_out": out_path,
        "runtime_err": err_path, "result": result, "terminal": terminal,
    }
    if (
        os.path.lexists(run_root)
        or any(supplied_paths[name] != expected_paths[name] for name in supplied_paths)
    ):
        raise OperatorStopped("pilot_run_root_invalid")
    environment = build_candidate_environment(
        os.environ,
        source_root=root,
        bundle_path=bundle,
        bundle_sha256=bundle_sha256,
        authorization_path=authorization,
        authorization_sha256=authorization_sha256,
        snapshot_fingerprint=snapshot_fingerprint,
        deployment_id=deployment_id,
        port=port,
        qdrant_collection=qdrant_collection,
        sql_database=sql_database,
        trace_path=trace,
    )
    _ensure_port_free(port)
    run_root.mkdir(parents=False)
    _exclusive_json(expected_paths["consumed"], {
        "schema": "query-decomposition-pilot-consumed-v1",
        "authorization_sha256": authorization_sha256,
        "consolidated_launch_draft_sha256": (
            authorization_value["consolidated_launch_draft"]["sha256"]
        ),
        "consumed_at": _format(now_fn()),
        "retry_authorized": False,
    })
    for path in (trace, out_path, err_path):
        path.parent.mkdir(parents=True, exist_ok=True)
    out_stream = out_path.open("xb")
    err_stream = err_path.open("xb")
    process = None
    pilot_completed = False
    try:
        creationflags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
        process = popen(
            [str(executable), "-m", "mech_chatbot.api.rag_server"],
            cwd=root,
            env=environment,
            stdout=out_stream,
            stderr=err_stream,
            creationflags=creationflags,
        )
        _exclusive_json(state_path, {
            "schema": "query-decomposition-pilot-runtime-state-v1",
            "source_commit": _source_commit(root),
            "supervisor_pid": os.getpid(),
            "pid": int(process.pid),
            "port": port,
            "deployment_id": deployment_id,
            "runtime_url": f"http://127.0.0.1:{port}",
            "started_at": _format(now_fn()),
            "runtime_stopped": False,
        })
        fetch = health_fetcher or (
            lambda: fetch_runtime_health(f"http://127.0.0.1:{port}", service_token)
        )
        deadline = time.monotonic() + 90
        live = None
        while time.monotonic() < deadline:
            if process.poll() is not None:
                raise OperatorStopped("runtime_exited_before_health")
            try:
                candidate = fetch()
                if not _fixed_health_valid(
                    candidate, source_commit=_source_commit(root),
                    deployment_id=deployment_id,
                    snapshot_fingerprint=snapshot_fingerprint,
                    bundle_sha256=bundle_sha256,
                    qdrant_collection=qdrant_collection,
                    sql_database=sql_database,
                ):
                    raise OperatorStopped("runtime_health_drift")
                live = candidate
                break
            except OperatorStopped as exc:
                if str(exc) != "runtime_health_unavailable":
                    raise
            time.sleep(1)
        if live is None:
            raise OperatorStopped("runtime_health_preflight_failed")
        _exclusive_json(frozen_path, live)
        pilot_result = run_pilot(
            source_root=root,
            schedule_path=schedule,
            authorization_path=authorization,
            authorization_sha256=authorization_sha256,
            bundle_path=bundle,
            bundle_sha256=bundle_sha256,
            manifest_path=manifest,
            manifest_sha256=manifest_sha256,
            runtime_url=f"http://127.0.0.1:{port}",
            frozen_health=live,
            trace_path=trace,
            wal_path=wal_path,
            claim_dir=claim_dir,
            service_token=service_token,
            health=fetch,
            **pilot_injections,
        )
        pilot_completed = True
        return pilot_result
    finally:
        out_stream.close()
        err_stream.close()
        stopped = process is None
        if process is not None:
            if process.poll() is None:
                process.terminate()
                try:
                    process.wait(timeout=15)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait(timeout=5)
            stopped = process.poll() is not None
        try:
            _exclusive_json(stop_path, {
                "schema": "query-decomposition-pilot-runtime-stop-v1",
                "runtime_stopped": stopped,
                "stopped_at": _format(datetime.now(timezone.utc)),
                "preserve_wal_and_artifacts": True,
            })
        except OperatorStopped:
            pass
        if not pilot_completed and not defer_capture_cleanup and captures.exists():
            try:
                cleanup_terminal_captures(
                    capture_dir=captures,
                    authorization_sha256=authorization_sha256,
                )
            except (OSError, RuntimeError, ValueError):
                try:
                    _exclusive_json(run_root / "capture-cleanup-failure.json", {
                        "schema": "query-pilot-capture-cleanup-failure-v1",
                        "status": "terminal_failure",
                        "pilot_accepted": False,
                        "retry_authorized": False,
                    })
                except OperatorStopped:
                    pass


def main(argv: list[str] | None = None) -> int:
    from scripts.ops.query_decomposition_pilot_operator_cli import operator_main

    return operator_main(argv)


if __name__ == "__main__":
    raise SystemExit(main())
