"""Validate real pilot-evaluation manifests before a department rollout gate.

The tool is intentionally a *manifest validator*, not an evaluator.  It never
generates questions, expected answers, or pass/fail results.  Business owners
must supply the real JSONL evaluation corpus and name the pilot departments
being considered.

Schema v4 contract for each JSONL record:

* department, question, scenario
* expected document or file
* expected page or section
* version policy
* expected keywords
* security expectation
* refusal expectation

For a deliberate refusal case, the document/file and page/section fields must
still be present, but may be explicitly ``null`` when there is no permitted
source to cite.  This distinguishes a reviewed negative case from an omitted
expectation.
"""

from __future__ import annotations

import argparse
import json
from collections import Counter
from pathlib import Path
from typing import Any, Iterable


MANIFEST_SCHEMA_VERSION = "pilot-eval-v4"
MINIMUM_QUESTIONS_PER_DEPARTMENT = 75
DEFAULT_PILOT_DEPARTMENT_COUNT = 3

_DOCUMENT_FIELDS = (
    "expected_doc_id",
    "expected_document",
    "expected_doc",
    "expected_file",
    "expected_filename",
    "expected_source_file",
)
_LOCATION_FIELDS = ("expected_page", "expected_section")
_VERSION_POLICY_FIELDS = ("expected_version_policy", "version_policy")
_KEYWORD_FIELDS = ("expected_keywords", "keywords")
_SECURITY_FIELDS = ("security_expectation", "expected_security")
_REFUSAL_FIELDS = ("refusal_expectation", "should_refuse", "expected_refusal")


def _has_meaningful_value(value: Any) -> bool:
    """Return whether a JSON value intentionally contains an expectation."""
    if value is None:
        return False
    if isinstance(value, str):
        return bool(value.strip())
    if isinstance(value, (list, tuple, set, dict)):
        return bool(value)
    return True


def _first_present(record: dict[str, Any], aliases: Iterable[str]) -> tuple[str | None, Any]:
    for name in aliases:
        if name in record:
            return name, record[name]
    return None, None


def _require_nonempty_text(record: dict[str, Any], field: str, line_number: int) -> str:
    value = record.get(field)
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"Dong {line_number}: thieu {field}")
    return value.strip()


def _validate_keywords(value: Any, *, line_number: int, refusal_expected: bool) -> None:
    if not isinstance(value, list):
        raise ValueError(f"Dong {line_number}: expected_keywords/keywords phai la array JSON")
    if any(not isinstance(item, str) or not item.strip() for item in value):
        raise ValueError(f"Dong {line_number}: expected_keywords/keywords chi duoc chua chuoi khong rong")
    if not refusal_expected and not value:
        raise ValueError(f"Dong {line_number}: expected_keywords/keywords khong duoc rong voi cau tra loi")


def _validate_record(record: dict[str, Any], line_number: int) -> dict[str, Any]:
    """Validate one v4 record and return only non-sensitive summary metadata."""
    department = _require_nonempty_text(record, "department", line_number)
    _require_nonempty_text(record, "question", line_number)
    scenario = _require_nonempty_text(record, "scenario", line_number)

    refusal_field, refusal_value = _first_present(record, _REFUSAL_FIELDS)
    if refusal_field is None or not isinstance(refusal_value, bool):
        raise ValueError(
            f"Dong {line_number}: thieu hoac sai kieu refusal_expectation/should_refuse (phai la boolean)"
        )
    refusal_expected = refusal_value

    version_field, version_value = _first_present(record, _VERSION_POLICY_FIELDS)
    if version_field is None or not isinstance(version_value, str) or not version_value.strip():
        raise ValueError(f"Dong {line_number}: thieu expected_version_policy/version_policy")

    security_field, security_value = _first_present(record, _SECURITY_FIELDS)
    if security_field is None or not _has_meaningful_value(security_value):
        raise ValueError(f"Dong {line_number}: thieu security_expectation/expected_security")

    keyword_field, keyword_value = _first_present(record, _KEYWORD_FIELDS)
    if keyword_field is None:
        raise ValueError(f"Dong {line_number}: thieu expected_keywords/keywords")
    _validate_keywords(keyword_value, line_number=line_number, refusal_expected=refusal_expected)

    document_field, document_value = _first_present(record, _DOCUMENT_FIELDS)
    location_field, location_value = _first_present(record, _LOCATION_FIELDS)
    if document_field is None:
        raise ValueError(f"Dong {line_number}: thieu expected document/file")
    if location_field is None:
        raise ValueError(f"Dong {line_number}: thieu expected page/section")
    if not refusal_expected and not _has_meaningful_value(document_value):
        raise ValueError(f"Dong {line_number}: expected document/file khong duoc rong voi cau tra loi")
    if not refusal_expected and not _has_meaningful_value(location_value):
        raise ValueError(f"Dong {line_number}: expected page/section khong duoc rong voi cau tra loi")

    return {
        "department": department,
        "scenario": scenario,
        "refusal_expected": refusal_expected,
        "has_expected_document": _has_meaningful_value(document_value),
        "has_expected_location": _has_meaningful_value(location_value),
    }


