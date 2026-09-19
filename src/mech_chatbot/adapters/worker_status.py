"""Read the local ingestion worker's recent readiness heartbeat."""

from pathlib import Path
import time
from contextlib import contextmanager
from threading import Event, Thread

HEARTBEAT_INTERVAL_SECONDS = 10
HEARTBEAT_MAX_AGE_SECONDS = 30


@contextmanager
def worker_heartbeat(marker):
    stop = Event()

    def update():
        while not stop.wait(HEARTBEAT_INTERVAL_SECONDS):
            try:
                Path(marker).write_text('ready\n', encoding='utf-8')
            except OSError:
                # Readers fail closed once the last successful heartbeat ages out.
                continue

    thread = Thread(target=update, daemon=True, name='ingestion-heartbeat') if marker else None
    if thread:
        thread.start()
    try:
        yield
    finally:
        stop.set()
        if thread:
            thread.join()


def read_worker_status(marker):
    if not marker:
        return 'unconfigured'
    try:
        path = Path(marker)
        age = time.time() - path.stat().st_mtime
        if path.read_text(encoding='utf-8').strip() == 'ready' and 0 <= age <= HEARTBEAT_MAX_AGE_SECONDS:
            return 'ready'
    except (OSError, UnicodeError):
        pass
    return 'unavailable'
