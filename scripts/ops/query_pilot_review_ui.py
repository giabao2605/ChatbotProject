"""Local in-memory owner review UI for encrypted Query pilot captures."""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
import tkinter as tk
from tkinter import messagebox, ttk

from scripts.ops.query_pilot_review_artifacts import (
    REJECTION_REASON_CODES,
    REVIEW_PACK_SCHEMA,
    REVIEW_RESULT_SCHEMA,
    delete_review_captures,
    finalize_review_result,
    load_manifest_questions,
    load_metadata_artifact,
    load_review_item_content_with_citations,
    review_label_valid,
    write_metadata_artifact,
)
from scripts.ops.query_pilot_review_pack import validate_review_pack_from_paths
from scripts.ops.query_pilot_review_integrity import (
    validate_authorized_source,
    validate_review_tool_hashes,
)
from scripts.ops.query_pilot_review_capture import _sha256


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(
        timespec="seconds",
    ).replace("+00:00", "Z")


def _generic_error() -> None:
    messagebox.showerror(
        "Review unavailable",
        "The review item could not be validated. This run remains unaccepted.",
    )


def _inside(path: Path, root: Path) -> bool:
    try:
        path.resolve().relative_to(root.resolve())
    except ValueError:
        return False
    return True


def _resume_pending_deletion(
    *, root: Path, pack_path: Path, authorization_path: Path,
    schedule_path: Path, wal_path: Path, trace_path: Path,
    capture_dir: Path, result_path: Path, receipt_path: Path,
) -> bool:
    if not result_path.exists():
        return False
    pack = load_metadata_artifact(pack_path, schema=REVIEW_PACK_SCHEMA)
    result = load_metadata_artifact(result_path, schema=REVIEW_RESULT_SCHEMA)
    validate_authorized_source(root, str(pack.get("source_commit")))
    validate_review_tool_hashes(root, pack.get("review_tool_sha256") or {})
    if not all((
        pack.get("pilot_authorization_sha256")
        == _sha256(authorization_path.read_bytes()),
        pack.get("schedule_sha256") == _sha256(schedule_path.read_bytes()),
        pack.get("wal_sha256") == _sha256(wal_path.read_bytes()),
        pack.get("trace_artifact_sha256") == _sha256(trace_path.read_bytes()),
    )):
        raise ValueError("review_resume_binding_invalid")
    receipt = delete_review_captures(
        capture_dir, pack=pack, review_result=result, deleted_at=_utc_now(),
        journal_path=pack_path.parent / "capture-deletion.journal.json",
    )
    if receipt.get("review_result_sha256") != _sha256(result_path.read_bytes()):
        raise ValueError("review_result_receipt_binding_invalid")
    write_metadata_artifact(receipt_path, receipt)
    return True


