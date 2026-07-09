"""P1 — Test NHAT QUAN SQL <-> Qdrant.

Chay khi co CA SQL Server va Qdrant staging/test:

    $env:RUN_DB_TESTS=1
    $env:RUN_QDRANT_TESTS=1
    pytest tests/integration/test_sql_qdrant_consistency.py -v

Bat bien can bao ve:
- Moi doc da vector hoa (TaiLieu.TrangThaiVector=1) phai co it nhat 1 point Qdrant.
- Payload Qdrant metadata phai khop cac field RBAC/phan loai trong SQL:
  SecurityLevel, Domain, PhongBanChiaSe/phong_ban_quyen, Site.

Luu y an toan:
- Test chi READ SQL/Qdrant, khong sua du lieu.
- Mac dinh sample 50 doc moi nhat; co the doi bang CONSISTENCY_SAMPLE_LIMIT.
"""
import os

import pytest
from sqlalchemy import text

pytestmark = [pytest.mark.integration, pytest.mark.security]


@pytest.fixture(scope="module")
def engine():
    if os.getenv("RUN_DB_TESTS") != "1":
        pytest.skip("Can SQL Server that: dat RUN_DB_TESTS=1")
    from mech_chatbot.db.repository import engine as _engine
    if _engine is None:
        pytest.skip("engine=None: kiem tra SQL_SERVER/SQL_DATABASE trong .env")
    return _engine


@pytest.fixture(scope="module")
def qdrant():
    if os.getenv("RUN_QDRANT_TESTS") != "1":
        pytest.skip("Can Qdrant that: dat RUN_QDRANT_TESTS=1")
    qc = pytest.importorskip("qdrant_client")
    url = os.getenv("QDRANT_URL")
    if not url:
        pytest.skip("Thieu QDRANT_URL")
    return qc.QdrantClient(url=url, api_key=os.getenv("QDRANT_API_KEY"), timeout=60)


@pytest.fixture(scope="module")
def qmodels():
    qc = pytest.importorskip("qdrant_client")
    return qc.models


def _sample_vectorized_docs(engine):
    limit = int(os.getenv("CONSISTENCY_SAMPLE_LIMIT", "50"))
    with engine.connect() as conn:
        rows = conn.execute(
            text(
                f"""
                SELECT TOP ({limit})
                    DocID,
                    TenFile,
                    ThuMuc,
                    Domain,
                    SecurityLevel,
                    Site
                FROM TaiLieu
                WHERE TrangThaiVector = 1
                  AND (LifecycleStatus IS NULL OR LifecycleStatus <> 'deleting')
                ORDER BY DocID DESC
                """
            )
        ).mappings().all()
    return rows


def _document_departments(engine, doc_id, fallback_dept):
    with engine.connect() as conn:
        rows = conn.execute(
            text("SELECT DeptCode FROM dbo.PhongBanChiaSe WHERE DocID = :d"),
            {"d": doc_id},
        ).fetchall()
    depts = [r[0] for r in rows if r and r[0]]
    return depts or [fallback_dept]


def _scroll_points_for_doc(qdrant, qmodels, collection, doc_id, limit=10):
    points, _ = qdrant.scroll(
        collection_name=collection,
        scroll_filter=qmodels.Filter(
            must=[
                qmodels.FieldCondition(
                    key="metadata.doc_id",
                    match=qmodels.MatchValue(value=int(doc_id)),
                )
            ]
        ),
        limit=limit,
        with_payload=True,
        with_vectors=False,
    )
    return points


def _payload_meta(point):
    payload = point.payload or {}
    meta = payload.get("metadata") or {}
    return meta if isinstance(meta, dict) else {}


def _csv_tokens(value):
    if value is None:
        return []
    if isinstance(value, (list, tuple, set)):
        raw = []
        for item in value:
            raw.extend(str(item).split(","))
    else:
        raw = str(value).split(",")
    return sorted({x.strip() for x in raw if x and x.strip()})


def test_every_vectorized_doc_has_qdrant_points(engine, qdrant, qmodels):
    from mech_chatbot.config.settings import QDRANT_COLLECTION

    docs = _sample_vectorized_docs(engine)
    if not docs:
        pytest.skip("Khong co TaiLieu.TrangThaiVector=1 de doi chieu")

    missing = []
    for doc in docs:
        points = _scroll_points_for_doc(qdrant, qmodels, QDRANT_COLLECTION, doc["DocID"], limit=1)
        if not points:
            missing.append({"DocID": doc["DocID"], "TenFile": doc["TenFile"], "ThuMuc": doc["ThuMuc"]})

    assert not missing, f"SQL co TrangThaiVector=1 nhung Qdrant khong co points: {missing[:10]}"


def test_qdrant_payload_matches_sql_rbac_metadata(engine, qdrant, qmodels):
    from mech_chatbot.config.settings import QDRANT_COLLECTION

    docs = _sample_vectorized_docs(engine)
    if not docs:
        pytest.skip("Khong co TaiLieu.TrangThaiVector=1 de doi chieu")

    mismatches = []
    for doc in docs:
        points = _scroll_points_for_doc(qdrant, qmodels, QDRANT_COLLECTION, doc["DocID"], limit=1)
        if not points:
            # Test tren se bao missing; bo qua tai day de thong bao ro rang hon.
            continue

        meta = _payload_meta(points[0])
        doc_id = doc["DocID"]

        sql_security = (doc["SecurityLevel"] or "").strip() or None
        q_security = meta.get("security_level")
        if sql_security and q_security != sql_security:
            mismatches.append((doc_id, "security_level", sql_security, q_security))

        sql_domain = (doc["Domain"] or "").strip() or None
        q_domain = meta.get("domain")
        if sql_domain and q_domain != sql_domain:
            mismatches.append((doc_id, "domain", sql_domain, q_domain))

        sql_site = (doc["Site"] or "").strip() or None
        q_site = meta.get("site")
        if sql_site and q_site != sql_site:
            mismatches.append((doc_id, "site", sql_site, q_site))

        sql_depts = _csv_tokens(_document_departments(engine, doc_id, doc["ThuMuc"]))
        q_depts = _csv_tokens(meta.get("phong_ban_quyen"))
        # SQL PhongBanChiaSe/ThuMuc la source-of-truth toi thieu; payload co the them CHUNG.
        if sql_depts and not set(sql_depts).issubset(set(q_depts)):
            mismatches.append((doc_id, "phong_ban_quyen", sql_depts, q_depts))

    assert not mismatches, f"Payload Qdrant lech SQL RBAC metadata: {mismatches[:20]}"
