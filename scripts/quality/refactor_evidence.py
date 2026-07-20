"""Deterministic, privacy-safe evidence helpers for refactor artifacts."""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from pathlib import PurePosixPath, PureWindowsPath
from types import MappingProxyType
from typing import Any, Mapping


REDACTED = "<redacted>"
TIMESTAMP = "<timestamp>"
TRACE_ID = "<trace-id>"
REQUEST_ID = "<request-id>"
VOLATILE_DURATION = "<duration>"
ABSOLUTE_PATH = "<absolute-path>"

_NON_ALPHANUMERIC = re.compile(r"[^a-z0-9]+")
_CAMEL_CASE_BOUNDARY = re.compile(r"(?<=[a-z0-9])(?=[A-Z])")
_AUTHORIZATION_VALUE = re.compile(
    r"(?i)(?P<prefix>\bauthorization\s*[:=]\s*)"
    r"(?:(?:bearer|basic|token|api[-_]?key)\s+)?\S+"
)
_SENSITIVE_NAME_PATTERN = (
    r"(?:[a-z0-9]+[-_])*(?:api[-_]?key|authorization|bearer|token|"
    r"password|passwd|pwd|secret|credential|cookie|dsn|connection[-_]?string|"
    r"(?:database|db|postgres|postgresql|mysql|mssql|redis|mongodb|qdrant)"
    r"[-_]?(?:url|uri))"
)
_SENSITIVE_VALUE_PATTERN = r"\"[^\"]*\"|'[^']*'|\S+"
_SENSITIVE_CLI_ARGUMENT = re.compile(
    rf"(?P<prefix>(?<![\w-])-{{1,2}})"
    rf"(?P<name>{_SENSITIVE_NAME_PATTERN})"
    rf"(?P<separator>\s*[:=]\s*|\s+)"
    rf"(?P<value>{_SENSITIVE_VALUE_PATTERN})",
    re.IGNORECASE | re.VERBOSE,
)
_SENSITIVE_NAMED_ASSIGNMENT = re.compile(
    rf"(?P<prefix>(?<![\w-])(?:export\s+)?)"
    rf"(?P<name>{_SENSITIVE_NAME_PATTERN})"
    rf"(?P<separator>\s*[:=]\s*)"
    rf"(?P<value>{_SENSITIVE_VALUE_PATTERN})",
    re.IGNORECASE | re.VERBOSE,
)
_CREDENTIAL_URI = re.compile(
    r"(?i)\b[a-z][a-z0-9+.-]*://[^\s/@:'\"<>]+"
    r"(?::[^\s/@'\"<>]+)?@[^\s'\"<>]+"
)
_FILE_URI = re.compile(r"(?i)file:///(?:[^\s'\"<>]+)")
_WINDOWS_ABSOLUTE_PATH = re.compile(
    r"(?i)(?<![\w])(?:[a-z]:[\\/]|\\\\)[^\s'\"<>|]+"
)
_COMMON_POSIX_ABSOLUTE_PATH = re.compile(
    r"(?i)(?<![\w])/(?:home|users|tmp|var|opt|srv|workspace|mnt|root)"
    r"(?:/[^\s'\"<>|]*)?"
)
_ANY_POSIX_ABSOLUTE_PATH = re.compile(r"(?<![:/\w])/(?!/)[^\s'\"<>|]+")
_SENSITIVE_EXACT_KEYS = frozenset(
    {
        "authorization",
        "bearer",
        "connectionstring",
        "connectionurl",
        "databaseuri",
        "databaseurl",
        "dburl",
        "dsn",
        "password",
        "passwd",
        "pwd",
        "token",
    }
)
_TIMESTAMP_KEYS = frozenset(
    {
        "createdat",
        "finishedat",
        "generatedat",
        "startedat",
        "timestamp",
        "ts",
        "updatedat",
    }
)
_CONTRACT_DURATION_TERMS = ("budget", "limit", "max", "min", "target", "threshold")
_TOKEN_METRIC_WORDS = frozenset(
    {
        "budget",
        "count",
        "counts",
        "estimate",
        "estimated",
        "first",
        "input",
        "latency",
        "limit",
        "limits",
        "max",
        "min",
        "output",
        "total",
        "usage",
        "url",
    }
)


def _normalized_key(key: object) -> str:
    return _NON_ALPHANUMERIC.sub("", str(key).casefold())


def _key_words(key: object) -> frozenset[str]:
    separated = _CAMEL_CASE_BOUNDARY.sub("_", str(key))
    return frozenset(part for part in _NON_ALPHANUMERIC.split(separated.casefold()) if part)


