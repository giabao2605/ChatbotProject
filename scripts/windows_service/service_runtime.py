"""Production service adapter shared by the app, RAG server and worker."""
from pathlib import Path
import os
import time

ROOT = Path(__file__).resolve().parents[2]
STATE = Path(os.environ.get('PROGRAMDATA', 'C:/ProgramData')) / 'MechRAGServices'
NAMES = {'app': 'MechRAGApp', 'rag': 'MechRAGRag', 'worker': 'MechRAGWorker'}
PYTHON = ROOT / 'chat_env/Scripts/python.exe'
HOST_PYTHON = Path('C:/Users/bao.nguyen/AppData/Local/Programs/Python/Python312/python.exe')


def service_name(role):
    if role not in NAMES:
        raise ValueError('Unknown service role')
    return NAMES[role]


def child_environment(parent, root=ROOT):
    return {**parent, 'PYTHONUTF8': '1', 'PYTHONUNBUFFERED': '1',
            'INGESTION_WORKER_READY_FILE': str(STATE / 'worker-heartbeat' / 'ready.txt'),
            'PYTHONPATH': str(root / 'src'),
            'PATH': 'C:/Program Files/Git/cmd;' + parent.get('PATH', ''),
            'GIT_CONFIG_COUNT': '1', 'GIT_CONFIG_KEY_0': 'safe.directory',
            'GIT_CONFIG_VALUE_0': root.as_posix(),
            'HF_HOME': 'C:/Users/bao.nguyen/.cache/huggingface',
            'HF_HUB_OFFLINE': '1', 'TRANSFORMERS_OFFLINE': '1',
            'HF_HUB_DISABLE_TELEMETRY': '1',
            'FASTEMBED_CACHE_PATH': 'C:/Users/bao.nguyen/.codex/cache/fastembed'}


def expected_exit(stopping, code):
    return bool(stopping and code == 0)


class StopAwareJobStore:
    def __init__(self, original, stopped):
        self.original, self.stopped = original, stopped

    def claim_next(self, worker_id):
        if self.stopped():
            raise SystemExit(0)
        return self.original.claim_next(worker_id)

    def __getattr__(self, name):
        return getattr(self.original, name)


class StopAwareClock:
    def __init__(self, stopped):
        self.stopped = stopped

    def monotonic(self):
        return time.monotonic()

    def sleep(self, seconds):
        deadline = time.monotonic() + seconds
        while True:
            if self.stopped():
                raise SystemExit(0)
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                return
            time.sleep(min(remaining, 0.25))


def settings_for_role(role):
    from dotenv import dotenv_values
    from mech_chatbot.config.settings import Settings
    service_name(role)
    values = {**dotenv_values(ROOT / '.env'), **os.environ}
    if role == 'app':
        values = {**values, **dotenv_values(STATE / 'app-security.env'),
                  'APP_ENV': 'production', 'APP_COOKIE_SECURE': 'true',
                  'APP_COOKIE_SAMESITE': 'lax',
                  'APP_TRUSTED_HOSTS': 'localhost,127.0.0.1,preview.mechraglab.click,mechraglab.click'}
    return Settings.from_env({**values, 'APP_SERVER_HOST': '127.0.0.1',
                              'RAG_SERVER_HOST': '127.0.0.1'})


def run(role, stop_file):
    import asyncio
    from dataclasses import replace
    # Preserve the proven native DLL load order of the current launcher.
    import bcrypt, pyarrow, onnxruntime, numpy  # noqa: F401
    settings = settings_for_role(role)
    if role == 'worker':
        from mech_chatbot.config.logging import LoggingConfig, configure_logging
        from mech_chatbot.composition.worker_runtime import build_worker_runtime
        from mech_chatbot.workers.ingestion_worker import run_worker
        configure_logging(LoggingConfig.from_settings(settings))
        runtime = build_worker_runtime(settings, worker_id='MechRAGWorker')
        stoppable = replace(runtime, clock=StopAwareClock(stop_file.exists),
                            job_store=StopAwareJobStore(runtime.job_store, stop_file.exists))
        try:
            run_worker(stoppable)
        finally:
            runtime.close()
        return
    import uvicorn
    if role == 'app':
        from mech_chatbot.api.app_server import create_app
        application, port = create_app(settings), 8080
    else:
        from mech_chatbot.api.rag_server import create_rag_app
        application, port = create_rag_app(settings), 8100
    server = uvicorn.Server(uvicorn.Config(application, host='127.0.0.1', port=port,
                                         log_level='info', timeout_graceful_shutdown=90))
    async def main():
        async def watch():
            while not server.should_exit:
                if stop_file.exists():
                    server.should_exit = True
                    return
                await asyncio.sleep(0.25)
        watcher = asyncio.create_task(watch())
        try:
            await server.serve()
        finally:
            watcher.cancel()
    asyncio.run(main())



