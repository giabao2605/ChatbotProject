"""Review-generated output cleanup; the operator must verify live usage separately."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path, PureWindowsPath
import stat
import subprocess


PROTECTED = (
    '.git', 'chat_env', 'src', 'data', 'database', 'logs', 'reports', 'docs',
    'scripts', 'tests', 'mech_chatbot_tests_layered', 'node_modules',
    'web-ui/dist', 'web-ui/node_modules', '.local/domain-service-20261021',
    '.local/worktrees', '.local/venvs', '.local/late-interaction-env',
    '9.1.00678', '9.3.03843', '9.3.03844', '9.3.03951', '9.3.04068', '9.3.04080',
)


def _relative(value):
    if not isinstance(value, str) or not value or '\\' in value:
        raise ValueError('Use an explicit repository-relative forward-slash path')
    parts = value.split('/')
    if (PureWindowsPath(value).drive or value.startswith('/') or
            any(p in ('', '.', '..') or p.endswith((' ', '.')) for p in parts) or
            any(c in value for c in ':*?[]~\x00')):
        raise ValueError('Unsafe relative path')
    return value


def _no_links(path):
    for part in (path, *path.parents):
        try:
            info = part.lstat()
        except FileNotFoundError:
            continue
        if (stat.S_ISLNK(info.st_mode) or
                getattr(info, 'st_file_attributes', 0) & 0x400):
            raise ValueError('Reparse points and symbolic links are protected')


def _root(value):
    path = Path(os.path.abspath(value))
    _no_links(path)
    result = subprocess.run(['git', '-C', str(path), 'rev-parse', '--show-toplevel'],
                            capture_output=True, text=True, check=True)
    if Path(result.stdout.strip()).resolve() != path.resolve():
        raise ValueError('Root must be the Git checkout root')
    return path


def _target(root, relative, protected_paths):
    relative = _relative(relative)
    lowered = relative.casefold()
    protections = (*PROTECTED, *(_relative(p) for p in protected_paths))
    for protected in protections:
        protected = protected.casefold()
        if (lowered == protected or lowered.startswith(protected + '/') or
                protected.startswith(lowered + '/')):
            raise ValueError('Candidate intersects a protected path')
    parts = relative.split('/')
    allowed = (parts[0] == '.pytest_cache' or
               (len(parts) == 1 and parts[0].startswith('.coverage')) or
               (parts[0] == 'tmp' and len(parts) > 1) or
               (parts[0] == '.local' and len(parts) > 2) or
               lowered in ('web-ui/tsconfig.tsbuildinfo', 'web-ui/tsconfig.node.tsbuildinfo') or
               any(lowered == p or lowered.startswith(p + '/') for p in
                   ('web-ui/coverage', 'web-ui/test-results', 'web-ui/playwright-report')))
    if not allowed or any(p.casefold() == '.git' or p.casefold().startswith('.env') for p in parts):
        raise ValueError('Candidate is outside generated-output scopes')
    path = root.joinpath(*parts)
    _no_links(path)
    tracked = subprocess.run(['git', '-C', str(root), 'ls-files', '-z', '--',
                              ':(literal,icase)' + relative], capture_output=True, check=True)
    if tracked.stdout:
        raise ValueError('Tracked files are protected')
    return path


def _stamp(path):
    _no_links(path)
    before = path.lstat()
    if not stat.S_ISREG(before.st_mode):
        raise ValueError('Only regular files can be removed')
    if before.st_nlink != 1:
        raise ValueError('Hardlinks may be used by live services and are protected')
    with path.open('rb') as stream:
        digest = hashlib.file_digest(stream, 'sha256').hexdigest()
    after = path.lstat()
    fields = ('st_dev', 'st_ino', 'st_size', 'st_mtime_ns', 'st_mode', 'st_nlink')
    values = tuple(getattr(before, field) for field in fields)
    if values != tuple(getattr(after, field) for field in fields):
        raise ValueError('File changed during inspection')
    return (*values, digest)


def _inventory(path):
    if not path.exists():
        return (), ()
    if path.is_file():
        return ((path, _stamp(path)),), ()
    files, directories = [], []
    def fail(error):
        raise error
    for base, dirs, names in os.walk(path, followlinks=False, onerror=fail):
        directory = Path(base)
        _no_links(directory)
        directories.append((directory, directory.stat().st_mtime_ns))
        for name in dirs:
            if name.casefold() == '.git' or name.casefold().startswith('.env'):
                raise ValueError('Nested Git state or environment files are protected')
            _no_links(directory / name)
        for name in names:
            item = directory / name
            if name.casefold() == '.git' or name.casefold().startswith('.env'):
                raise ValueError('Nested Git state or environment files are protected')
            files.append((item, _stamp(item)))
    return tuple(sorted(files)), tuple(sorted(directories))


def _summary(root, files, directories):
    records = [(str(p.relative_to(root)), stamp) for p, stamp in files]
    records += [(str(p.relative_to(root)) + '/', stamp) for p, stamp in directories]
    digest = hashlib.sha256(json.dumps(records, separators=(',', ':')).encode()).hexdigest()
    return {'digest': digest, 'bytes': sum(stamp[2] for _, stamp in files),
            'files': len(files), 'directories': len(directories)}


def inspect_candidate(root, relative_path, protected_paths=()):
    """Return a JSON snapshot; callers must retain it unchanged for execution."""
    root = _root(root)
    path = _target(root, relative_path, protected_paths)
    return _summary(root, *_inventory(path))


def _validate_manifest(root, manifest):
    if (not isinstance(manifest, dict) or manifest.get('version') != 1 or
            not isinstance(manifest.get('root'), str) or
            Path(manifest['root']).resolve() != root):
        raise ValueError('Manifest version/root mismatch')
    entries, protected = manifest.get('entries'), manifest.get('protected_paths', [])
    if not isinstance(entries, list) or not isinstance(protected, list):
        raise ValueError('Entries and protected_paths must be lists')
    paths = []
    for entry in entries:
        if (not isinstance(entry, dict) or entry.get('classification') != 'disposable' or
                not isinstance(entry.get('reason'), str) or not entry['reason'].strip() or
                not isinstance(entry.get('evidence'), list) or not entry['evidence'] or
                not all(isinstance(e, str) and e.strip() for e in entry['evidence']) or
                not isinstance(entry.get('snapshot'), dict)):
            raise ValueError('Every candidate needs classification, reason, evidence and snapshot')
        path = _target(root, entry['path'], protected)
        if any(path == p or p in path.parents or path in p.parents for p in paths):
            raise ValueError('Overlapping candidates are not allowed')
        paths.append(path)
    return tuple(zip(entries, paths)), tuple(protected)


def _remove(root, files, directories):
    removed_files, removed_bytes = 0, 0
    try:
        for path, reviewed in files:
            if _stamp(path) != reviewed:
                raise ValueError('File changed before removal')
            # Never force permissions or modify contents to remove a locked file.
            path.unlink()
            removed_files += 1
            removed_bytes += reviewed[2]
        for path, _ in sorted(directories, key=lambda pair: len(pair[0].parts), reverse=True):
            _no_links(path)
            path.rmdir()
    except (OSError, ValueError) as error:
        return {'status': 'partial' if removed_files else 'blocked',
                'removed_files': removed_files, 'removed_bytes': removed_bytes,
                'error': str(error)}
    return {'status': 'removed', 'removed_files': removed_files, 'removed_bytes': removed_bytes}


def _process(root, path, entry, protected, apply):
    try:
        _target(root, entry['path'], protected)
        if not path.exists():
            return {'path': entry['path'], 'status': 'absent', 'removed_files': 0, 'removed_bytes': 0}
        files, directories = _inventory(path)
        snapshot = _summary(root, files, directories)
        if snapshot != entry['snapshot']:
            raise ValueError('Candidate changed after review')
        outcome = _remove(root, files, directories) if apply else {'status': 'preview'}
        return {'path': entry['path'], **snapshot, **outcome}
    except (OSError, ValueError, subprocess.CalledProcessError) as error:
        return {'path': entry['path'], 'status': 'blocked', 'removed_files': 0,
                'removed_bytes': 0, 'error': str(error)}


def run_manifest(root, manifest, *, apply=False):
    root = _root(root)
    candidates, protected = _validate_manifest(root, manifest)
    results = []
    for entry, path in candidates:
        result = _process(root, path, entry, protected, apply)
        results.append(result)
        if apply and result['status'] in ('blocked', 'partial'):
            break
    return {'mode': 'apply' if apply else 'preview', 'results': results}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root', required=True)
    parser.add_argument('--manifest', required=True, type=Path)
    parser.add_argument('--apply', action='store_true')
    args = parser.parse_args()
    try:
        manifest = json.loads(args.manifest.read_text(encoding='utf-8-sig'))
        result = run_manifest(args.root, manifest, apply=args.apply)
        print(json.dumps(result, indent=2))
        return int(any(item['status'] in ('blocked', 'partial') for item in result['results']))
    except (OSError, ValueError, KeyError, TypeError, subprocess.CalledProcessError) as error:
        print(json.dumps({'status': 'blocked', 'error': str(error)}))
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
