from __future__ import annotations

import json
from pathlib import Path
import subprocess
import sys

import pytest

from scripts.eval.run_rerank_provider_ab import (
    _assert_approved_manifest,
    _rate_limiter,
    main,
)
from mech_chatbot.evaluation.rerank_provider import (
    ManifestError,
    canonical_sha256,
    evaluate_gate,
    load_manifest,
    run_provider_ab,
)


pytestmark = pytest.mark.unit


def _case(case_id: str = "bearing-life") -> dict:
    return {
        "case_id": case_id,
        "query": "Tuoi tho vong bi duoc tinh nhu the nao?",
        "candidates": [
            {
                "candidate_id": "bearing-life-formula",
                "text": "Tuoi tho L10 tinh tu tai dong quy doi va tai trong co ban.",
                "relevance": 3,
                "governance_allowed": True,
            },
            {
                "candidate_id": "bearing-installation",
                "text": "Quy trinh gia nhiet va lap vong bi len truc.",
                "relevance": 1,
                "governance_allowed": True,
            },
            {
                "candidate_id": "lubrication",
                "text": "Chon mo boi tron theo nhiet do va toc do.",
                "relevance": 0,
                "governance_allowed": True,
            },
        ],
    }


def _write_manifest(tmp_path, cases: list[dict]):
    path = tmp_path / "manifest.jsonl"
    path.write_text(
        "".join(json.dumps(case, ensure_ascii=False) + "\n" for case in cases),
        encoding="utf-8",
    )
    return path


def test_manifest_is_closed_set_and_hash_is_canonical(tmp_path):
    path = _write_manifest(tmp_path, [_case()])

    cases = load_manifest(path)

    assert cases[0]["case_id"] == "bearing-life"
    assert cases[0]["candidates"][0]["candidate_id"] == "bearing-life-formula"
    assert canonical_sha256({"b": 2, "a": 1}) == canonical_sha256(
        {"a": 1, "b": 2}
    )


def test_real_calls_accept_only_predeclared_governed_manifest():
    approved = load_manifest(
        Path(__file__).resolve().parents[2]
        / "data"
        / "rerank_provider_eval_v1"
        / "manifest.jsonl"
    )
    _assert_approved_manifest(approved)
    approved[0]["query"] += " changed"

    with pytest.raises(ManifestError, match="predeclared governed manifest"):
        _assert_approved_manifest(approved)


@pytest.mark.parametrize(
    "mutation,match",
    [
        (lambda case: case.pop("query"), "query"),
        (
            lambda case: case["candidates"].append(
                dict(case["candidates"][0])
            ),
            "candidate_id",
        ),
        (
            lambda case: case["candidates"][0].update(
                governance_allowed=False
            ),
            "governance",
        ),
        (
            lambda case: case.update(case_id="contains PII"),
            "case_id",
        ),
    ],
)
def test_manifest_rejects_open_or_ungoverned_cases(
    tmp_path,
    mutation,
    match,
):
    case = _case()
    mutation(case)
    path = _write_manifest(tmp_path, [case])

    with pytest.raises(ManifestError, match=match):
        load_manifest(path)


