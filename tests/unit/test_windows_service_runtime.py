"""Contract checks for the relocated production launcher; no live services."""
import importlib.util
import json
from pathlib import Path
import subprocess
import sys
from datetime import datetime, timedelta, timezone
from unittest.mock import Mock
from types import SimpleNamespace

import pytest

ROOT = Path(__file__).resolve().parents[2]
LAUNCHER = ROOT / "scripts/windows_service/service_runtime.py"
SPEC = importlib.util.spec_from_file_location("canonical_service_runtime", LAUNCHER)
runtime = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(runtime)


def request(**overrides):
    return {"role": "app", "nonce": "a" * 48, "source_commit": "b" * 40,
            "created_at": datetime.now(timezone.utc).isoformat(), **overrides}


def test_root_is_independent_of_working_directory(tmp_path):
    code = ("import importlib.util,json; "
            f"s=importlib.util.spec_from_file_location('r',{str(LAUNCHER)!r}); "
            "m=importlib.util.module_from_spec(s); s.loader.exec_module(m); "
            "print(json.dumps([str(m.ROOT),str(m.PYTHON)]))")
    values = json.loads(subprocess.check_output([sys.executable, "-B", "-c", code], cwd=tmp_path, text=True))
    assert values == [str(ROOT), str(ROOT / "chat_env/Scripts/python.exe")]


def test_clean_checkout_rejects_untracked_files_hidden_by_git_config(tmp_path):
    subprocess.run(['git', 'init', '-q', str(tmp_path)], check=True)
    subprocess.run(['git', '-C', str(tmp_path), 'config', 'status.showUntrackedFiles', 'no'], check=True)
    runtime.assert_clean_checkout(tmp_path)
    (tmp_path / 'unreviewed.py').write_text('unexpected = True\n')
    assert not subprocess.check_output(['git', '-C', str(tmp_path), 'status', '--porcelain'])
    with pytest.raises(ValueError, match='not clean'):
        runtime.assert_clean_checkout(tmp_path)


def test_clean_checkout_rejects_submodule_changes_hidden_by_git_config(tmp_path):
    parent, child = tmp_path / 'parent', tmp_path / 'child'
    for repo in (parent, child):
        subprocess.run(['git', 'init', '-q', str(repo)], check=True)
        (repo / 'source.txt').write_text('reviewed\n')
        subprocess.run(['git', '-C', str(repo), 'add', 'source.txt'], check=True)
        subprocess.run(['git', '-C', str(repo), '-c', 'user.name=Test', '-c',
                        'user.email=test@example.invalid', '-c', 'commit.gpgsign=false',
                        'commit', '-qm', 'fixture'], check=True)
    subprocess.run(['git', '-C', str(parent), '-c', 'protocol.file.allow=always',
                    'submodule', 'add', '-q', str(child), 'nested'], check=True)
    subprocess.run(['git', '-C', str(parent), '-c', 'user.name=Test', '-c',
                    'user.email=test@example.invalid', '-c', 'commit.gpgsign=false',
                    'commit', '-qm', 'submodule fixture'], check=True)
    subprocess.run(['git', '-C', str(parent), 'config', 'submodule.nested.ignore', 'all'], check=True)
    runtime.assert_clean_checkout(parent)
    (parent / 'nested/source.txt').write_text('unreviewed\n')
    assert not subprocess.check_output(['git', '-C', str(parent), 'status', '--porcelain'])
    with pytest.raises(ValueError, match='not clean'):
        runtime.assert_clean_checkout(parent)


