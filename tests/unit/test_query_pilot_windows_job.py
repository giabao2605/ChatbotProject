"""Real local process tests; no service or provider traffic."""

import os
from pathlib import Path
import subprocess
import sys
import time

import pytest

pytestmark = pytest.mark.skipif(sys.platform != "win32", reason="Windows Job Objects")


def test_owned_process_returns_exit_code_and_preserves_arguments(tmp_path):
    from scripts.ops.query_pilot_windows_job import WindowsJobProcess

    output = tmp_path / "argument.txt"
    argument = 'space and "quotes" and \\ trailing\\'
    command = [sys.executable, "-c", "import pathlib,sys; pathlib.Path(sys.argv[1]).write_text(sys.argv[2]); sys.exit(7)", str(output), argument]
    with WindowsJobProcess(command, cwd=tmp_path, env=dict(os.environ)) as child:
        assert child.wait(timeout=10) == 7
    assert output.read_text() == argument


def test_timeout_and_explicit_termination_empty_the_owned_tree(tmp_path):
    from scripts.ops.query_pilot_windows_job import WindowsJobProcess

    with WindowsJobProcess([sys.executable, "-c", "import time; time.sleep(30)"], cwd=tmp_path) as child:
        with pytest.raises(subprocess.TimeoutExpired):
            child.wait(timeout=0.01)
        assert child.wait_empty(timeout=0) is False
        child.terminate()
        assert child.wait_empty(timeout=10) is True


def _await_file(path):
    deadline = time.monotonic() + 10
    while time.monotonic() < deadline:
        if path.exists() and path.read_text():
            return path.read_text()
        time.sleep(0.02)
    raise AssertionError(f"Process did not publish {path.name}")


@pytest.mark.parametrize("abrupt", [False, True])
def test_owner_exit_kills_venv_descendants_but_not_unrelated_process(tmp_path, abrupt):
    import win32api
    import win32event

    from scripts.ops.query_pilot_windows_job import WindowsJobProcess

    marker = tmp_path / "grandchild.pid"
    root_marker = tmp_path / "root.pid"
    grandchild_code = "import pathlib,os,time; pathlib.Path(%r).write_text(str(os.getpid())); time.sleep(30)" % str(marker)
    root_code = "import subprocess,sys,pathlib,os,time; pathlib.Path(%r).write_text(str(os.getpid())); subprocess.Popen([sys.executable,'-c',%r]); time.sleep(30)" % (str(root_marker), grandchild_code)
    command = [sys.executable, "-c", root_code]
    handles = []
    with WindowsJobProcess([sys.executable, "-c", "import time; time.sleep(30)"], cwd=tmp_path) as unrelated:
        if abrupt:
            repo = Path(__file__).resolve().parents[2]
            host_code = "import sys,time; sys.path.insert(0,%r); from scripts.ops.query_pilot_windows_job import WindowsJobProcess; child=WindowsJobProcess(%r,cwd=%r); time.sleep(30)" % (str(repo), command, str(tmp_path))
            owner = subprocess.Popen([sys._base_executable, "-c", host_code], creationflags=subprocess.CREATE_NO_WINDOW)
        else:
            owner = WindowsJobProcess(command, cwd=tmp_path)
        closed = False
        try:
            for path in (marker, root_marker):
                handles.append(win32api.OpenProcess(0x00100000, False, int(_await_file(path))))
            if abrupt:
                owner.kill()
                owner.wait(timeout=10)
            else:
                owner.__exit__(None, None, None)
                closed = True
            assert all(win32event.WaitForSingleObject(handle, 10000) == win32event.WAIT_OBJECT_0 for handle in handles)
            with pytest.raises(subprocess.TimeoutExpired):
                unrelated.wait(timeout=0)
        finally:
            if abrupt and owner.poll() is None:
                owner.kill()
                owner.wait(timeout=10)
            elif not abrupt and not closed:
                owner.__exit__(None, None, None)
            for handle in handles:
                handle.Close()


@pytest.mark.parametrize("command", [[], "python", ["python"], [sys.executable, "bad\0arg"]])
def test_invalid_command_rejected_before_launch(tmp_path, command):
    from scripts.ops.query_pilot_windows_job import WindowsJobProcess

    with pytest.raises(ValueError):
        WindowsJobProcess(command, cwd=tmp_path)


def test_failed_assignment_never_executes_child_and_closes_process(tmp_path, monkeypatch):
    import win32api
    import win32event
    import win32job

    from scripts.ops.query_pilot_windows_job import WindowsJobProcess

    marker = tmp_path / "must-not-exist"
    handles = []

    def reject_assignment(job, process):
        current = win32api.GetCurrentProcess()
        handles.append(win32api.DuplicateHandle(current, process, current, 0, False, 2))
        raise OSError("injected assignment failure")

    monkeypatch.setattr(win32job, "AssignProcessToJobObject", reject_assignment)
    try:
        with pytest.raises(OSError, match="injected assignment failure"):
            WindowsJobProcess([sys.executable, "-c", "import pathlib; pathlib.Path(%r).touch()" % str(marker)], cwd=tmp_path)
        assert win32event.WaitForSingleObject(handles[0], 10000) == win32event.WAIT_OBJECT_0
        assert not marker.exists()
    finally:
        for handle in handles:
            handle.Close()


def test_missing_executable_fails_without_starting_process(tmp_path):
    from scripts.ops.query_pilot_windows_job import WindowsJobProcess

    with pytest.raises(Exception):
        WindowsJobProcess([str(tmp_path / "missing.exe")], cwd=tmp_path)


def test_thread_close_failure_stops_process_without_waiting_for_gc(tmp_path, monkeypatch):
    import win32api
    import win32event
    import win32process

    from scripts.ops.query_pilot_windows_job import WindowsJobProcess

    create = win32process.CreateProcess
    resume = win32process.ResumeThread
    handles = []

    class Thread:
        def __init__(self, handle):
            self.handle = handle

        def Close(self):
            self.handle.Close()
            raise OSError("injected thread close failure")

    def create_process(*args):
        process, thread, pid, tid = create(*args)
        current = win32api.GetCurrentProcess()
        handles.append(win32api.DuplicateHandle(current, process, current, 0, False, 2))
        return process, Thread(thread), pid, tid

    monkeypatch.setattr(win32process, "CreateProcess", create_process)
    monkeypatch.setattr(win32process, "ResumeThread", lambda thread: resume(thread.handle))
    try:
        with pytest.raises(OSError, match="injected thread close failure") as error:
            WindowsJobProcess([sys.executable, "-c", "import time; time.sleep(30)"], cwd=tmp_path)
        assert error.traceback
        assert win32event.WaitForSingleObject(handles[0], 1000) == win32event.WAIT_OBJECT_0
    finally:
        for handle in handles:
            if win32event.WaitForSingleObject(handle, 0) == win32event.WAIT_TIMEOUT:
                win32process.TerminateProcess(handle, 1)
            handle.Close()


@pytest.mark.parametrize("timeout", [-1, float("inf"), float("nan")])
def test_invalid_wait_deadlines_rejected(tmp_path, timeout):
    from scripts.ops.query_pilot_windows_job import WindowsJobProcess

    with WindowsJobProcess([sys.executable, "-c", "import time; time.sleep(30)"], cwd=tmp_path) as child:
        with pytest.raises(ValueError):
            child.wait(timeout=timeout)
        with pytest.raises(ValueError):
            child.wait_empty(timeout=timeout)
