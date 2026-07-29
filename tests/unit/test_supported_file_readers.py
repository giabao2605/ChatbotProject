from types import SimpleNamespace

import pytest

from mech_chatbot.ingestion.pdf import readers


def _write(tmp_path, name, content, *, binary=False):
    path = tmp_path / name
    if binary:
        path.write_bytes(content)
    else:
        path.write_text(content, encoding="utf-8")
    return path


@pytest.mark.parametrize(
    ("name", "content", "expected_text", "expected_kind"),
    [
        ("data.json", '{"name": "Chi tiet"}', '"name": "Chi tiet"', "du_lieu_json"),
        ("data.xml", "<root><name>Chi tiet</name></root>", "Chi tiet", "du_lieu_xml"),
        ("notes.txt", "Noi dung ky thuat", "Noi dung ky thuat", "van_ban"),
        ("notes.md", "# Tieu de\nNoi dung", "# Tieu de\nNoi dung", "van_ban"),
    ],
)
def test_extract_text_dispatches_text_formats(
    tmp_path, name, content, expected_text, expected_kind
):
    path = _write(tmp_path, name, content)

    text, kind = readers.extract_text_from_supported_file(str(path), name)

    assert expected_text in text
    assert kind == expected_kind


@pytest.mark.parametrize(
    ("name", "content"),
    [
        ("binary.md", b"text\x00payload"),
        ("controls.markdown", b"ok" + (b"\x01" * 10)),
        ("invalid.md", b"\xff\xfe"),
    ],
)
def test_markdown_rejects_non_text_payloads(tmp_path, name, content):
    path = _write(tmp_path, name, content, binary=True)

    with pytest.raises(ValueError):
        readers.extract_text_from_supported_file(str(path), name)


def test_json_and_xml_fall_back_to_original_text_when_malformed(tmp_path):
    json_path = _write(tmp_path, "broken.json", "not-json")
    xml_path = _write(tmp_path, "broken.xml", "<root>")

    assert readers.extract_text_from_supported_file(
        str(json_path), json_path.name
    ) == ("not-json", "du_lieu_json")
    assert readers.extract_text_from_supported_file(
        str(xml_path), xml_path.name
    ) == ("<root>", "du_lieu_xml")


def test_html_fallback_removes_tags_and_decodes_entities(tmp_path, monkeypatch):
    path = _write(tmp_path, "page.html", "<h1>A&amp;B</h1><p>Thong so</p>")
    monkeypatch.setattr(readers, "BeautifulSoup", None)

    text, kind = readers.extract_text_from_supported_file(str(path), path.name)

    assert "A&B" in text
    assert "Thong so" in text
    assert "<h1>" not in text
    assert kind == "van_ban_html"


def test_html_parser_boundary_is_used_when_available(tmp_path, monkeypatch):
    path = _write(tmp_path, "page.htm", "<h1>ignored</h1>")
    soup = SimpleNamespace(get_text=lambda separator: f"parsed{separator}html")

    def parse(raw_text, parser):
        assert raw_text == "<h1>ignored</h1>"
        assert parser == "html.parser"
        return soup

    monkeypatch.setattr(readers, "BeautifulSoup", parse)

    assert readers.extract_text_from_supported_file(
        str(path), path.name
    ) == ("parsed\nhtml", "van_ban_html")


def test_text_reader_reports_failure_after_all_supported_encodings(
    tmp_path, monkeypatch
):
    path = _write(tmp_path, "notes.txt", b"payload", binary=True)
    encodings = []

    def fail_open(_path, _mode, *, encoding):
        encodings.append(encoding)
        raise UnicodeDecodeError(encoding, b"x", 0, 1, "bad")

    monkeypatch.setattr(readers, "open", fail_open, raising=False)

    with pytest.raises(UnicodeDecodeError, match="encoding pho bien"):
        readers.extract_text_from_supported_file(str(path), path.name)

    assert encodings == ["utf-8-sig", "utf-8", "cp1258", "cp1252", "latin-1"]


class _Frame:
    def __init__(self, text, *, empty=False):
        self._text = text
        self.empty = empty

    def fillna(self, _replacement):
        return self

    def to_string(self, *, index):
        assert index is False
        return self._text


def test_csv_reader_retries_encoding_and_formats_table(tmp_path, monkeypatch):
    path = _write(tmp_path, "parts.csv", "code,qty\nA,1")
    calls = []

    def read_csv(file_path, *, sep, encoding):
        calls.append((file_path, sep, encoding))
        if encoding == "utf-8-sig":
            raise UnicodeDecodeError("utf-8", b"x", 0, 1, "bad")
        return _Frame("code qty\nA 1")

    monkeypatch.setattr(readers, "pd", SimpleNamespace(read_csv=read_csv))

    text, kind = readers.extract_text_from_supported_file(str(path), path.name)

    assert text == "Bang du lieu tu parts.csv\ncode qty\nA 1"
    assert kind == "bang_du_lieu"
    assert [call[2] for call in calls] == ["utf-8-sig", "utf-8"]


def test_csv_reader_propagates_last_decode_error(tmp_path, monkeypatch):
    path = _write(tmp_path, "parts.tsv", "code\tqty")
    calls = []

    def read_csv(_path, *, sep, encoding):
        calls.append((sep, encoding))
        raise UnicodeDecodeError(encoding, b"x", 0, 1, "bad")

    monkeypatch.setattr(readers, "pd", SimpleNamespace(read_csv=read_csv))

    with pytest.raises(UnicodeDecodeError):
        readers.extract_text_from_supported_file(str(path), path.name)

    assert calls == [
        ("\t", "utf-8-sig"),
        ("\t", "utf-8"),
        ("\t", "cp1258"),
        ("\t", "cp1252"),
        ("\t", "latin-1"),
    ]


