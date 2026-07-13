from __future__ import annotations

from pathlib import Path

import pytest


pytestmark = pytest.mark.unit

ROOT = Path(__file__).resolve().parents[2]
TEMPLATE_DIR = ROOT / "scripts" / "eval" / "templates" / "wave3"
DEPARTMENTS = ("production", "maintenance", "quality_control", "iso")


@pytest.mark.parametrize("department", DEPARTMENTS)
def test_wave3_manifest_templates_are_intentionally_empty(department: str) -> None:
    lines = (TEMPLATE_DIR / f"{department}.jsonl").read_text(encoding="utf-8").splitlines()

    assert lines
    assert all(not line.strip() or line.lstrip().startswith("#") for line in lines)


@pytest.mark.parametrize("department", DEPARTMENTS)
def test_wave3_manifest_templates_document_required_scenarios(department: str) -> None:
    content = (TEMPLATE_DIR / f"{department}.jsonl").read_text(encoding="utf-8")

    assert "citation" in content
    assert "denial" in content
    assert "insufficient_evidence" in content


def test_wave3_template_guide_requires_real_75_question_sets() -> None:
    content = (TEMPLATE_DIR / "README.md").read_text(encoding="utf-8")

    assert "--expected-departments Production,Maintenance,QualityControl,ISO" in content
    assert "--expected-department-count 4" in content
    assert "--minimum 75" in content
    assert "không ghi evaluation gate" in content
