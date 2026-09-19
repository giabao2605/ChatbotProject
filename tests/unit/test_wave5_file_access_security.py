from pathlib import Path

import pytest

from mech_chatbot.api import file_access


pytestmark = [pytest.mark.unit, pytest.mark.security]


class _Rows:
    def __init__(self, one=None, many=()):
        self._one = one
        self._many = tuple(many)

    def fetchone(self):
        return self._one

    def fetchall(self):
        return list(self._many)


class _Connection:
    def __init__(self, *, document=None, departments=(), page=None, vision=None, error=None):
        self._document = document
        self._departments = tuple(departments)
        self._page = page
        self._vision = vision
        self._error = error

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def execute(self, statement, _parameters):
        if self._error is not None:
            raise self._error
        query = str(statement)
        if "FROM dbo.TaiLieu" in query:
            return _Rows(one=self._document)
        if "PhongBanChiaSe" in query:
            return _Rows(many=self._departments)
        if "VisionSummary" in query:
            return _Rows(one=self._vision)
        return _Rows(one=self._page)


class _Engine:
    def __init__(self, **connection_values):
        self._connection_values = dict(connection_values)

    def connect(self):
        return _Connection(**self._connection_values)


def _published_row(file_path):
    return (
        9,
        "drawing.pdf",
        str(file_path),
        "Technical",
        "INTERNAL",
        "HQ",
        "published",
        "approved",
        1,
        "published",
        1,
        "effective",
        None,
        None,
    )


def test_database_record_loading_normalizes_values_and_skips_blank_grants(monkeypatch, tmp_path):
    raw_file = tmp_path / "drawing.pdf"
    monkeypatch.setattr(
        file_access,
        "engine",
        _Engine(
            document=_published_row(raw_file),
            departments=[("Technical",), (None,), ()],
        ),
    )

    record = file_access.load_document_access_record(9)

    assert record == file_access.DocumentAccessRecord(
        doc_id=9,
        ten_file="drawing.pdf",
        file_path=str(raw_file),
        thu_muc="Technical",
        security_level="internal",
        site="HQ",
        lifecycle_status="published",
        review_status="approved",
        departments=("Technical",),
        servable=True,
        publication_state="published",
        is_current=True,
        effective_status="effective",
        effective_date=None,
        expiry_date=None,
    )


@pytest.mark.parametrize("engine", [None, _Engine(document=None)])
def test_missing_engine_or_document_returns_no_access_record(monkeypatch, engine):
    monkeypatch.setattr(file_access, "engine", engine)

    assert file_access.load_document_access_record(404) is None


def test_can_access_document_combines_database_record_with_policy(monkeypatch, tmp_path):
    raw_file = tmp_path / "drawing.pdf"
    monkeypatch.setattr(
        file_access,
        "engine",
        _Engine(document=_published_row(raw_file), departments=[("Technical",)]),
    )

    decision, record = file_access.can_access_document({"roles": ["admin"]}, 9)

    assert record is not None
    assert decision == file_access.AccessDecision(True, "global_admin", security_level="internal")


def test_access_policy_fails_closed_for_missing_document_and_strict_cross_site(monkeypatch):
    assert file_access.evaluate_document_access({}, None).reason == "document_not_found"
    monkeypatch.setenv("RBAC_STRICT_SITE_FILTER", "yes")
    record = file_access.DocumentAccessRecord(
        doc_id=9,
        ten_file="drawing.pdf",
        file_path="data/raw/drawing.pdf",
        thu_muc="Technical",
        security_level="public",
        site="DN",
        lifecycle_status="published",
        review_status="approved",
        departments=("Technical",),
    )
    profile = {
        "roles": ["viewer", ""],
        "department": "Technical",
        "allowed_departments": ["", "Technical"],
        "max_security_level": "public",
        "allowed_sites": ["HQ", ""],
    }

    decision = file_access.evaluate_document_access(profile, record)

    assert decision == file_access.AccessDecision(False, "site_denied", security_level="public")


def test_relative_and_original_paths_are_resolved_only_inside_approved_roots(monkeypatch, tmp_path):
    raw_root = tmp_path / "data" / "raw"
    raw_root.mkdir(parents=True)
    document = raw_root / "drawing.pdf"
    document.write_bytes(b"pdf")
    monkeypatch.setattr(file_access, "project_root", lambda: tmp_path)
    monkeypatch.setattr(file_access, "data_raw_root", lambda: raw_root)
    record = file_access.DocumentAccessRecord(
        doc_id=9,
        ten_file="drawing.pdf",
        file_path="data/raw/drawing.pdf",
        thu_muc="Technical",
        security_level="public",
        site="HQ",
        lifecycle_status="published",
        review_status="approved",
        departments=("Technical",),
    )

    assert file_access.resolve_under_root("data/raw/drawing.pdf", [raw_root]) == document.resolve()
    assert file_access.original_file_path(record) == document.resolve()
    assert file_access.resolve_under_root(None, [raw_root]) is None
    assert file_access.resolve_under_root("data/processed/secret.txt", [raw_root]) is None
    assert file_access.data_processed_root() == tmp_path / "data" / "processed"


def test_page_image_fallback_uses_only_basename_inside_processed_root(monkeypatch, tmp_path):
    processed = tmp_path / "processed"
    processed.mkdir()
    local_image = processed / "page-9-1.png"
    local_image.write_bytes(b"png")
    monkeypatch.setattr(file_access, "data_processed_root", lambda: processed)
    monkeypatch.setattr(
        file_access,
        "engine",
        _Engine(page=("/untrusted/worker/path/page-9-1.png",)),
    )

    assert file_access.page_image_path(9, 1) == local_image.resolve()


def test_page_image_and_vision_checks_fail_closed_for_missing_or_broken_metadata(monkeypatch, tmp_path):
    processed = tmp_path / "processed"
    processed.mkdir()
    local_image = processed / "page.png"
    local_image.write_bytes(b"png")
    monkeypatch.setattr(file_access, "data_processed_root", lambda: processed)

    monkeypatch.setattr(file_access, "engine", _Engine(page=None))
    assert file_access.page_image_path(9, 1) is None

    monkeypatch.setattr(file_access, "engine", None)
    assert file_access.page_has_vision(9, 1) is False

    monkeypatch.setattr(file_access, "engine", _Engine(vision=("", str(local_image))))
    assert file_access.page_has_vision(9, 1) is False

    monkeypatch.setattr(file_access, "engine", _Engine(error=RuntimeError("database unavailable")))
    assert file_access.page_has_vision(9, 1) is False


def test_page_has_vision_requires_summary_and_existing_approved_image(monkeypatch, tmp_path):
    processed = tmp_path / "processed"
    processed.mkdir()
    local_image = processed / "page.png"
    local_image.write_bytes(b"png")
    monkeypatch.setattr(file_access, "data_processed_root", lambda: processed)
    monkeypatch.setattr(
        file_access,
        "engine",
        _Engine(vision=("detected dimensions", str(local_image)), page=(str(local_image),)),
    )

    assert file_access.page_has_vision(9, 1) is True


def test_chat_image_accepts_plain_filename_but_rejects_empty_identifier(monkeypatch, tmp_path):
    raw_root = tmp_path / "raw"
    monkeypatch.setattr(file_access, "data_raw_root", lambda: raw_root)

    assert file_access.chat_image_path("capture.png") == (raw_root / "Chat_Images" / "capture.png").resolve()
    assert file_access.chat_image_path("") is None