def _is_sensitive_key(key: object) -> bool:
    normalized = _normalized_key(key)
    words = _key_words(key)
    token_like = bool(words.intersection({"token", "tokens"})) and not words.intersection(
        _TOKEN_METRIC_WORDS
    )
    return (
        normalized in _SENSITIVE_EXACT_KEYS
        or "apikey" in normalized
        or "authorization" in normalized
        or "password" in normalized
        or bool(words.intersection({"passwd", "pwd"}))
        or "secret" in normalized
        or "privatekey" in normalized
        or "sessionid" in normalized
        or bool(words.intersection({"cookie", "cookies", "credential", "credentials"}))
        or "connectionstring" in normalized
        or normalized.endswith(
            ("connectionuri", "connectionurl", "databaseuri", "databaseurl", "dsn")
        )
        or token_like
    )


def redact_sensitive_fields(value: Any) -> Any:
    """Return an immutable recursive copy with sensitive keyed values redacted."""

    if isinstance(value, Mapping):
        return MappingProxyType(
            {
                key: REDACTED if _is_sensitive_key(key) else redact_sensitive_fields(item)
                for key, item in value.items()
            }
        )
    if isinstance(value, (list, tuple)):
        return tuple(redact_sensitive_fields(item) for item in value)
    if isinstance(value, (set, frozenset)):
        return frozenset(redact_sensitive_fields(item) for item in value)
    return value


def _volatile_marker(key: object) -> str | None:
    normalized = _normalized_key(key)
    if normalized in _TIMESTAMP_KEYS or normalized.endswith("timestamp"):
        return TIMESTAMP
    if normalized.endswith("traceid"):
        return TRACE_ID
    if normalized.endswith("requestid"):
        return REQUEST_ID
    if ("elapsed" in normalized or "latency" in normalized) and not any(
        term in normalized for term in _CONTRACT_DURATION_TERMS
    ):
        return VOLATILE_DURATION
    return None


def _is_absolute_path(value: str) -> bool:
    return PurePosixPath(value).is_absolute() or PureWindowsPath(value).is_absolute()


def _is_path_key(key: object) -> bool:
    normalized = _normalized_key(key)
    words = _key_words(key)
    return bool(
        words.intersection({"dir", "directories", "directory", "file", "files", "path", "paths"})
        or normalized in {"cwd", "root", "workspace"}
        or normalized.endswith(("path", "paths"))
    )


def _is_unstructured_path_text_key(key: object) -> bool:
    return _normalized_key(key) in {"command", "commands", "workingtreestatus"}


def _redact_assignment(match: re.Match[str]) -> str:
    name = match.group("name")
    words = _key_words(name)
    if words.intersection({"token", "tokens"}) and words.intersection(
        _TOKEN_METRIC_WORDS
    ):
        return match.group(0)
    return (
        f"{match.group('prefix')}{name}{match.group('separator')}"
        f"{REDACTED}"
    )


def _replace_posix_path(match: re.Match[str]) -> str:
    path = match.group(0)
    if re.match(r"^/(?:api(?:/|$)|v\d+(?:/|$))", path, re.IGNORECASE):
        return path
    return ABSOLUTE_PATH


def _canonicalize_text(
    value: str, *, path_context: bool, unstructured_path_text: bool
) -> str:
    sanitized = _AUTHORIZATION_VALUE.sub(
        lambda match: f"{match.group('prefix')}{REDACTED}", value
    )
    sanitized = _SENSITIVE_CLI_ARGUMENT.sub(_redact_assignment, sanitized)
    sanitized = _SENSITIVE_NAMED_ASSIGNMENT.sub(_redact_assignment, sanitized)
    sanitized = _CREDENTIAL_URI.sub(REDACTED, sanitized)
    if path_context and _is_absolute_path(sanitized):
        return ABSOLUTE_PATH
    for pattern in (_FILE_URI, _WINDOWS_ABSOLUTE_PATH, _COMMON_POSIX_ABSOLUTE_PATH):
        sanitized = pattern.sub(ABSOLUTE_PATH, sanitized)
    if unstructured_path_text:
        sanitized = _ANY_POSIX_ABSOLUTE_PATH.sub(_replace_posix_path, sanitized)
    return sanitized


