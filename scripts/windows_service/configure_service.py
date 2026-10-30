"""Create/configure only; never starts or stops the application."""
import argparse
import subprocess
import win32service as scm
from service_runtime import NAMES, HOST_PYTHON, ROOT, service_name


def service_command(role, legacy=False):
    service_name(role)
    directory = '.local/domain-service-20261021' if legacy else 'scripts/windows_service'
    return subprocess.list2cmdline([str(HOST_PYTHON), '-X', 'utf8',
                                   str(ROOT / directory / 'service_host.py'), role])


def configure(role, enable=False):
    name = service_name(role)
    manager = scm.OpenSCManager(None, None, scm.SC_MANAGER_CONNECT | scm.SC_MANAGER_CREATE_SERVICE)
    service = None
    command = service_command(role)
    try:
        try:
            service = scm.OpenService(manager, name, scm.SERVICE_ALL_ACCESS)
        except Exception as error:
            if getattr(error, 'winerror', None) != 1060:
                raise
            if enable:
                raise RuntimeError('Create service before enabling automatic startup') from None
            service = scm.CreateService(manager, name, name, scm.SERVICE_ALL_ACCESS,
                                        scm.SERVICE_WIN32_OWN_PROCESS, scm.SERVICE_DEMAND_START,
                                        scm.SERVICE_ERROR_NORMAL, command, None, 0,
                                        ['MSSQL$SQLEXPRESS'], 'NT SERVICE\\' + name, None)
        config = scm.QueryServiceConfig(service)
        if config[3] != command or config[7].lower() != ('NT SERVICE\\' + name).lower():
            raise RuntimeError('Existing service configuration differs; refusing overwrite')
        if scm.QueryServiceStatus(service)[1] != scm.SERVICE_STOPPED:
            raise RuntimeError('Service must be stopped before configuring')
        scm.ChangeServiceConfig2(service, scm.SERVICE_CONFIG_SERVICE_SID_INFO, scm.SERVICE_SID_TYPE_UNRESTRICTED)
        scm.ChangeServiceConfig2(service, scm.SERVICE_CONFIG_FAILURE_ACTIONS,
                                 {'ResetPeriod': 86400, 'RebootMsg': '', 'Command': '',
                                  'Actions': [(scm.SC_ACTION_RESTART, 10000),
                                              (scm.SC_ACTION_RESTART, 30000),
                                              (scm.SC_ACTION_RESTART, 60000)]})
        scm.ChangeServiceConfig2(service, scm.SERVICE_CONFIG_FAILURE_ACTIONS_FLAG, True)
        if enable:
            scm.ChangeServiceConfig(service, scm.SERVICE_NO_CHANGE, scm.SERVICE_AUTO_START,
                                     scm.SERVICE_NO_CHANGE, None, None, 0, None, None, None, None)
            scm.ChangeServiceConfig2(service, scm.SERVICE_CONFIG_DELAYED_AUTO_START_INFO, True)
    finally:
        if service is not None:
            scm.CloseServiceHandle(service)
        scm.CloseServiceHandle(manager)


def relocate(role, rollback=False, apply=False):
    """Change only the command between the known layouts; never start or stop."""
    name = service_name(role)
    before = service_command(role, legacy=not rollback)
    after = service_command(role, legacy=rollback)
    target = ROOT / ('.local/domain-service-20261021' if rollback else 'scripts/windows_service')
    manager = scm.OpenSCManager(None, None, scm.SC_MANAGER_CONNECT)
    service = None
    try:
        access = scm.SERVICE_QUERY_CONFIG | scm.SERVICE_QUERY_STATUS
        if apply:
            access |= scm.SERVICE_CHANGE_CONFIG
        service = scm.OpenService(manager, name, access)
        config = scm.QueryServiceConfig(service)
        if (config[0] != scm.SERVICE_WIN32_OWN_PROCESS or config[3] != before
                or config[7].lower() != ('NT SERVICE\\' + name).lower()):
            raise RuntimeError('Service identity or command differs; refusing relocation')
        observed_state = scm.QueryServiceStatus(service)[1]
        if apply and observed_state != scm.SERVICE_STOPPED:
            raise RuntimeError('Service must be stopped before relocation')
        if apply:
            if not (target / 'service_host.py').is_file() or not (target / 'service_runtime.py').is_file():
                raise FileNotFoundError('Relocation target launcher is missing')
            # Recheck the observed configuration immediately before the sole mutation.
            if (scm.QueryServiceConfig(service) != config
                    or scm.QueryServiceStatus(service)[1] != scm.SERVICE_STOPPED):
                raise RuntimeError('Service changed during relocation')
            scm.ChangeServiceConfig(service, scm.SERVICE_NO_CHANGE, scm.SERVICE_NO_CHANGE,
                                    scm.SERVICE_NO_CHANGE, after, None, 0, None, None, None, None)
        return {'service': name, 'from': before, 'to': after, 'applied': apply,
                'observed_state': observed_state}
    finally:
        if service is not None:
            scm.CloseServiceHandle(service)
        scm.CloseServiceHandle(manager)


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('role', choices=NAMES)
    parser.add_argument('--enable-automatic', action='store_true')
    parser.add_argument('--relocate', action='store_true')
    parser.add_argument('--rollback', action='store_true')
    parser.add_argument('--apply', action='store_true')
    args = parser.parse_args()
    if args.relocate:
        if args.enable_automatic:
            parser.error('Relocation cannot change startup policy')
        import json
        print(json.dumps(relocate(args.role, args.rollback, args.apply), indent=2))
    else:
        if args.rollback or args.apply:
            parser.error('--rollback and --apply require --relocate')
        configure(args.role, args.enable_automatic)
