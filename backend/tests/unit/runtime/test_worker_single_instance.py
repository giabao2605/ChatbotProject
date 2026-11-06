from pathlib import Path
import subprocess
import sys

import pytest

from mech_chatbot.workers.single_instance import worker_lock


def test_processes_in_different_checkouts_share_lock_and_crash_releases_it(tmp_path):
    source = Path(__file__).resolve().parents[3] / "src"
    lock = tmp_path / "shared" / "ingestion-worker.lock"
    first_cwd, second_cwd = tmp_path / "checkout-a", tmp_path / "checkout-b"
    first_cwd.mkdir()
    second_cwd.mkdir()
    script = (
        "import sys; sys.path.insert(0, sys.argv[1]); "
        "from mech_chatbot.workers.single_instance import worker_lock; "
        "from pathlib import Path\n"
        "with worker_lock(Path(sys.argv[2])):\n"
        " print('owner', flush=True)\n"
        " if len(sys.argv) > 3: input()\n"
    )
    command = [sys.executable, "-B", "-c", script, str(source), str(lock)]
    owner = subprocess.Popen(command + ["hold"], cwd=first_cwd, stdin=subprocess.PIPE,
                             stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    try:
        assert owner.stdout.readline().strip() == "owner"
        loser = subprocess.run(command, cwd=second_cwd, capture_output=True, text=True, timeout=10)
        assert loser.returncode != 0
        assert "WorkerAlreadyRunning" in loser.stderr
        owner.kill()
        owner.wait(timeout=10)
        successor = subprocess.run(command, cwd=second_cwd, capture_output=True, text=True, timeout=10)
        assert successor.returncode == 0, successor.stderr
        assert successor.stdout.strip() == "owner"
        assert lock.is_file()
    finally:
        if owner.poll() is None:
            owner.kill()
            owner.wait(timeout=10)


def test_lock_path_errors_fail_closed_without_fallback(tmp_path):
    invalid_parent = tmp_path / "file-not-directory"
    invalid_parent.write_text("keep")
    with pytest.raises(OSError):
        with worker_lock(invalid_parent / "worker.lock"):
            pytest.fail("invalid path must never run worker")
    assert list(tmp_path.iterdir()) == [invalid_parent]


def test_default_lock_path_is_shared_programdata(monkeypatch, tmp_path):
    monkeypatch.setenv("PROGRAMDATA", str(tmp_path))
    with worker_lock():
        assert (tmp_path / "MechRAGServices/worker-heartbeat/ingestion-worker.lock").exists()
