import hashlib
import json
from pathlib import Path

import pytest

from scripts.ops import query_pilot_capture_lifecycle as lifecycle


def _capture_set(tmp_path: Path) -> tuple[Path, dict[str, str]]:
    capture_dir = tmp_path / "review-captures"
    capture_dir.mkdir()
    expected = {}
    for index in (1, 2):
        path = capture_dir / f"query-pilot-{index:03d}.capture.json"
        raw = f"ciphertext-{index}".encode()
        path.write_bytes(raw)
        expected[path.name] = hashlib.sha256(raw).hexdigest()
    return capture_dir, expected


def test_delete_capture_set_resumes_after_partial_unlink(tmp_path, monkeypatch):
    capture_dir, expected = _capture_set(tmp_path)
    journal_path = tmp_path / "capture-deletion.journal.json"
    real_unlink = Path.unlink
    failed_once = False

    def interrupted_unlink(path, *args, **kwargs):
        nonlocal failed_once
        if not failed_once and path.name.endswith(".capture.json"):
            failed_once = True
            real_unlink(path, *args, **kwargs)
            raise OSError("synthetic interruption")
        return real_unlink(path, *args, **kwargs)

    monkeypatch.setattr(Path, "unlink", interrupted_unlink)
    with pytest.raises(OSError, match="synthetic interruption"):
        lifecycle.delete_capture_set(
            capture_dir=capture_dir,
            journal_path=journal_path,
            operation="owner_review",
            binding_sha256="a" * 64,
            expected_files=expected,
        )

    prepared = json.loads(journal_path.read_text(encoding="utf-8"))
    assert prepared["status"] == "prepared"
    assert (tmp_path / "review-captures.quarantine").exists()

    monkeypatch.setattr(Path, "unlink", real_unlink)
    finalized, journal_sha256 = lifecycle.delete_capture_set(
        capture_dir=capture_dir,
        journal_path=journal_path,
        operation="owner_review",
        binding_sha256="a" * 64,
        expected_files=expected,
    )

    assert finalized["status"] == "finalized"
    assert len(journal_sha256) == 64
    assert list(capture_dir.iterdir()) == []
    assert not (tmp_path / "review-captures.quarantine").exists()


def test_finalized_deletion_refuses_reintroduced_ciphertext(tmp_path):
    capture_dir, expected = _capture_set(tmp_path)
    journal_path = tmp_path / "capture-deletion.journal.json"
    lifecycle.delete_capture_set(
        capture_dir=capture_dir,
        journal_path=journal_path,
        operation="terminal_cleanup",
        binding_sha256="b" * 64,
        expected_files=expected,
    )
    (capture_dir / "query-pilot-999.capture.json").write_bytes(b"reintroduced")

    with pytest.raises(ValueError, match="capture_deletion_incomplete"):
        lifecycle.delete_capture_set(
            capture_dir=capture_dir,
            journal_path=journal_path,
            operation="terminal_cleanup",
            binding_sha256="b" * 64,
            expected_files=expected,
        )
