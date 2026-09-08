"""Source binding checks use a disposable Git repository, never provider traffic."""
import hashlib
from pathlib import Path
import subprocess
import stat
import sys
from types import SimpleNamespace

import pytest


def test_matrix_process_identity_requires_actual_checkout_and_interpreter(tmp_path):
    from scripts.integrated_eval.math_query_dispatch import validate_matrix_process_identity

    source = Path(__file__).resolve().parents[2]
    assert validate_matrix_process_identity(source, expected_python=Path(sys.executable)) is None
    with pytest.raises(ValueError, match="matrix_process_identity_invalid"):
        validate_matrix_process_identity(tmp_path, expected_python=Path(sys.executable))
    with pytest.raises(ValueError, match="matrix_process_identity_invalid"):
        validate_matrix_process_identity(source, expected_python=tmp_path / "python.exe")

def _git(root, *args):
    return subprocess.check_output([
        "git", "-c", "core.hooksPath=" + str(root / ".git/no-hooks"),
        "-c", "commit.gpgsign=false", "-c", "core.autocrlf=false", *args], cwd=root, text=True).strip()


def test_dispatch_source_requires_exact_clean_commit_and_tool_inventory(tmp_path, monkeypatch):
    from scripts.integrated_eval.math_query_dispatch import validate_matrix_source, MATRIX_TOOL_PATHS

    root = tmp_path / "source"
    root.mkdir()
    _git(root, "init", "-q")
    hashes = {}
    for relative in MATRIX_TOOL_PATHS:
        path = root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"# synthetic tool\n")
        hashes[relative] = hashlib.sha256(path.read_bytes()).hexdigest()
    _git(root, "add", ".")
    _git(root, "-c", "user.name=Offline Test", "-c", "user.email=offline@example.invalid",
         "-c", "core.autocrlf=false", "commit", "-qm", "synthetic source", "--no-verify")
    commit = _git(root, "rev-parse", "HEAD")
    monkeypatch.delattr(Path, "is_junction", raising=False)
    validate_matrix_source(root, source_commit=commit, tool_hashes=hashes)
    (root / "nested").mkdir()
    with pytest.raises(ValueError, match="matrix_source_root_mismatch"):
        validate_matrix_source(root / "nested", source_commit=commit, tool_hashes=hashes)
    with pytest.raises(ValueError, match="matrix_source_commit_mismatch"):
        validate_matrix_source(root, source_commit="0" * 40, tool_hashes=hashes)
    with pytest.raises(ValueError, match="matrix_tool_inventory_invalid"):
        validate_matrix_source(root, source_commit=commit, tool_hashes={})
    wrong = {**hashes, MATRIX_TOOL_PATHS[0]: "0" * 64}
    with pytest.raises(ValueError, match="matrix_tool_hash_mismatch"):
        validate_matrix_source(root, source_commit=commit, tool_hashes=wrong)
    original_lstat = Path.lstat
    for redirected, message in ((root, "matrix_source_path_invalid"),
                                (root / MATRIX_TOOL_PATHS[0], "matrix_tool_path_invalid")):
        def lstat(path, *args, **kwargs):
            if path == redirected:
                return SimpleNamespace(st_mode=stat.S_IFDIR,
                                       st_file_attributes=stat.FILE_ATTRIBUTE_REPARSE_POINT)
            return original_lstat(path, *args, **kwargs)

        with monkeypatch.context() as patch:
            patch.setattr(Path, "lstat", lstat)
            with pytest.raises(ValueError, match=message):
                validate_matrix_source(root, source_commit=commit, tool_hashes=hashes)
    (root / "untracked.txt").write_text("untracked", encoding="utf-8")
    with pytest.raises(RuntimeError, match="clean git worktree"):
        validate_matrix_source(root, source_commit=commit, tool_hashes=hashes)


@pytest.mark.parametrize("commit,hashes,error", [
    (None, {}, "matrix_source_commit_invalid"),
    ("not-a-commit", {}, "matrix_source_commit_invalid"),
    ("a" * 40, None, "matrix_tool_inventory_invalid"),
])
def test_dispatch_source_rejects_malformed_contract_before_filesystem(tmp_path, commit, hashes, error):
    from scripts.integrated_eval.math_query_dispatch import validate_matrix_source

    with pytest.raises(ValueError, match=error):
        validate_matrix_source(tmp_path / "missing", source_commit=commit, tool_hashes=hashes)


def test_fresh_matrix_root_is_contained_and_validation_does_not_create_it(tmp_path):
    from scripts.integrated_eval.math_query_dispatch import validate_fresh_matrix_root

    source = tmp_path / "source"
    source.mkdir()
    target = source / ".local" / "matrix-window-01"
    assert validate_fresh_matrix_root(source, ".local/matrix-window-01") == target
    assert not (source / ".local").exists()
    target.mkdir(parents=True)
    with pytest.raises(ValueError, match="matrix_run_root_not_fresh"):
        validate_fresh_matrix_root(source, ".local/matrix-window-01")


