"""Capture deterministic, privacy-safe Phase 0 refactor contract evidence."""

from __future__ import annotations

import argparse
import hashlib
import importlib
import json
import sys
from collections.abc import Callable, Mapping, Sequence
from contextlib import ExitStack, contextmanager
from pathlib import Path
from typing import Any, Iterator, Protocol
from unittest.mock import patch


_PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

from scripts.quality.refactor_evidence import (
    ManifestInputs,
    REDACTED,
    build_manifest,
    canonical_json_bytes,
    canonicalize_evidence,
)


ARTIFACT_NAMES = (
    "openapi-app.json",
    "openapi-rag.json",
    "sse-success.jsonl",
    "sse-busy.jsonl",
    "upload-review-samples.json",
    "pytest-baseline.txt",
    "manifest.json",
)

_PROVENANCE_FIELDS = frozenset(
    {
        "commit_sha",
        "baseline_sha",
        "working_tree_status",
        "os_name",
        "python_version",
        "dependency_lock_sha256",
        "settings",
        "feature_flags",
        "data_snapshot",
        "collection",
        "provider_configuration",
        "concurrency",
        "commands",
        "pytest_baseline",
    }
)
_PYTEST_FIELDS = (
    "scope",
    "status",
    "passed",
    "failed",
    "skipped",
    "warnings",
)


class _OpenAPIApp(Protocol):
    def openapi(self) -> Mapping[str, Any]: ...


class _BaselineQdrantRuntime:
    """No-I/O Qdrant boundary used while observing the in-memory app."""

    client = object()
    collection_name = "baseline-capture"

    def close(self) -> None:
        return None


AppFactory = Callable[[], _OpenAPIApp]
SseTranscripts = Mapping[str, Sequence[Mapping[str, Any]]]
SseCapture = Callable[[], SseTranscripts]
_UPLOAD_REVIEW_SAMPLES = {
    "upload_accepted": {
        "ok": True,
        "job_id": 9,
        "file_name": "sample.pdf",
    },
    "review_bulk_publish": {
        "ok": True,
        "updated": 0,
        "pending": 1,
        "failed": 0,
        "failures": [{"ok": True, "doc_id": 42, "state": "processing"}],
    },
}


def _ensure_source_root_importable() -> None:
    source_root = _PROJECT_ROOT / "src"
    source_text = str(source_root)
    if source_text not in sys.path:
        sys.path.insert(0, source_text)


def _default_app_factory() -> _OpenAPIApp:
    _ensure_source_root_importable()
    from mech_chatbot.api.app_server import app

    return app


def _default_rag_app_factory() -> _OpenAPIApp:
    _ensure_source_root_importable()
    from mech_chatbot.api.rag_server import app

    return app


class _ObservedRagResponse:
    def __init__(
        self,
        *,
        status_code: int = 200,
        text: str = "",
        events: Sequence[tuple[str, Mapping[str, Any]]] = (),
    ) -> None:
        self.status_code = status_code
        self.text = text
        self.ok = 200 <= status_code < 400
        self._events = tuple(events)

    def __enter__(self) -> _ObservedRagResponse:
        return self

    def __exit__(self, *_args: object) -> bool:
        return False

    def iter_lines(self, decode_unicode: bool = True) -> Iterator[str]:
        del decode_unicode
        for event, data in self._events:
            yield f"event: {event}"
            yield "data: " + json.dumps(data, ensure_ascii=False)
            yield ""


def _evidence_profile() -> dict[str, Any]:
    return {
        "user_id": 7,
        "username": "baseline-observer",
        "display_name": "Baseline Observer",
        "department": "Engineering",
        "roles": ["viewer"],
        "allowed_departments": ["Engineering"],
        "max_security_level": "internal",
        "allowed_sites": ["TEST"],
        "preferred_language": "vi",
    }


def _success_rag_response() -> _ObservedRagResponse:
    citation = {
        "doc_id": 42,
        "trang": 3,
        "file_goc": "sample.pdf",
        "version_no": 1,
        "score": 0.91,
        "security_level": "internal",
        "source_id": "D42P3",
    }
    return _ObservedRagResponse(
        events=(
            (
                "metadata",
                {
                    "new_part_ids": ["P123"],
                    "debug_info": {
                        "conversation_context": {"topic": "sample"},
                        "retrieved_docs": [citation],
                        "citation_docs": [citation],
                    },
                },
            ),
            ("delta", {"text": "Câu trả lời "}),
            (
                "delta",
                {
                    "text": (
                        "[Nguồn: sample.pdf, Trang 3, Version 1, "
                        "SourceID D42P3]"
                    )
                },
            ),
            ("done", {"ok": True, "elapsed_ms": 25}),
        )
    )


