"""L2 - Vector store / Qdrant.

Can RUN_QDRANT_TESTS=1 + QDRANT_URL (va QDRANT_API_KEY neu can).
Kiem: collection ton tai, co index bat buoc (dac biet metadata.doc_id),
va khung doi soat SQL <-> Qdrant (MAU).
"""
import pytest

pytestmark = [pytest.mark.integration, pytest.mark.l2]


class TestQdrantSchema:
    def test_collection_exists(self, qdrant_client, qdrant_collection):
        assert qdrant_client.collection_exists(qdrant_collection), (
            "Khong thay collection %s" % qdrant_collection
        )

    def test_collection_not_empty(self, qdrant_client, qdrant_collection):
        cnt = qdrant_client.count(qdrant_collection, exact=True).count
        assert cnt > 0, "Collection rong -> can ingest du lieu truoc."

    def test_required_payload_indexes(self, qdrant_client, qdrant_collection):
        info = qdrant_client.get_collection(qdrant_collection)
        schema = getattr(info, "payload_schema", None) or {}
        keys = set(schema.keys())
        # Index quan trong nhat: thieu no -> Duyet/Tu choi loi 'Index required but not found'
        assert "metadata.doc_id" in keys, (
            "Thieu index metadata.doc_id. Chay: python scripts/create_qdrant_indexes.py"
        )

    @pytest.mark.skip(reason="MAU L2-3: mo rong test doi soat SQL<->Qdrant. Xem README.")
    def test_sql_qdrant_consistency_TEMPLATE(self, qdrant_client, qdrant_collection, db_engine):
        # GOI Y: lay tap doc_id 'active' tu SQL; lay tap doc_id co trong Qdrant;
        # assert 0 vector mo coi (co o Qdrant, mat o SQL) va 0 doc ket trang thai 'deleting'.
        # Tham khao scripts/danger_ops/reconcile_sql_qdrant.py (chay --fix phai backup truoc).
        raise NotImplementedError
