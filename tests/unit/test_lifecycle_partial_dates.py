import pytest
from sqlalchemy import create_engine, text

from mech_chatbot.db.repositories import lifecycle


pytestmark = pytest.mark.unit


@pytest.mark.parametrize("field,column", [
    ("effective_date", "EffectiveDate"),
    ("expiry_date", "ExpiryDate"),
    ("review_date", "ReviewDate"),
])
def test_partial_dates_preserve_other_dates_and_only_sync_changed_metadata(monkeypatch, field, column):
    engine = create_engine("sqlite://")
    baseline = {"EffectiveDate": "2026-01-01", "ExpiryDate": "2026-09-01", "ReviewDate": "2026-08-01"}
    with engine.begin() as connection:
        connection.execute(text("CREATE TABLE TaiLieu (DocID INTEGER PRIMARY KEY, EffectiveDate TEXT, ExpiryDate TEXT, ReviewDate TEXT)"))
        connection.execute(text("INSERT INTO TaiLieu VALUES (9, :EffectiveDate, :ExpiryDate, :ReviewDate)"), baseline)
    monkeypatch.setattr(lifecycle, "engine", engine)
    monkeypatch.setattr(lifecycle, "_ensure_engine", lambda: None)
    vectors = []
    monkeypatch.setattr(lifecycle._r_qdrant, "update_qdrant_metadata", lambda *args: vectors.append(args))
    monkeypatch.setattr(lifecycle._r_semantic_cache, "_invalidate_semantic_cache", lambda *_: None)
    monkeypatch.setattr(lifecycle._r_audit, "write_audit_log", lambda **_: None)
    try:
        assert lifecycle.set_document_lifecycle(9, **{field: "2026-07-02"}) is True
        with engine.connect() as connection:
            actual = dict(connection.execute(text("SELECT EffectiveDate, ExpiryDate, ReviewDate FROM TaiLieu WHERE DocID=9")).mappings().one())
        assert actual == {**baseline, column: "2026-07-02"}
        assert vectors == [(9, {field: "2026-07-02"})]
        assert lifecycle.set_document_lifecycle(9, effective_date="", expiry_date=None) is False
        assert len(vectors) == 1
    finally:
        engine.dispose()