def _parse_sse(body: str) -> tuple[dict[str, Any], ...]:
    events: list[dict[str, Any]] = []
    for block in body.strip().split("\n\n"):
        lines = block.splitlines()
        names = [
            line.removeprefix("event: ")
            for line in lines
            if line.startswith("event: ")
        ]
        data = [
            json.loads(line.removeprefix("data: "))
            for line in lines
            if line.startswith("data: ")
        ]
        if names:
            events.append({"event": names[0], "data": data[0] if data else None})
    return tuple(events)


@contextmanager
def _isolated_app_client(
    app_server: Any, rag_response: _ObservedRagResponse
) -> Iterator[Any]:
    from fastapi.testclient import TestClient

    previous_overrides = dict(app_server.app.dependency_overrides)
    client = None
    try:
        app_server.app.dependency_overrides[app_server.csrf_profile] = _evidence_profile
        client = TestClient(app_server.app)
        with ExitStack() as stack:
            stack.enter_context(
                patch.object(
                    app_server.app.state,
                    "post",
                    return_value=rag_response,
                )
            )
            stack.enter_context(
                patch.object(
                    app_server.app.state,
                    "qdrant_builder",
                    return_value=_BaselineQdrantRuntime(),
                )
            )
            stack.enter_context(
                patch.object(
                    app_server,
                    "refresh_expired_status",
                    return_value={},
                )
            )
            stack.enter_context(patch.object(app_server, "_pilot_route", return_value=None))
            stack.enter_context(
                patch.object(app_server, "_verify_image_upload", return_value=None)
            )
            stack.enter_context(
                patch.object(app_server, "save_chat_history", return_value=123)
            )
            for name in ("save_answer_evidence", "save_answer_sources", "write_audit_log"):
                stack.enter_context(patch.object(app_server, name, return_value=None))
            stack.enter_context(patch.object(app_server, "page_has_vision", return_value=False))
            stack.enter_context(client)
            yield client
    finally:
        if client is not None:
            client.close()
        app_server.app.dependency_overrides.clear()
        app_server.app.dependency_overrides.update(previous_overrides)


def _observe_chat_endpoint(
    app_server: Any, response: _ObservedRagResponse
) -> tuple[dict[str, Any], ...]:
    with _isolated_app_client(app_server, response) as client:
        result = client.post(
            "/api/chat/message",
            json={"session_id": "baseline-session", "question": "Câu hỏi mẫu"},
        )
    if result.status_code != 200:
        raise RuntimeError(f"chat evidence endpoint returned HTTP {result.status_code}")
    return _parse_sse(result.text)


def _default_sse_capture() -> SseTranscripts:
    """Observe browser-facing SSE through the real FastAPI endpoint in memory."""

    _ensure_source_root_importable()
    from mech_chatbot.api import app_server

    return {
        "success": _observe_chat_endpoint(app_server, _success_rag_response()),
        "busy": _observe_chat_endpoint(
            app_server,
            _ObservedRagResponse(status_code=503, text="busy"),
        ),
    }


def _json_value(value: Any) -> Any:
    """Return a plain JSON-compatible copy after canonicalization."""

    return json.loads(canonical_json_bytes(value))


def _write_json(path: Path, value: Any) -> bytes:
    payload = canonical_json_bytes(value) + b"\n"
    path.write_bytes(payload)
    return payload


def _is_sensitive_openapi_name(name: str) -> bool:
    probe = canonicalize_evidence({name: "value"})
    return probe[name] == REDACTED


def _canonicalize_openapi(
    value: Any,
    *,
    context: str = "schema",
    secret_property: bool = False,
) -> Any:
    """Canonicalize values without mistaking OpenAPI identifiers for secrets."""

    if isinstance(value, Mapping):
        return _canonicalize_openapi_mapping(
            value,
            context=context,
            secret_property=secret_property,
        )
    if isinstance(value, (list, tuple)):
        return tuple(
            _canonicalize_openapi(
                item,
                context=context,
                secret_property=secret_property,
            )
            for item in value
        )
    return canonicalize_evidence({"value": value})["value"]


