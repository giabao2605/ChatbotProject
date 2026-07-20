import json
from types import SimpleNamespace

import pytest

from mech_chatbot.ingestion import document_classifier as classifier


pytestmark = pytest.mark.unit


class _Page:
    def __init__(self, text):
        self._text = text

    def get_text(self):
        return self._text


class _Document:
    def __init__(self, pages):
        self._pages = tuple(_Page(page) for page in pages)
        self.closed = False

    def __len__(self):
        return len(self._pages)

    def __getitem__(self, index):
        return self._pages[index]

    def close(self):
        self.closed = True


class _Result:
    def __init__(self, row):
        self._row = row

    def fetchone(self):
        return self._row


class _Connection:
    def __init__(self, row=None, error=None):
        self._row = row
        self._error = error

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def execute(self, *_args, **_kwargs):
        if self._error is not None:
            raise self._error
        return _Result(self._row)


class _Engine:
    def __init__(self, row=None, error=None):
        self._row = row
        self._error = error

    def connect(self):
        return _Connection(self._row, self._error)


def test_pdf_text_extractors_close_documents_and_select_representative_pages(monkeypatch):
    first_document = _Document(["cover", "scope", "appendix"])
    representative_document = _Document([f"content-{index}" for index in range(10)])
    opened = iter((first_document, representative_document))
    monkeypatch.setattr(classifier.fitz, "open", lambda _path: next(opened))

    first_pages = classifier.extract_first_pages("drawing.pdf", num_pages=2)
    representative = classifier.extract_pages_for_classification(
        "drawing.pdf", max_pages=4, char_budget=10_000
    )

    assert first_pages == "--- Page 1 ---\ncover\n--- Page 2 ---\nscope\n"
    assert [marker in representative for marker in ("Page 1", "Page 2", "Page 6", "Page 10")] == [
        True,
        True,
        True,
        True,
    ]
    assert "Page 3" not in representative
    assert first_document.closed is True
    assert representative_document.closed is True


def test_representative_page_extraction_obeys_budget_and_handles_empty_or_broken_pdf(monkeypatch):
    budgeted = _Document(["a" * 50, "b" * 50])
    empty = _Document([])
    opened = iter((budgeted, empty))
    monkeypatch.setattr(classifier.fitz, "open", lambda _path: next(opened))

    assert len(classifier.extract_pages_for_classification("long.pdf", char_budget=25)) == 25
    assert classifier.extract_pages_for_classification("empty.pdf") == ""
    assert empty.closed is True

    monkeypatch.setattr(
        classifier.fitz,
        "open",
        lambda _path: (_ for _ in ()).throw(RuntimeError("invalid pdf payload")),
    )
    assert classifier.extract_first_pages("broken.pdf") == ""
    assert classifier.extract_pages_for_classification("broken.pdf") == ""


@pytest.mark.parametrize(
    ("row", "error", "expected"),
    [
        ((42, "VALVE"), None, 42),
        (None, None, None),
        (None, RuntimeError("database unavailable"), None),
    ],
)
def test_existing_family_lookup_returns_identifier_or_fails_closed(monkeypatch, row, error, expected):
    monkeypatch.setattr(classifier, "engine", _Engine(row=row, error=error))

    assert classifier.check_existing_family("VALVE") == expected


def test_classification_uses_safe_profile_type_and_promotes_existing_family(monkeypatch, tmp_path):
    path = tmp_path / "Valve_v2.pdf"
    path.write_bytes(b"not-read-because-boundary-is-faked")
    monkeypatch.setattr(classifier.fitz, "open", lambda _path: _Document(["valve drawing"]))
    monkeypatch.setattr(classifier, "engine", _Engine(row=(42, "VALVE")))
    monkeypatch.setattr(
        "mech_chatbot.ingestion.domain_registry.resolve_domain_by_department",
        lambda _department: "mechanical",
    )
    monkeypatch.setattr(
        "mech_chatbot.ingestion.domain_registry.resolve_security_by_department",
        lambda _department: "internal",
    )
    monkeypatch.setattr(
        classifier,
        "cohere_invoke",
        lambda *_a, **_k: SimpleNamespace(
            content="```json\n"
            + json.dumps(
                {
                    "base_code": "Valve",
                    "version_no": 2,
                    "document_type": "bom",
                    "detected_action": "new_document",
                }
            )
            + "\n```"
        ),
    )

    result = classifier.classify_document(
        str(path),
        thu_muc="Technical",
        document_types=["technical_drawing", " ", "bom"],
    )

    assert result["base_code"] == "valve"
    assert result["document_type"] == "bom"
    assert result["document_type_validation"] == "profile_valid"
    assert result["possible_existing_family"] == "valve"
    assert result["detected_action"] == "new_version"
    assert result["security_level"] == "internal"


def test_non_object_classifier_response_falls_back_without_leaking_provider_payload(monkeypatch):
    monkeypatch.setattr(classifier.fitz, "open", lambda _path: _Document(["request text"]))
    monkeypatch.setattr(classifier, "cohere_invoke", lambda *_a, **_k: SimpleNamespace(content='["secret"]'))
    monkeypatch.setattr(
        "mech_chatbot.ingestion.domain_registry.resolve_domain_by_department",
        lambda _department: "generic",
    )
    monkeypatch.setattr(
        "mech_chatbot.ingestion.domain_registry.resolve_security_by_department",
        lambda _department: "confidential",
    )

    result = classifier.classify_document(
        "request.pdf", original_filename="request.pdf", thu_muc="HR", document_types=[]
    )

    assert result["classification_failed"] is True
    assert result["document_type_validation"] == "classifier_error_fallback"
    assert result["reason"] == "Classifier fallback: ValueError."
    assert "secret" not in result["reason"]
