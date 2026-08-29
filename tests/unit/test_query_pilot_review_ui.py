"""Local Query pilot owner-review UI boundary."""

import hashlib
from pathlib import Path

import pytest

from scripts.ops import query_pilot_review_ui as review_ui
from scripts.ops.query_pilot_review_artifacts import REVIEW_PACK_SCHEMA


class _FakeVar:
    def __init__(self, value=None):
        self.value = value if value is not None else ""

    def get(self):
        return self.value

    def set(self, value):
        self.value = value


class _FakeWidget:
    def __init__(self, *_args, **_kwargs):
        self.command = None

    def bind(self, *_args, **_kwargs):
        return None

    def columnconfigure(self, *_args, **_kwargs):
        return None

    def configure(self, **kwargs):
        self.command = kwargs.get("command", self.command)

    def delete(self, *_args, **_kwargs):
        return None

    def focus_set(self):
        return None

    def grid(self, *_args, **_kwargs):
        return None

    def insert(self, *_args, **_kwargs):
        return None

    def rowconfigure(self, *_args, **_kwargs):
        return None


class _FakeRoot(_FakeWidget):
    def __init__(self, state):
        super().__init__()
        self.state = state
        self.destroyed = False

    def destroy(self):
        self.destroyed = True

    def geometry(self, _value):
        return None

    def mainloop(self):
        button = self.state["button"]
        for _ in range(2):
            for variable in self.state["boolean_vars"]:
                variable.set(True)
            blank = [
                variable for variable in self.state["string_vars"]
                if variable.get() == ""
            ]
            blank[0].set("accepted")
            blank[1].set("pass")
            button.command()
        assert self.destroyed is True

    def minsize(self, *_args):
        return None

    def protocol(self, *_args):
        return None

    def title(self, _value):
        return None


def _install_fake_tk(monkeypatch):
    state = {
        "boolean_vars": [],
        "string_vars": [],
    }

    def variable_factory(kind):
        def build(value=None):
            variable = _FakeVar(value)
            state[kind].append(variable)
            return variable
        return build

    def button_factory(*args, **kwargs):
        button = _FakeWidget(*args, **kwargs)
        state["button"] = button
        return button

    monkeypatch.setattr(review_ui.tk, "Tk", lambda: _FakeRoot(state))
    monkeypatch.setattr(
        review_ui.tk, "StringVar", variable_factory("string_vars"),
    )
    monkeypatch.setattr(
        review_ui.tk, "BooleanVar", variable_factory("boolean_vars"),
    )
    monkeypatch.setattr(review_ui.tk, "Text", _FakeWidget)
    for name in (
        "Checkbutton", "Combobox", "Entry", "Frame", "Label", "LabelFrame",
        "Radiobutton",
    ):
        monkeypatch.setattr(review_ui.ttk, name, _FakeWidget)
    monkeypatch.setattr(review_ui.ttk, "Button", button_factory)
    return state


def test_review_ui_rejects_paths_outside_dot_local_before_opening_window(
    tmp_path: Path,
):
    with pytest.raises(ValueError, match="review_ui_path_invalid"):
        review_ui.run_review_ui(
            source_root=tmp_path,
            pack_path=tmp_path / "outside-pack.json",
            manifest_path=tmp_path / "manifest.jsonl",
            authorization_path=tmp_path / ".local" / "authorization.json",
            schedule_path=tmp_path / ".local" / "schedule.json",
            wal_path=tmp_path / ".local" / "run" / "pilot.wal.jsonl",
            trace_path=tmp_path / ".local" / "run" / "trace.jsonl",
            capture_dir=tmp_path / ".local" / "run" / "review-captures",
            review_result_path=tmp_path / ".local" / "run" / "review-result.json",
            deletion_receipt_path=(
                tmp_path / ".local" / "run" / "deletion-receipt.json"
            ),
        )


def test_review_ui_rejects_artifacts_from_different_run_roots(tmp_path: Path):
    local = tmp_path / ".local"
    with pytest.raises(ValueError, match="review_ui_path_invalid"):
        review_ui.run_review_ui(
            source_root=tmp_path,
            pack_path=local / "run-a" / "review-pack.json",
            manifest_path=tmp_path / "manifest.jsonl",
            authorization_path=local / "authorization.json",
            schedule_path=local / "schedule.json",
            wal_path=local / "run-a" / "pilot.wal.jsonl",
            trace_path=local / "run-a" / "trace.jsonl",
            capture_dir=local / "run-b" / "review-captures",
            review_result_path=local / "run-a" / "review-result.json",
            deletion_receipt_path=local / "run-a" / "deletion-receipt.json",
        )


