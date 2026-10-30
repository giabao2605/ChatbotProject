"""Cleanup operates only on reviewed generated files in an isolated checkout."""
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys

import pytest


SCRIPT = Path(__file__).resolve().parents[2] / 'scripts/ops/safe_repo_cleanup.py'


@pytest.fixture
def cleanup():
    spec = importlib.util.spec_from_file_location('safe_repo_cleanup', SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def repo(tmp_path):
    subprocess.run(['git', 'init', '-q', str(tmp_path)], check=True)
    return tmp_path


def candidate(repo, name='tmp/coverage.json'):
    path = repo / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text('generated output', encoding='utf-8')
    return path


def manifest(cleanup, repo, name='tmp/coverage.json', protected=()):
    return {
        'version': 1, 'root': str(repo), 'protected_paths': list(protected),
        'entries': [{'path': name, 'classification': 'disposable',
                     'reason': 'Completed isolated test output',
                     'evidence': ['reviewed-test-run'],
                     'snapshot': cleanup.inspect_candidate(repo, name, protected)}],
    }


def test_cli_defaults_to_preview_without_touching_candidate(cleanup, repo, tmp_path):
    path = candidate(repo)
    plan = tmp_path / 'manifest.json'
    plan.write_text(json.dumps(manifest(cleanup, repo)), encoding='utf-8')
    result = subprocess.run([sys.executable, str(SCRIPT), '--root', str(repo),
                             '--manifest', str(plan)], capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout)['results'][0]['status'] == 'preview'
    assert path.read_text() == 'generated output'


def test_apply_removes_reviewed_tree_and_is_repeatable(cleanup, repo):
    path = candidate(repo, '.local/finished/dependency-env/test.txt')
    candidate(repo, '.local/finished/dependency-env/nested/other.txt')
    plan = manifest(cleanup, repo, '.local/finished/dependency-env')
    result = cleanup.run_manifest(repo, plan, apply=True)
    assert result['results'][0]['status'] == 'removed'
    assert result['results'][0]['removed_files'] == 2
    assert not path.parent.exists()
    assert cleanup.run_manifest(repo, plan, apply=True)['results'][0]['status'] == 'absent'


@pytest.mark.parametrize('name', ['../outside', '/tmp/output', 'C:/outside', '.',
                                'tmp', 'tmp/*.json', 'tmp/a:stream', 'tmp/a/../b',
                                'tmp/a.', 'tmp/a ', 'tmp\\a', 'tmp//a'])
def test_rejects_unsafe_or_broad_paths(cleanup, repo, name):
    with pytest.raises(ValueError):
        cleanup.inspect_candidate(repo, name)


@pytest.mark.parametrize('name', ['.git', 'chat_env', 'src/data/cache', 'data/cache',
                                'logs/old.log', 'reports/old.json', 'web-ui/dist',
                                'web-ui/node_modules', '.local', '.local/worktrees/old',
                                '.local/venvs/old', '.local/late-interaction-env',
                                '.local/domain-service-20261021/old.log',
                                '9.3.03844', '.env', '.local/run/.env'])
def test_hard_protections_cannot_be_removed_from_manifest(cleanup, repo, name):
    with pytest.raises(ValueError):
        cleanup.inspect_candidate(repo, name, ())


@pytest.mark.parametrize('target', ['.local/run/shared', '.local/run/shared/sub', '.local/run'])
def test_additional_protection_blocks_ancestors_and_descendants(cleanup, repo, target):
    with pytest.raises(ValueError):
        cleanup.inspect_candidate(repo, target, ['.local/run/shared'])


def test_tracked_generated_file_is_protected(cleanup, repo):
    candidate(repo)
    subprocess.run(['git', '-C', str(repo), 'add', 'tmp/coverage.json'], check=True)
    with pytest.raises(ValueError, match='Tracked'):
        cleanup.inspect_candidate(repo, 'tmp/coverage.json')


def test_tracked_file_alternate_case_is_protected(cleanup, repo):
    candidate(repo, 'tmp/Coverage.JSON')
    subprocess.run(['git', '-C', str(repo), 'add', 'tmp/Coverage.JSON'], check=True)
    with pytest.raises(ValueError, match='Tracked'):
        cleanup.inspect_candidate(repo, 'tmp/coverage.json')


@pytest.mark.parametrize('change', ['content', 'added_file', 'mtime'])
def test_changed_snapshot_blocks_entire_candidate(cleanup, repo, change):
    path = candidate(repo, 'tmp/run/a.json')
    plan = manifest(cleanup, repo, 'tmp/run')
    if change == 'content':
        path.write_text('changed output', encoding='utf-8')
    elif change == 'added_file':
        candidate(repo, 'tmp/run/b.json')
    else:
        os.utime(path, ns=(path.stat().st_atime_ns, path.stat().st_mtime_ns + 100000))
    result = cleanup.run_manifest(repo, plan, apply=True)
    assert result['results'][0]['status'] == 'blocked'
    assert path.exists()


def test_hardlinks_are_protected_even_when_only_alias_would_be_removed(cleanup, repo):
    original = candidate(repo, 'chat_env/shared.dat')
    link = repo / 'tmp/copy.dat'
    link.parent.mkdir()
    os.link(original, link)
    before = original.stat()
    with pytest.raises(ValueError, match='Hardlinks'):
        manifest(cleanup, repo, 'tmp/copy.dat')
    assert link.exists()
    assert original.read_text() == 'generated output'
    assert original.stat().st_mode == before.st_mode
    assert original.stat().st_mtime_ns == before.st_mtime_ns


def test_new_hardlink_after_review_blocks_whole_candidate(cleanup, repo):
    first = candidate(repo, 'tmp/run/a.json')
    shared = candidate(repo, 'tmp/run/b.json')
    plan = manifest(cleanup, repo, 'tmp/run')
    (repo / 'chat_env').mkdir()
    os.link(shared, repo / 'chat_env/shared.dat')
    result = cleanup.run_manifest(repo, plan, apply=True)['results'][0]
    assert result['status'] == 'blocked'
    assert result['removed_files'] == 0
    assert first.exists() and shared.exists()


def test_directory_junction_and_its_parent_are_protected(cleanup, repo, tmp_path):
    outside = repo.parent / (repo.name + '-outside')
    outside.mkdir()
    (outside / 'keep.txt').write_text('keep')
    junction = repo / 'tmp/run/junction'
    junction.parent.mkdir(parents=True)
    if os.name == 'nt':
        result = subprocess.run(['cmd', '/c', 'mklink', '/J', str(junction), str(outside)],
                                capture_output=True, text=True)
        assert result.returncode == 0, result.stderr
    else:
        junction.symlink_to(outside, target_is_directory=True)
    try:
        for name in ('tmp/run', 'tmp/run/junction', 'tmp/run/junction/keep.txt'):
            with pytest.raises(ValueError, match='Reparse'):
                cleanup.inspect_candidate(repo, name)
        assert (outside / 'keep.txt').read_text() == 'keep'
    finally:
        if os.name == 'nt':
            junction.rmdir()
        else:
            junction.unlink()


def test_nested_repository_is_protected(cleanup, repo):
    candidate(repo, 'tmp/run/.git/HEAD')
    with pytest.raises(ValueError, match='Nested Git'):
        cleanup.inspect_candidate(repo, 'tmp/run')


@pytest.mark.parametrize('name', ['.local/DOMAIN~1/cloudflared.exe',
                                '.local/old/.git/objects/abc', 'tmp/run/.git/config'])
def test_short_alias_and_nested_git_entrypoints_rejected(cleanup, repo, name):
    with pytest.raises(ValueError):
        cleanup.inspect_candidate(repo, name)


@pytest.mark.skipif(os.name != 'nt', reason='Windows file sharing contract')
def test_windows_locked_file_stops_without_forcing_deletion(cleanup, repo):
    import ctypes
    from ctypes import wintypes
    first = candidate(repo, 'tmp/run/a.json')
    locked = candidate(repo, 'tmp/run/b.json')
    last = candidate(repo, 'tmp/run/c.json')
    plan = manifest(cleanup, repo, 'tmp/run')
    kernel = ctypes.WinDLL('kernel32', use_last_error=True)
    kernel.CreateFileW.argtypes = (wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD,
                                  wintypes.LPVOID, wintypes.DWORD, wintypes.DWORD,
                                  wintypes.HANDLE)
    kernel.CreateFileW.restype = wintypes.HANDLE
    kernel.CloseHandle.argtypes = (wintypes.HANDLE,)
    handle = kernel.CreateFileW(str(locked), 0x80000000, 3, None, 3, 0, None)
    assert handle != ctypes.c_void_p(-1).value
    try:
        result = cleanup.run_manifest(repo, plan, apply=True)['results'][0]
        assert result['status'] == 'partial'
        assert result['removed_files'] == 1
        assert not first.exists()
        assert locked.read_text() == 'generated output'
        assert last.exists()
    finally:
        kernel.CloseHandle(handle)


def test_cli_apply_and_changed_manifest_exit_codes(cleanup, repo):
    path = candidate(repo)
    plan = repo / 'manifest.json'
    plan.write_text(json.dumps(manifest(cleanup, repo)))
    command = [sys.executable, str(SCRIPT), '--root', str(repo), '--manifest', str(plan), '--apply']
    result = subprocess.run(command, capture_output=True, text=True)
    assert result.returncode == 0 and not path.exists()
    candidate(repo)
    result = subprocess.run(command, capture_output=True, text=True)
    assert result.returncode == 1
    assert json.loads(result.stdout)['results'][0]['status'] == 'blocked'
    assert path.exists()
