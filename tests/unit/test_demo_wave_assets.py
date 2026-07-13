from pathlib import Path

import pytest

from scripts.demo_wave.generate_demo_assets import DEPARTMENTS, SCENARIO_COUNTS, generate_corpus, generate_eval


pytestmark = pytest.mark.unit


def test_demo_asset_generator_is_complete_and_deterministic(tmp_path: Path):
    corpus = generate_corpus(tmp_path)
    cases = generate_eval(tmp_path)

    assert len(DEPARTMENTS) == 14
    assert len(corpus) == 14 * 6
    assert len(cases) == 14 * 75
    assert sum(SCENARIO_COUNTS.values()) == 75
    assert all(len([c for c in cases if c["department"] == dept]) == 75 for dept in DEPARTMENTS)
    assert all(record["batch_id"] == "demo-wave-v1" for record in corpus + cases)


def test_demo_corpus_contains_serving_and_lifecycle_negatives(tmp_path: Path):
    records = generate_corpus(tmp_path)
    for department in DEPARTMENTS:
        dept_records = [row for row in records if row["department"] == department]
        assert len([row for row in dept_records if row["should_serve"]]) == 3
        assert {row["effective_status"] for row in dept_records} >= {"effective", "expired", "superseded"}


def test_accountant_cases_and_corpus_are_confidential(tmp_path: Path):
    corpus = generate_corpus(tmp_path)
    cases = generate_eval(tmp_path)
    assert all(row["security_level"] == "confidential" for row in corpus if row["department"] == "Accountant")
    assert all(row["max_security_level"] == "confidential" for row in cases if row["department"] == "Accountant")