@pytest.mark.parametrize('failed_gate', ['clean', 'signature', 'activation'])
def test_identity_preflight_records_rejection_before_sql(tmp_path, monkeypatch, failed_gate):
    import hashlib
    from mech_chatbot.governance import feature_activation
    state, repo = tmp_path / 'state', tmp_path / 'repo'
    (state / 'preflight-requests').mkdir(parents=True)
    (state / 'rag').mkdir()
    repo.mkdir()
    payload = request(role='rag')
    (state / 'preflight-requests/rag.json').write_text(json.dumps(payload))
    bundle_bytes = json.dumps({'source_commit': 'b' * 40}).encode()
    (repo / 'bundle.json').write_bytes(bundle_bytes)
    settings = SimpleNamespace(RAG_ACTIVATION_BUNDLE_PATH='bundle.json',
        RAG_DEPLOYMENT_GIT_SHA='b' * 40,
        RAG_ACTIVATION_BUNDLE_SHA256=hashlib.sha256(bundle_bytes).hexdigest())
    stages = []
    monkeypatch.setattr(runtime, 'ROOT', repo)
    monkeypatch.setattr(runtime, 'STATE', state)
    monkeypatch.setattr(runtime, 'verify_request_acl', lambda path: stages.append('acl'))
    token = Mock()
    monkeypatch.setitem(sys.modules, 'win32api', SimpleNamespace(GetCurrentProcess=lambda: 1))
    monkeypatch.setitem(sys.modules, 'win32security', SimpleNamespace(TOKEN_QUERY=1, TokenUser=1,
        OpenProcessToken=lambda *args: token, GetTokenInformation=lambda *args: ['sid'],
        LookupAccountName=lambda *args: ['sid'], ConvertSidToStringSid=lambda sid: sid))
    for name in ('bcrypt', 'pyarrow', 'onnxruntime', 'numpy'):
        monkeypatch.setitem(sys.modules, name, SimpleNamespace())
    monkeypatch.setattr(runtime, 'settings_for_role', lambda role: settings)
    monkeypatch.setattr(feature_activation, 'current_git_commit', lambda root: 'b' * 40)

    def clean(root):
        stages.append('clean')
        if failed_gate == 'clean':
            raise ValueError('Source checkout is not clean')

    def signature(bundle, **kwargs):
        stages.append('signature')
        return failed_gate != 'signature'

    def activation(*args, **kwargs):
        stages.append('activation')
        return SimpleNamespace(valid=False, live_authorized=False)

    monkeypatch.setattr(runtime, 'assert_clean_checkout', clean)
    monkeypatch.setattr(feature_activation, 'release_signature_valid', signature)
    monkeypatch.setattr(feature_activation, 'activation_status', activation)
    monkeypatch.setitem(sys.modules, 'mech_chatbot.api.rag_server',
                        SimpleNamespace(_environment_snapshot=lambda value: {}))
    sql = Mock(side_effect=AssertionError('SQL must not be reached'))
    monkeypatch.setitem(sys.modules, 'mech_chatbot.db.engine', SimpleNamespace(create_db_engine=sql))
    assert runtime.identity_preflight('rag') is False
    result = json.loads((state / 'rag/identity-preflight.json').read_text())
    assert result['ok'] is False and result['error_type'] == 'ValueError'
    assert result['nonce'] == payload['nonce']
    expected = ['acl', 'clean', 'signature', 'activation']
    assert stages == expected[:expected.index(failed_gate) + 1]
    sql.assert_not_called()
    token.Close.assert_called_once()


def test_preflight_requires_fresh_request_matching_role_and_actual_head():
    value = request()
    assert runtime.validate_preflight_request(value, "app", "b" * 40) == value
    for invalid in (request(role="rag"), request(source_commit="c" * 40),
                    request(nonce="tiny"), request(source_commit=""),
                    request(created_at=(datetime.now(timezone.utc) - timedelta(hours=3)).isoformat()),
                    request(created_at=datetime.now().isoformat())):
        with pytest.raises(ValueError):
            runtime.validate_preflight_request(invalid, "app", "b" * 40)
    with pytest.raises(ValueError):
        runtime.validate_preflight_request(request(), "app", None)