def validate_preflight_request(value, role, current_commit):
    import re
    from datetime import datetime, timezone
    if not isinstance(value, dict) or value.get('role') != role:
        raise ValueError('Preflight role mismatch')
    if (not re.fullmatch('[a-f0-9]{40}', str(current_commit or ''))
            or value.get('source_commit') != current_commit):
        raise ValueError('Preflight commit mismatch')
    if not re.fullmatch('[a-f0-9]{32,128}', str(value.get('nonce', ''))):
        raise ValueError('Invalid preflight nonce')
    created = datetime.fromisoformat(value['created_at'])
    if created.tzinfo is None or not -60 <= (datetime.now(timezone.utc)-created).total_seconds() <= 3600:
        raise ValueError('Preflight request expired')
    return value


def validate_release(bundle, authorized_commit):
    from mech_chatbot.governance.feature_activation import release_signature_valid
    if (not isinstance(bundle, dict) or bundle.get('source_commit') != authorized_commit
            or not release_signature_valid(bundle, root=ROOT)):
        raise ValueError('Release signature or authorized source commit mismatch')


def validate_activation(settings, authorized_commit, bundle_bytes):
    import hashlib
    from mech_chatbot.api.rag_server import _environment_snapshot
    from mech_chatbot.governance.feature_activation import activation_status
    digest = hashlib.sha256(bundle_bytes).hexdigest()
    if (settings.RAG_DEPLOYMENT_GIT_SHA != authorized_commit
            or settings.RAG_ACTIVATION_BUNDLE_SHA256 != digest):
        raise ValueError('Deployment settings do not match the authorized release')
    status = activation_status(_environment_snapshot(settings), root=ROOT, current_commit=authorized_commit)
    if (not status.valid or not status.live_authorized
            or status.decision_source_commit != authorized_commit or status.bundle_sha256 != digest):
        raise ValueError('Release is not authorized for live operation')


def verify_request_acl(path):
    import win32security
    trusted = {'S-1-5-18', 'S-1-5-32-544', 'S-1-5-21-147480584-2920096037-2778548978-2139'}
    # Request directory cannot be replaceable by the service that produces the proof.
    for item in (path.parent, path):
        info = win32security.GetNamedSecurityInfo(str(item), win32security.SE_FILE_OBJECT,
            win32security.OWNER_SECURITY_INFORMATION | win32security.DACL_SECURITY_INFORMATION)
        if win32security.ConvertSidToStringSid(info.GetSecurityDescriptorOwner()) not in trusted:
            raise PermissionError('Preflight request owner is not trusted')
        acl = info.GetSecurityDescriptorDacl()
        if acl is None:
            raise PermissionError('Preflight request has no access control')
        for index in range(acl.GetAceCount()):
            ace = acl.GetAce(index)
            if ace[0][0] == win32security.ACCESS_ALLOWED_ACE_TYPE:
                # FILE_WRITE/APPEND, DELETE_CHILD, DELETE, WRITE_DAC and WRITE_OWNER.
                if ace[1] & 0x500D0156 and win32security.ConvertSidToStringSid(ace[2]) not in trusted:
                    raise PermissionError('Preflight request is writable by untrusted identity')


def validate_sql_identity(role, database, database_user, sysadmin, db_owner):
    expected_user = 'NT SERVICE\\' + service_name(role)
    if (database != 'Mech_Chatbot_DB' or database_user.lower() != expected_user.lower()
            or sysadmin != 0 or db_owner != 0):
        raise PermissionError('Wrong SQL principal, database, or excessive privileges')


def assert_clean_checkout(root=ROOT):
    import subprocess
    if subprocess.check_output(['git', 'status', '--porcelain', '--untracked-files=all',
                                '--ignore-submodules=none'], cwd=root, text=True).strip():
        raise ValueError('Source checkout is not clean')


