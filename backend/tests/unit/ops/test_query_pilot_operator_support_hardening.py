"""SSE terminal and encrypted-review capture boundary tests."""

from __future__ import annotations

import pytest

from scripts.ops.query_pilot_operator_support import (
    OperatorStopped,
    consolidated_binding_valid,
    pilot_run_paths,
    pilot_run_root,
    send_query_sse,
)


_CITATION = {
    "doc_id": 41,
    "page_no": 1,
    "file_name": "confidential-policy.pdf",
    "file_goc": "confidential-policy.pdf",
    "version_no": 3,
    "score": 0.91,
    "trang": 1,
    "source_id": "D41P1",
}


class _Response:
    def __init__(self, events: tuple[tuple[str, dict], ...]):
        self._events = events

    def raise_for_status(self):
        return None

    def iter_lines(self, decode_unicode=True):
        import json

        assert decode_unicode is True
        for event, payload in self._events:
            yield f"event: {event}"
            yield "data: " + json.dumps(payload)
            yield ""

    def close(self):
        return None


def _send(events: tuple[tuple[str, dict], ...], *, capture: bool = True):
    return send_query_sse(
        "http://127.0.0.1:8302",
        "service-token",
        "private question",
        post=lambda *_args, **_kwargs: _Response(events),
        capture_answer=capture,
    )


def test_selected_sse_capture_returns_structured_citation_events():
    trace_id, answer, citations = _send((
        ("token", {"text": "answer [SRC:D41P1]"}),
        ("citation", _CITATION),
        ("done", {"ok": True, "trace_id": "trace-01"}),
    ))

    assert trace_id == "trace-01"
    assert answer == b"answer [SRC:D41P1]"
    assert citations == (_CITATION,)
    answer[:] = b"\0" * len(answer)


@pytest.mark.parametrize(
    "events",
    (
        (
            ("token", {"text": "answer"}),
            ("done", {"ok": True, "trace_id": "trace-01"}),
            ("done", {"ok": True, "trace_id": "trace-01"}),
        ),
        (
            ("token", {"text": "answer"}),
            ("done", {"ok": True, "trace_id": "trace-01"}),
            ("token", {"text": "late plaintext"}),
        ),
        (
            ("token", {"text": "answer"}),
            ("done", {"ok": True, "trace_id": "trace-01"}),
            ("citation", _CITATION),
        ),
        (
            ("token", {"text": "answer"}),
            ("done", {"ok": True, "trace_id": "trace-01"}),
            ("warning", {"message": "late event"}),
        ),
    ),
)
def test_sse_rejects_duplicate_terminal_or_any_event_after_terminal(events):
    with pytest.raises(OperatorStopped, match="rag_stream_terminal_invalid"):
        _send(events)


def test_sse_rejects_non_schema_citation_before_returning_plaintext():
    with pytest.raises(OperatorStopped, match="rag_citation_invalid"):
        _send((
            ("token", {"text": "answer"}),
            ("citation", {**_CITATION, "debug_secret": "must-not-capture"}),
            ("done", {"ok": True, "trace_id": "trace-01"}),
        ))


def test_pilot_run_root_is_source_relative_and_has_fixed_layout(tmp_path):
    authorization = {"pilot_run_root": ".local/launch-01/run"}

    run_root = pilot_run_root(authorization, tmp_path)

    assert run_root == (tmp_path / ".local/launch-01/run").resolve()
    assert pilot_run_paths(run_root)["wal"] == run_root / "pilot.wal.jsonl"
    with pytest.raises(OperatorStopped, match="pilot_run_root_invalid"):
        pilot_run_root({"pilot_run_root": "../outside"}, tmp_path)


def test_consolidated_binding_rejects_run_root_drift(tmp_path):
    import hashlib
    import json

    consolidated_path = tmp_path / ".local" / "draft.json"
    consolidated_path.parent.mkdir(parents=True)
    consolidated = {
        "schema": "query-decomposition-consolidated-launch-draft-v1",
        "source_commit": "a" * 40,
        "pilot_run_root": ".local/launch-01/run",
    }
    consolidated_path.write_text(json.dumps(consolidated), encoding="utf-8")
    consolidated_ref = {
        "path": ".local/draft.json",
        "sha256": hashlib.sha256(consolidated_path.read_bytes()).hexdigest(),
        "schema": consolidated["schema"],
    }
    approval_path = tmp_path / ".local" / "approval.json"
    approval = {
        "schema": "query-decomposition-pilot-approval-v1",
        "consolidated_launch_draft": consolidated_ref,
        "pilot_run_root": consolidated["pilot_run_root"],
    }
    approval_path.write_text(json.dumps(approval), encoding="utf-8")
    authorization = {
        "source_commit": consolidated["source_commit"],
        "pilot_run_root": consolidated["pilot_run_root"],
        "pilot_approval": {
            "path": ".local/approval.json",
            "sha256": hashlib.sha256(approval_path.read_bytes()).hexdigest(),
            "schema": approval["schema"],
        },
        "consolidated_launch_draft": consolidated_ref,
    }

    assert consolidated_binding_valid(authorization, tmp_path)
    authorization["pilot_run_root"] = ".local/launch-02/run"
    assert not consolidated_binding_valid(authorization, tmp_path)
