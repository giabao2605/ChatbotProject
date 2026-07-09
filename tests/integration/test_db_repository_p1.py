"""P1 — Integration tests cho DB repository invariants.

Chay:
    $env:RUN_DB_TESTS=1
    pytest tests/integration/test_db_repository_p1.py -v

Bao ve:
- Re-ingest guard: tai lieu published+approved KHONG duoc reset/re-ingest.
- Migration idempotent: cac object P1/P0 quan trong ton tai sau migration.

An toan:
- Test tao TenFile rieng tien to `pytest_reingest_guard_...` va cleanup sau test.
- Khong dung file/tai lieu that.
"""
import uuid

import pytest
from sqlalchemy import text

pytestmark = pytest.mark.integration


@pytest.fixture(scope="module")
def engine():
    from mech_chatbot.db.repository import engine as _engine
    if _engine is None:
        pytest.skip("engine=None: kiem tra SQL_SERVER/SQL_DATABASE trong .env")
    return _engine


@pytest.fixture
def published_doc(engine):
    """Tao 1 doc gia lap published+approved de test re-ingest guard."""
    filename = f"pytest_reingest_guard_{uuid.uuid4().hex}.pdf"
    folder = "CHUNG"
    doc_id = None
    with engine.begin() as conn:
        row = conn.execute(
            text(
                """
                INSERT INTO TaiLieu (
                    TenFile, ThuMuc, TrangThaiVector,
                    LifecycleStatus, ReviewStatus, IsCurrent,
                    Domain, SecurityLevel
                )
                OUTPUT INSERTED.DocID
                VALUES (
                    :f, :t, 1,
                    'published', 'approved', 1,
                    'generic', 'internal'
                )
                """
            ),
            {"f": filename, "t": folder},
        ).fetchone()
        doc_id = int(row[0])
        conn.execute(
            text("INSERT INTO dbo.PhongBanChiaSe (DocID, DeptCode) VALUES (:d, :dept)"),
            {"d": doc_id, "dept": folder},
        )

    yield {"DocID": doc_id, "TenFile": filename, "ThuMuc": folder}

    with engine.begin() as conn:
        # Cleanup cac bang phu neu ham test co cham vao.
        for table in ["TaiLieuKyThuat", "BangKeVatTu", "DocumentPages", "TechnicalAttributes", "DocumentAttributes"]:
            try:
                conn.execute(text(f"DELETE FROM {table} WHERE DocID = :d"), {"d": doc_id})
            except Exception:
                pass
        conn.execute(text("DELETE FROM TaiLieu WHERE DocID = :d"), {"d": doc_id})


class TestReingestGuard:
    def test_published_approved_doc_cannot_be_reset_for_reingest(self, published_doc):
        """Published+approved = immutable voi duong reset metadata.

        Neu test nay fail, pipeline co nguy co ghi de tai lieu da duyet/published.
        """
        from mech_chatbot.db import repository as repo

        with pytest.raises(ValueError, match="published"):
            repo.reset_document_metadata(published_doc["TenFile"], published_doc["ThuMuc"])


class TestMigrationObjects:
    def test_login_attempts_table_exists_for_shared_rate_limit(self, engine):
        """P0/P1 guard: rate-limit shared store phai co bang chung."""
        with engine.connect() as conn:
            exists = conn.execute(
                text("SELECT OBJECT_ID('dbo.LoginAttempts', 'U')")
            ).scalar()
        assert exists is not None

    def test_login_attempts_index_exists(self, engine):
        with engine.connect() as conn:
            exists = conn.execute(
                text(
                    """
                    SELECT 1
                    FROM sys.indexes
                    WHERE name = 'IX_LoginAttempts_User_Time'
                      AND object_id = OBJECT_ID('dbo.LoginAttempts')
                    """
                )
            ).scalar()
        assert exists == 1

    def test_tailieu_has_rbac_metadata_columns(self, engine):
        required = {"Domain", "SecurityLevel", "Site"}
        with engine.connect() as conn:
            rows = conn.execute(
                text(
                    """
                    SELECT COLUMN_NAME
                    FROM INFORMATION_SCHEMA.COLUMNS
                    WHERE TABLE_SCHEMA = 'dbo'
                      AND TABLE_NAME = 'TaiLieu'
                      AND COLUMN_NAME IN ('Domain', 'SecurityLevel', 'Site')
                    """
                )
            ).fetchall()
        found = {r[0] for r in rows}
        assert required.issubset(found)

    def test_phong_ban_chia_se_table_exists_for_document_departments(self, engine):
        with engine.connect() as conn:
            exists = conn.execute(
                text("SELECT OBJECT_ID('dbo.PhongBanChiaSe', 'U')")
            ).scalar()
            index_exists = conn.execute(
                text(
                    """
                    SELECT 1
                    FROM sys.indexes
                    WHERE name = 'IX_PhongBanChiaSe_Dept'
                      AND object_id = OBJECT_ID('dbo.PhongBanChiaSe')
                    """
                )
            ).scalar()
        assert exists is not None
        assert index_exists == 1

    def test_lifecycle_review_constraints_exist(self, engine):
        """Guard migration/schema: lifecycle states phai bi rang buoc hop le."""
        required = {"CHK_LifecycleStatus", "CHK_ReviewStatus"}
        with engine.connect() as conn:
            rows = conn.execute(
                text(
                    """
                    SELECT name
                    FROM sys.check_constraints
                    WHERE parent_object_id = OBJECT_ID('dbo.TaiLieu')
                      AND name IN ('CHK_LifecycleStatus', 'CHK_ReviewStatus')
                    """
                )
            ).fetchall()
        found = {r[0] for r in rows}
        assert required.issubset(found)