def _canonicalize_openapi_mapping(
    value: Mapping[str, Any],
    *,
    context: str,
    secret_property: bool,
) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, item in value.items():
        if not isinstance(key, str):
            raise TypeError("OpenAPI mapping keys must be strings")
        if context == "paths":
            result[key] = _canonicalize_openapi(item)
        elif context == "properties":
            result[key] = _canonicalize_openapi(
                item,
                secret_property=_is_sensitive_openapi_name(key),
            )
        elif context == "example" and _is_sensitive_openapi_name(key):
            result[key] = REDACTED
        elif key in {"paths", "properties"}:
            result[key] = _canonicalize_openapi(item, context=key)
        elif secret_property and key in {"default", "example", "examples"}:
            result[key] = REDACTED
        elif key in {"example", "examples"}:
            result[key] = _canonicalize_openapi(item, context="example")
        elif key.casefold().startswith("x-") and _is_sensitive_openapi_name(key):
            result[key] = REDACTED
        elif isinstance(item, (Mapping, list, tuple)):
            result[key] = _canonicalize_openapi(item, secret_property=secret_property)
        else:
            keyed = canonicalize_evidence({key: item})[key]
            result[key] = (
                canonicalize_evidence({"value": item})["value"]
                if keyed == REDACTED
                else keyed
            )
    return result


