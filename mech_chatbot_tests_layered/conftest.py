"""Cau hinh chung cho bo test theo tang (L1 -> L7).

- Tu dong tim va them `src/` vao sys.path (de import duoc `mech_chatbot`).
  => Chi can dat thu muc nay BEN TRONG repo (cung cap voi `src/`) la chay duoc.
  => Hoac set bien moi truong MECH_SRC tro toi thu muc `src`.
- Cung cap cac fixture dung chung: db_engine, qdrant_client, rag_server_url...
- Cac fixture ha tang se TU DONG SKIP neu chua bat cong tac moi truong tuong ung,
  nen bo test khong bao gio 'do' chi vi thieu DB/Qdrant/Server.
"""
import os
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))


def _find_src(start):
    d = start
    for _ in range(12):
        cand = os.path.join(d, "src", "mech_chatbot")
        if os.path.isdir(cand):
            return os.path.join(d, "src")
        parent = os.path.dirname(d)
        if parent == d:
            break
        d = parent
    return None


_SRC = os.environ.get("MECH_SRC") or _find_src(_HERE) or _find_src(os.getcwd())
if _SRC and _SRC not in sys.path:
    sys.path.insert(0, _SRC)

import pytest  # noqa: E402


def _env_true(name):
    return os.environ.get(name, "").strip().lower() in {"1", "true", "yes", "on"}


@pytest.fixture(scope="session")
def mech_src_path():
    if not _SRC:
        pytest.skip(
            "Khong tim thay src/mech_chatbot. Dat thu muc test nay trong repo "
            "(cung cap voi src/) hoac set bien moi truong MECH_SRC."
        )
    return _SRC


@pytest.fixture(scope="session")
def db_engine():
    """SQLAlchemy engine tu repository. Skip tru khi RUN_DB_TESTS=1.

    ⚠️ Chi tro toi DB STAGING CLONE, tuyet doi khong dung DB that.
    """
    if not _env_true("RUN_DB_TESTS"):
        pytest.skip("Can RUN_DB_TESTS=1 (tro toi DB staging clone) de chay test nay.")
    try:
        from mech_chatbot.db.repository import engine
    except Exception as e:
        pytest.skip(f"Khong import duoc repository.engine: {e}")
    if engine is None:
        pytest.skip("repository.engine=None -> kiem tra connection string / ODBC driver / SQL Server.")
    return engine


@pytest.fixture(scope="session")
def qdrant_client():
    """Qdrant client. Skip tru khi RUN_QDRANT_TESTS=1 va co QDRANT_URL."""
    if not _env_true("RUN_QDRANT_TESTS"):
        pytest.skip("Can RUN_QDRANT_TESTS=1 de chay test Qdrant.")
    qc = pytest.importorskip("qdrant_client")
    url = os.environ.get("QDRANT_URL")
    api_key = os.environ.get("QDRANT_API_KEY")
    if not url:
        pytest.skip("Thieu QDRANT_URL (vd http://localhost:6333).")
    return qc.QdrantClient(url=url, api_key=api_key, timeout=30)


@pytest.fixture(scope="session")
def qdrant_collection():
    try:
        from mech_chatbot.config.settings import QDRANT_COLLECTION
        return QDRANT_COLLECTION
    except Exception:
        return os.environ.get("QDRANT_COLLECTION", "TaiLieuKyThuat_v2")


@pytest.fixture(scope="session")
def rag_server_url():
    url = os.environ.get("RAG_SERVER_URL")
    if not url:
        pytest.skip("Thieu RAG_SERVER_URL (vd http://localhost:8100).")
    return url.rstrip("/")