def test_excel_reader_preserves_sheet_names_and_empty_sheet(tmp_path, monkeypatch):
    path = _write(tmp_path, "parts.xlsx", b"placeholder", binary=True)
    frames = {"BOM": _Frame("code qty\nA 1"), "Empty": _Frame("", empty=True)}
    monkeypatch.setattr(
        readers,
        "pd",
        SimpleNamespace(read_excel=lambda *_args, **_kwargs: frames),
    )

    text, kind = readers.extract_text_from_supported_file(str(path), path.name)

    assert "Sheet: BOM\ncode qty\nA 1" in text
    assert "Sheet: Empty\n(Bang rong)" in text
    assert kind == "bang_du_lieu"


def test_word_reader_combines_paragraphs_and_tables(tmp_path, monkeypatch):
    path = _write(tmp_path, "manual.docx", b"placeholder", binary=True)
    document = SimpleNamespace(
        paragraphs=[SimpleNamespace(text=" Huong dan "), SimpleNamespace(text=" ")],
        tables=[
            SimpleNamespace(
                rows=[
                    SimpleNamespace(
                        cells=[SimpleNamespace(text="Ma"), SimpleNamespace(text="A\n01")]
                    )
                ]
            )
        ],
    )
    monkeypatch.setattr(
        readers,
        "docx",
        SimpleNamespace(Document=lambda _path: document),
    )

    text, kind = readers.extract_text_from_supported_file(str(path), path.name)

    assert text == "Huong dan\n\nBang 1:\nMa | A 01"
    assert kind == "van_ban_word"


def test_presentation_reader_combines_text_and_tables(tmp_path, monkeypatch):
    path = _write(tmp_path, "manual.pptx", b"placeholder", binary=True)
    text_shape = SimpleNamespace(has_text_frame=True, text=" Tong quan ", has_table=False)
    table_shape = SimpleNamespace(
        has_text_frame=False,
        has_table=True,
        table=SimpleNamespace(
            rows=[
                SimpleNamespace(
                    cells=[SimpleNamespace(text="Ma"), SimpleNamespace(text="A\n01")]
                )
            ]
        ),
    )
    presentation = SimpleNamespace(
        slides=[SimpleNamespace(shapes=[text_shape, table_shape])]
    )
    monkeypatch.setattr(readers, "Presentation", lambda _path: presentation)

    text, kind = readers.extract_text_from_supported_file(str(path), path.name)

    assert text == "Slide 1:\nTong quan\n\nBang:\nMa | A 01"
    assert kind == "slide"


def test_presentation_reader_ignores_empty_shapes_and_slides(tmp_path, monkeypatch):
    path = _write(tmp_path, "empty.pptx", b"placeholder", binary=True)
    shapes = [
        SimpleNamespace(has_text_frame=True, text=" ", has_table=False),
        SimpleNamespace(
            has_text_frame=False,
            has_table=True,
            table=SimpleNamespace(rows=[]),
        ),
    ]
    monkeypatch.setattr(
        readers,
        "Presentation",
        lambda _path: SimpleNamespace(slides=[SimpleNamespace(shapes=shapes)]),
    )

    assert readers.extract_text_from_supported_file(
        str(path), path.name
    ) == ("", "slide")


def test_optional_reader_dependency_is_required_at_public_boundary(
    tmp_path, monkeypatch
):
    path = _write(tmp_path, "parts.csv", "code,qty")
    monkeypatch.setattr(readers, "pd", None)

    with pytest.raises(ImportError, match="requirements.txt"):
        readers.extract_text_from_supported_file(str(path), path.name)


def test_image_reader_requires_model_and_formats_structured_vision(
    tmp_path, monkeypatch
):
    path = _write(tmp_path, "drawing.png", b"image", binary=True)
    image = object()
    model = object()
    monkeypatch.setattr(readers.Image, "open", lambda _path: image)
    monkeypatch.setattr(
        readers,
        "call_vision_model",
        lambda actual_model, prompt, actual_image: (
            SimpleNamespace(text='{"materials": ["steel"]}')
            if actual_model is model and actual_image is image and "drawing.png" in prompt
            else None
        ),
    )
    monkeypatch.setattr(readers, "parse_vision_json", lambda _text: {"materials": ["steel"]})
    monkeypatch.setattr(readers, "format_vision_data", lambda data: f"materials={data['materials'][0]}")

    with pytest.raises(ValueError, match="PROXYLLM_API_KEY"):
        readers.extract_text_from_supported_file(str(path), path.name)

    assert readers.extract_text_from_supported_file(
        str(path), path.name, model
    ) == ("materials=steel", "image_summary")


def test_image_reader_falls_back_to_raw_model_text(tmp_path, monkeypatch):
    path = _write(tmp_path, "drawing.jpg", b"image", binary=True)
    monkeypatch.setattr(readers.Image, "open", lambda _path: object())
    monkeypatch.setattr(
        readers,
        "call_vision_model",
        lambda *_args: SimpleNamespace(text="raw OCR"),
    )
    monkeypatch.setattr(readers, "parse_vision_json", lambda _text: None)

    assert readers.extract_text_from_supported_file(
        str(path), path.name, object()
    ) == ("raw OCR", "image_summary")


def test_unsupported_extension_reports_supported_formats(tmp_path):
    path = _write(tmp_path, "archive.bin", b"binary", binary=True)

    with pytest.raises(ValueError, match=r"Dinh dang \.bin.*\.pdf"):
        readers.extract_text_from_supported_file(str(path), path.name)