def read_questions(path: Path) -> list[dict[str, Any]]:
    """Read and strictly validate a v4 JSONL manifest without exposing prompts."""
    questions: list[dict[str, Any]] = []
    for line_number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        try:
            value = json.loads(stripped)
        except json.JSONDecodeError as exc:
            raise ValueError(f"Dong {line_number}: JSON khong hop le") from exc
        if not isinstance(value, dict):
            raise ValueError(f"Dong {line_number}: moi item phai la object")
        questions.append(_validate_record(value, line_number))
    return questions


def parse_expected_departments(raw: str) -> list[str]:
    departments = [value.strip() for value in str(raw or "").split(",") if value.strip()]
    duplicates = sorted({value for value in departments if departments.count(value) > 1})
    if duplicates:
        raise ValueError("Danh sach phong ban pilot bi trung: " + ", ".join(duplicates))
    return departments


def summarize(
    questions: list[dict[str, Any]],
    minimum: int,
    *,
    expected_departments: Iterable[str] = (),
    expected_department_count: int = DEFAULT_PILOT_DEPARTMENT_COUNT,
) -> dict[str, Any]:
    """Produce a safe gate report with counts only, never question text."""
    expected = [str(value).strip() for value in expected_departments if str(value).strip()]
    counts = Counter(str(item["department"]).strip() for item in questions)
    scenarios: dict[str, Counter[str]] = {}
    refusal_counts: Counter[str] = Counter()
    for item in questions:
        department = str(item["department"]).strip()
        scenarios.setdefault(department, Counter())[str(item["scenario"]).strip()] += 1
        if item.get("refusal_expected"):
            refusal_counts[department] += 1

    missing_departments = [department for department in expected if department not in counts]
    unexpected_departments = [department for department in sorted(counts) if department not in expected]
    expected_count_ok = bool(expected) and len(expected) == expected_department_count
    department_rows = {
        department: {
            "question_count": count,
            "meets_minimum": count >= minimum,
            "scenarios": dict(scenarios.get(department, {})),
            "refusal_case_count": int(refusal_counts.get(department, 0)),
            "is_expected_pilot_department": department in expected,
        }
        for department, count in sorted(counts.items())
    }
    expected_departments_meet_minimum = all(
        counts.get(department, 0) >= minimum for department in expected
    )
    configuration_errors: list[str] = []
    if not expected:
        configuration_errors.append("Phai khai bao --expected-departments cho pilot gate")
    elif not expected_count_ok:
        configuration_errors.append(
            f"Pilot nay ky vong {expected_department_count} phong ban, nhan {len(expected)}"
        )

    passed = (
        not configuration_errors
        and not missing_departments
        and not unexpected_departments
        and expected_departments_meet_minimum
    )
    return {
        "manifest_schema": MANIFEST_SCHEMA_VERSION,
        "total": len(questions),
        "minimum_per_department": minimum,
        "expected_pilot_department_count": expected_department_count,
        "expected_pilot_departments": expected,
        "departments": department_rows,
        "missing_departments": missing_departments,
        "unexpected_departments": unexpected_departments,
        "configuration_errors": configuration_errors,
        "passed": passed,
    }


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Validate a real v4 pilot-evaluation JSONL manifest; it never fabricates data."
    )
    parser.add_argument("manifest", type=Path, help="JSONL evaluation manifest supplied by business reviewers")
    parser.add_argument("--minimum", type=int, default=MINIMUM_QUESTIONS_PER_DEPARTMENT)
    parser.add_argument(
        "--expected-departments",
        "--departments",
        dest="expected_departments",
        default="",
        help="CSV of the real pilot department codes; required for a passing gate",
    )
    parser.add_argument(
        "--expected-department-count",
        type=int,
        default=DEFAULT_PILOT_DEPARTMENT_COUNT,
        help="Pilot wave size from the 3 -> 4 -> 4 -> 4 rollout plan (default: 3)",
    )
    args = parser.parse_args()
    if args.minimum < MINIMUM_QUESTIONS_PER_DEPARTMENT:
        raise SystemExit(f"--minimum khong duoc nho hon {MINIMUM_QUESTIONS_PER_DEPARTMENT}")
    if args.expected_department_count < 1:
        raise SystemExit("--expected-department-count phai lon hon 0")

    try:
        questions = read_questions(args.manifest)
        expected = parse_expected_departments(args.expected_departments)
    except ValueError as exc:
        raise SystemExit(str(exc)) from exc

    report = summarize(
        questions,
        args.minimum,
        expected_departments=expected,
        expected_department_count=args.expected_department_count,
    )
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if report["passed"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
