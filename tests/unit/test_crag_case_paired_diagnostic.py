from __future__ import annotations

from types import SimpleNamespace

import pytest


pytestmark = pytest.mark.unit


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