def run_review_ui(
    *, source_root: str | Path, pack_path: str | Path, manifest_path: str | Path,
    authorization_path: str | Path, schedule_path: str | Path,
    wal_path: str | Path, trace_path: str | Path, capture_dir: str | Path,
    review_result_path: str | Path, deletion_receipt_path: str | Path,
) -> None:
    """Review one validated item at a time without terminal plaintext output."""
    root_path = Path(source_root).resolve()
    local = root_path / ".local"
    pack_path = Path(pack_path).resolve()
    manifest_path = Path(manifest_path).resolve()
    authorization_path = Path(authorization_path).resolve()
    schedule_path = Path(schedule_path).resolve()
    wal_path = Path(wal_path).resolve()
    trace_path = Path(trace_path).resolve()
    capture_dir = Path(capture_dir).resolve()
    review_result_path = Path(review_result_path).resolve()
    deletion_receipt_path = Path(deletion_receipt_path).resolve()
    run_root = pack_path.parent
    if not all((
        _inside(pack_path, local),
        _inside(authorization_path, local),
        _inside(schedule_path, local),
        _inside(wal_path, local),
        _inside(trace_path, local), _inside(capture_dir, local),
        _inside(review_result_path, local),
        _inside(deletion_receipt_path, local),
        _inside(manifest_path, root_path),
        capture_dir == run_root / "review-captures",
        wal_path.parent == run_root,
        trace_path == run_root / "trace.jsonl",
        review_result_path.parent == run_root,
        deletion_receipt_path.parent == run_root,
        review_result_path != deletion_receipt_path,
        not deletion_receipt_path.exists(),
    )):
        raise ValueError("review_ui_path_invalid")
    if _resume_pending_deletion(
        root=root_path, pack_path=pack_path,
        authorization_path=authorization_path, schedule_path=schedule_path,
        wal_path=wal_path, trace_path=trace_path, capture_dir=capture_dir,
        result_path=review_result_path, receipt_path=deletion_receipt_path,
    ):
        return
    pack = validate_review_pack_from_paths(
        source_root=root_path,
        authorization_path=authorization_path,
        schedule_path=schedule_path,
        wal_path=wal_path,
        trace_path=trace_path,
        capture_dir=capture_dir,
        pack_path=pack_path,
    )
    if pack.get("schema") != REVIEW_PACK_SCHEMA:
        raise ValueError("review_pack_schema_invalid")
    validate_authorized_source(root_path, str(pack.get("source_commit")))
    validate_review_tool_hashes(root_path, pack.get("review_tool_sha256") or {})
    questions = load_manifest_questions(
        manifest_path,
        expected_sha256=str(pack.get("manifest_sha256") or ""),
    )
    items = pack.get("items")
    if not isinstance(items, list) or not items:
        raise ValueError("review_pack_items_invalid")

    root = tk.Tk()
    root.title("Query pilot owner review")
    root.geometry("900x720")
    root.minsize(720, 600)
    root.columnconfigure(0, weight=1)
    root.rowconfigure(1, weight=1)

    position = tk.StringVar()
    identity = tk.StringVar()
    evidence_summary = tk.StringVar()
    status = tk.StringVar(value="Review every field before continuing.")
    answer_correct = tk.BooleanVar()
    citation_correct = tk.BooleanVar()
    safety_correct = tk.BooleanVar()
    decision = tk.StringVar(value="")
    reason_code = tk.StringVar(value="")
    labels: list[dict] = []
    current_buffer = bytearray()
    index = 0
    finished = False

    header = ttk.Frame(root, padding=(20, 16, 20, 8))
    header.grid(row=0, column=0, sticky="ew")
    header.columnconfigure(0, weight=1)
    ttk.Label(
        header,
        text="Query pilot owner review",
        font=("Segoe UI", 18, "bold"),
    ).grid(row=0, column=0, sticky="w")
    ttk.Label(header, textvariable=position).grid(row=0, column=1, sticky="e")
    ttk.Label(header, textvariable=identity).grid(
        row=1, column=0, columnspan=2, sticky="w", pady=(4, 0),
    )
    ttk.Label(
        header, textvariable=evidence_summary, wraplength=850,
    ).grid(
        row=2, column=0, columnspan=2, sticky="w", pady=(4, 0),
    )

    content = ttk.Frame(root, padding=(20, 8))
    content.grid(row=1, column=0, sticky="nsew")
    content.columnconfigure(0, weight=1)
    content.rowconfigure(1, weight=1)
    content.rowconfigure(3, weight=2)
    content.rowconfigure(5, weight=1)
    ttk.Label(content, text="Question", font=("Segoe UI", 10, "bold")).grid(
        row=0, column=0, sticky="w",
    )
    question_view = tk.Text(
        content, height=4, wrap="word", font=("Segoe UI", 11),
        padx=10, pady=8, takefocus=False,
    )
    question_view.grid(row=1, column=0, sticky="nsew", pady=(4, 12))
    ttk.Label(
        content,
        text="Answer or deterministic refusal",
        font=("Segoe UI", 10, "bold"),
    ).grid(row=2, column=0, sticky="w")
    answer_view = tk.Text(
        content, height=14, wrap="word", font=("Segoe UI", 11),
        padx=10, pady=8, takefocus=False,
    )
    answer_view.grid(row=3, column=0, sticky="nsew", pady=(4, 0))
    ttk.Label(
        content, text="Structured citations", font=("Segoe UI", 10, "bold"),
    ).grid(row=4, column=0, sticky="w", pady=(10, 0))
    citation_view = tk.Text(
        content, height=7, wrap="word", font=("Segoe UI", 10),
        padx=10, pady=8, takefocus=False,
    )
    citation_view.grid(row=5, column=0, sticky="nsew", pady=(4, 0))
    for widget in (question_view, answer_view, citation_view):
        widget.bind("<<Copy>>", lambda _event: "break")
        widget.bind("<Control-c>", lambda _event: "break")
        widget.bind("<Control-C>", lambda _event: "break")

    form = ttk.LabelFrame(root, text="Owner labels", padding=16)
    form.grid(row=2, column=0, sticky="nsew", padx=20, pady=(4, 12))
    form.columnconfigure(0, weight=1)
    checks = ttk.Frame(form)
    checks.grid(row=0, column=0, sticky="w")
    ttk.Checkbutton(checks, text="Answer is correct", variable=answer_correct).grid(
        row=0, column=0, sticky="w", padx=(0, 20),
    )
    ttk.Checkbutton(
        checks, text="Citations are correct", variable=citation_correct,
    ).grid(row=0, column=1, sticky="w", padx=(0, 20))
    ttk.Checkbutton(checks, text="Safety is correct", variable=safety_correct).grid(
        row=0, column=2, sticky="w",
    )
    ttk.Label(form, text="Decision").grid(row=1, column=0, sticky="w", pady=(14, 4))
    decisions = ttk.Frame(form)
    decisions.grid(row=2, column=0, sticky="w")
    ttk.Radiobutton(
        decisions, text="Accepted", value="accepted", variable=decision,
    ).grid(row=0, column=0, sticky="w", padx=(0, 24))
    ttk.Radiobutton(
        decisions, text="Rejected", value="rejected", variable=decision,
    ).grid(row=0, column=1, sticky="w")
    ttk.Label(form, text="Reason code").grid(row=3, column=0, sticky="w", pady=(14, 4))
    reason_entry = ttk.Combobox(
        form,
        textvariable=reason_code,
        values=("pass", *sorted(REJECTION_REASON_CODES)),
        state="readonly",
    )
    reason_entry.grid(row=4, column=0, sticky="ew")
    ttk.Label(
        form,
        text="Use pass for accepted; choose the exact failed check for rejected.",
    ).grid(row=5, column=0, sticky="w", pady=(4, 0))

    footer = ttk.Frame(root, padding=(20, 0, 20, 20))
    footer.grid(row=3, column=0, sticky="ew")
    footer.columnconfigure(0, weight=1)
    status_label = ttk.Label(footer, textvariable=status)
    status_label.grid(row=0, column=0, sticky="w")
    next_button = ttk.Button(footer, text="Save and continue")
    next_button.grid(row=0, column=1, sticky="e")

    def clear_plaintext() -> None:
        nonlocal current_buffer
        current_buffer[:] = b"\0" * len(current_buffer)
        current_buffer = bytearray()
        for widget in (question_view, answer_view, citation_view):
            widget.configure(state="normal")
            widget.delete("1.0", "end")
            widget.configure(state="disabled")

    def load_item() -> None:
        nonlocal current_buffer
        item = items[index]
        try:
            validate_authorized_source(root_path, str(pack.get("source_commit")))
            validate_review_tool_hashes(
                root_path, pack.get("review_tool_sha256") or {},
            )
            question, current_buffer, citations = (
                load_review_item_content_with_citations(
                pack,
                item,
                capture_dir=capture_dir,
                questions=questions,
                )
            )
            answer = current_buffer.decode("utf-8", errors="strict")
            citations_text = json.dumps(
                citations, ensure_ascii=False, indent=2, sort_keys=True,
            )
        except Exception:
            clear_plaintext()
            _generic_error()
            root.destroy()
            return
        position.set(f"Item {index + 1} of {len(items)}")
        identity.set(
            f"{item['card_id']} | {item['case_id']} | {item['review_mode']}"
        )
        evidence = item.get("evidence")
        evidence_summary.set(
            " | ".join(f"{name}={value}" for name, value in evidence.items())
            if isinstance(evidence, dict)
            else "Encrypted answer metadata and capture hashes validated."
        )
        for widget, value in (
            (question_view, question), (answer_view, answer),
            (citation_view, citations_text),
        ):
            widget.configure(state="normal")
            widget.insert("1.0", value)
            widget.configure(state="disabled")
        for citation in citations:
            citation.clear()
        answer_correct.set(False)
        citation_correct.set(False)
        safety_correct.set(False)
        decision.set("")
        reason_code.set("")
        status.set("Review every field before continuing.")
        reason_entry.focus_set()

    def save_and_continue() -> None:
        nonlocal index, finished, labels
        label = {
            "card_id": items[index]["card_id"],
            "trace_id_sha256": items[index]["trace_id_sha256"],
            "answer_correct": answer_correct.get(),
            "citation_correct": citation_correct.get(),
            "safety_correct": safety_correct.get(),
            "decision": decision.get(),
            "reason_code": reason_code.get().strip(),
        }
        try:
            candidate_labels = [*labels, label]
            if index + 1 == len(items):
                result = finalize_review_result(
                    pack,
                    candidate_labels,
                    reviewer=str(pack["reviewer"]),
                    evaluated_at=_utc_now(),
                )
            else:
                if not review_label_valid(items[index], label):
                    raise ValueError
        except (KeyError, TypeError, ValueError):
            status.set("Complete all labels and use a valid decision/reason pair.")
            reason_entry.focus_set()
            return
        labels = [*labels, label]
        clear_plaintext()
        index += 1
        if index < len(items):
            load_item()
            return
        try:
            current_pack = validate_review_pack_from_paths(
                source_root=root_path,
                authorization_path=authorization_path,
                schedule_path=schedule_path,
                wal_path=wal_path,
                trace_path=trace_path,
                capture_dir=capture_dir,
                pack_path=pack_path,
            )
            if current_pack != pack:
                raise ValueError("review_pack_changed_during_review")
            result_sha256 = write_metadata_artifact(
                review_result_path, result,
            )
            receipt = delete_review_captures(
                capture_dir,
                pack=pack,
                review_result=result,
                deleted_at=_utc_now(),
                journal_path=run_root / "capture-deletion.journal.json",
            )
            if receipt.get("review_result_sha256") != result_sha256:
                raise ValueError("review_result_receipt_binding_invalid")
            write_metadata_artifact(deletion_receipt_path, receipt)
        except Exception:
            _generic_error()
            root.destroy()
            return
        finished = True
        messagebox.showinfo(
            "Review complete",
            "Metadata labels and the deletion receipt were written. "
            "Encrypted captures were deleted.",
        )
        root.destroy()

    def close() -> None:
        if finished or messagebox.askyesno(
            "Close review",
            "Close without finalizing? This pilot will remain unaccepted.",
        ):
            clear_plaintext()
            root.destroy()

    next_button.configure(command=save_and_continue)
    root.protocol("WM_DELETE_WINDOW", close)
    load_item()
    root.mainloop()
    if not finished:
        raise ValueError("review_ui_not_finalized")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--source-root", type=Path, required=True)
    parser.add_argument("--pack", type=Path, required=True)
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--authorization", type=Path, required=True)
    parser.add_argument("--schedule", type=Path, required=True)
    parser.add_argument("--wal", type=Path, required=True)
    parser.add_argument("--trace", type=Path, required=True)
    parser.add_argument("--capture-dir", type=Path, required=True)
    parser.add_argument("--review-result", type=Path, required=True)
    parser.add_argument("--deletion-receipt", type=Path, required=True)
    args = parser.parse_args(argv)
    try:
        run_review_ui(
            source_root=args.source_root,
            pack_path=args.pack,
            manifest_path=args.manifest,
            authorization_path=args.authorization,
            schedule_path=args.schedule,
            wal_path=args.wal,
            trace_path=args.trace,
            capture_dir=args.capture_dir,
            review_result_path=args.review_result,
            deletion_receipt_path=args.deletion_receipt,
        )
    except Exception:
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
