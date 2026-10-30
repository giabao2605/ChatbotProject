"""Cloudflared relocation is verified without touching Windows services."""
import importlib.util
from pathlib import Path
from unittest.mock import Mock, patch
from types import SimpleNamespace

import pytest

pytest.importorskip('win32service')
SPEC = importlib.util.spec_from_file_location('cloudflared_relocation',
    Path(__file__).resolve().parents[2] / 'scripts/windows_service/relocate_cloudflared.py')
module = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(module)


def fake_scm(rollback=False, state=None, command=None, account='LocalSystem'):
    fake = Mock(spec=module.scm)
    for name in ('SC_MANAGER_CONNECT', 'SERVICE_QUERY_CONFIG', 'SERVICE_QUERY_STATUS',
                 'SERVICE_CHANGE_CONFIG', 'SERVICE_NO_CHANGE', 'SERVICE_STOPPED',
                 'SERVICE_RUNNING', 'SERVICE_WIN32_OWN_PROCESS'):
        setattr(fake, name, getattr(module.scm, name))
    fake.QueryServiceConfig.return_value = (fake.SERVICE_WIN32_OWN_PROCESS, 2, 1,
        command or module.command(module.NEW if rollback else module.OLD), '', 0, [], account, '')
    fake.QueryServiceStatus.return_value = (0, state or fake.SERVICE_STOPPED, 0, 0, 0, 0, 0)
    return fake


def test_preview_reports_running_and_unprepared_without_mutation():
    fake = fake_scm(state=module.scm.SERVICE_RUNNING)
    with patch.object(module, 'scm', fake), patch.object(module, 'verify_binaries', side_effect=FileNotFoundError):
        result = module.relocate()
    assert not result['applied'] and not result['ready']
    assert result['binary_error'] == 'FileNotFoundError'
    fake.ChangeServiceConfig.assert_not_called()
    fake.StartService.assert_not_called()
    fake.ControlService.assert_not_called()
    assert fake.OpenService.call_args.args[2] == fake.SERVICE_QUERY_CONFIG | fake.SERVICE_QUERY_STATUS


@pytest.mark.parametrize('rollback', [False, True])
def test_apply_changes_only_known_command_and_keeps_token_path(rollback):
    fake = fake_scm(rollback=rollback)
    with patch.object(module, 'scm', fake), patch.object(module, 'verify_binaries', return_value='a' * 64):
        result = module.relocate(rollback=rollback, apply=True)
    assert result['applied']
    args = fake.ChangeServiceConfig.call_args.args
    assert args[1:4] == (fake.SERVICE_NO_CHANGE,) * 3
    assert args[4] == module.command(module.OLD if rollback else module.NEW)
    assert args[4].endswith('tunnel run --token-file C:\\ProgramData\\cloudflared\\token')
    fake.StartService.assert_not_called()
    fake.ControlService.assert_not_called()


@pytest.mark.parametrize('options', [dict(command='foreign.exe'), dict(account='other'),
    dict(state=module.scm.SERVICE_RUNNING)])
def test_apply_rejects_command_account_or_running_state(options):
    fake = fake_scm(**options)
    with patch.object(module, 'scm', fake), pytest.raises(RuntimeError):
        module.relocate(apply=True)
    fake.ChangeServiceConfig.assert_not_called()


def test_apply_rejects_configuration_or_binary_drift():
    for drift in ('config', 'binary'):
        fake = fake_scm()
        if drift == 'config':
            fake.QueryServiceConfig.side_effect = [fake.QueryServiceConfig.return_value, ('changed',)]
        with patch.object(module, 'scm', fake), patch.object(module, 'verify_binaries',
                side_effect=['a' * 64, ('b' if drift == 'binary' else 'a') * 64]), pytest.raises(RuntimeError):
            module.relocate(apply=True)
        fake.ChangeServiceConfig.assert_not_called()


def test_file_identity_requires_same_bytes_and_no_reparse(tmp_path):
    old, new = tmp_path / 'old.exe', tmp_path / 'new.exe'
    old.write_bytes(b'known binary')
    new.write_bytes(b'known binary')
    with patch.object(module, 'OLD', old), patch.object(module, 'NEW', new), \
            patch.object(module, 'verify_acl'):
        assert len(module.verify_binaries()) == 64
        new.write_bytes(b'different binary')
        with pytest.raises(ValueError):
            module.verify_binaries()
        with patch.object(module, 'reject_reparse', side_effect=PermissionError), pytest.raises(PermissionError):
            module.verify_binaries()


def test_acl_rejects_untrusted_write_and_null_dacl():
    security = module.win32security
    descriptor = Mock()
    descriptor.GetSecurityDescriptorOwner.return_value = 'S-1-5-18'
    acl = Mock()
    descriptor.GetSecurityDescriptorDacl.return_value = acl
    acl.GetAceCount.return_value = 1
    acl.GetAce.return_value = ((security.ACCESS_ALLOWED_ACE_TYPE, 0), 2, 'S-1-1-0')
    with patch.object(security, 'GetNamedSecurityInfo', return_value=descriptor), \
            patch.object(security, 'ConvertSidToStringSid', side_effect=lambda value: value):
        with pytest.raises(PermissionError):
            module.verify_acl(Path('C:/fake'))
        acl.GetAce.return_value = ((security.ACCESS_ALLOWED_ACE_TYPE, 0), 2, 'S-1-5-18')
        module.verify_acl(Path('C:/fake'))
        descriptor.GetSecurityDescriptorDacl.return_value = None
        with pytest.raises(PermissionError):
            module.verify_acl(Path('C:/fake'))


def test_reparse_on_ancestor_is_rejected():
    def attributes(path):
        return SimpleNamespace(st_file_attributes=0x400 if path.name == 'linked' else 0)
    with patch.object(Path, 'lstat', attributes), pytest.raises(PermissionError):
        module.reject_reparse(Path('C:/linked/bin/cloudflared.exe'))


def test_apply_never_changes_service_when_binary_is_untrusted():
    fake = fake_scm()
    with patch.object(module, 'scm', fake), patch.object(module, 'verify_binaries',
            side_effect=PermissionError('unsafe ACL')), pytest.raises(PermissionError):
        module.relocate(apply=True)
    fake.ChangeServiceConfig.assert_not_called()
    assert fake.CloseServiceHandle.call_count == 2


def test_windows_ancestor_owner_does_not_grant_binary_or_write_trust():
    security = module.win32security
    installer = 'S-1-5-80-956008885-3418522649-1831038044-1853292631-2271478464'
    descriptor = Mock()
    descriptor.GetSecurityDescriptorOwner.return_value = installer
    acl = Mock()
    descriptor.GetSecurityDescriptorDacl.return_value = acl
    acl.GetAceCount.return_value = 0
    with patch.object(security, 'GetNamedSecurityInfo', return_value=descriptor), \
            patch.object(security, 'ConvertSidToStringSid', side_effect=lambda value: value):
        module.verify_acl(Path('C:/'), replace_only=True)
        with pytest.raises(PermissionError, match='owner'):
            module.verify_acl(Path('C:/fake/cloudflared.exe'))
        acl.GetAceCount.return_value = 1
        for trustee in (installer, 'S-1-1-0'):
            acl.GetAce.return_value = ((security.ACCESS_ALLOWED_ACE_TYPE, 0), 0x40000, trustee)
            with pytest.raises(PermissionError, match='writable'):
                module.verify_acl(Path('C:/'), replace_only=True)
