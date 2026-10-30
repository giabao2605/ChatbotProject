from pathlib import Path
import subprocess
import sys
from types import SimpleNamespace

import pytest

from scripts.ops.safe_repo_cleanup import _target
from scripts.eval.semantic_task_routing import _source_paths
from scripts.windows_service.service_runtime import child_environment

ROOT = Path(__file__).resolve().parents[3]


def test_service_uses_restructured_source():
    root = Path('example')
    assert child_environment({}, root)['PYTHONPATH'] == str(root / 'backend' / 'src')


@pytest.mark.parametrize('path', ['backend/src', 'backend/tests', 'backend/tests_layered',
                                 'frontend/dist', 'frontend/node_modules'])
def test_cleanup_protects_restructured_paths(tmp_path, path):
    with pytest.raises(ValueError, match='protected'):
        _target(tmp_path, path, ())


@pytest.mark.parametrize('prefix', ['src', 'backend/src'])
def test_capture_uses_actual_checkout_layout(tmp_path, prefix):
    (tmp_path / prefix / 'mech_chatbot').mkdir(parents=True)
    assert _source_paths(tmp_path, {'src/mech_chatbot/example.py', 'tests/unit/example.py'}) == {
        f'{prefix}/mech_chatbot/example.py',
        ('backend/' if prefix.startswith('backend/') else '') + 'tests/unit/example.py',
    }


def test_capture_rejects_missing_package(tmp_path):
    with pytest.raises(ValueError, match='Source package unavailable'):
        _source_paths(tmp_path, {'src/mech_chatbot/example.py'})


def test_cleanup_allows_frontend_generated_output(tmp_path, monkeypatch):
    monkeypatch.setattr('scripts.ops.safe_repo_cleanup.subprocess.run',
                        lambda *args, **kwargs: SimpleNamespace(stdout=b''))
    assert _target(tmp_path, 'frontend/coverage', ()) == tmp_path / 'frontend/coverage'


def test_evaluator_loads_golden_questions_from_other_directory(tmp_path):
    code = '''
import sys
from types import SimpleNamespace
sys.stdout.reconfigure(encoding='utf-8')
sys.path.insert(0, sys.argv[1])
from scripts.eval import evaluate_chatbot as module
module.DefaultRagExecutor = lambda: SimpleNamespace(run=lambda *args: ())
module.collect_rag_events = lambda events: SimpleNamespace(answer='', ref_text='')
module.run_eval()
'''
    result = subprocess.run([sys.executable, '-I', '-B', '-c', code, str(ROOT)],
                            cwd=tmp_path, capture_output=True, text=True, encoding='utf-8')
    assert result.returncode == 0, result.stderr
    assert result.stdout.count('Testing:') == 2


def test_assay_imports_project_tests_outside_pytest(tmp_path):
    code = '''
import sys
sys.path[:0] = [sys.argv[1], sys.argv[2]]
import scripts.eval.run_semantic_task_assay
import tests
print(tests.__file__)
'''
    result = subprocess.run([sys.executable, '-I', '-B', '-c', code, str(ROOT),
                             str(ROOT / 'backend/src')], cwd=tmp_path,
                            capture_output=True, text=True, encoding='utf-8')
    assert result.returncode == 0, result.stderr
    assert Path(result.stdout.strip()).resolve() == (ROOT / 'backend/tests/__init__.py').resolve()
