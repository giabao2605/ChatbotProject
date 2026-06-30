"""Test integration tang DB — CAN SQL Server that.
Chay: RUN_DB_TESTS=1 pytest -m integration tests/integration/test_db_repository.py

Goi y: tro vao DB STAGING/TEST rieng, KHONG dung production.
Day la khung mau — dien them theo schema thuc te (database/schema/01_baseline.sql).
"""
import pytest

pytestmark = pytest.mark.integration


@pytest.fixture(scope="module")
def conn():
    from mech_chatbot.db.repository import engine
    if engine is None:
        pytest.skip("engine=None: kiem tra SQL_SERVER/SQL_DATABASE trong .env")
    with engine.connect() as c:
        yield c


class TestConnectivity:
    def test_engine_connects(self, conn):
        from sqlalchemy import text
        assert conn.execute(text("SELECT 1")).scalar() == 1


class TestReingestGuard:
    """Tai lieu da published+approved KHONG cho re-ingest (bao toan du lieu).
    Xem repository.py: raise ValueError khi lifecycle=published & review=approved.
    """
    @pytest.mark.xfail(reason="Can seed du lieu test truoc; dien fixture theo schema that", strict=False)
    def test_published_doc_cannot_be_reingested(self, conn):
        from mech_chatbot.db import repository as repo
        with pytest.raises(ValueError):
            # TODO: tao truoc 1 doc published+approved roi goi ham upsert/re-ingest tuong ung
            repo.upsert_document_version(...)  # noqa: F821


class TestRbacQueries:
    @pytest.mark.security
    def test_user_departments_drives_access(self, conn):
        """User khong co ban ghi UserDepartments -> chi duoc CHUNG (fallback an toan).
        Khoi tao user test khong gan dept, goi authenticate_user, assert ['CHUNG'].
        """
        pytest.skip("Dien sau: tao user test khong co UserDepartments, kiem tra fallback CHUNG")