def identity_preflight(role):
    import hashlib
    import json
    import tempfile
    from datetime import datetime, timezone
    result = {'role': role, 'ok': False, 'source_commit': None, 'checks': []}
    request_path = STATE / 'preflight-requests' / (role + '.json')
    try:
        verify_request_acl(request_path)
        payload = request_path.read_bytes()
        request = json.loads(payload)
        import win32api, win32security
        token = win32security.OpenProcessToken(win32api.GetCurrentProcess(), win32security.TOKEN_QUERY)
        try:
            actual_sid = win32security.GetTokenInformation(token, win32security.TokenUser)[0]
        finally:
            token.Close()
        expected_sid = win32security.LookupAccountName(None, 'NT SERVICE\\' + service_name(role))[0]
        if actual_sid != expected_sid:
            raise PermissionError('Wrong Windows identity')
        result['identity_sid'] = win32security.ConvertSidToStringSid(actual_sid)
        result['checks'].append('windows_identity')
        import bcrypt, pyarrow, onnxruntime, numpy  # noqa: F401
        settings = settings_for_role(role)
        from mech_chatbot.governance.feature_activation import current_git_commit
        commit = current_git_commit(ROOT)
        request = validate_preflight_request(request, role, commit)
        result = {**result, 'nonce': request['nonce'], 'request_sha256': hashlib.sha256(payload).hexdigest()}
        assert_clean_checkout(ROOT)
        result['source_commit'] = commit
        bundle_path = Path(settings.RAG_ACTIVATION_BUNDLE_PATH)
        if not bundle_path.is_absolute():
            bundle_path = ROOT / bundle_path
        bundle_bytes = bundle_path.read_bytes()
        bundle = json.loads(bundle_bytes)
        validate_release(bundle, request['source_commit'])
        validate_activation(settings, request['source_commit'], bundle_bytes)
        result['checks'].append('signed_release')
        if role == 'app':
            from mech_chatbot.config.validate import assert_app_security_valid
            assert_app_security_valid(settings)
            with (ROOT / 'web-ui/dist/index.html').open('rb') as handle:
                handle.read(1)
            result['checks'].append('app_security_and_ui')
        from sqlalchemy import text
        from mech_chatbot.config.settings import SqlSettings
        from mech_chatbot.db.engine import create_db_engine
        engine = create_db_engine(SqlSettings.from_settings(settings))
        try:
            with engine.connect() as connection:
                login, database, database_user, transport, sysadmin, db_owner = connection.execute(text(
                    "SELECT ORIGINAL_LOGIN(), DB_NAME(), USER_NAME(), "
                    "CONNECTIONPROPERTY('net_transport'), IS_SRVROLEMEMBER('sysadmin'), IS_MEMBER('db_owner')")).one()
            result['sql_login'] = login
            result['sql_database'] = database
            result['sql_database_user'] = database_user
            result['sql_transport'] = transport
            result['sql_server'] = settings.SQL_SERVER
            # SSPI may report the machine as ORIGINAL_LOGIN; USER_NAME binds the granted service SID.
            validate_sql_identity(role, database, database_user, sysadmin, db_owner)
        finally:
            engine.dispose()
        result['checks'].append('sql_identity_and_database')
        if role in ('rag', 'worker'):
            cache = Path(os.environ['HF_HOME']) / 'hub' / 'models--BAAI--bge-m3'
            files = list((cache / 'snapshots').rglob('*'))
            readable = 0
            for item in files:
                if item.is_file():
                    with item.open('rb') as handle:
                        handle.read(1)
                    readable += 1
            if not readable:
                raise FileNotFoundError('Embedding model cache absent')
            fastembed = Path(os.environ['FASTEMBED_CACHE_PATH'])
            if not fastembed.is_dir() or not any(fastembed.iterdir()):
                raise FileNotFoundError('Fastembed cache absent')
            result['checks'].append('model_cache_read')
        directories = [STATE / role, ROOT / 'logs', ROOT / 'data/raw/Uploads',
                       ROOT / 'data/processed', ROOT / 'data/cache']
        for directory in directories:
            with tempfile.NamedTemporaryFile(prefix='.service-probe-', dir=directory, delete=True) as handle:
                handle.write(b'preflight')
                handle.flush()
                handle.seek(0)
                if handle.read() != b'preflight':
                    raise OSError('Probe verification failed')
        result['checks'].append('runtime_directories_write')
        result['ok'] = True
    except Exception as error:
        result['error_type'] = type(error).__name__
    result['finished_at'] = datetime.now(timezone.utc).isoformat()
    destination = STATE / role / 'identity-preflight.json'
    temporary = destination.with_suffix('.tmp')
    temporary.write_text(json.dumps(result, indent=2) + '\n', encoding='utf-8')
    temporary.replace(destination)
    # Failed probes intentionally stop cleanly; the caller must inspect ok and nonce.
    return result['ok']


if __name__ == '__main__':
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument('role', choices=NAMES)
    parser.add_argument('--stop-file', type=Path, required=True)
    parser.add_argument('--preflight', action='store_true')
    args = parser.parse_args()
    os.chdir(ROOT)
    os.environ.update(child_environment(os.environ))
    if args.preflight:
        identity_preflight(args.role)
    else:
        run(args.role, args.stop_file)