def _canonicalize(
    value: Any, *, path_context: bool, unstructured_path_text: bool
) -> Any:
    """Canonicalize recursively while carrying whether a value represents a path."""

    if isinstance(value, Mapping):
        canonical: dict[object, Any] = {}
        for key, item in value.items():
            if _is_sensitive_key(key):
                canonical[key] = REDACTED
                continue
            marker = _volatile_marker(key)
            canonical[key] = (
                marker
                if marker is not None
                else _canonicalize(
                    item,
                    path_context=path_context or _is_path_key(key),
                    unstructured_path_text=(
                        unstructured_path_text
                        or _is_unstructured_path_text_key(key)
                    ),
                )
            )
        return MappingProxyType(canonical)
    if isinstance(value, (list, tuple)):
        return tuple(
            _canonicalize(
                item,
                path_context=path_context,
                unstructured_path_text=unstructured_path_text,
            )
            for item in value
        )
    if isinstance(value, (set, frozenset)):
        return frozenset(
            _canonicalize(
                item,
                path_context=path_context,
                unstructured_path_text=unstructured_path_text,
            )
            for item in value
        )
    if isinstance(value, str):
        return _canonicalize_text(
            value,
            path_context=path_context,
            unstructured_path_text=unstructured_path_text,
        )
    return value


def canonicalize_evidence(value: Any) -> Any:
    """Return an immutable, redacted copy with volatile evidence normalized."""

    return _canonicalize(
        value, path_context=False, unstructured_path_text=False
    )


def _json_compatible(value: Any) -> Any:
    if isinstance(value, Mapping):
        result: dict[str, Any] = {}
        for key, item in value.items():
            if not isinstance(key, str):
                raise TypeError("evidence mapping keys must be strings")
            result[key] = _json_compatible(item)
        return result
    if isinstance(value, (list, tuple)):
        return [_json_compatible(item) for item in value]
    if isinstance(value, (set, frozenset)):
        items = [_json_compatible(item) for item in value]
        return sorted(
            items,
            key=lambda item: json.dumps(
                item, ensure_ascii=False, separators=(",", ":"), sort_keys=True
            ),
        )
    return value


def canonical_json_bytes(value: Any) -> bytes:
    """Serialize sanitized evidence to deterministic UTF-8 JSON bytes."""

    canonical = _json_compatible(canonicalize_evidence(value))
    return json.dumps(
        canonical,
        allow_nan=False,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")


def canonical_sha256(value: Any) -> str:
    """Return the SHA-256 hex digest of canonical evidence JSON."""

    return hashlib.sha256(canonical_json_bytes(value)).hexdigest()


@dataclass(frozen=True, slots=True)
class ManifestInputs:
    """Explicit provenance accepted when constructing an evidence manifest."""

    commit_sha: str
    baseline_sha: str | None
    working_tree_status: tuple[str, ...]
    os_name: str
    python_version: str
    dependency_lock_sha256: str
    settings: Mapping[str, Any]
    feature_flags: Mapping[str, Any]
    data_snapshot: Mapping[str, Any]
    collection: str
    provider_configuration: Mapping[str, Any]
    concurrency: int
    commands: tuple[str, ...]
    artifact_sha256: Mapping[str, str]

    def __post_init__(self) -> None:
        if (
            isinstance(self.concurrency, bool)
            or not isinstance(self.concurrency, int)
            or self.concurrency < 1
        ):
            raise ValueError("concurrency must be a positive integer")
        for field_name in (
            "settings",
            "feature_flags",
            "data_snapshot",
            "provider_configuration",
            "artifact_sha256",
        ):
            object.__setattr__(
                self,
                field_name,
                canonicalize_evidence(getattr(self, field_name)),
            )
        object.__setattr__(self, "working_tree_status", tuple(self.working_tree_status))
        object.__setattr__(self, "commands", tuple(self.commands))


@dataclass(frozen=True, slots=True)
class EvidenceManifest:
    """Deeply immutable manifest plus its canonical representation and digest."""

    payload: Mapping[str, Any]
    canonical_bytes: bytes
    sha256: str


def build_manifest(inputs: ManifestInputs) -> EvidenceManifest:
    """Build a deterministic manifest solely from explicitly supplied inputs."""

    payload = canonicalize_evidence(
        {
            "schema": "refactor-evidence-manifest-v1",
            "git": {
                "commit_sha": inputs.commit_sha,
                "baseline_sha": inputs.baseline_sha,
                "working_tree_status": inputs.working_tree_status,
            },
            "runtime": {
                "os": inputs.os_name,
                "python_version": inputs.python_version,
            },
            "dependency_lock_sha256": inputs.dependency_lock_sha256,
            "settings": inputs.settings,
            "feature_flags": inputs.feature_flags,
            "data_snapshot": inputs.data_snapshot,
            "collection": inputs.collection,
            "provider_configuration": inputs.provider_configuration,
            "execution": {
                "concurrency": inputs.concurrency,
                "commands": inputs.commands,
            },
            "artifact_sha256": inputs.artifact_sha256,
        }
    )
    serialized = canonical_json_bytes(payload)
    return EvidenceManifest(
        payload=payload,
        canonical_bytes=serialized,
        sha256=hashlib.sha256(serialized).hexdigest(),
    )
