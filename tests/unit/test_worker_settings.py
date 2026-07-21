from __future__ import annotations

from mech_chatbot.config.worker_settings import worker_int


def test_worker_int_reads_value_and_enforces_minimum(monkeypatch) -> None:
    monkeypatch.setenv("WORKER_TEST_INTERVAL", "2")

    assert worker_int("WORKER_TEST_INTERVAL", 15, 5) == 5


def test_worker_int_uses_default_for_invalid_value(monkeypatch) -> None:
    monkeypatch.setenv("WORKER_TEST_INTERVAL", "not-an-int")

    assert worker_int("WORKER_TEST_INTERVAL", 15, 5) == 15
