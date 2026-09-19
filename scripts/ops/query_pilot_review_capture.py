"""DPAPI-encrypted answer capture for future Query pilot owner review."""

from __future__ import annotations

import base64
from collections import Counter
import ctypes
from ctypes import wintypes
import csv
import hashlib
import json
import math
import os
from pathlib import Path
import re
import subprocess
from typing import Callable, Mapping


REVIEW_CAPTURE_DESIGN_SHA256 = (
    "885886bdb18a249c36cffd5e2acf9684dac59d1daa74930b36792269a77840da"
)
CAPTURE_SCHEMA = "query-decomposition-pilot-review-capture-v1"
BASELINE_REVIEW_CAPTURE_COUNT = 20
MAX_CAPTURE_COUNT = BASELINE_REVIEW_CAPTURE_COUNT
MAX_ANSWER_BYTES = 256 * 1024
MAX_CITATION_COUNT = 32
MAX_CITATIONS_BYTES = 64 * 1024
MAX_CAPTURE_ARTIFACT_BYTES = 512 * 1024
_DESCRIPTION = "Query pilot review capture"
_CRYPTPROTECT_UI_FORBIDDEN = 0x1
_METADATA_FIELDS = frozenset({
    "schema",
    "source_commit",
    "review_capture_design_sha256",
    "pilot_draft_sha256",
    "consolidated_launch_draft_sha256",
    "pilot_authorization_sha256",
    "schedule_sha256",
    "card_id",
    "case_id",
    "request_sha256",
    "trace_id_sha256",
    "answer_sha256",
    "answer_utf8_bytes",
    "citation_count",
    "citations_sha256",
    "citations_utf8_bytes",
    "attempt_number",
    "ciphertext_sha256",
})
_CITATION_FIELDS = frozenset({
    "doc_id", "page_no", "file_name", "file_goc", "version_no", "score",
    "trang", "source_id",
})


class _DataBlob(ctypes.Structure):
    _fields_ = [
        ("cbData", wintypes.DWORD),
        ("pbData", ctypes.POINTER(ctypes.c_ubyte)),
    ]


def _sha256(raw: bytes | bytearray | memoryview) -> str:
    return hashlib.sha256(raw).hexdigest()


def _digest(value: object, *, length: int = 64) -> bool:
    return (
        isinstance(value, str)
        and len(value) == length
        and not set(value) - set("0123456789abcdef")
    )


def _canonical(value: object) -> bytes:
    return json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":"),
    ).encode("utf-8")


def _read_capture_bytes(path: str | Path) -> bytes:
    with Path(path).open("rb") as stream:
        raw = stream.read(MAX_CAPTURE_ARTIFACT_BYTES + 1)
    if len(raw) > MAX_CAPTURE_ARTIFACT_BYTES:
        raise ValueError("capture_artifact_too_large")
    return raw


def _inside(path: Path, root: Path) -> bool:
    try:
        path.resolve().relative_to(root.resolve())
    except ValueError:
        return False
    return True


def _strict_json(raw: bytes) -> object:
    def pairs(values):
        result = {}
        for key, value in values:
            if key in result:
                raise ValueError("capture_json_duplicate_field")
            result[key] = value
        return result

    try:
        value = json.loads(raw.decode("utf-8"), object_pairs_hook=pairs)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError("capture_json_invalid") from exc
    return value


def _strict_object(raw: bytes) -> dict:
    value = _strict_json(raw)
    if not isinstance(value, dict):
        raise ValueError("capture_json_invalid")
    return value


def _canonical_citations(value: object) -> tuple[tuple[dict, ...], bytearray]:
    if not isinstance(value, (list, tuple)) or len(value) > MAX_CITATION_COUNT:
        raise ValueError("capture_citations_invalid")
    normalized = []
    for item in value:
        if not isinstance(item, Mapping) or set(item) != _CITATION_FIELDS:
            raise ValueError("capture_citations_invalid")
        doc_id = item.get("doc_id")
        page_no = item.get("page_no")
        file_name = item.get("file_name")
        version_no = item.get("version_no")
        score = item.get("score")
        source_id = item.get("source_id")
        valid = all((
            type(doc_id) is int and doc_id > 0,
            type(page_no) is int and page_no > 0,
            item.get("trang") == page_no,
            isinstance(file_name, str) and 0 < len(file_name) <= 4096,
            item.get("file_goc") == file_name,
            version_no is None or (
                type(version_no) is int and version_no > 0
            ) or (
                isinstance(version_no, str) and 0 < len(version_no) <= 128
            ),
            score is None or (
                type(score) in {int, float} and math.isfinite(score)
            ),
            isinstance(source_id, str)
            and source_id == f"D{doc_id}P{page_no}",
        ))
        if not valid:
            raise ValueError("capture_citations_invalid")
        normalized.append({key: item[key] for key in sorted(_CITATION_FIELDS)})
    raw = bytearray(_canonical(normalized))
    if len(raw) > MAX_CITATIONS_BYTES:
        raw[:] = b"\0" * len(raw)
        raise ValueError("capture_citations_invalid")
    return tuple(normalized), raw


