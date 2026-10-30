"""Host-wide ingestion worker ownership, independent of the source checkout."""

from contextlib import contextmanager
import errno
import os
from pathlib import Path


WORKER_ALREADY_RUNNING = 73


class WorkerAlreadyRunning(RuntimeError):
    pass


@contextmanager
def worker_lock(path: Path | None = None):
    lock_path = path if path is not None else (
        Path(os.environ.get("PROGRAMDATA", "C:/ProgramData"))
        / "MechRAGServices/worker-heartbeat/ingestion-worker.lock"
    )
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    with lock_path.open("a+b") as stream:
        stream.seek(0, os.SEEK_END)
        if stream.tell() == 0:
            stream.write(b"0")
            stream.flush()
        stream.seek(0)
        try:
            if os.name == "nt":
                import msvcrt

                msvcrt.locking(stream.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl

                fcntl.flock(stream.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as error:
            if error.errno in (errno.EACCES, errno.EAGAIN) or getattr(error, "winerror", None) in (33, 36):
                raise WorkerAlreadyRunning("An ingestion worker is already running on this host") from error
            raise
        # Closing the handle releases ownership even after process termination.
        yield
