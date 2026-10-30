import json
import os
from pathlib import Path
import shutil
import subprocess
import sys


def test_cli_bootstraps_backend_src_and_passes_folder_as_string(tmp_path):
    root = Path(__file__).resolve().parents[3]
    script = tmp_path / "scripts/nap_them_file.py"
    script.parent.mkdir()
    shutil.copyfile(root / "scripts/nap_them_file.py", script)
    package = tmp_path / "backend/src/mech_chatbot"
    (package / "config").mkdir(parents=True)
    (package / "ingestion").mkdir()
    (package / "config/logging.py").write_text(
        "from types import SimpleNamespace\nlogger = SimpleNamespace(info=lambda *a: None, error=lambda *a: None)\n"
    )
    (package / "ingestion/file_ingestor.py").write_text(
        "import json\nfrom pathlib import Path\n"
        "def learn_new_file(file_path, name, **kwargs):\n"
        " Path(file_path).parents[3].joinpath('call.json').write_text(\n"
        "  json.dumps({'file': name, 'folder': kwargs['thu_muc'], 'scan': kwargs['scan_sensitive']}))\n"
        " return True, 'ingestion stub', {}\n"
    )
    sample = tmp_path / "data/raw/QA/sample.txt"
    sample.parent.mkdir(parents=True)
    sample.write_text("fixture")
    result = subprocess.run(
        [sys.executable, "-B", str(script), "QA/sample.txt"], cwd=tmp_path,
        env={**os.environ, "PYTHONPATH": ""}, capture_output=True, text=True, encoding="utf-8", timeout=10,
    )
    assert result.returncode == 0, result.stderr
    assert json.loads((tmp_path / "call.json").read_text()) == {
        "file": "sample.txt", "folder": "QA", "scan": True,
    }