def _utf8_bytes_valid(raw: bytes | bytearray | memoryview) -> bool:
    view = memoryview(raw).cast("B")
    index = 0
    try:
        while index < len(view):
            first = view[index]
            if first <= 0x7F:
                index += 1
                continue
            if 0xC2 <= first <= 0xDF:
                if index + 1 >= len(view) or view[index + 1] & 0xC0 != 0x80:
                    return False
                index += 2
                continue
            if first == 0xE0:
                if (
                    index + 2 >= len(view)
                    or not 0xA0 <= view[index + 1] <= 0xBF
                    or view[index + 2] & 0xC0 != 0x80
                ):
                    return False
                index += 3
                continue
            if 0xE1 <= first <= 0xEC or 0xEE <= first <= 0xEF:
                if (
                    index + 2 >= len(view)
                    or view[index + 1] & 0xC0 != 0x80
                    or view[index + 2] & 0xC0 != 0x80
                ):
                    return False
                index += 3
                continue
            if first == 0xED:
                if (
                    index + 2 >= len(view)
                    or not 0x80 <= view[index + 1] <= 0x9F
                    or view[index + 2] & 0xC0 != 0x80
                ):
                    return False
                index += 3
                continue
            if first == 0xF0:
                if (
                    index + 3 >= len(view)
                    or not 0x90 <= view[index + 1] <= 0xBF
                    or view[index + 2] & 0xC0 != 0x80
                    or view[index + 3] & 0xC0 != 0x80
                ):
                    return False
                index += 4
                continue
            if 0xF1 <= first <= 0xF3:
                if (
                    index + 3 >= len(view)
                    or view[index + 1] & 0xC0 != 0x80
                    or view[index + 2] & 0xC0 != 0x80
                    or view[index + 3] & 0xC0 != 0x80
                ):
                    return False
                index += 4
                continue
            if first == 0xF4:
                if (
                    index + 3 >= len(view)
                    or not 0x80 <= view[index + 1] <= 0x8F
                    or view[index + 2] & 0xC0 != 0x80
                    or view[index + 3] & 0xC0 != 0x80
                ):
                    return False
                index += 4
                continue
            return False
        return True
    finally:
        view.release()


def _blob(buffer: bytearray | bytes) -> tuple[_DataBlob, object]:
    array_type = ctypes.c_ubyte * len(buffer)
    array = (
        array_type.from_buffer(buffer)
        if isinstance(buffer, bytearray)
        else array_type.from_buffer_copy(buffer)
    )
    return _DataBlob(len(buffer), array), array


def _dpapi(
    function_name: str, raw: bytearray | bytes, *, mutable_output: bool = False,
) -> bytes | bytearray:
    if os.name != "nt":
        raise RuntimeError("windows_dpapi_required")
    crypt32 = ctypes.WinDLL("crypt32", use_last_error=True)
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.LocalFree.argtypes = (wintypes.HLOCAL,)
    kernel32.LocalFree.restype = wintypes.HLOCAL
    source, source_buffer = _blob(raw)
    output = _DataBlob()
    function = getattr(crypt32, function_name)
    function.argtypes = (
        ctypes.POINTER(_DataBlob), wintypes.LPCWSTR,
        ctypes.POINTER(_DataBlob), wintypes.LPVOID, wintypes.LPVOID,
        wintypes.DWORD, ctypes.POINTER(_DataBlob),
    )
    function.restype = wintypes.BOOL
    description = _DESCRIPTION if function_name == "CryptProtectData" else None
    succeeded = function(
        ctypes.byref(source), description, None, None, None,
        _CRYPTPROTECT_UI_FORBIDDEN, ctypes.byref(output),
    )
    try:
        if not succeeded:
            raise RuntimeError("windows_dpapi_operation_failed")
        view = (ctypes.c_ubyte * output.cbData).from_address(
            ctypes.addressof(output.pbData.contents),
        )
        return bytearray(view) if mutable_output else bytes(view)
    finally:
        if output.pbData:
            ctypes.memset(output.pbData, 0, output.cbData)
            kernel32.LocalFree(output.pbData)
        del source_buffer