def test_review_ui_cli_fails_silently_without_plaintext_or_stacktrace(
    tmp_path: Path, capsys,
):
    result = review_ui.main([
        "--source-root", str(tmp_path),
        "--pack", str(tmp_path / "outside-pack.json"),
        "--manifest", str(tmp_path / "manifest.jsonl"),
        "--authorization", str(tmp_path / ".local" / "authorization.json"),
        "--schedule", str(tmp_path / ".local" / "schedule.json"),
        "--wal", str(tmp_path / ".local" / "run" / "pilot.wal.jsonl"),
        "--trace", str(tmp_path / ".local" / "run" / "trace.jsonl"),
        "--capture-dir", str(tmp_path / ".local" / "run" / "captures"),
        "--review-result", str(tmp_path / ".local" / "run" / "result.json"),
        "--deletion-receipt", str(
            tmp_path / ".local" / "run" / "receipt.json"
        ),
    ])

    assert result == 1
    assert capsys.readouterr() == ("", "")


def test_review_ui_completes_two_items_and_zeroes_plaintext(
    tmp_path: Path, monkeypatch,
):
    state = _install_fake_tk(monkeypatch)
    local = tmp_path / ".local" / "run"
    local.mkdir(parents=True)
    manifest = tmp_path / "manifest.jsonl"
    manifest.write_text("synthetic", encoding="utf-8")
    trace_hashes = ("1" * 64, "2" * 64)
    items = [
        {
            "card_id": f"query-pilot-{index + 1:03d}",
            "case_id": f"complex-{index + 1:02d}",
            "trace_id_sha256": trace_hashes[index],
            "review_mode": "encrypted_answer",
        }
        for index in range(2)
    ]
    pack = {
        "schema": REVIEW_PACK_SCHEMA,
        "source_commit": "a" * 40,
        "review_capture_design_sha256": "b" * 64,
        "consolidated_launch_draft_sha256": "f" * 64,
        "pilot_authorization_sha256": "c" * 64,
        "schedule_sha256": "d" * 64,
        "pilot_run_root": ".local/run",
        "wal_sha256": "6" * 64,
        "trace_artifact_sha256": "7" * 64,
        "review_tool_sha256": {"scripts/ops/review.py": "8" * 64},
        "manifest_sha256": hashlib.sha256(b"synthetic").hexdigest(),
        "reviewer": "bao.nguyen",
        "review_item_count": 2,
        "items": items,
    }
    buffers = []
    written = []

    monkeypatch.setattr(
        review_ui,
        "validate_review_pack_from_paths",
        lambda *_args, **_kwargs: pack,
    )
    monkeypatch.setattr(
        review_ui, "validate_authorized_source", lambda *_args: None,
    )
    monkeypatch.setattr(
        review_ui, "validate_review_tool_hashes", lambda *_args: None,
    )
    monkeypatch.setattr(
        review_ui,
        "load_manifest_questions",
        lambda *_args, **_kwargs: {
            "complex-01": "private question one",
            "complex-02": "private question two",
        },
    )

    def load_content(_pack, item, **_kwargs):
        buffer = bytearray(f"private {item['card_id']}".encode())
        buffers.append(buffer)
        return "private question", buffer, ()

    monkeypatch.setattr(
        review_ui, "load_review_item_content_with_citations", load_content,
    )
    monkeypatch.setattr(
        review_ui,
        "write_metadata_artifact",
        lambda path, value: written.append((path, value)) or "e" * 64,
    )
    monkeypatch.setattr(
        review_ui,
        "delete_review_captures",
        lambda *_args, **_kwargs: {
            "schema": "synthetic-receipt-v1",
            "review_result_sha256": "e" * 64,
        },
    )
    monkeypatch.setattr(review_ui.messagebox, "showinfo", lambda *_args: None)

    result = review_ui.main([
        "--source-root", str(tmp_path),
        "--pack", str(local / "review-pack.json"),
        "--manifest", str(manifest),
        "--authorization", str(local / "pilot-authorization.json"),
        "--schedule", str(local / "schedule.json"),
        "--wal", str(local / "pilot.wal.jsonl"),
        "--trace", str(local / "trace.jsonl"),
        "--capture-dir", str(local / "review-captures"),
        "--review-result", str(local / "review-result.json"),
        "--deletion-receipt", str(local / "deletion-receipt.json"),
    ])

    assert result == 0
    assert len(written) == 2
    assert [Path(path).name for path, _value in written] == [
        "review-result.json",
        "deletion-receipt.json",
    ]
    assert all(buffer == bytearray(len(buffer)) for buffer in buffers)
    assert state["button"].command is not None


