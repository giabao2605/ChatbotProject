"""Preview or relocate the existing Cloudflared command; never start/stop or copy."""
import argparse
import hashlib
import json
from pathlib import Path
import subprocess

import win32security
import win32service as scm

ROOT = Path(__file__).resolve().parents[2]
OLD = ROOT / '.local/domain-service-20261021/cloudflared.exe'
NEW = Path('C:/ProgramData/MechRAGServices/bin/cloudflared.exe')
TOKEN = Path('C:/ProgramData/cloudflared/token')
TRUSTED = {'S-1-5-18', 'S-1-5-32-544', 'S-1-5-21-147480584-2920096037-2778548978-2139'}
WINDOWS_INSTALLER = 'S-1-5-80-956008885-3418522649-1831038044-1853292631-2271478464'


def command(binary):
    return subprocess.list2cmdline([str(binary), 'tunnel', 'run', '--token-file', str(TOKEN)])


def reject_reparse(path):
    for item in (path, *path.parents):
        if item.lstat().st_file_attributes & 0x400:
            raise PermissionError('Reparse point in executable path')


def verify_acl(path, replace_only=False):
    descriptor = win32security.GetNamedSecurityInfo(str(path), win32security.SE_FILE_OBJECT,
        win32security.OWNER_SECURITY_INFORMATION | win32security.DACL_SECURITY_INFORMATION)
    owner = win32security.ConvertSidToStringSid(descriptor.GetSecurityDescriptorOwner())
    # Windows owns volume ancestors through TrustedInstaller, not the service binary.
    if owner not in TRUSTED and not (replace_only and owner == WINDOWS_INSTALLER):
        raise PermissionError('Executable path owner is not trusted')
    acl = descriptor.GetSecurityDescriptorDacl()
    if acl is None:
        raise PermissionError('Executable path has no DACL')
    mask = 0x500D0040 if replace_only else 0x500D0156
    for index in range(acl.GetAceCount()):
        ace = acl.GetAce(index)
        if ace[0][1] & 8:  # Inherit-only ACE does not grant rights on this object.
            continue
        if ace[0][0] == win32security.ACCESS_DENIED_ACE_TYPE:
            continue
        if ace[0][0] != win32security.ACCESS_ALLOWED_ACE_TYPE:
            raise PermissionError('Unsupported executable path ACL entry')
        if ace[1] & mask and win32security.ConvertSidToStringSid(ace[2]) not in TRUSTED:
            raise PermissionError('Executable path is writable by an untrusted identity')


def verify_binaries():
    for binary in (OLD, NEW):
        reject_reparse(binary)
        if not binary.is_file() or binary.stat().st_nlink != 1:
            raise PermissionError('Executable must be an independent regular file')
        for index, item in enumerate((binary, *binary.parents)):
            # Ancestors must not allow replacement; unrelated child creation is safe.
            verify_acl(item, replace_only=index > 2)
    with OLD.open('rb') as handle:
        expected = hashlib.file_digest(handle, 'sha256').hexdigest()
    with NEW.open('rb') as handle:
        actual = hashlib.file_digest(handle, 'sha256').hexdigest()
    if actual != expected:
        raise ValueError('Cloudflared executable bytes differ')
    return expected


def relocate(rollback=False, apply=False):
    before, after = (command(NEW), command(OLD)) if rollback else (command(OLD), command(NEW))
    manager = scm.OpenSCManager(None, None, scm.SC_MANAGER_CONNECT)
    service = None
    try:
        access = scm.SERVICE_QUERY_CONFIG | scm.SERVICE_QUERY_STATUS
        if apply:
            access |= scm.SERVICE_CHANGE_CONFIG
        service = scm.OpenService(manager, 'Cloudflared', access)
        config = scm.QueryServiceConfig(service)
        if (config[0] != scm.SERVICE_WIN32_OWN_PROCESS or config[3] != before
                or config[7].lower() != 'localsystem'):
            raise RuntimeError('Cloudflared identity or command differs')
        state = scm.QueryServiceStatus(service)[1]
        if apply and state != scm.SERVICE_STOPPED:
            raise RuntimeError('Cloudflared must be stopped before relocation')
        result = {'service': 'Cloudflared', 'from': before, 'to': after,
                  'state': state, 'applied': False, 'ready': False}
        try:
            digest = verify_binaries()
        except (OSError, ValueError, win32security.error) as error:
            if apply:
                raise
            return {**result, 'binary_error': type(error).__name__}
        if apply:
            if (verify_binaries() != digest or scm.QueryServiceConfig(service) != config
                    or scm.QueryServiceStatus(service)[1] != scm.SERVICE_STOPPED):
                raise RuntimeError('Cloudflared changed during relocation')
            scm.ChangeServiceConfig(service, scm.SERVICE_NO_CHANGE, scm.SERVICE_NO_CHANGE,
                                    scm.SERVICE_NO_CHANGE, after, None, 0, None, None, None, None)
        return {**result, 'sha256': digest, 'ready': state == scm.SERVICE_STOPPED, 'applied': apply}
    finally:
        if service is not None:
            scm.CloseServiceHandle(service)
        scm.CloseServiceHandle(manager)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--rollback', action='store_true')
    parser.add_argument('--apply', action='store_true')
    options = parser.parse_args()
    print(json.dumps(relocate(options.rollback, options.apply), indent=2))
