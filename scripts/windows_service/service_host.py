"""SCM host with child-tree containment and bounded cooperative shutdown."""
import argparse
import subprocess
import threading
import time
import uuid

import servicemanager
import win32api
import win32con
import win32event
import win32file
import win32job
import win32process
import win32service
import win32serviceutil
import ntsecuritycon
from pywintypes import SECURITY_ATTRIBUTES
from service_runtime import ROOT, STATE, PYTHON, NAMES, child_environment, expected_exit, service_name


def create_child(role, stop_file, preflight=False):
    job = win32job.CreateJobObject(None, '')
    limits = win32job.QueryInformationJobObject(job, win32job.JobObjectExtendedLimitInformation)
    limits['BasicLimitInformation']['LimitFlags'] = win32job.JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
    win32job.SetInformationJobObject(job, win32job.JobObjectExtendedLimitInformation, limits)
    security = SECURITY_ATTRIBUTES()
    security.bInheritHandle = True
    output = win32file.CreateFile(str(STATE / role / 'runtime.log'), ntsecuritycon.FILE_APPEND_DATA,
                                  win32con.FILE_SHARE_READ | win32con.FILE_SHARE_WRITE,
                                  security, win32con.OPEN_ALWAYS, 0, None)
    null = win32file.CreateFile('NUL', win32con.GENERIC_READ, win32con.FILE_SHARE_READ,
                                security, win32con.OPEN_EXISTING, 0, None)
    startup = win32process.STARTUPINFO()
    startup.dwFlags = win32con.STARTF_USESTDHANDLES
    startup.hStdInput, startup.hStdOutput, startup.hStdError = null, output, output
    process = thread = None
    try:
        command = subprocess.list2cmdline([str(PYTHON), '-X', 'utf8', str(ROOT / 'scripts/windows_service/service_runtime.py'),
                                          role, '--stop-file', str(stop_file)] + (['--preflight'] if preflight else []))
        process, thread, _, _ = win32process.CreateProcess(
            str(PYTHON), command, None, None, True,
            win32con.CREATE_SUSPENDED | win32con.CREATE_NO_WINDOW | win32con.CREATE_UNICODE_ENVIRONMENT,
            child_environment(__import__('os').environ), str(ROOT), startup)
        win32job.AssignProcessToJobObject(job, process)
        win32process.ResumeThread(thread)
        return job, process
    except BaseException:
        if process is not None:
            win32process.TerminateProcess(process, 1)
            process.Close()
        job.Close()
        raise
    finally:
        if thread is not None:
            thread.Close()
        null.Close()
        output.Close()


def service_class(role):
    class Host(win32serviceutil.ServiceFramework):
        _svc_name_ = service_name(role)
        _svc_display_name_ = service_name(role)

        def __init__(self, args):
            super().__init__(args)
            self.stopping = threading.Event()
            self.stop_file = STATE / role / ('stop-' + uuid.uuid4().hex)

        def SvcStop(self):
            self.ReportServiceStatus(win32service.SERVICE_STOP_PENDING, waitHint=120000)
            self.stopping.set()
            self.stop_file.touch()

        def SvcShutdown(self):
            self.SvcStop()

        def GetAcceptedControls(self):
            return win32service.SERVICE_ACCEPT_STOP | win32service.SERVICE_ACCEPT_SHUTDOWN

        def SvcDoRun(self):
            job = process = None
            clean = False
            preflight = (STATE / 'preflight-requests' / (role + '.json')).exists()
            try:
                job, process = create_child(role, self.stop_file, preflight)
                deadline = None
                while win32event.WaitForSingleObject(process, 1000) == win32event.WAIT_TIMEOUT:
                    if self.stopping.is_set():
                        deadline = deadline or (time.monotonic() + 110)
                        self.ReportServiceStatus(win32service.SERVICE_STOP_PENDING, waitHint=120000)
                        if time.monotonic() >= deadline:
                            break
                if win32event.WaitForSingleObject(process, 0) != win32event.WAIT_TIMEOUT:
                    clean = expected_exit(self.stopping.is_set() or preflight, win32process.GetExitCodeProcess(process))
            except Exception as error:
                servicemanager.LogErrorMsg(self._svc_name_ + ': ' + type(error).__name__)
            finally:
                if job is not None:
                    job.Close()
                if process is not None:
                    win32event.WaitForSingleObject(process, 5000)
                    process.Close()
                self.stop_file.unlink(missing_ok=True)
            if not clean:
                raise RuntimeError('Service child exited unexpectedly or stop deadline elapsed')
    return Host


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('role', choices=NAMES)
    args = parser.parse_args()
    servicemanager.Initialize()
    servicemanager.PrepareToHostSingle(service_class(args.role))
    servicemanager.StartServiceCtrlDispatcher()