def dpapi_protect_current_user(plaintext: bytearray) -> bytes:
    """Protect one mutable plaintext buffer for the current Windows user."""
    ciphertext = _dpapi("CryptProtectData", plaintext)
    if not isinstance(ciphertext, bytes):
        raise RuntimeError("windows_dpapi_ciphertext_buffer_invalid")
    return ciphertext


def dpapi_unprotect_current_user(ciphertext: bytes) -> bytearray:
    """Decrypt one DPAPI blob into a caller-owned mutable buffer."""
    plaintext = _dpapi(
        "CryptUnprotectData", ciphertext, mutable_output=True,
    )
    if not isinstance(plaintext, bytearray):
        raise RuntimeError("windows_dpapi_plaintext_buffer_invalid")
    return plaintext


def restrict_directory_acl(path: Path) -> None:
    """Restrict a capture directory to the current user and LocalSystem."""
    if os.name != "nt":
        raise RuntimeError("windows_acl_required")
    try:
        identity = subprocess.check_output(
            ["whoami", "/user", "/fo", "csv", "/nh"],
            text=True,
            encoding="utf-8",
            errors="strict",
            stderr=subprocess.DEVNULL,
        )
        row = next(csv.reader([identity.strip()]))
        sid = row[1].strip()
        if not sid.startswith("S-1-"):
            raise ValueError
        subprocess.run(
            [
                "icacls", str(path), "/inheritance:r", "/grant:r",
                f"*{sid}:(OI)(CI)F", "*S-1-5-18:(OI)(CI)F",
            ],
            check=True,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
    except (OSError, ValueError, IndexError, subprocess.CalledProcessError) as exc:
        raise RuntimeError("capture_acl_restriction_failed") from exc


def prepare_capture_directory(
    path: str | Path, *, source_root: str | Path,
    acl_setter: Callable[[Path], None] = restrict_directory_acl,
) -> Path:
    """Create one fresh capture directory below the checkout's `.local`."""
    target = Path(path).resolve()
    local = Path(source_root).resolve() / ".local"
    if not _inside(target, local):
        raise ValueError("capture_directory_outside_dot_local")
    try:
        target.mkdir(parents=True, exist_ok=False)
    except FileExistsError as exc:
        raise ValueError("capture_directory_not_fresh") from exc
    try:
        acl_setter(target)
    except Exception:
        target.rmdir()
        raise
    return target


def review_capture_cards(schedule: Mapping[str, object]) -> frozenset[str]:
    """Validate and return the exact pre-frozen 20-card review sample."""
    cards = schedule.get("cards")
    selected_ids = schedule.get("review_capture_card_ids")
    if not isinstance(cards, list) or not isinstance(selected_ids, list):
        raise ValueError("review_capture_sample_invalid")
    selected = [
        card for card in cards
        if isinstance(card, dict) and card.get("review_capture_required") is True
    ]
    case_counts = Counter(card.get("case_id") for card in selected)
    actual_ids = [card.get("card_id") for card in selected]
    if not all((
        len(cards) == 100,
        len(selected) == BASELINE_REVIEW_CAPTURE_COUNT,
        len(selected_ids) == len(set(selected_ids)) == (
            BASELINE_REVIEW_CAPTURE_COUNT
        ),
        selected_ids == actual_ids,
        len(case_counts) == 10,
        set(case_counts.values()) == {2},
        all(isinstance(value, str) and value for value in actual_ids),
        all(_digest(card.get("request_sha256")) for card in selected),
    )):
        raise ValueError("review_capture_sample_invalid")
    return frozenset(selected_ids)


def _exclusive_atomic_bytes(path: Path, raw: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + ".tmp")
    try:
        with temporary.open("xb") as stream:
            stream.write(raw)
            stream.flush()
            os.fsync(stream.fileno())
        os.rename(temporary, path)
    except FileExistsError as exc:
        raise ValueError("capture_artifact_already_exists") from exc
    finally:
        temporary.unlink(missing_ok=True)


def _base_metadata(
    *, source_commit: str, pilot_draft_sha256: str,
    consolidated_launch_draft_sha256: str,
    pilot_authorization_sha256: str, schedule_sha256: str,
    card: Mapping[str, object], trace_id: str, answer: bytearray,
    citations_raw: bytearray, citation_count: int,
) -> dict:
    if card.get("review_capture_required") is not True:
        raise ValueError("capture_card_not_selected")
    if len(answer) > MAX_ANSWER_BYTES:
        raise ValueError("capture_answer_too_large")
    if not _utf8_bytes_valid(answer):
        raise ValueError("capture_answer_utf8_invalid")
    metadata = {
        "schema": CAPTURE_SCHEMA,
        "source_commit": source_commit,
        "review_capture_design_sha256": REVIEW_CAPTURE_DESIGN_SHA256,
        "pilot_draft_sha256": pilot_draft_sha256,
        "consolidated_launch_draft_sha256": (
            consolidated_launch_draft_sha256
        ),
        "pilot_authorization_sha256": pilot_authorization_sha256,
        "schedule_sha256": schedule_sha256,
        "card_id": card.get("card_id"),
        "case_id": card.get("case_id"),
        "request_sha256": card.get("request_sha256"),
        "trace_id_sha256": _sha256(trace_id.encode("utf-8")),
        "answer_sha256": _sha256(answer),
        "answer_utf8_bytes": len(answer),
        "citation_count": citation_count,
        "citations_sha256": _sha256(citations_raw),
        "citations_utf8_bytes": len(citations_raw),
        "attempt_number": 1,
    }
    valid = all((
        _digest(source_commit, length=40),
        _digest(pilot_draft_sha256),
        _digest(consolidated_launch_draft_sha256),
        _digest(pilot_authorization_sha256),
        _digest(schedule_sha256),
        _digest(metadata["request_sha256"]),
        isinstance(metadata["card_id"], str),
        re.fullmatch(r"query-pilot-\d{3}", str(metadata["card_id"])),
        isinstance(metadata["case_id"], str) and bool(metadata["case_id"]),
        isinstance(trace_id, str) and bool(trace_id),
    ))
    if not valid:
        raise ValueError("capture_binding_invalid")
    return metadata


def _load_capture_artifact(
    path: str | Path, *, expected: Mapping[str, object],
) -> tuple[bytes, dict]:
    raw = _read_capture_bytes(path)
    artifact = _strict_object(raw)
    if set(artifact) != _METADATA_FIELDS | {"ciphertext_base64"}:
        raise ValueError("capture_fields_invalid")
    if raw != _canonical(artifact) + b"\n":
        raise ValueError("capture_artifact_not_canonical")
    metadata = {name: artifact[name] for name in _METADATA_FIELDS}
    if metadata != dict(expected) or metadata.get("schema") != CAPTURE_SCHEMA:
        raise ValueError("capture_binding_mismatch")
    try:
        ciphertext = base64.b64decode(
            artifact["ciphertext_base64"], validate=True,
        )
    except (TypeError, ValueError) as exc:
        raise ValueError("capture_ciphertext_invalid") from exc
    if _sha256(ciphertext) != metadata["ciphertext_sha256"]:
        raise ValueError("capture_ciphertext_mismatch")
    return ciphertext, metadata


def _discard_invalid_capture(path: Path) -> None:
    try:
        path.unlink(missing_ok=True)
        return
    except OSError:
        pass
    try:
        os.replace(path, path.with_name(path.name + ".invalid"))
    except OSError:
        pass


def capture_answer(
    *, capture_dir: str | Path, source_commit: str,
    pilot_draft_sha256: str, consolidated_launch_draft_sha256: str,
    pilot_authorization_sha256: str,
    schedule_sha256: str, card: Mapping[str, object], trace_id: str,
    answer: bytearray, citations: tuple[Mapping[str, object], ...] = (),
    protect: Callable[[bytearray], bytes] = dpapi_protect_current_user,
    unprotect: Callable[[bytes], bytearray] = dpapi_unprotect_current_user,
) -> dict:
    """Persist one selected answer and its citations as ciphertext only."""
    requested_target = Path(capture_dir)
    if requested_target.is_symlink():
        raise ValueError("capture_directory_invalid")
    target = requested_target.resolve()
    try:
        entries = list(target.iterdir())
    except OSError as exc:
        raise ValueError("capture_directory_invalid") from exc
    if target.is_symlink() or any(
        entry.is_symlink()
        or not entry.is_file()
        or re.fullmatch(r"query-pilot-\d{3}\.capture\.json", entry.name) is None
        for entry in entries
    ):
        raise ValueError("capture_directory_invalid")
    existing = entries
    if len(existing) >= MAX_CAPTURE_COUNT:
        raise ValueError("capture_count_exceeded")
    normalized_citations, citations_raw = _canonical_citations(citations)
    try:
        metadata = _base_metadata(
            source_commit=source_commit,
            pilot_draft_sha256=pilot_draft_sha256,
            consolidated_launch_draft_sha256=consolidated_launch_draft_sha256,
            pilot_authorization_sha256=pilot_authorization_sha256,
            schedule_sha256=schedule_sha256,
            card=card,
            trace_id=trace_id,
            answer=answer,
            citations_raw=citations_raw,
            citation_count=len(normalized_citations),
        )
        plaintext = bytearray(_canonical(metadata))
        plaintext.extend(b"\n")
        plaintext.extend(answer)
        plaintext.extend(citations_raw)
        try:
            ciphertext = protect(plaintext)
        finally:
            plaintext[:] = b"\0" * len(plaintext)
    finally:
        citations_raw[:] = b"\0" * len(citations_raw)
    if not isinstance(ciphertext, bytes) or not ciphertext:
        raise ValueError("capture_encryption_invalid")
    complete = {**metadata, "ciphertext_sha256": _sha256(ciphertext)}
    artifact = {
        **complete,
        "ciphertext_base64": base64.b64encode(ciphertext).decode("ascii"),
    }
    artifact_raw = _canonical(artifact) + b"\n"
    if len(artifact_raw) > MAX_CAPTURE_ARTIFACT_BYTES:
        raise ValueError("capture_artifact_too_large")
    artifact_path = target / f"{metadata['card_id']}.capture.json"
    _exclusive_atomic_bytes(
        artifact_path,
        artifact_raw,
    )
    recovered = None
    try:
        try:
            persisted_raw = _read_capture_bytes(artifact_path)
            persisted = _strict_object(persisted_raw)
        except (OSError, ValueError) as exc:
            raise ValueError("capture_persistence_verification_failed") from exc
        if persisted_raw != artifact_raw or persisted != artifact:
            raise ValueError("capture_persistence_verification_failed")
        recovered, recovered_citations, recovered_metadata = load_captured_review(
            artifact_path, expected=complete, unprotect=unprotect,
        )
        if not all((
            recovered == answer,
            recovered_citations == normalized_citations,
            recovered_metadata == complete,
        )):
            raise ValueError("capture_roundtrip_verification_failed")
    except (OSError, RuntimeError, ValueError) as exc:
        _discard_invalid_capture(artifact_path)
        if isinstance(exc, ValueError) and str(exc) == (
            "capture_persistence_verification_failed"
        ):
            raise
        raise ValueError("capture_roundtrip_verification_failed") from exc
    finally:
        if recovered is not None:
            recovered[:] = b"\0" * len(recovered)
    return complete


def load_captured_review(
    path: str | Path, *, expected: Mapping[str, object],
    unprotect: Callable[[bytes], bytearray] = dpapi_unprotect_current_user,
) -> tuple[bytearray, tuple[dict, ...], dict]:
    """Validate and decrypt one answer/citation capture in memory."""
    ciphertext, metadata = _load_capture_artifact(path, expected=expected)
    plaintext = unprotect(ciphertext)
    if not isinstance(plaintext, bytearray):
        raise ValueError("capture_plaintext_buffer_invalid")
    try:
        separator = plaintext.find(b"\n")
        if separator < 0:
            raise ValueError("capture_plaintext_invalid")
        inner = _strict_object(bytes(memoryview(plaintext)[:separator]))
        expected_inner = {
            key: value for key, value in metadata.items()
            if key != "ciphertext_sha256"
        }
        if inner != expected_inner:
            raise ValueError("capture_inner_binding_mismatch")
        answer_start = separator + 1
        answer_length = metadata.get("answer_utf8_bytes")
        if type(answer_length) is not int or not 0 <= answer_length <= (
            MAX_ANSWER_BYTES
        ):
            raise ValueError("capture_answer_mismatch")
        answer_end = answer_start + answer_length
        if answer_end > len(plaintext):
            raise ValueError("capture_plaintext_invalid")
        answer_view = memoryview(plaintext)[answer_start:answer_end]
        if not _utf8_bytes_valid(answer_view):
            raise ValueError("capture_answer_utf8_invalid")
        if (
            len(answer_view) != metadata["answer_utf8_bytes"]
            or _sha256(answer_view) != metadata["answer_sha256"]
        ):
            raise ValueError("capture_answer_mismatch")
        citations_view = memoryview(plaintext)[answer_end:]
        if (
            len(citations_view) != metadata.get("citations_utf8_bytes")
            or len(citations_view) > MAX_CITATIONS_BYTES
            or _sha256(citations_view) != metadata.get("citations_sha256")
        ):
            raise ValueError("capture_citations_mismatch")
        citations_value = _strict_json(bytes(citations_view))
        citations, citations_raw = _canonical_citations(citations_value)
        try:
            if (
                citations_raw != citations_view
                or len(citations) != metadata.get("citation_count")
            ):
                raise ValueError("capture_citations_mismatch")
        finally:
            citations_raw[:] = b"\0" * len(citations_raw)
        recovered = bytearray(answer_view)
        del citations_view, answer_view
        return recovered, citations, metadata
    finally:
        plaintext[:] = b"\0" * len(plaintext)


def load_captured_answer(
    path: str | Path, *, expected: Mapping[str, object],
    unprotect: Callable[[bytes], bytearray] = dpapi_unprotect_current_user,
) -> tuple[bytearray, dict]:
    """Compatibility loader that returns only answer bytes and metadata."""
    answer, _citations, metadata = load_captured_review(
        path, expected=expected, unprotect=unprotect,
    )
    return answer, metadata


def validate_capture_chain(
    capture_dir: str | Path, *, expected: tuple[Mapping[str, object], ...],
) -> tuple[dict, ...]:
    """Validate the exact accumulated ciphertext chain before dispatch."""
    if not isinstance(expected, tuple) or len(expected) > MAX_CAPTURE_COUNT:
        raise ValueError("capture_chain_invalid")
    requested_target = Path(capture_dir)
    if requested_target.is_symlink():
        raise ValueError("capture_chain_invalid")
    target = requested_target.resolve()
    try:
        entries = list(target.iterdir())
    except OSError as exc:
        raise ValueError("capture_chain_invalid") from exc
    card_ids = [item.get("card_id") for item in expected]
    expected_names = {
        f"{card_id}.capture.json" for card_id in card_ids
        if isinstance(card_id, str)
    }
    if not all((
        len(card_ids) == len(set(card_ids)) == len(expected_names),
        len(entries) == len(expected_names),
        {entry.name for entry in entries} == expected_names,
        all(not entry.is_symlink() and entry.is_file() for entry in entries),
    )):
        raise ValueError("capture_chain_invalid")
    validated = []
    try:
        for item in expected:
            card_id = item.get("card_id")
            ciphertext, metadata = _load_capture_artifact(
                target / f"{card_id}.capture.json", expected=item,
            )
            del ciphertext
            validated.append(metadata)
    except (OSError, TypeError, ValueError) as exc:
        raise ValueError("capture_chain_invalid") from exc
    return tuple(validated)


__all__ = [
    "CAPTURE_SCHEMA",
    "BASELINE_REVIEW_CAPTURE_COUNT",
    "MAX_ANSWER_BYTES",
    "MAX_CAPTURE_ARTIFACT_BYTES",
    "MAX_CAPTURE_COUNT",
    "MAX_CITATION_COUNT",
    "MAX_CITATIONS_BYTES",
    "REVIEW_CAPTURE_DESIGN_SHA256",
    "capture_answer",
    "dpapi_protect_current_user",
    "dpapi_unprotect_current_user",
    "load_captured_answer",
    "load_captured_review",
    "prepare_capture_directory",
    "restrict_directory_acl",
    "review_capture_cards",
    "validate_capture_chain",
]
