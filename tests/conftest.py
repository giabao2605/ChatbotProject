"""Cau hinh chung cho pytest.

- Them <repo>/src vao sys.path de import duoc package `mech_chatbot`.
- Khai bao fixtures dung chung (user gia lap cho test RBAC).
- Tu dong skip test integration/eval khi chua bat bien moi truong tuong ung.
"""
import os
import sys
from pathlib import Path

import pytest

# --- Path setup -------------------------------------------------------------
_REPO_ROOT = Path(__file__).resolve().parent.parent
_SRC = _REPO_ROOT / "src"
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))


# --- Skip rules theo bien moi truong ---------------------------------------
def pytest_collection_modifyitems(config, items):
    run_db = os.getenv("RUN_DB_TESTS") == "1"
    run_qdrant = os.getenv("RUN_QDRANT_TESTS") == "1"
    # A configured URL is common in a developer .env and must not make the
    # default test suite issue paid/external RAG requests.  Live evaluation is
    # intentionally an explicit opt-in.
    run_eval = os.getenv("RUN_EVAL_TESTS") == "1" and bool(os.getenv("RAG_SERVER_URL"))
    skip_db = pytest.mark.skip(reason="Can SQL Server that: dat RUN_DB_TESTS=1")
    skip_qdrant = pytest.mark.skip(reason="Can Qdrant that: dat RUN_QDRANT_TESTS=1")
    skip_eval = pytest.mark.skip(
        reason="Can RAG server that: dat RUN_EVAL_TESTS=1 va RAG_SERVER_URL=..."
    )
    for item in items:
        if "integration" in item.keywords:
            needs_qdrant = "qdrant" in item.name.lower() or "consistency" in item.name.lower()
            if needs_qdrant and not run_qdrant:
                item.add_marker(skip_qdrant)
            elif not needs_qdrant and not run_db:
                item.add_marker(skip_db)
        if "eval" in item.keywords and not run_eval:
            item.add_marker(skip_eval)


# --- Fixtures: user gia lap cho test phan quyen -----------------------------
@pytest.fixture
def make_user():
    """Tao dict user giong output cua auth.service.authenticate_user."""
    def _make(
        username="tester",
        roles=None,
        allowed_departments=None,
        max_security_level="internal",
        allowed_sites=None,
        department="CHUNG",
    ):
        return {
            "user_id": 1,
            "username": username,
            "display_name": username,
            "department": department,
            "roles": roles or ["viewer"],
            "allowed_departments": allowed_departments or ["CHUNG"],
            "max_security_level": max_security_level,
            "allowed_sites": allowed_sites or [],
        }
    return _make
