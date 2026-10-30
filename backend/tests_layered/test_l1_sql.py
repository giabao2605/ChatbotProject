"""L1 - Du lieu / SQL.

PHAN CHAY NGAY (unit): cac ham sanitize thuan trong repository.py
(khong can DB - chi can import duoc module).

PHAN CAN DB (integration): kiem ket noi + MAU test cho nhom ham PHA HUY.
Bat bang RUN_DB_TESTS=1 va CHI tro toi DB staging clone.
"""
import pytest

# Import repository o muc module: neu thieu sqlalchemy/dotenv -> skip ca file.
repo = pytest.importorskip(
    "mech_chatbot.db.repository",
    reason="Khong import duoc repository (thieu sqlalchemy/pyodbc/dotenv?).",
)


class TestSanitizeHelpers:
    pytestmark = [pytest.mark.unit, pytest.mark.l1]

    def test_sanitize_text_strips_and_nullifies(self):
        assert repo._sanitize_text(None) is None
        assert repo._sanitize_text("  hello  ") == "hello"
        assert repo._sanitize_text("n/a") is None
        assert repo._sanitize_text("null") is None
        assert repo._sanitize_text("") is None

    def test_sanitize_text_caps_length(self):
        out = repo._sanitize_text("x" * 100, max_len=10)
        assert out is not None and len(out) == 10

    def test_sanitize_int(self):
        assert repo._sanitize_int("abc123def") == 123
        assert repo._sanitize_int("khong co so") is None
        assert repo._sanitize_int(None) is None
        assert repo._sanitize_int("45") == 45

    def test_cap_len_keeps_value(self):
        # KHAC _sanitize_text: khong bien 'null' thanh None, chi cat do dai.
        assert repo._cap_len(None, 10) is None
        assert repo._cap_len("null", 10) == "null"
        assert repo._cap_len("x" * 100, 10) == "x" * 10

    def test_normalize_base_code(self):
        assert repo.normalize_base_code("Ban Ve_01.pdf") == "ban-ve-01"
        assert repo.normalize_base_code("") == ""


@pytest.mark.integration
@pytest.mark.l1
class TestSqlIntegration:
    """Can RUN_DB_TESTS=1 + DB staging clone."""

    def test_db_connectivity(self, db_engine):
        from sqlalchemy import text
        with db_engine.connect() as conn:
            assert conn.execute(text("SELECT 1")).scalar() == 1

    def test_schema_version_table_exists(self, db_engine):
        from sqlalchemy import text
        with db_engine.connect() as conn:
            row = conn.execute(text(
                "SELECT COUNT(*) FROM INFORMATION_SCHEMA.TABLES "
                "WHERE TABLE_NAME IN ('_SchemaVersions','SchemaVersions','__EFMigrationsHistory')"
            )).scalar()
        assert row is not None  # ghi nhan; dieu chinh ten bang theo he cua ban.

    @pytest.mark.skip(reason="MAU L1-3: viet test rollback cho tung ham pha huy. Xem README.")
    def test_destructive_rollback_TEMPLATE(self, db_engine):
        # GOI Y KHUON MAU (dien logic that theo schema cua ban):
        #   1. Trong 1 transaction thu nghiem, tao doc gia (INSERT TaiLieu...).
        #   2. Goi ham can test, vd:
        #        from mech_chatbot.db.repository import delete_document_completely
        #        delete_document_completely(doc_id)
        #   3. Assert TaiLieu / BOM / DocumentPages / TechnicalAttributes / IngestionJobs
        #      lien quan deu sach; co ban ghi audit (write_audit_log).
        #   4. Case ROLLBACK: co tinh truyen doc_id sai/gay loi giua chung ->
        #      assert KHONG con trang thai nua voi (khong xoa mot phan).
        #   5. Dam bao dung DB staging clone, don dep sau moi test.
        raise NotImplementedError