def test_review_ui_fails_closed_when_deletion_receipt_write_fails(
    tmp_path: Path, monkeypatch, capsys,
):
    _install_fake_tk(monkeypatch)
    local = tmp_path / ".local" / "run"
    local.mkdir(parents=True)
    manifest = tmp_path / "manifest.jsonl"
    manifest.write_text("synthetic", encoding="utf-8")
    trace_hashes = ("1" * 64, "2" * 64)
    items = [
        {
            "card_id": f"query-pilot-{index + 1:03d}",
            "case_id": f"complex-{index + 1:02d}",
            "trace_id_sha256": trace_hashes[index],
            "review_mode": "encrypted_answer",
        }
        for index in range(2)
    ]
    pack = {
        "schema": REVIEW_PACK_SCHEMA,
        "source_commit": "a" * 40,
        "review_capture_design_sha256": "b" * 64,
        "consolidated_launch_draft_sha256": "f" * 64,
        "pilot_authorization_sha256": "c" * 64,
        "schedule_sha256": "d" * 64,
        "pilot_run_root": ".local/run",
        "wal_sha256": "6" * 64,
        "trace_artifact_sha256": "7" * 64,
        "review_tool_sha256": {"scripts/ops/review.py": "8" * 64},
        "manifest_sha256": hashlib.sha256(b"synthetic").hexdigest(),
        "reviewer": "bao.nguyen",
        "review_item_count": 2,
        "items": items,
    }
    write_attempts = []

    monkeypatch.setattr(
        review_ui,
        "validate_review_pack_from_paths",
        lambda *_args, **_kwargs: pack,
    )
    monkeypatch.setattr(
        review_ui, "validate_authorized_source", lambda *_args: None,
    )
    monkeypatch.setattr(
        review_ui, "validate_review_tool_hashes", lambda *_args: None,
    )
    monkeypatch.setattr(
        review_ui,
        "load_manifest_questions",
        lambda *_args, **_kwargs: {
            "complex-01": "private question one",
            "complex-02": "private question two",
        },
    )
    monkeypatch.setattr(
        review_ui,
        "load_review_item_content_with_citations",
        lambda _pack, item, **_kwargs: (
            "private question",
            bytearray(f"private {item['card_id']}".encode()),
            (),
        ),
    )
    monkeypatch.setattr(
        review_ui,
        "delete_review_captures",
        lambda *_args, **_kwargs: {
            "schema": "synthetic-receipt-v1",
            "review_result_sha256": "e" * 64,
        },
    )

    def fail_receipt_write(path, _value):
        write_attempts.append(Path(path).name)
        if Path(path).name == "deletion-receipt.json":
            raise ValueError("synthetic receipt write failure")
        return "e" * 64

    monkeypatch.setattr(review_ui, "write_metadata_artifact", fail_receipt_write)
    monkeypatch.setattr(review_ui.messagebox, "showerror", lambda *_args: None)

    result = review_ui.main([
        "--source-root", str(tmp_path),
        "--pack", str(local / "review-pack.json"),
        "--manifest", str(manifest),
        "--authorization", str(local / "pilot-authorization.json"),
        "--schedule", str(local / "schedule.json"),
        "--wal", str(local / "pilot.wal.jsonl"),
        "--trace", str(local / "trace.jsonl"),
        "--capture-dir", str(local / "review-captures"),
        "--review-result", str(local / "review-result.json"),
        "--deletion-receipt", str(local / "deletion-receipt.json"),
    ])

    assert result == 1
    assert write_attempts == ["review-result.json", "deletion-receipt.json"]
    assert capsys.readouterr() == ("", "")