def test_claim_matrix_run_root_creates_exact_directory_once(tmp_path):
    from scripts.integrated_eval.math_query_dispatch import claim_matrix_run_root

    source = tmp_path / "source"
    source.mkdir()
    claimed = claim_matrix_run_root(source, ".local/matrix-window-01")

    assert claimed == source / ".local/matrix-window-01"
    assert claimed.is_dir()
    with pytest.raises(ValueError, match="matrix_run_root_not_fresh"):
        claim_matrix_run_root(source, ".local/matrix-window-01")


def test_claim_matrix_run_root_rejects_reparse_parent(tmp_path, monkeypatch):
    from scripts.integrated_eval.math_query_dispatch import claim_matrix_run_root

    source = tmp_path / "source"
    source.mkdir()
    (source / ".local").mkdir()
    original_lstat = Path.lstat

    def lstat(path, *args, **kwargs):
        if path == source / ".local":
            return SimpleNamespace(st_mode=stat.S_IFDIR,
                                   st_file_attributes=stat.FILE_ATTRIBUTE_REPARSE_POINT)
        return original_lstat(path, *args, **kwargs)

    monkeypatch.setattr(Path, "lstat", lstat)
    with pytest.raises(ValueError, match="matrix_run_root_redirected"):
        claim_matrix_run_root(source, ".local/matrix-window-01")
    assert not (source / ".local/matrix-window-01").exists()


def test_concurrent_matrix_root_claim_has_exactly_one_winner(tmp_path):
    from concurrent.futures import ThreadPoolExecutor
    from threading import Barrier
    from scripts.integrated_eval.math_query_dispatch import claim_matrix_run_root

    source = tmp_path / "source"
    source.mkdir()
    barrier = Barrier(8)

    def claim(_index):
        barrier.wait(timeout=10)
        try:
            return claim_matrix_run_root(source, ".local/concurrent-window")
        except ValueError as exc:
            assert str(exc) in {"matrix_run_root_not_fresh", "matrix_run_root_invalid"}
            return None

    with ThreadPoolExecutor(max_workers=8) as pool:
        results = list(pool.map(claim, range(8)))
    target = source / ".local/concurrent-window"
    assert results.count(target) == 1
    assert results.count(None) == 7
    assert target.is_dir()


def test_matrix_claim_preserves_competing_owner_after_freshness_check(tmp_path, monkeypatch):
    from scripts.integrated_eval.math_query_dispatch import claim_matrix_run_root

    source = tmp_path / "source"
    source.mkdir()
    target = source / ".local/contended-window"
    original_mkdir = Path.mkdir

    def mkdir(path, *args, **kwargs):
        if path == target:
            original_mkdir(path)
            (path / "owner.txt").write_text("competing-owner", encoding="utf-8")
        return original_mkdir(path, *args, **kwargs)

    monkeypatch.setattr(Path, "mkdir", mkdir)
    with pytest.raises(ValueError, match="matrix_run_root_not_fresh"):
        claim_matrix_run_root(source, ".local/contended-window")
    assert (target / "owner.txt").read_text(encoding="utf-8") == "competing-owner"


@pytest.mark.parametrize("relative", ["", ".", ".local", ".local/../outside", "../outside",
                                    "outside", ".local/window/", ".local//window",
                                    ".local/window:stream", ".local/CON", ".local/window."])
def test_matrix_root_rejects_noncanonical_or_unsafe_path(tmp_path, relative):
    from scripts.integrated_eval.math_query_dispatch import validate_fresh_matrix_root

    with pytest.raises(ValueError, match="matrix_run_root_invalid"):
        validate_fresh_matrix_root(tmp_path, relative)


@pytest.mark.parametrize("redirect_at", [".local", ".local/window"])
def test_matrix_root_rejects_reparse_before_resolve(tmp_path, monkeypatch, redirect_at):
    from scripts.integrated_eval.math_query_dispatch import validate_fresh_matrix_root

    original_lstat = Path.lstat
    def lstat(path, *args, **kwargs):
        if path == tmp_path / redirect_at:
            return SimpleNamespace(st_mode=stat.S_IFDIR,
                                   st_file_attributes=stat.FILE_ATTRIBUTE_REPARSE_POINT)
        return original_lstat(path, *args, **kwargs)

    monkeypatch.setattr(Path, "lstat", lstat)
    with pytest.raises(ValueError, match="matrix_run_root_redirected"):
        validate_fresh_matrix_root(tmp_path, ".local/window")
