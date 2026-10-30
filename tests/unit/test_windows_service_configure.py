"""SCM mutations are mocked; tests never open the live service manager."""
import importlib.util
from pathlib import Path
import sys
from unittest.mock import Mock, patch

import pytest

pytest.importorskip('win32service')
ROOT = Path(__file__).resolve().parents[2]
DIRECTORY = ROOT / 'scripts/windows_service'


def load(name):
    spec = importlib.util.spec_from_file_location(name, DIRECTORY / (name + '.py'))
    module = importlib.util.module_from_spec(spec)
    with patch.dict(sys.modules, {name: module}):
        spec.loader.exec_module(module)
    return module


runtime = load('service_runtime')
with patch.dict(sys.modules, {'service_runtime': runtime}):
    configure = load('configure_service')
    host = load('service_host')


def fake_scm(command, state=None, identity='NT SERVICE\\MechRAGApp'):
    fake = Mock(spec=configure.scm)
    for name in ('SC_MANAGER_CONNECT', 'SC_MANAGER_CREATE_SERVICE', 'SERVICE_QUERY_CONFIG',
                 'SERVICE_QUERY_STATUS', 'SERVICE_CHANGE_CONFIG', 'SERVICE_ALL_ACCESS',
                 'SERVICE_NO_CHANGE', 'SERVICE_STOPPED', 'SERVICE_WIN32_OWN_PROCESS'):
        setattr(fake, name, getattr(configure.scm, name))
    fake.QueryServiceConfig.return_value = (configure.scm.SERVICE_WIN32_OWN_PROCESS, 2, 1,
        command, '', 0, ['MSSQL$SQLEXPRESS'], identity, 'MechRAGApp')
    fake.QueryServiceStatus.return_value = (0, state or configure.scm.SERVICE_STOPPED, 0, 0, 0, 0, 0)
    return fake


def test_relocation_preview_and_apply_use_exact_old_new_pair():
    old, new = configure.service_command('app', legacy=True), configure.service_command('app')
    fake = fake_scm(old)
    with patch.object(configure, 'scm', fake):
        plan = configure.relocate('app')
        assert plan == {'service': 'MechRAGApp', 'from': old, 'to': new, 'applied': False,
                        'observed_state': fake.SERVICE_STOPPED}
        fake.ChangeServiceConfig.assert_not_called()
        result = configure.relocate('app', apply=True)
    assert result['applied']
    args = fake.ChangeServiceConfig.call_args.args
    assert args[1:5] == (fake.SERVICE_NO_CHANGE, fake.SERVICE_NO_CHANGE, fake.SERVICE_NO_CHANGE, new)
    fake.StartService.assert_not_called()
    fake.ControlService.assert_not_called()


def test_running_service_can_be_previewed_without_write_access_or_mutation():
    fake = fake_scm(configure.service_command('app', legacy=True), configure.scm.SERVICE_RUNNING)
    with patch.object(configure, 'scm', fake):
        plan = configure.relocate('app')
    assert plan['observed_state'] == configure.scm.SERVICE_RUNNING
    assert not plan['applied']
    assert fake.OpenService.call_args.args[2] == fake.SERVICE_QUERY_CONFIG | fake.SERVICE_QUERY_STATUS
    fake.ChangeServiceConfig.assert_not_called()
    fake.StartService.assert_not_called()
    fake.ControlService.assert_not_called()


def test_rollback_requires_canonical_command_and_restores_only_legacy_command():
    fake = fake_scm(configure.service_command('app'))
    with patch.object(configure, 'scm', fake), patch.object(Path, 'is_file', return_value=True):
        plan = configure.relocate('app', rollback=True, apply=True)
    assert plan['to'] == configure.service_command('app', legacy=True)
    assert fake.ChangeServiceConfig.call_args.args[4] == plan['to']


@pytest.mark.parametrize('command,state,identity', [
    ('foreign.exe', None, 'NT SERVICE\\MechRAGApp'),
    (None, configure.scm.SERVICE_RUNNING, 'NT SERVICE\\MechRAGApp'),
    (None, None, 'LocalSystem'),
])
def test_relocation_refuses_drift_or_running_service(command, state, identity):
    fake = fake_scm(command or configure.service_command('app', legacy=True), state, identity)
    with patch.object(configure, 'scm', fake), pytest.raises(RuntimeError):
        configure.relocate('app', apply=True)
    fake.ChangeServiceConfig.assert_not_called()
    fake.CloseServiceHandle.assert_called()


def test_unknown_role_never_opens_scm():
    with patch.object(configure, 'scm') as fake, pytest.raises(ValueError):
        configure.relocate('foreign', apply=True)
    fake.OpenSCManager.assert_not_called()


def test_original_configure_still_rejects_foreign_command():
    fake = fake_scm('foreign.exe')
    with patch.object(configure, 'scm', fake), pytest.raises(RuntimeError):
        configure.configure('app')
    fake.ChangeServiceConfig.assert_not_called()
    fake.ChangeServiceConfig2.assert_not_called()


def test_child_command_uses_canonical_runtime_and_containment(tmp_path):
    process, thread, job = Mock(), Mock(), Mock()
    with patch.object(host, 'win32job') as jobs, patch.object(host, 'win32file') as files, \
         patch.object(host, 'win32process') as processes:
        jobs.CreateJobObject.return_value = job
        jobs.QueryInformationJobObject.return_value = {'BasicLimitInformation': {}}
        processes.CreateProcess.return_value = process, thread, 101, 102
        result = host.create_child('app', tmp_path / 'stop', preflight=True)
    assert result == (job, process)
    command = processes.CreateProcess.call_args.args[1]
    assert str(ROOT / 'scripts/windows_service/service_runtime.py') in command
    assert '--preflight' in command
    assert processes.CreateProcess.call_args.args[7] == str(ROOT)
    jobs.AssignProcessToJobObject.assert_called_once_with(job, process)
    processes.ResumeThread.assert_called_once_with(thread)
    thread.Close.assert_called_once()


def test_failed_child_assignment_is_terminated_and_handles_closed(tmp_path):
    process, thread, job = Mock(), Mock(), Mock()
    with patch.object(host, 'win32job') as jobs, patch.object(host, 'win32file'), \
         patch.object(host, 'win32process') as processes:
        jobs.CreateJobObject.return_value = job
        jobs.QueryInformationJobObject.return_value = {'BasicLimitInformation': {}}
        processes.CreateProcess.return_value = process, thread, 101, 102
        jobs.AssignProcessToJobObject.side_effect = OSError('cannot contain')
        with pytest.raises(OSError):
            host.create_child('app', tmp_path / 'stop')
    processes.TerminateProcess.assert_called_once_with(process, 1)
    process.Close.assert_called_once()
    thread.Close.assert_called_once()
    job.Close.assert_called_once()