def _write_openapi_json(path: Path, value: Any) -> bytes:
    payload = json.dumps(
        _canonicalize_openapi(value),
        allow_nan=False,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8") + b"\n"
    path.write_bytes(payload)
    return payload


def _write_jsonl(path: Path, values: Sequence[Mapping[str, Any]]) -> bytes:
    payload = b"".join(canonical_json_bytes(value) + b"\n" for value in values)
    path.write_bytes(payload)
    return payload


def _validate_provenance_keys(provenance: Mapping[str, Any]) -> None:
    supplied = frozenset(provenance)
    missing = sorted(_PROVENANCE_FIELDS - supplied)
    unknown = sorted(supplied - _PROVENANCE_FIELDS)
    if missing:
        raise ValueError(f"missing provenance fields: {', '.join(missing)}")
    if unknown:
        raise ValueError(f"unknown provenance fields: {', '.join(unknown)}")


def _validate_provenance_types(provenance: Mapping[str, Any]) -> None:
    for field in ("settings", "feature_flags", "data_snapshot", "provider_configuration"):
        if not isinstance(provenance[field], Mapping):
            raise ValueError(f"{field} must be a mapping")
    for field in ("working_tree_status", "commands"):
        value = provenance[field]
        if isinstance(value, (str, bytes)) or not isinstance(value, Sequence):
            raise ValueError(f"{field} must be a sequence")
    for field in (
        "commit_sha",
        "os_name",
        "python_version",
        "dependency_lock_sha256",
        "collection",
    ):
        if not isinstance(provenance[field], str):
            raise ValueError(f"{field} must be a string")
    if provenance["baseline_sha"] is not None and not isinstance(
        provenance["baseline_sha"], str
    ):
        raise ValueError("baseline_sha must be a string or null")
    concurrency = provenance["concurrency"]
    if isinstance(concurrency, bool) or not isinstance(concurrency, int) or concurrency < 1:
        raise ValueError("concurrency must be a positive integer")


def _validate_pytest_baseline(value: Any) -> None:
    pytest_baseline = value
    if not isinstance(pytest_baseline, Mapping):
        raise ValueError("pytest_baseline must be a mapping")
    supplied_pytest = frozenset(pytest_baseline)
    expected_pytest = frozenset(_PYTEST_FIELDS)
    if supplied_pytest != expected_pytest:
        missing_pytest = sorted(expected_pytest - supplied_pytest)
        unknown_pytest = sorted(supplied_pytest - expected_pytest)
        details = []
        if missing_pytest:
            details.append(f"missing: {', '.join(missing_pytest)}")
        if unknown_pytest:
            details.append(f"unknown: {', '.join(unknown_pytest)}")
        raise ValueError(f"invalid pytest_baseline fields ({'; '.join(details)})")
    for field in ("scope", "status"):
        if not isinstance(pytest_baseline[field], str):
            raise ValueError(f"pytest_baseline.{field} must be a string")
    for field in ("passed", "failed", "skipped", "warnings"):
        count = pytest_baseline[field]
        if isinstance(count, bool) or not isinstance(count, int) or count < 0:
            raise ValueError(f"pytest_baseline.{field} must be a non-negative integer")


def _validate_provenance(provenance: Mapping[str, Any]) -> None:
    _validate_provenance_keys(provenance)
    _validate_provenance_types(provenance)
    _validate_pytest_baseline(provenance["pytest_baseline"])


def _render_pytest_baseline(value: Mapping[str, Any]) -> bytes:
    safe = _json_value(value)
    lines = ("schema=pytest-baseline-v1",) + tuple(
        f"{field}={safe[field]}" for field in _PYTEST_FIELDS
    )
    return ("\n".join(lines) + "\n").encode("utf-8")


def _validate_sse_transcripts(value: SseTranscripts) -> None:
    if frozenset(value) != {"success", "busy"}:
        raise ValueError("SSE capture must contain exactly success and busy transcripts")
    for scenario, events in value.items():
        if isinstance(events, (str, bytes)) or not isinstance(events, Sequence):
            raise ValueError(f"SSE {scenario} transcript must be a sequence")
        for event in events:
            if not isinstance(event, Mapping) or frozenset(event) != {"event", "data"}:
                raise ValueError(f"SSE {scenario} events must contain event and data")


def _make_manifest_inputs(
    provenance: Mapping[str, Any], artifact_sha256: Mapping[str, str]
) -> ManifestInputs:
    return ManifestInputs(
        commit_sha=str(provenance["commit_sha"]),
        baseline_sha=(
            None if provenance["baseline_sha"] is None else str(provenance["baseline_sha"])
        ),
        working_tree_status=tuple(str(item) for item in provenance["working_tree_status"]),
        os_name=str(provenance["os_name"]),
        python_version=str(provenance["python_version"]),
        dependency_lock_sha256=str(provenance["dependency_lock_sha256"]),
        settings=provenance["settings"],
        feature_flags=provenance["feature_flags"],
        data_snapshot=provenance["data_snapshot"],
        collection=str(provenance["collection"]),
        provider_configuration=provenance["provider_configuration"],
        concurrency=provenance["concurrency"],
        commands=tuple(str(item) for item in provenance["commands"]),
        artifact_sha256=artifact_sha256,
    )


def capture_refactor_baseline(
    output_dir: str | Path,
    *,
    provenance: Mapping[str, Any],
    app_factory: AppFactory = _default_app_factory,
    rag_app_factory: AppFactory = _default_rag_app_factory,
    sse_capture: SseCapture = _default_sse_capture,
) -> dict[str, Any]:
    """Write the complete Phase 0 evidence bundle without external I/O.

    Runtime provenance is accepted only as explicit data. OpenAPI and browser
    SSE contracts are observed in memory. Default SSE capture replaces RAG,
    persistence, audit, and vision system boundaries with deterministic fakes.
    """

    _validate_provenance(provenance)
    target = Path(output_dir)
    target.mkdir(parents=True, exist_ok=True)
    transcripts = sse_capture()
    _validate_sse_transcripts(transcripts)

    payloads = {
        "openapi-app.json": _write_openapi_json(
            target / "openapi-app.json", app_factory().openapi()
        ),
        "openapi-rag.json": _write_openapi_json(
            target / "openapi-rag.json", rag_app_factory().openapi()
        ),
        "sse-success.jsonl": _write_jsonl(
            target / "sse-success.jsonl", transcripts["success"]
        ),
        "sse-busy.jsonl": _write_jsonl(
            target / "sse-busy.jsonl", transcripts["busy"]
        ),
        "upload-review-samples.json": _write_json(
            target / "upload-review-samples.json", _UPLOAD_REVIEW_SAMPLES
        ),
        "pytest-baseline.txt": _render_pytest_baseline(provenance["pytest_baseline"]),
    }
    (target / "pytest-baseline.txt").write_bytes(payloads["pytest-baseline.txt"])
    hashes = {
        name: hashlib.sha256(payload).hexdigest()
        for name, payload in sorted(payloads.items())
    }
    manifest = build_manifest(_make_manifest_inputs(provenance, hashes))
    (target / "manifest.json").write_bytes(manifest.canonical_bytes + b"\n")
    return _json_value(manifest.payload)


def _load_factory(reference: str) -> AppFactory:
    module_name, separator, attribute_name = reference.partition(":")
    if not separator or not module_name or not attribute_name:
        raise ValueError("factory reference must use module:attribute")
    candidate = getattr(importlib.import_module(module_name), attribute_name)
    if not callable(candidate):
        raise TypeError(f"factory is not callable: {reference}")
    return candidate


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--provenance", type=Path, required=True)
    parser.add_argument(
        "--app-factory",
        default="scripts.quality.capture_refactor_baseline:_default_app_factory",
    )
    parser.add_argument(
        "--rag-app-factory",
        default="scripts.quality.capture_refactor_baseline:_default_rag_app_factory",
    )
    parser.add_argument(
        "--sse-capture",
        default="scripts.quality.capture_refactor_baseline:_default_sse_capture",
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    provenance = json.loads(args.provenance.read_text(encoding="utf-8"))
    if not isinstance(provenance, Mapping):
        raise ValueError("provenance document must be a JSON object")
    capture_refactor_baseline(
        args.output_dir,
        provenance=provenance,
        app_factory=_load_factory(args.app_factory),
        rag_app_factory=_load_factory(args.rag_app_factory),
        sse_capture=_load_factory(args.sse_capture),
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
