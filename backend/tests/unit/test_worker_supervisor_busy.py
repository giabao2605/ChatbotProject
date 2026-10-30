from pathlib import Path
import shutil
import subprocess
import sys

import pytest


@pytest.mark.skipif(sys.platform != "win32", reason="Windows process supervisor")
def test_supervisor_stops_on_busy_exit_and_preserves_owner_heartbeat(tmp_path):
    powershell = shutil.which("powershell.exe") or shutil.which("pwsh")
    if powershell is None:
        pytest.skip("PowerShell required")
    root = Path(__file__).resolve().parents[3]
    marker = tmp_path / "ready.txt"
    marker.write_text("owner heartbeat")
    (tmp_path / "run_worker.py").write_text(
        "from pathlib import Path\n"
        "with Path('runs.txt').open('a') as stream: stream.write('run\\n')\n"
        "raise SystemExit(73)\n"
    )
    result = subprocess.run([
        powershell, "-NoProfile", "-ExecutionPolicy", "Bypass", "-File",
        str(root / "scripts/ops/worker_supervisor.ps1"),
        "-ProjectRoot", str(tmp_path), "-PythonExe", sys.executable,
        "-WorkerOutputLog", str(tmp_path / "out.log"),
        "-WorkerErrorLog", str(tmp_path / "err.log"),
        "-ReadyFile", str(marker), "-RestartDelaySeconds", "1",
    ], cwd=tmp_path, capture_output=True, text=True, timeout=20)
    assert result.returncode == 73, result.stdout + result.stderr
    assert (tmp_path / "runs.txt").read_text() == "run\n"
    assert marker.read_text() == "owner heartbeat"