def test_ab_uses_identical_inputs_and_alternates_predeclared_order():
    cases = [_case("case-1"), _case("case-2"), _case("case-3")]
    calls: list[tuple[str, str, tuple[tuple[str, str], ...]]] = []

    def provider(name, ranked_ids):
        def call(case_id, query, candidates):
            calls.append(
                (
                    name,
                    case_id,
                    tuple(
                        (candidate["candidate_id"], candidate["text"])
                        for candidate in candidates
                    ),
                )
            )
            return ranked_ids[case_id]

        return call

    voyage = provider(
        "voyage",
        {
            "case-1": ["bearing-life-formula", "bearing-installation", "lubrication"],
            "case-2": ["bearing-life-formula", "bearing-installation", "lubrication"],
            "case-3": ["bearing-life-formula", "bearing-installation", "lubrication"],
        },
    )
    jina = provider(
        "jina",
        {
            "case-1": ["bearing-life-formula", "bearing-installation", "lubrication"],
            "case-2": ["bearing-life-formula", "bearing-installation", "lubrication"],
            "case-3": ["bearing-life-formula", "bearing-installation", "lubrication"],
        },
    )

    report = run_provider_ab(
        cases,
        providers={"voyage": voyage, "jina": jina},
        clock=lambda: 0.001,
    )

    assert [(name, case_id) for name, case_id, _ in calls] == [
        ("voyage", "case-1"),
        ("jina", "case-1"),
        ("jina", "case-2"),
        ("voyage", "case-2"),
        ("voyage", "case-3"),
        ("jina", "case-3"),
    ]
    assert calls[0][2] == calls[1][2]
    assert calls[2][2] == calls[3][2]
    assert report["arm_orders"] == [
        ["voyage", "jina"],
        ["jina", "voyage"],
        ["voyage", "jina"],
    ]
    assert report["providers"]["voyage"]["retry_count"] == 0
    assert report["providers"]["jina"]["retry_count"] == 0


def test_report_has_quality_latency_and_no_raw_payload():
    sentinel_query = "RAW_QUERY_MUST_NOT_LEAK"
    sentinel_document = "RAW_DOCUMENT_MUST_NOT_LEAK"
    sentinel_key = "jina_SECRET_MUST_NOT_LEAK"
    case = _case()
    case["query"] = sentinel_query
    case["candidates"][0]["text"] = sentinel_document

    report = run_provider_ab(
        [case],
        providers={
            "voyage": lambda *_args: [
                "bearing-life-formula",
                "bearing-installation",
                "lubrication",
            ],
            "jina": lambda *_args: [
                "bearing-installation",
                "bearing-life-formula",
                "lubrication",
            ],
        },
        clock=iter([0.0, 0.01, 0.02, 0.04]).__next__,
        provider_metadata={
            "voyage": {"model": "rerank-2.5-lite"},
            "jina": {
                "model": "jina-reranker-v3",
                "api_key": sentinel_key,
            },
        },
    )
    serialized = json.dumps(report, ensure_ascii=False)

    assert report["providers"]["voyage"]["recall_at_3"] == 1.0
    assert report["providers"]["voyage"]["ndcg_at_3"] == 1.0
    assert report["providers"]["voyage"]["top1_accuracy"] == 1.0
    assert report["providers"]["voyage"]["latency_p50_ms"] == 10.0
    assert report["providers"]["voyage"]["latency_p95_ms"] == 10.0
    assert report["providers"]["voyage"]["error_count"] == 0
    assert report["providers"]["voyage"]["fallback_count"] == 0
    assert report["providers"]["voyage"]["governance_escape_count"] == 0
    assert sentinel_query not in serialized
    assert sentinel_document not in serialized
    assert sentinel_key not in serialized
    assert "query" not in report["cases"][0]
    assert "candidates" not in report["cases"][0]


def test_cli_writes_non_authorizing_artifact_with_at_most_three_cases(
    tmp_path,
):
    manifest = _write_manifest(
        tmp_path,
        [_case(f"case-{index}") for index in range(1, 5)],
    )
    output = tmp_path / "artifact.json"
    calls = {"voyage": 0, "jina": 0}

    def provider(name):
        def call(_case_id, _query, candidates):
            calls[name] += 1
            return [candidate["candidate_id"] for candidate in candidates]

        return call

    exit_code = main(
        [
            "--manifest",
            str(manifest),
            "--output",
            str(output),
            "--max-cases",
            "3",
        ],
        provider_calls={
            "voyage": provider("voyage"),
            "jina": provider("jina"),
        },
        clock=lambda: 0.0,
    )
    artifact = json.loads(output.read_text(encoding="utf-8"))

    assert exit_code == 0
    assert calls == {"voyage": 3, "jina": 3}
    assert artifact["report"]["case_count"] == 3
    assert artifact["gate"]["passed"] is True
    assert artifact["gate"]["release_authorized"] is False


