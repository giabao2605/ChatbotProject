from pathlib import Path

from fastapi import FastAPI
from fastapi.testclient import TestClient

from mech_chatbot.api import app_server, file_access
from mech_chatbot.config import logging as chatbot_logging
from mech_chatbot.ingestion.pdf import config as pdf_config


REPO_ROOT = Path(__file__).resolve().parents[3]


def test_runtime_paths_still_use_repository_data_and_logs():
    assert file_access.project_root() == REPO_ROOT
    assert file_access.data_raw_root() == REPO_ROOT / "data" / "raw"
    assert file_access.data_processed_root() == REPO_ROOT / "data" / "processed"
    assert chatbot_logging.log_dir == REPO_ROOT / "logs"
    assert Path(pdf_config.IMAGE_DIR) == REPO_ROOT / "data" / "processed"
    assert Path(pdf_config.DEFAULT_VISION_CACHE_DIR) == REPO_ROOT / "src" / "data" / "cache" / "vision"


def test_browser_app_serves_relocated_frontend_build(tmp_path, monkeypatch):
    dist = tmp_path / "frontend" / "dist"
    dist.mkdir(parents=True)
    (dist / "index.html").write_text("relocated frontend", encoding="utf-8")
    monkeypatch.setattr(app_server, "_project_root", lambda: tmp_path)

    application = FastAPI()
    app_server._install_http_surface(application)

    with TestClient(application) as client:
        response = client.get("/")
    assert response.status_code == 200
    assert "relocated frontend" in response.text


def test_stored_legacy_page_path_stays_readable_after_relocation(tmp_path, monkeypatch):
    page = tmp_path / "src" / "data" / "processed" / "page.png"
    page.parent.mkdir(parents=True)
    page.write_bytes(b"legacy image")
    monkeypatch.setattr(file_access, "project_root", lambda: tmp_path)

    storage = file_access._storage()
    assert storage.resolve_page_image("src/data/processed/page.png", allow_fallback=False) == page.resolve()
    assert storage.resolve_original("src/data/processed/page.png") is None
