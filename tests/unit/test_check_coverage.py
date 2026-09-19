import json
import subprocess
import sys
from pathlib import Path

import pytest


pytestmark = pytest.mark.unit

CHECK_COVERAGE = (
    Path(__file__).resolve().parents[2] / "scripts" / "quality" / "check_coverage.py"
)


def _run_report_text(
    tmp_path: Path,
    report_text: str,
    *,
    min_line: str = "80",
    min_branch: str = "80",
) -> subprocess.CompletedProcess[str]:
    report_path = tmp_path / "coverage.json"
    report_path.write_text(report_text, encoding="utf-8")
    return subprocess.run(
        [
            sys.executable,
            str(CHECK_COVERAGE),
            str(report_path),
            "--min-line",
            min_line,
            "--min-branch",
            min_branch,
        ],
        capture_output=True,
        check=False,
        text=True,
    )


def _run_checker(
    tmp_path: Path, totals: dict[str, object]
) -> subprocess.CompletedProcess[str]:
    return _run_report_text(tmp_path, json.dumps({"totals": totals}))


def test_cli_accepts_separate_line_and_branch_coverage_at_threshold(tmp_path: Path):
    result = _run_checker(
        tmp_path,
        {
            "covered_lines": 80,
            "num_statements": 100,
            "covered_branches": 40,
            "num_branches": 50,
            "percent_covered": 99.0,
        },
    )

    assert result.returncode == 0, result.stderr
    assert "line coverage: 80.000000%" in result.stdout
    assert "branch coverage: 80.000000%" in result.stdout


@pytest.mark.parametrize(
    ("covered_lines", "covered_branches", "expected_errors"),
    [
        (79, 40, ["line coverage 79.000000% is below minimum 80.000000%"]),
        (80, 39, ["branch coverage 78.000000% is below minimum 80.000000%"]),
        (
            79,
            39,
            [
                "line coverage 79.000000% is below minimum 80.000000%",
                "branch coverage 78.000000% is below minimum 80.000000%",
            ],
        ),
    ],
)
def test_cli_reports_each_failed_threshold_independently(
    tmp_path: Path,
    covered_lines: int,
    covered_branches: int,
    expected_errors: list[str],
):
    result = _run_checker(
        tmp_path,
        {
            "covered_lines": covered_lines,
            "num_statements": 100,
            "covered_branches": covered_branches,
            "num_branches": 50,
            "percent_covered": 99.0,
        },
    )

    assert result.returncode == 1
    for message in expected_errors:
        assert message in result.stderr


def test_cli_failure_message_preserves_near_threshold_precision(tmp_path: Path):
    result = _run_checker(
        tmp_path,
        {
            "covered_lines": 15_999,
            "num_statements": 20_000,
            "covered_branches": 40,
            "num_branches": 50,
        },
    )

    assert result.returncode == 1
    assert (
        "line coverage 79.995000% is below minimum 80.000000%"
        in result.stderr
    )


@pytest.mark.parametrize(
    ("report_text", "expected_error"),
    [
        ('{"totals": "secret-token-123"', "coverage report is not valid JSON"),
        ('{"metadata": {"token": "secret-token-123"}}', "missing object 'totals'"),
    ],
)
def test_cli_rejects_malformed_report_without_leaking_contents(
    tmp_path: Path,
    report_text: str,
    expected_error: str,
):
    result = _run_report_text(tmp_path, report_text)

    assert result.returncode == 2
    assert expected_error in result.stderr
    assert "secret-token-123" not in result.stderr
    assert "Traceback" not in result.stderr


def test_cli_fails_closed_when_report_has_zero_branches(tmp_path: Path):
    result = _run_checker(
        tmp_path,
        {
            "covered_lines": 80,
            "num_statements": 100,
            "covered_branches": 0,
            "num_branches": 0,
        },
    )

    assert result.returncode == 2
    assert (
        "cannot evaluate branch coverage because num_branches is zero"
        in result.stderr
    )
    assert "Traceback" not in result.stderr


@pytest.mark.parametrize(
    ("totals", "expected_error"),
    [
        (
            {
                "covered_lines": 80,
                "num_statements": 100,
                "num_branches": 50,
            },
            "missing numeric total 'covered_branches'",
        ),
        (
            {
                "covered_lines": "secret-token-123",
                "num_statements": 100,
                "covered_branches": 40,
                "num_branches": 50,
            },
            "total 'covered_lines' must be a non-negative integer",
        ),
        (
            {
                "covered_lines": 80,
                "num_statements": 100,
                "covered_branches": 40.5,
                "num_branches": 50,
            },
            "total 'covered_branches' must be a non-negative integer",
        ),
    ],
)
def test_cli_rejects_missing_or_malformed_totals_without_leaking_values(
    tmp_path: Path,
    totals: dict[str, object],
    expected_error: str,
):
    result = _run_checker(tmp_path, totals)

    assert result.returncode == 2
    assert expected_error in result.stderr
    assert "secret-token-123" not in result.stderr
    assert "Traceback" not in result.stderr


def test_cli_reports_unreadable_report_without_a_traceback(tmp_path: Path):
    missing_report = tmp_path / "missing-coverage.json"
    result = subprocess.run(
        [
            sys.executable,
            str(CHECK_COVERAGE),
            str(missing_report),
            "--min-line",
            "80",
            "--min-branch",
            "80",
        ],
        capture_output=True,
        check=False,
        text=True,
    )

    assert result.returncode == 2
    assert "cannot read coverage report" in result.stderr
    assert "Traceback" not in result.stderr


@pytest.mark.parametrize(
    ("min_line", "min_branch"),
    [("-0.01", "80"), ("80", "100.01")],
)
def test_cli_rejects_thresholds_outside_percentage_range(
    tmp_path: Path,
    min_line: str,
    min_branch: str,
):
    report_text = json.dumps(
        {
            "totals": {
                "covered_lines": 100,
                "num_statements": 100,
                "covered_branches": 50,
                "num_branches": 50,
            }
        }
    )

    result = _run_report_text(
        tmp_path,
        report_text,
        min_line=min_line,
        min_branch=min_branch,
    )

    assert result.returncode == 2
    assert "must be between 0 and 100" in result.stderr
