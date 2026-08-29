"""Small fail-closed helpers for the Query pilot operator."""

from __future__ import annotations

import os
from pathlib import Path
import re
import socket
from urllib.parse import urlparse

from mech_chatbot.governance.artifact_references import (
    load_json_reference,
    resolve_path,
)
from scripts.ops.query_pilot_review_capture import (
    MAX_ANSWER_BYTES,
    _canonical_citations,
)


_TERMINAL_REASON = re.compile(r"[a-z][a-z0-9_]{0,63}")


class OperatorStopped(RuntimeError):
    """Terminal pilot stop; the same root must not be resumed."""


def inside(path: Path, root: Path) -> bool:
    try:
        path.resolve().relative_to(root.resolve())
    except ValueError:
        return False
    return True


def operator_outputs_fresh(paths: tuple[Path, ...]) -> bool:
    """Return false when any exact output path has already been consumed."""
    return not any(os.path.lexists(path) for path in paths)


def terminal_reason(error: Exception) -> str:
    """Return a bounded reason code without persisting exception content."""
    if isinstance(error, OperatorStopped):
        candidate = str(error)
        if _TERMINAL_REASON.fullmatch(candidate):
            return candidate
    return "operator_failure"


def consolidated_binding_valid(
    authorization: dict, source_root: Path,
) -> bool:
    """Validate the approval and its referenced consolidated draft bytes."""
    approval_reference = authorization.get("pilot_approval")
    consolidated_reference = authorization.get("consolidated_launch_draft")
    if not all((
        isinstance(approval_reference, dict),
        isinstance(consolidated_reference, dict),
    )):
        return False
    try:
        approval_path = resolve_path(
            approval_reference.get("path"), source_root,
        )
        consolidated_path = resolve_path(
            consolidated_reference.get("path"), source_root,
        )
        if not all((
            inside(approval_path, source_root),
            inside(consolidated_path, source_root),
        )):
            return False
        approval = load_json_reference(approval_reference, root=source_root)
        consolidated = load_json_reference(
            consolidated_reference, root=source_root,
        )
    except (OSError, TypeError, ValueError):
        return False
    return bool(
        isinstance(approval, dict)
        and isinstance(consolidated, dict)
        and consolidated_reference == approval.get("consolidated_launch_draft")
        and consolidated.get("source_commit")
        == authorization.get("source_commit")
        and consolidated.get("pilot_run_root")
        == approval.get("pilot_run_root")
        == authorization.get("pilot_run_root")
    )


def pilot_run_root(authorization: dict, source_root: Path) -> Path:
    """Resolve the one source-relative, approval-bound pilot run root."""
    value = authorization.get("pilot_run_root")
    if not isinstance(value, str) or value != Path(value).as_posix():
        raise OperatorStopped("pilot_run_root_invalid")
    target = (source_root / value).resolve()
    if not inside(target, source_root / ".local"):
        raise OperatorStopped("pilot_run_root_invalid")
    return target


def pilot_run_paths(run_root: Path) -> dict[str, Path]:
    """Return the fixed layout for a single-use pilot root."""
    return {
        "trace": run_root / "trace.jsonl",
        "wal": run_root / "pilot.wal.jsonl",
        "claims": run_root / "claims",
        "captures": run_root / "review-captures",
        "frozen_health": run_root / "frozen-health.json",
        "runtime_state": run_root / "runtime-state.json",
        "runtime_stop": run_root / "runtime-stop.json",
        "runtime_out": run_root / "runtime.out.log",
        "runtime_err": run_root / "runtime.err.log",
        "result": run_root / "result.json",
        "terminal": run_root / "terminal.json",
        "consumed": run_root / "consumed.json",
    }


def loopback_url(value: object) -> str:
    """Allow only an explicit local HTTP origin with no credentials or path."""
    raw = str(value or "")
    try:
        parsed = urlparse(raw)
        valid = all((
            parsed.scheme == "http",
            parsed.hostname == "127.0.0.1",
            parsed.username is None,
            parsed.password is None,
            parsed.path in {"", "/"},
            not parsed.query,
            not parsed.fragment,
            parsed.port is not None and 0 < parsed.port <= 65535,
        ))
    except ValueError:
        valid = False
    if not valid:
        raise OperatorStopped("runtime_url_invalid")
    return raw.rstrip("/")


def ensure_port_free(port: int) -> None:
    """Fail if another process already accepts connections on the pilot port."""
    try:
        with socket.create_connection(("127.0.0.1", port), timeout=0.5):
            raise OperatorStopped("runtime_port_already_in_use")
    except OperatorStopped:
        raise
    except OSError:
        return


