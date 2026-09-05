"""Own an operator process tree through a non-inheritable Windows Job handle.

CreateProcess children cannot execute before assignment. Closing the owner's
last job handle kills job-associated descendants even on abrupt owner death.
The operator must use subprocess/CreateProcess, not WMI or brokered launches
which can create processes outside this job.
"""

from collections.abc import Mapping, Sequence
import math
from pathlib import Path
import subprocess
import sys
import time


class WindowsJobProcess:
    """A process plus descendants; context exit always stops the owned tree."""

    def __init__(
        self, command: Sequence[str], *, cwd: Path, env: Mapping[str, str] | None = None
    ) -> None:
        if sys.platform != "win32":
            raise OSError("Windows Job Objects are required")
        if isinstance(command, (str, bytes)) or not command:
            raise ValueError("command must be a nonempty argument sequence")
        self.command = tuple(command)
        if not all(isinstance(arg, str) and "\0" not in arg for arg in self.command):
            raise ValueError("command arguments must be strings without NUL")
        if not Path(self.command[0]).is_absolute():
            raise ValueError("executable must be an absolute path")
        import win32job
        import win32process

        self._job = win32job.CreateJobObject(None, "")
        self._process = None
        thread = None
        try:
            limits = win32job.QueryInformationJobObject(
                self._job, win32job.JobObjectExtendedLimitInformation
            )
            limits = {**limits, "BasicLimitInformation": {
                **limits["BasicLimitInformation"],
                "LimitFlags": win32job.JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE,
            }}
            win32job.SetInformationJobObject(
                self._job, win32job.JobObjectExtendedLimitInformation, limits
            )
            self._process, thread, self.pid, _ = win32process.CreateProcess(
                self.command[0], subprocess.list2cmdline(self.command), None, None,
                False, win32process.CREATE_SUSPENDED | win32process.CREATE_NO_WINDOW,
                None if env is None else dict(env), str(cwd), win32process.STARTUPINFO(),
            )
            win32job.AssignProcessToJobObject(self._job, self._process)
            win32process.ResumeThread(thread)
            started_thread, thread = thread, None
            started_thread.Close()
        except BaseException:
            # Assignment can fail: terminate the still-suspended process by handle.
            try:
                if self._process is not None:
                    try:
                        win32process.TerminateProcess(self._process, 1)
                    finally:
                        self._process.Close()
            finally:
                self._job.Close()
            raise
        finally:
            if thread is not None:
                thread.Close()

    def __enter__(self) -> "WindowsJobProcess":
        return self

    def wait(self, timeout: float) -> int:
        """Wait only for the root; tree cleanup is unconditional on context exit."""
        import win32event
        import win32process

        if not math.isfinite(timeout) or timeout < 0 or timeout >= 4294967:
            raise ValueError("timeout must be finite and within the Win32 wait range")
        status = win32event.WaitForSingleObject(self._process, math.ceil(timeout * 1000))
        if status == win32event.WAIT_TIMEOUT:
            raise subprocess.TimeoutExpired(self.command, timeout)
        if status != win32event.WAIT_OBJECT_0:
            raise OSError("Owned process wait failed")
        return win32process.GetExitCodeProcess(self._process)

    def wait_empty(self, timeout: float) -> bool:
        """Verify every descendant has exited before recording cleanup success."""
        import win32job

        if not math.isfinite(timeout) or timeout < 0:
            raise ValueError("timeout must be finite and nonnegative")
        deadline = time.monotonic() + timeout
        while True:
            accounting = win32job.QueryInformationJobObject(
                self._job, win32job.JobObjectBasicAccountingInformation
            )
            if accounting["ActiveProcesses"] == 0:
                return True
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                return False
            time.sleep(min(0.02, remaining))

    def terminate(self, exit_code: int = 1) -> None:
        """Terminate only this job's tree, never a process selected by PID."""
        import win32job

        win32job.TerminateJobObject(self._job, exit_code)

    def __exit__(self, exc_type, exc_value, traceback) -> None:
        try:
            self.terminate()
            if not self.wait_empty(timeout=10):
                raise TimeoutError("Owned process tree did not stop within 10 seconds")
        finally:
            try:
                self._job.Close()
            finally:
                self._process.Close()