def test_cli_path_entrypoint_can_show_help_outside_repo(tmp_path):
    script = (
        Path(__file__).resolve().parents[2]
        / "scripts"
        / "eval"
        / "run_rerank_provider_ab.py"
    )

    result = subprocess.run(
        [sys.executable, str(script), "--help"],
        cwd=tmp_path,
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 0
    assert "--max-cases" in result.stdout


def test_voyage_pacing_waits_before_latency_measurement_without_retry():
    now = iter([0.0, 0.0, 5.0, 21.0]).__next__
    sleeps = []
    limiter = _rate_limiter(
        minimum_interval_seconds=21.0,
        monotonic=now,
        sleeper=sleeps.append,
    )

    limiter()
    limiter()

    assert sleeps == [16.0]


def test_rate_limit_wait_is_excluded_from_provider_latency():
    events = []
    clock = iter([10.0, 10.5, 20.0, 20.5]).__next__

    report = run_provider_ab(
        [_case()],
        providers={
            "voyage": lambda *_args: [
                "bearing-life-formula",
                "bearing-installation",
                "lubrication",
            ],
            "jina": lambda *_args: [
                "bearing-life-formula",
                "bearing-installation",
                "lubrication",
            ],
        },
        clock=clock,
        before_provider_call={"voyage": lambda: events.append("waited")},
    )

    assert events == ["waited"]
    assert report["providers"]["voyage"]["latency_p95_ms"] == 500.0


def test_error_and_governance_escape_fallback_once_without_retry():
    case = _case()

    def failed(*_args):
        raise TimeoutError("secret response body")

    report = run_provider_ab(
        [case],
        providers={
            "voyage": failed,
            "jina": lambda *_args: [
                "outside-closed-set",
                "bearing-life-formula",
            ],
        },
        clock=lambda: 0.001,
    )

    voyage = report["providers"]["voyage"]
    jina = report["providers"]["jina"]
    assert voyage["error_count"] == 1
    assert voyage["fallback_count"] == 1
    assert voyage["retry_count"] == 0
    assert jina["governance_escape_count"] == 1
    assert jina["fallback_count"] == 1
    assert jina["retry_count"] == 0
    assert report["cases"][0]["arms"]["voyage"]["error_type"] == "TimeoutError"
    assert "secret response body" not in json.dumps(report)


def test_gate_is_fail_closed_and_never_authorizes_release():
    report = run_provider_ab(
        [_case()],
        providers={
            "voyage": lambda *_args: [
                "bearing-life-formula",
                "bearing-installation",
                "lubrication",
            ],
            "jina": lambda *_args: [
                "bearing-life-formula",
                "bearing-installation",
                "lubrication",
            ],
        },
        clock=lambda: 0.001,
    )

    passed = evaluate_gate(report)
    degraded = json.loads(json.dumps(report))
    degraded["providers"]["jina"]["ndcg_at_3"] = 0.0
    failed = evaluate_gate(degraded)
    slow = json.loads(json.dumps(report))
    slow["providers"]["voyage"]["latency_p95_ms"] = 100.0
    slow["providers"]["jina"]["latency_p95_ms"] = 126.0
    latency_failed = evaluate_gate(slow)
    invalid_baseline = json.loads(json.dumps(report))
    invalid_baseline["providers"]["voyage"]["latency_p95_ms"] = 0.0
    invalid_baseline["providers"]["jina"]["latency_p95_ms"] = 1.0
    invalid_baseline_gate = evaluate_gate(invalid_baseline)

    assert passed["passed"] is True
    assert passed["release_authorized"] is False
    assert failed["passed"] is False
    assert "quality_non_inferior" in failed["failed_checks"]
    assert latency_failed["passed"] is False
    assert "latency_ratio" in latency_failed["failed_checks"]
    assert invalid_baseline_gate["passed"] is False
    assert invalid_baseline_gate["latency_ratio"] is None