def send_query_sse(
    base_url: str,
    service_token: str,
    question: str,
    *,
    post=None,
    timeout_seconds: float = 150.0,
    capture_answer: bool = False,
) -> tuple[str, bytearray | None] | tuple[
    str, bytearray, tuple[dict, ...]
]:
    """Dispatch one local request and retain selected answer bytes in memory."""
    if not service_token or not isinstance(question, str) or not question.strip():
        raise OperatorStopped("request_input_invalid")
    session = None
    if post is None:
        import requests

        session = requests.Session()
        session.trust_env = False
        post = session.post
    from mech_chatbot.adapters.pilot_replay import iter_sse_events

    response = None
    answer = bytearray() if capture_answer else None
    citations: list[dict] = []
    try:
        response = post(
            loopback_url(base_url) + "/chat/stream",
            headers={"X-RAG-Service-Token": service_token},
            json={
                "user_id": 81,
                "username": "admin_bao",
                "user_question": question,
                "current_part_ids": [],
                "response_language": "vi",
            },
            stream=True,
            allow_redirects=False,
            timeout=(10, timeout_seconds),
        )
        response.raise_for_status()
        trace_id = ""
        terminal_seen = False
        for event, payload in iter_sse_events(response):
            if terminal_seen:
                payload.clear()
                raise OperatorStopped("rag_stream_terminal_invalid")
            if event == "error":
                raise OperatorStopped("rag_stream_error")
            if event in {"token", "delta"} and answer is not None:
                text = payload.get("text")
                if not isinstance(text, str):
                    raise OperatorStopped("rag_answer_token_invalid")
                token = bytearray(text, "utf-8")
                payload.clear()
                del text
                try:
                    if len(answer) + len(token) > MAX_ANSWER_BYTES:
                        raise OperatorStopped("capture_answer_too_large")
                    answer.extend(token)
                finally:
                    token[:] = b"\0" * len(token)
            if event == "citation" and answer is not None:
                try:
                    normalized, raw = _canonical_citations((payload,))
                except ValueError:
                    payload.clear()
                    raise OperatorStopped("rag_citation_invalid") from None
                try:
                    citations.append(normalized[0])
                    _canonical_citations(tuple(citations))
                except ValueError:
                    raise OperatorStopped("rag_citation_invalid") from None
                finally:
                    raw[:] = b"\0" * len(raw)
                    payload.clear()
            if event == "done":
                terminal_seen = True
                if payload.get("ok") is not True:
                    raise OperatorStopped("rag_stream_terminal_invalid")
                trace_id = str(payload.get("trace_id") or "").strip()
        if not trace_id:
            raise OperatorStopped("rag_trace_missing")
        if answer is not None and not answer:
            raise OperatorStopped("rag_answer_missing")
        if answer is not None:
            return trace_id, answer, tuple(citations)
        return trace_id, answer
    except OperatorStopped:
        if answer is not None:
            answer[:] = b"\0" * len(answer)
        for citation in citations:
            citation.clear()
        raise
    except Exception:
        if answer is not None:
            answer[:] = b"\0" * len(answer)
        for citation in citations:
            citation.clear()
        raise OperatorStopped("rag_request_failed") from None
    finally:
        if response is not None:
            response.close()
        if session is not None:
            session.close()


def fetch_runtime_health(base_url: str, service_token: str, *, get=None) -> dict:
    """Read authenticated health from the explicit local pilot runtime."""
    if not service_token:
        raise OperatorStopped("service_token_missing")
    session = None
    if get is None:
        import requests

        session = requests.Session()
        session.trust_env = False
        get = session.get
    response = None
    try:
        response = get(
            loopback_url(base_url) + "/health",
            headers={"X-RAG-Service-Token": service_token},
            timeout=5,
            allow_redirects=False,
        )
        response.raise_for_status()
        value = response.json()
        if not isinstance(value, dict):
            raise OperatorStopped("runtime_health_invalid")
        return value
    except OperatorStopped:
        raise
    except Exception:
        raise OperatorStopped("runtime_health_unavailable") from None
    finally:
        if response is not None:
            response.close()
        if session is not None:
            session.close()


__all__ = [
    "OperatorStopped", "consolidated_binding_valid", "ensure_port_free",
    "fetch_runtime_health", "inside", "loopback_url", "operator_outputs_fresh",
    "pilot_run_paths", "pilot_run_root", "send_query_sse", "terminal_reason",
]
