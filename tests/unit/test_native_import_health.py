from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest


PROJECT_ROOT = Path(__file__).resolve().parents[2]
SOURCE_ROOT = PROJECT_ROOT / "src"
FATAL_MARKERS = (
    "windows fatal exception: access violation",
    "0xc0000005",
)


@pytest.mark.skipif(sys.platform != "win32", reason="Windows native import-order guard")
def test_rag_pipeline_import_has_no_native_access_violation():
    environment = {
        **os.environ,
        "HF_HUB_OFFLINE": "1",
        "PYTHONPATH": str(SOURCE_ROOT),
        "QDRANT_API_KEY": "test-only",
        "QDRANT_URL": "http://127.0.0.1:1",
        "RAG_EXECUTION_CONTEXT": "test",
        "TRANSFORMERS_OFFLINE": "1",
    }
    import_script = """
import mech_chatbot
import langchain_qdrant
import qdrant_client

class OfflineQdrantClient:
    def __init__(self, *args, **kwargs):
        pass

    def collection_exists(self, *args, **kwargs):
        return True

qdrant_client.QdrantClient = OfflineQdrantClient
langchain_qdrant.FastEmbedSparse = lambda *args, **kwargs: object()
langchain_qdrant.QdrantVectorStore = lambda *args, **kwargs: object()

import mech_chatbot.rag.pipeline
print('pipeline_import_ok', flush=True)
"""
    result = subprocess.run(
        [
            sys.executable,
            "-X",
            "faulthandler",
            "-c",
            import_script,
        ],
        cwd=PROJECT_ROOT,
        env=environment,
        capture_output=True,
        text=True,
        timeout=180,
        check=False,
    )
    diagnostics = f"{result.stdout}\n{result.stderr}".lower()
    fatal_markers = [marker for marker in FATAL_MARKERS if marker in diagnostics]

    assert result.returncode == 0, f"pipeline import exit={result.returncode}"
    assert fatal_markers == [], f"native fatal markers detected: {fatal_markers}"
    assert "pipeline_import_ok" in result.stdout
