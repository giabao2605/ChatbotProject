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
- Mac dinh doi chieu SourceSystem=upload trong collection chinh; fixture eval
  dung collection rieng va khong duoc tron vao snapshot nay.
"""
import os
import json
from pathlib import Path

import pytest
from sqlalchemy import text

pytestmark = [pytest.mark.integration, pytest.mark.security]


def _pinned_snapshot():
    raw_path = os.getenv("CONSISTENCY_SNAPSHOT_PATH", "").strip()
    if not raw_path:
        return None
    payload = json.loads(Path(raw_path).read_text(encoding="utf-8"))
    if payload.get("schema") != "phase3-ingestion-consistency-snapshot-v1":
        raise AssertionError("invalid ingestion consistency snapshot schema")
    return payload


@pytest.fixture(scope="module")
def engine():
    if os.getenv("RUN_DB_TESTS") != "1":
        pytest.skip("Can SQL Server that: dat RUN_DB_TESTS=1")
    from mech_chatbot.config.settings import SqlSettings, load_settings
    from mech_chatbot.db.engine import build_database_runtime

    runtime = build_database_runtime(
        SqlSettings.from_settings(load_settings())
    )
    try:
        yield runtime.engine
    finally:
        runtime.close()


@pytest.fixture(scope="module")
def qdrant():
    if os.getenv("RUN_QDRANT_TESTS") != "1":
        pytest.skip("Can Qdrant that: dat RUN_QDRANT_TESTS=1")
    qc = pytest.importorskip("qdrant_client")
    from mech_chatbot.config.settings import QdrantSettings, load_settings

    settings = QdrantSettings.from_settings(load_settings())
    client = qc.QdrantClient(
        url=settings.url,
        api_key=settings.api_key,
        timeout=60,
    )
    try:
        yield client
    finally:
        client.close()


@pytest.fixture(scope="module")
def qmodels():
    qc = pytest.importorskip("qdrant_client")
    return qc.models


def _sample_vectorized_docs(engine):
    snapshot = _pinned_snapshot()
    expected_documents = (snapshot or {}).get("documents") or []
    requested_limit = max(
        int(os.getenv("CONSISTENCY_SAMPLE_LIMIT", "50")),
        len(expected_documents),
    )
    limit = max(1, min(500, requested_limit))
    source_system = str(
        (snapshot or {}).get("source_system")
        or os.getenv("CONSISTENCY_SOURCE_SYSTEM", "upload")
    ).strip()
    with engine.connect() as conn:
        rows = conn.execute(
            text(
                """
                SELECT TOP (:limit)
                    DocID,
                    TenFile,
                    ThuMuc,
                    SourceSystem,
                    Domain,
                    SecurityLevel,
                    Site,
                    Servable,
                    PublicationState,
                    PublicationVersion,
                    ExternalProcessingPolicy
                FROM TaiLieu
                WHERE TrangThaiVector = 1
                  AND (LifecycleStatus IS NULL OR LifecycleStatus <> 'deleting')
                  AND COALESCE(SourceSystem, 'upload') = :source_system
                ORDER BY DocID DESC
                """
            ),
            {"limit": limit, "source_system": source_system},
        ).mappings().all()
    if snapshot is not None:
        expected = {
            int(item["doc_id"]): str(item["file_name"])
            for item in expected_documents
        }
        actual = {int(row["DocID"]): str(row["TenFile"]) for row in rows}
        assert {
            doc_id: actual.get(doc_id) for doc_id in expected
        } == expected, "SQL document identity drifted from pinned Phase 3 snapshot"
        rows = [row for row in rows if int(row["DocID"]) in expected]
    return rows


def _assert_snapshot_collection(collection):
    snapshot = _pinned_snapshot()
    if snapshot is not None:
        assert snapshot.get("collection") == collection


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
    from mech_chatbot.config.settings import load_settings

    collection = load_settings().QDRANT_COLLECTION
    _assert_snapshot_collection(collection)
    docs = _sample_vectorized_docs(engine)
    if not docs:
        pytest.skip("Khong co TaiLieu.TrangThaiVector=1 de doi chieu")

    missing = []
    for doc in docs:
        points = _scroll_points_for_doc(qdrant, qmodels, collection, doc["DocID"], limit=1)
        if not points:
            missing.append({"DocID": doc["DocID"], "TenFile": doc["TenFile"], "ThuMuc": doc["ThuMuc"]})

    assert not missing, f"SQL co TrangThaiVector=1 nhung Qdrant khong co points: {missing[:10]}"


def test_qdrant_payload_matches_sql_rbac_metadata(engine, qdrant, qmodels):
    from mech_chatbot.config.settings import load_settings

    collection = load_settings().QDRANT_COLLECTION
    _assert_snapshot_collection(collection)
    docs = _sample_vectorized_docs(engine)
    if not docs:
        pytest.skip("Khong co TaiLieu.TrangThaiVector=1 de doi chieu")

    mismatches = []
    for doc in docs:
        points = _scroll_points_for_doc(qdrant, qmodels, collection, doc["DocID"], limit=1)
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

        if bool(meta.get("servable")) != bool(doc["Servable"]):
            mismatches.append((doc_id, "servable", bool(doc["Servable"]), meta.get("servable")))
        if meta.get("publication_state") != doc["PublicationState"]:
            mismatches.append((doc_id, "publication_state", doc["PublicationState"], meta.get("publication_state")))
        if int(meta.get("publication_version") or 0) != int(doc["PublicationVersion"] or 0):
            mismatches.append((doc_id, "publication_version", doc["PublicationVersion"], meta.get("publication_version")))
        if meta.get("external_processing_policy") != doc["ExternalProcessingPolicy"]:
            mismatches.append((doc_id, "external_processing_policy", doc["ExternalProcessingPolicy"], meta.get("external_processing_policy")))

        sql_depts = _csv_tokens(_document_departments(engine, doc_id, doc["ThuMuc"]))
        q_depts = _csv_tokens(meta.get("phong_ban_quyen"))
        # SQL PhongBanChiaSe/ThuMuc la source-of-truth toi thieu; payload co the them CHUNG.
        if sql_depts and not set(sql_depts).issubset(set(q_depts)):
            mismatches.append((doc_id, "phong_ban_quyen", sql_depts, q_depts))

    assert not mismatches, f"Payload Qdrant lech SQL RBAC metadata: {mismatches[:20]}"
