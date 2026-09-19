import sys
from types import ModuleType, SimpleNamespace

import pytest

from mech_chatbot.ingestion.pdf import chunking


pytestmark = pytest.mark.unit


def test_contextual_chunk_flag_follows_explicit_config(monkeypatch):
    monkeypatch.setenv("ENABLE_CONTEXTUAL_CHUNK", "false")
    enabled = chunking.PdfIngestionConfig(contextual_chunk_enabled=True)
    disabled = chunking.PdfIngestionConfig(contextual_chunk_enabled=False)

    assert chunking._contextual_chunk_enabled(enabled) is True
    assert chunking._contextual_chunk_enabled(disabled) is False


def test_context_prefix_contains_available_document_metadata():
    prefix = chunking._build_chunk_context_prefix(
        {
            "ten_san_pham": "Valve Assembly",
            "base_code": "VA-10",
            "loai_tai_lieu": "drawing",
            "cong_doan": "Machining",
            "vat_lieu": "SUS304",
        }
    )

    assert prefix == (
        "[Ngu canh] Tai lieu: Valve Assembly; Ma: VA-10; Loai: drawing; "
        "Cong doan/phong ban: Machining; Vat lieu: SUS304."
    )


@pytest.mark.parametrize(
    ("metadata", "expected"),
    [
        ({"file_goc": "fallback.pdf", "ma_doi_tuong": ["A-1"]}, "Tai lieu: fallback.pdf; Ma: A-1"),
        ({"ma_doi_tuong": "B-2", "doc_type": "bom"}, "Ma: B-2; Loai: bom"),
        ({"ma_doi_tuong": []}, ""),
        (None, ""),
    ],
)
def test_context_prefix_uses_fallback_fields_without_inventing_metadata(metadata, expected):
    prefix = chunking._build_chunk_context_prefix(metadata)

    if expected:
        assert prefix == f"[Ngu canh] {expected}."
    else:
        assert prefix == ""


def test_word_tokenization_is_lazy_and_cached(monkeypatch):
    calls = []
    fake_underthesea = ModuleType("underthesea")

    def word_tokenize(text, format):
        calls.append((text, format))
        return "van_ban_ky_thuat"

    fake_underthesea.word_tokenize = word_tokenize
    monkeypatch.setitem(sys.modules, "underthesea", fake_underthesea)
    chunking.tokenize_cached.cache_clear()

    assert chunking.tokenize_cached("van ban ky thuat") == "van_ban_ky_thuat"
    assert chunking.tokenize_cached("van ban ky thuat") == "van_ban_ky_thuat"
    assert calls == [("van ban ky thuat", "text")]
    chunking.tokenize_cached.cache_clear()


def test_tokenizer_loads_once_and_reports_encoded_length(monkeypatch):
    calls = []
    tokenizer = SimpleNamespace(encode=lambda text: [1, 2, 3] if text else [])
    fake_transformers = ModuleType("transformers")
    fake_transformers.AutoTokenizer = SimpleNamespace(
        from_pretrained=lambda model: calls.append(model) or tokenizer
    )
    monkeypatch.setitem(sys.modules, "transformers", fake_transformers)
    monkeypatch.setattr(chunking, "GLOBAL_TOKENIZER", None)
    monkeypatch.setattr(chunking, "_TOKENIZER_LOADED", False)

    assert chunking.tokenizer_length("engineering text") == 3
    assert chunking._get_tokenizer() is tokenizer
    assert calls == [chunking.EMBEDDING_MODEL_NAME]


def test_tokenizer_failure_falls_back_to_character_length_without_retry(monkeypatch):
    calls = []
    fake_transformers = ModuleType("transformers")

    def unavailable(model):
        calls.append(model)
        raise OSError("model cache unavailable")

    fake_transformers.AutoTokenizer = SimpleNamespace(from_pretrained=unavailable)
    monkeypatch.setitem(sys.modules, "transformers", fake_transformers)
    monkeypatch.setattr(chunking, "GLOBAL_TOKENIZER", None)
    monkeypatch.setattr(chunking, "_TOKENIZER_LOADED", False)

    assert chunking.tokenizer_length("abcd") == 4
    assert chunking.tokenizer_length("xy") == 2
    assert calls == [chunking.EMBEDDING_MODEL_NAME]
