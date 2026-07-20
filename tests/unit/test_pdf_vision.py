from types import SimpleNamespace

import pytest

from mech_chatbot.ingestion import domain_handlers, vision_cache
from mech_chatbot.ingestion.pdf import vision


pytestmark = pytest.mark.unit


class _Model:
    def __init__(self, response="ok"):
        self.response = response
        self.calls = []

    def generate_content(self, payload):
        self.calls.append(payload)
        return SimpleNamespace(text=self.response)


def test_call_vision_model_uses_text_or_multimodal_payload():
    model = _Model()
    image = object()

    assert vision.call_vision_model(model, "text") == SimpleNamespace(text="ok")
    assert vision.call_vision_model(model, "image", image) == SimpleNamespace(text="ok")
    assert model.calls == ["text", ["image", image]]


@pytest.mark.parametrize(
    ("raw_text", "expected"),
    [
        ('```json\n{"materials": ["steel"]}\n```', {"materials": ["steel"]}),
        ('prefix ```\n{"value": 2}\n``` suffix', {"value": 2}),
        ('answer: {"value": 3} end', {"value": 3}),
        ('{"value": 4}', {"value": 4}),
        ("not json", None),
        (None, None),
    ],
)
def test_parse_vision_json_handles_provider_response_shapes(raw_text, expected):
    assert vision.parse_vision_json(raw_text) == expected


def test_format_vision_data_preserves_all_supported_fields():
    result = vision.format_vision_data(
        {
            "document_codes": ["DWG-1", 2],
            "part_names": ["Truc"],
            "materials": ["C45"],
            "dimensions": ["10 mm"],
            "tolerances": ["+/-0.1"],
            "technical_notes": ["Mai canh", 3],
            "bom_rows": [{"code": "A"}],
            "uncertain_fields": ["stamp"],
        }
    )

    assert "DWG-1, 2" in result
    assert "Tên chi tiết/vật tư: Truc" in result
    assert "Vật liệu: C45" in result
    assert "Kích thước nổi bật: 10 mm" in result
    assert "Dung sai: +/-0.1" in result
    assert "Mai canh\n  3" in result
    assert "{'code': 'A'}" in result
    assert "stamp" in result
    assert vision.format_vision_data({}) == ""
    assert vision.format_vision_data(None) == ""
    assert vision.format_vision_data({"materials": []}) == ""


@pytest.mark.parametrize("workers", [None, "bad", "1", "0"])
def test_prewarm_is_noop_when_worker_setting_is_disabled(monkeypatch, workers):
    if workers is None:
        monkeypatch.delenv("INGEST_VISION_PREWARM_WORKERS", raising=False)
    else:
        monkeypatch.setenv("INGEST_VISION_PREWARM_WORKERS", workers)
    doc = SimpleNamespace(__len__=lambda: 1)

    assert vision._prewarm_vision_cache(doc, "a.pdf", "QA", "generic", object()) is None


def test_prewarm_is_noop_without_model_or_document(monkeypatch):
    monkeypatch.setenv("INGEST_VISION_PREWARM_WORKERS", "2")

    assert vision._prewarm_vision_cache(None, "a.pdf", "QA", "generic", object()) is None
    assert vision._prewarm_vision_cache([], "a.pdf", "QA", "generic", None) is None


class _Pixmap:
    def __init__(self, saved_paths):
        self.saved_paths = saved_paths

    def save(self, path):
        self.saved_paths.append(path)


class _Page:
    def __init__(self, text, saved_paths):
        self.text = text
        self.saved_paths = saved_paths
        self.dpi = None

    def get_text(self, mode):
        assert mode == "text"
        return self.text

    def get_pixmap(self, *, dpi):
        self.dpi = dpi
        return _Pixmap(self.saved_paths)


class _Document:
    def __init__(self, pages):
        self.pages = pages

    def __len__(self):
        return len(self.pages)

    def load_page(self, index):
        return self.pages[index]


def test_prewarm_renders_only_needed_pages_and_populates_cache(tmp_path, monkeypatch):
    saved_paths = []
    light_page = _Page("short text", saved_paths)
    heavy_page = _Page("x" * 1501, saved_paths)
    doc = _Document([light_page, heavy_page])
    cached = {}
    progress = []
    model = _Model('{"materials": ["steel"]}')
    monkeypatch.setenv("INGEST_VISION_PREWARM_WORKERS", "2")
    monkeypatch.setenv("PDF_RENDER_DPI", "144")
    monkeypatch.setattr(vision, "IMAGE_DIR", str(tmp_path))
    monkeypatch.setattr(
        domain_handlers,
        "get_handler",
        lambda domain: SimpleNamespace(vision_always=False),
    )
    monkeypatch.setattr(vision_cache, "hash_image_file", lambda path: f"hash:{path}")
    monkeypatch.setattr(vision_cache, "get", lambda key: cached.get(key))
    monkeypatch.setattr(vision_cache, "put", lambda key, value: cached.update({key: value}))
    monkeypatch.setattr(vision.Image, "open", lambda path: f"image:{path}")

    vision._prewarm_vision_cache(
        doc,
        "drawing.pdf",
        'QA:*?"',
        "generic",
        model,
        progress.append,
    )

    assert len(saved_paths) == 1
    assert saved_paths[0].endswith("QA_drawing_page1.png")
    assert light_page.dpi == 144
    assert heavy_page.dpi is None
    assert progress == ["Pre-warm Vision song song 1 trang (workers=2)..."]
    assert list(cached.values()) == [{"materials": ["steel"]}]
    assert len(model.calls) == 1


def test_prewarm_skips_cached_image_without_calling_provider(tmp_path, monkeypatch):
    page = _Page("short", [])
    model = _Model()
    monkeypatch.setenv("INGEST_VISION_PREWARM_WORKERS", "2")
    monkeypatch.setattr(vision, "IMAGE_DIR", str(tmp_path))
    monkeypatch.setattr(
        domain_handlers,
        "get_handler",
        lambda _domain: SimpleNamespace(vision_always=True),
    )
    monkeypatch.setattr(vision_cache, "hash_image_file", lambda _path: "cached")
    monkeypatch.setattr(vision_cache, "get", lambda _key: {"materials": ["steel"]})

    vision._prewarm_vision_cache(
        _Document([page]), "drawing.pdf", "", "mechanical", model
    )

    assert model.calls == []


def test_prewarm_is_best_effort_for_page_and_setup_failures(monkeypatch):
    class BrokenDocument:
        def __len__(self):
            return 1

        def load_page(self, _index):
            raise RuntimeError("render failed")

    monkeypatch.setenv("INGEST_VISION_PREWARM_WORKERS", "2")
    monkeypatch.setattr(
        domain_handlers,
        "get_handler",
        lambda _domain: SimpleNamespace(vision_always=True),
    )
    assert vision._prewarm_vision_cache(
        BrokenDocument(), "a.pdf", "QA", "mechanical", object()
    ) is None

    monkeypatch.setattr(
        domain_handlers,
        "get_handler",
        lambda _domain: (_ for _ in ()).throw(RuntimeError("handler failed")),
    )
    assert vision._prewarm_vision_cache(
        _Document([]), "a.pdf", "QA", "mechanical", object()
    ) is None
