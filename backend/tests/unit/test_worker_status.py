import os
import time
from threading import Event

import pytest

from mech_chatbot.adapters.worker_status import read_worker_status

pytestmark = pytest.mark.unit


def test_worker_status_requires_recent_heartbeat(tmp_path):
    marker = tmp_path / 'worker.ready'
    assert read_worker_status(None) == 'unconfigured'
    assert read_worker_status(marker) == 'unavailable'
    marker.write_text('ready\n', encoding='utf-8')
    assert read_worker_status(marker) == 'ready'
    stale = time.time() - 60
    os.utime(marker, (stale, stale))
    assert read_worker_status(marker) == 'unavailable'


def test_worker_status_rejects_invalid_marker(tmp_path):
    marker = tmp_path / 'worker.ready'
    marker.write_text('starting', encoding='utf-8')
    assert read_worker_status(marker) == 'unavailable'


def test_heartbeat_updates_until_context_exits(tmp_path, monkeypatch):
    from mech_chatbot.adapters import worker_status

    marker = tmp_path / 'worker.ready'
    monkeypatch.setattr(worker_status, 'HEARTBEAT_INTERVAL_SECONDS', 0.01)
    with worker_status.worker_heartbeat(marker):
        deadline = time.monotonic() + 2
        while not marker.exists() and time.monotonic() < deadline:
            Event().wait(0.01)
        assert read_worker_status(marker) == 'ready'
    stamp = marker.stat().st_mtime_ns
    Event().wait(0.05)
    assert marker.stat().st_mtime_ns == stamp


def test_app_health_reports_missing_ready_and_stale_worker(tmp_path, monkeypatch):
    from types import SimpleNamespace
    from mech_chatbot.api.app_server import app_health

    marker = tmp_path / 'worker.ready'
    monkeypatch.setenv('INGESTION_WORKER_READY_FILE', str(marker))
    support = SimpleNamespace(database_ready=lambda: True)
    request = SimpleNamespace(app=SimpleNamespace(state=SimpleNamespace(
        runtime=SimpleNamespace(app_support_queries=support),
    )))
    assert app_health(request)['ingestion_worker'] == 'unavailable'
    marker.write_text('ready\n', encoding='utf-8')
    assert app_health(request)['ingestion_worker'] == 'ready'
    stale = time.time() - 60
    os.utime(marker, (stale, stale))
    assert app_health(request)['ingestion_worker'] == 'unavailable'
    assert app_health(request)['db'] == 'ok'
