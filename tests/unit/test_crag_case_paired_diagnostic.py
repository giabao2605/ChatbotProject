from __future__ import annotations

from types import SimpleNamespace

import pytest


pytestmark = pytest.mark.unit


def test_driver_preserves_progress_when_series_summary_fails(monkeypatch, tmp_path):
    diagnostic, context = _patch_completed_driver(monkeypatch, tmp_path, series_case_id="case-1")

    def fail_summary(**kwargs):
        raise ValueError("invalid aggregation")

    monkeypatch.setattr(diagnostic, "build_series_summary", fail_summary)
    report = diagnostic.run_diagnostic(
        manifest=tmp_path / "manifest.jsonl", preflight=tmp_path / "preflight.json",
        provider_smoke=tmp_path / "smoke.json", output=context.output,
        trace=tmp_path / "trace.jsonl",
    )
    assert report["case_pair_count"] == 1
    assert report["arm_run_count"] == 2
    assert report["status"] == "inconclusive"


def test_driver_keeps_validated_arm_when_other_arm_raises(monkeypatch, tmp_path):
    from scripts.crag_eval import run_diagnostic as diagnostic
    real_pair = diagnostic._run_case_pair
    diagnostic, context = _patch_completed_driver(monkeypatch, tmp_path, series_case_id="case-1")
    monkeypatch.setattr(diagnostic, "_run_case_pair", real_pair)
    calls = []

    def arm(*args, label, **kwargs):
        calls.append(label)
        if label == "baseline":
            raise RuntimeError("private error")
        return {"eval": {"provider_failure_count": 0, "provider_retries": 0}}

    monkeypatch.setattr(diagnostic, "_run_arm", arm)
    report = diagnostic.run_diagnostic(
        manifest=tmp_path / "manifest.jsonl", preflight=tmp_path / "preflight.json",
        provider_smoke=tmp_path / "smoke.json", output=context.output,
        trace=tmp_path / "trace.jsonl",
    )
    assert report["arm_run_count"] == 1
    assert report["case_pair_count"] == 0
    assert report["status"] == "inconclusive"
    assert report["execution_failure"] == {"error_type": "RuntimeError"}
    assert calls == ["candidate", "baseline"]


def test_case_summary_failure_keeps_both_validated_arms(monkeypatch, tmp_path):
    from scripts.crag_eval import run_diagnostic as diagnostic
    real_pair = diagnostic._run_case_pair
    diagnostic, context = _patch_completed_driver(monkeypatch, tmp_path, series_case_id="case-1")
    monkeypatch.setattr(diagnostic, "_run_case_pair", real_pair)
    monkeypatch.setattr(diagnostic, "_run_arm", lambda *args, **kwargs: {
        "eval": {"passed_cases": "invalid", "provider_failure_count": 0, "provider_retries": 0},
    })
    report = diagnostic.run_diagnostic(
        manifest=tmp_path / "manifest.jsonl", preflight=tmp_path / "preflight.json",
        provider_smoke=tmp_path / "smoke.json", output=context.output,
        trace=tmp_path / "trace.jsonl",
    )
    assert report["arm_run_count"] == 2
    assert report["case_pair_count"] == 0
    assert report["status"] == "inconclusive"
    assert report["execution_failure"] == {"error_type": "ValueError"}


def test_driver_preserves_completed_pairs_when_next_pair_raises(monkeypatch, tmp_path):
    diagnostic, context = _patch_completed_driver(monkeypatch, tmp_path, series_case_id="case-1")
    context.case_ids = ("case-1", "case-2")

    def run_pair(*args, case_id, arm_starts, **kwargs):
        if case_id == "case-2":
            raise RuntimeError("private failure detail")
        return ({"case_id": case_id, "baseline": {}, "candidate": {}},
                (*arm_starts, "baseline", "candidate"), False)

    monkeypatch.setattr(diagnostic, "_run_case_pair", run_pair)
    report = diagnostic.run_diagnostic(
        manifest=tmp_path / "manifest.jsonl", preflight=tmp_path / "preflight.json",
        provider_smoke=tmp_path / "smoke.json", output=context.output,
        trace=tmp_path / "trace.jsonl",
    )
    assert report["status"] == "inconclusive"
    assert report["case_pair_count"] == 1
    assert report["arm_run_count"] == 2
    assert report["execution_failure"] == {"error_type": "RuntimeError"}
    assert "private failure detail" not in (context.output / "outcome.json").read_text()


def _patch_completed_driver(monkeypatch, tmp_path, *, series_case_id):
    from scripts.crag_eval import run_diagnostic as diagnostic

    context = SimpleNamespace(
        output=tmp_path / "diagnostic",
        case_ids=("case-1",),
        source_commit="a" * 40,
    )
    monkeypatch.setattr(diagnostic, "_prepare_context", lambda *args: context)
    monkeypatch.setattr(diagnostic, "_write_declaration", lambda _context: "b" * 64)
    monkeypatch.setattr(
        diagnostic,
        "_run_case_pair",
        lambda *args, arm_starts, **kwargs: (
            {"case_id": "case-1", "baseline": {}, "candidate": {}},
            (*arm_starts, "arm"),
            False,
        ),
    )
    monkeypatch.setattr(
        diagnostic,
        "build_series_summary",
        lambda **kwargs: {
            "id": kwargs["series_id"],
            "arm_order": kwargs["arm_order"],
            "case_count": 1,
            "case_ids": [series_case_id],
            "baseline": {},
            "candidate": {},
            "gate": {"passed": False, "checks": {}},
        },
    )
    return diagnostic, context


def test_driver_tombstones_case_order_drift_during_finalization(
    monkeypatch,
    tmp_path,
):
    diagnostic, context = _patch_completed_driver(
        monkeypatch,
        tmp_path,
        series_case_id="wrong-case",
    )

    report = diagnostic.run_diagnostic(
        manifest=tmp_path / "manifest.jsonl",
        preflight=tmp_path / "preflight.json",
        provider_smoke=tmp_path / "smoke.json",
        output=context.output,
        trace=tmp_path / "trace.jsonl",
    )

    assert report["status"] == "inconclusive"
    assert report["execution_failure"] == {"error_type": "ValueError"}
    assert (context.output / "outcome.json").is_file()


def test_driver_tombstones_base_outcome_aggregation_error(monkeypatch, tmp_path):
    diagnostic, context = _patch_completed_driver(
        monkeypatch,
        tmp_path,
        series_case_id="case-1",
    )
    real_builder = diagnostic.build_diagnostic_outcome

    def fail_nonempty(pairs, **kwargs):
        if pairs:
            raise ValueError("Bearer must-not-appear")
        return real_builder(pairs, **kwargs)

    monkeypatch.setattr(diagnostic, "build_diagnostic_outcome", fail_nonempty)

    report = diagnostic.run_diagnostic(
        manifest=tmp_path / "manifest.jsonl",
        preflight=tmp_path / "preflight.json",
        provider_smoke=tmp_path / "smoke.json",
        output=context.output,
        trace=tmp_path / "trace.jsonl",
    )

    outcome_text = (context.output / "outcome.json").read_text(encoding="utf-8")
    assert report["status"] == "inconclusive"
    assert report["execution_failure"] == {"error_type": "ValueError"}
    assert "must-not-appear" not in outcome_text
