"""Capture deterministic, privacy-safe Phase 0 refactor contract evidence."""

from __future__ import annotations

import argparse
import hashlib
import importlib
import json
import sys
from collections.abc import Callable, Mapping, Sequence
from pathlib import Path
from typing import Any, Protocol


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


AppFactory = Callable[[], _OpenAPIApp]


_SSE_SUCCESS = (
    {"event": "thinking", "data": {"message": "Đang suy nghĩ"}},
    {"event": "delta", "data": {"text": "Câu trả lời mẫu."}},
    {
        "event": "citation",
        "data": {
            "doc_id": 42,
            "page_no": 3,
            "file_name": "sample.pdf",
            "source_id": "D42P3",
        },
    },
    {
        "event": "done",
        "data": {
            "chat_id": 123,
            "new_part_ids": ["P123"],
            "conversation_context": {"topic": "sample"},
            "citations": [
                {
                    "doc_id": 42,
                    "page_no": 3,
                    "file_name": "sample.pdf",
                    "source_id": "D42P3",
                }
            ],
        },
    },
)
_SSE_BUSY = (
    {"event": "thinking", "data": {"message": "Đang suy nghĩ"}},
    {
        "event": "error",
        "data": {"status": 503, "message": "RAG server busy"},
    },
)
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
        result: dict[str, Any] = {}
        for raw_key, item in value.items():
            if not isinstance(raw_key, str):
                raise TypeError("OpenAPI mapping keys must be strings")
            key = raw_key
            if context == "paths":
                result[key] = _canonicalize_openapi(item)
            elif context == "properties":
                result[key] = _canonicalize_openapi(
                    item,
                    secret_property=_is_sensitive_openapi_name(key),
                )
            elif context == "example" and _is_sensitive_openapi_name(key):
                result[key] = REDACTED
            elif key == "paths":
                result[key] = _canonicalize_openapi(item, context="paths")
            elif key == "properties":
                result[key] = _canonicalize_openapi(item, context="properties")
            elif secret_property and key in {"default", "example", "examples"}:
                result[key] = REDACTED
            elif key in {"example", "examples"}:
                result[key] = _canonicalize_openapi(item, context="example")
            elif key.casefold().startswith("x-") and _is_sensitive_openapi_name(key):
                result[key] = REDACTED
            elif isinstance(item, (Mapping, list, tuple)):
                result[key] = _canonicalize_openapi(
                    item,
                    secret_property=secret_property,
                )
            else:
                keyed = canonicalize_evidence({key: item})[key]
                result[key] = (
                    canonicalize_evidence({"value": item})["value"]
                    if keyed == REDACTED
                    else keyed
                )
        return result
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


def _validate_provenance(provenance: Mapping[str, Any]) -> None:
    supplied = frozenset(provenance)
    missing = sorted(_PROVENANCE_FIELDS - supplied)
    unknown = sorted(supplied - _PROVENANCE_FIELDS)
    if missing:
        raise ValueError(f"missing provenance fields: {', '.join(missing)}")
    if unknown:
        raise ValueError(f"unknown provenance fields: {', '.join(unknown)}")
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
    pytest_baseline = provenance["pytest_baseline"]
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


def _render_pytest_baseline(value: Mapping[str, Any]) -> bytes:
    safe = _json_value(value)
    lines = ("schema=pytest-baseline-v1",) + tuple(
        f"{field}={safe[field]}" for field in _PYTEST_FIELDS
    )
    return ("\n".join(lines) + "\n").encode("utf-8")


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
) -> dict[str, Any]:
    """Write the complete Phase 0 evidence bundle without external I/O.

    Runtime provenance is accepted only as explicit data. The two factories are
    invoked solely to call FastAPI's in-memory ``openapi()`` method; the capture
    itself never contacts providers, databases, vector stores, or the network.
    """

    _validate_provenance(provenance)
    target = Path(output_dir)
    target.mkdir(parents=True, exist_ok=True)

    payloads = {
        "openapi-app.json": _write_openapi_json(
            target / "openapi-app.json", app_factory().openapi()
        ),
        "openapi-rag.json": _write_openapi_json(
            target / "openapi-rag.json", rag_app_factory().openapi()
        ),
        "sse-success.jsonl": _write_jsonl(target / "sse-success.jsonl", _SSE_SUCCESS),
        "sse-busy.jsonl": _write_jsonl(target / "sse-busy.jsonl", _SSE_BUSY),
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
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
