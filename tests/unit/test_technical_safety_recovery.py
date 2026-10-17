"""The evaluation adapter must preserve unsuccessful typed terminal events."""
import importlib.util
from pathlib import Path

import pytest


@pytest.fixture
def replay():
    path = Path(__file__).resolve().parents[2] / 'reports/rag-14-departments-20261005/department-question-banks/Technical.safety-recovery-20261015.py'
    spec = importlib.util.spec_from_file_location('technical_safety_recovery', path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.mark.parametrize('deadline', [False, True])
def test_stopped_requests_keep_real_terminal_and_cannot_export_success(replay, monkeypatch, tmp_path, deadline):
    monkeypatch.setattr(replay.legacy, 'capture', replay.capture)
    actual, checks = replay.legacy.stopped_math(tmp_path, deadline=deadline)
    assert all(checks.values()), checks
    assert len(actual['variants']) == 2


def test_completed_capture_keeps_original_math_observation(replay):
    engine = replay.legacy.comparison_store(('10', '15'))
    try:
        result, actual = replay.capture(engine, replay.legacy.comparison_request())
        assert type(result).__name__ == 'RagCollectedResult'
        assert actual['collected_outcome'] == 'answered'
        assert actual['export_attempt']['error_type'] is None
        assert actual['recorded_budget']['calculations'] == 4
    finally:
        engine.dispose()


def test_sql_adapter_reads_nested_source_locators(replay):
    engine = replay.legacy.create_engine('sqlite://')
    replay.sql_functions(engine)
    try:
        with engine.connect() as conn:
            row = conn.execute(replay.legacy.text("SELECT JSON_VALUE(:value,'$.locator.page'),JSON_VALUE(:value,'$.locator.row_key'),JSON_VALUE(:value,'$.missing.page')"),
                {'value':'{"locator":{"page":1,"row_key":"page-1:row-1"}}'}).one()
        assert tuple(row) == (1, 'page-1:row-1', None)
    finally:
        engine.dispose()


@pytest.mark.parametrize('deadline', [False, True])
def test_cancel_or_deadline_after_first_citation_stops_before_the_next(replay, monkeypatch, tmp_path, deadline):
    monkeypatch.setattr(replay.legacy, 'capture', replay.capture)
    actual, checks = replay.legacy.stopped_math(tmp_path, deadline=deadline)
    late = actual['variants'][1]['actual']
    assert all(checks.values()), checks
    assert len([event for event in late['events'] if event['type'] == 'RagCitation']) == 1


def test_http_replay_freezes_both_sql_actors_and_uses_real_acl(replay, monkeypatch, tmp_path):
    import json
    import sqlite3

    monkeypatch.setattr(replay, 'reference', lambda path: {'path':str(path), 'sha256':replay.legacy.h.digest(path)})
    bank = json.loads(replay.legacy.h.BANK.read_text(encoding='utf-8'))
    case = next(case for case in bank['cases'] if case['id'] == 'TEC-069')
    result = replay.replay_case(case, tmp_path/'TEC-069', replay.legacy.h.digest(replay.legacy.h.BANK))
    assert result['passed'], result
    with sqlite3.connect(':memory:') as connection:
        connection.executescript((tmp_path/'TEC-069/http-input.sql').read_text(encoding='utf-8'))
        assert connection.execute('SELECT Username FROM Users WHERE UserID=9').fetchone() == ('other-fixture',)
        assert connection.execute('SELECT Department FROM UserDepartments WHERE UserID=9').fetchone() == ('finance',)
        assert connection.execute('SELECT DeptName,Domain,Site FROM Departments WHERE DeptCode=?', ('finance',)).fetchone() == ('finance','general','factory')
