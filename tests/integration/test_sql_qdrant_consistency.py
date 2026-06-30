"""Test NHAT QUAN SQL <-> Qdrant — rui ro dac thu cua he RAG nay.
Chay: RUN_QDRANT_TESTS=1 RUN_DB_TESTS=1 pytest -m integration tests/integration/test_sql_qdrant_consistency.py

Bat bien can bao ve:
- Moi doc da vector hoa (TrangThaiVector=1) trong SQL phai co diem trong Qdrant.
- security_level / domain / phong_ban_quyen trong payload Qdrant phai KHOP voi SQL.
  (Lech => user co the thay tai lieu sai quyen.)

Khung mau — hoan thien query theo schema/collection thuc te.
"""
import os

import pytest

pytestmark = [pytest.mark.integration, pytest.mark.security]


@pytest.fixture(scope="module")
def qdrant():
    qc = pytest.importorskip("qdrant_client")
    url = os.getenv("QDRANT_URL")
    if not url:
        pytest.skip("Thieu QDRANT_URL")
    client = qc.QdrantClient(url=url, api_key=os.getenv("QDRANT_API_KEY"))
    yield client


class TestConsistency:
    @pytest.mark.xfail(reason="Khung mau: hoan thien doi chieu SQL vs Qdrant", strict=False)
    def test_every_vectorized_doc_has_points(self, qdrant):
        from mech_chatbot.config.settings import QDRANT_COLLECTION
        from mech_chatbot.db.repository import engine
        from sqlalchemy import text
        with engine.connect() as c:
            doc_ids = [r[0] for r in c.execute(
                text("SELECT DocID FROM TaiLieu WHERE TrangThaiVector = 1")
            ).fetchall()]
        missing = []
        for doc_id in doc_ids:
            # TODO: scroll Qdrant theo metadata.doc_id == doc_id, assert co diem
            pass
        assert not missing, f"Doc co trong SQL nhung thieu vector Qdrant: {missing}"

    @pytest.mark.xfail(reason="Khung mau: doi chieu security_level SQL vs payload", strict=False)
    def test_security_level_matches_payload(self, qdrant):
        pytest.skip("Dien: voi moi doc, so SecurityLevel(SQL) == payload.metadata.security_level")