def test_signed_release_must_match_authorized_head(monkeypatch):
    from mech_chatbot.governance import feature_activation
    signature = Mock(return_value=True)
    monkeypatch.setattr(feature_activation, "release_signature_valid", signature)
    runtime.validate_release({"source_commit": "b" * 40}, "b" * 40)
    signature.assert_called_once_with({"source_commit": "b" * 40}, root=runtime.ROOT)
    for bundle in ({"source_commit": "c" * 40}, {}, None):
        with pytest.raises(ValueError):
            runtime.validate_release(bundle, "b" * 40)
    signature.return_value = False
    with pytest.raises(ValueError):
        runtime.validate_release({"source_commit": "b" * 40}, "b" * 40)


@pytest.mark.parametrize('failure', ['deployment', 'digest', 'invalid', 'not_live', 'commit', 'activation_digest'])
def test_activation_binds_settings_digest_and_live_authorization(monkeypatch, failure):
    import hashlib
    from mech_chatbot.governance import feature_activation
    digest = hashlib.sha256(b'bundle').hexdigest()
    settings = SimpleNamespace(RAG_DEPLOYMENT_GIT_SHA='b' * 40, RAG_ACTIVATION_BUNDLE_SHA256=digest)
    status = SimpleNamespace(valid=True, live_authorized=True, decision_source_commit='b' * 40, bundle_sha256=digest)
    serializer = Mock(return_value={'effective': 'settings'})
    monkeypatch.setitem(sys.modules, 'mech_chatbot.api.rag_server', SimpleNamespace(_environment_snapshot=serializer))
    checker = Mock(return_value=status)
    monkeypatch.setattr(feature_activation, 'activation_status', checker)
    runtime.validate_activation(settings, 'b' * 40, b'bundle')
    checker.assert_called_once_with({'effective': 'settings'}, root=runtime.ROOT, current_commit='b' * 40)
    if failure == 'deployment':
        settings.RAG_DEPLOYMENT_GIT_SHA = 'c' * 40
    elif failure == 'digest':
        settings.RAG_ACTIVATION_BUNDLE_SHA256 = '0' * 64
    elif failure == 'invalid':
        status.valid = False
    elif failure == 'not_live':
        status.live_authorized = False
    elif failure == 'commit':
        status.decision_source_commit = 'c' * 40
    else:
        status.bundle_sha256 = '0' * 64
    with pytest.raises(ValueError):
        runtime.validate_activation(settings, 'b' * 40, b'bundle')


def test_environment_and_stopping_preserve_runtime_contract():
    parent = {"PATH": "old", "OTHER": "keep"}
    env = runtime.child_environment(parent, Path("C:/project"))
    assert parent == {"PATH": "old", "OTHER": "keep"}
    assert env["GIT_CONFIG_VALUE_0"] == "C:/project"
    assert env["HF_HUB_OFFLINE"] == "1"
    assert env["INGESTION_WORKER_READY_FILE"].endswith("ready.txt")
    store = Mock()
    with pytest.raises(SystemExit):
        runtime.StopAwareJobStore(store, lambda: True).claim_next("worker")
    store.claim_next.assert_not_called()
    wrapper = runtime.StopAwareJobStore(store, lambda: False)
    assert wrapper.claim_next("worker") is store.claim_next.return_value
    wrapper.mark_failed("job", "reason")
    store.mark_failed.assert_called_once_with("job", "reason")
    with pytest.raises(SystemExit):
        runtime.StopAwareClock(lambda: True).sleep(60)
    assert runtime.expected_exit(True, 0)
    assert not runtime.expected_exit(False, 0)
    assert not runtime.expected_exit(True, 1)


@pytest.mark.parametrize("values", [("other", "NT SERVICE\\MechRAGApp", 0, 0),
    ("Mech_Chatbot_DB", "dbo", 0, 0), ("Mech_Chatbot_DB", "NT SERVICE\\MechRAGApp", 1, 0),
    ("Mech_Chatbot_DB", "NT SERVICE\\MechRAGApp", 0, 1)])
def test_sql_identity_rejects_wrong_or_elevated_principal(values):
    runtime.validate_sql_identity("app", "Mech_Chatbot_DB", "NT SERVICE\\MechRAGApp", 0, 0)
    with pytest.raises(PermissionError):
        runtime.validate_sql_identity("app", *values)
