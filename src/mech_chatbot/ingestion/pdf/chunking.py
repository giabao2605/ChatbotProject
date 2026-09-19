# -*- coding: utf-8 -*-
"""Auto-split tu ingestion/pdf_processor.py (P1.3). Giu nguyen logic goc; chi tach file + import.

FIX (native crash 0xC0000005 luc khoi dong worker):
- `transformers`/`tokenizers` va `underthesea` la cac lib native (C/Rust). Khi
  stack native cua chung duoc khoi tao TRUOC PyMuPDF (`fitz`) trong cung tien
  trinh (truong hop `import chunking` som, truoc `pipeline`), viec goi
  `AutoTokenizer.from_pretrained(...)` NGAY luc import gay xung dot DLL ->
  tien trinh chet im lang (access violation), khong traceback.
- Cach xu ly: KHONG import/nap tokenizer & underthesea o cap module. Chuyen sang
  LAZY-INIT: chi import + load khi thuc su can (luc do `fitz`/pipeline da nap
  xong nen an toan). Nho vay `import chunking` khong con trigger native load,
  worker khong con "chet cam" luc khoi dong.
"""

from langchain_text_splitters import RecursiveCharacterTextSplitter
from functools import lru_cache, partial

from mech_chatbot.config.logging import logger

# cross-module (owned) imports
from mech_chatbot.ingestion.pdf.config import (
    EMBEDDING_CHUNK_OVERLAP,
    EMBEDDING_CHUNK_SIZE,
    EMBEDDING_MODEL_NAME,
    PdfIngestionConfig,
)


def _contextual_chunk_enabled(
    config: PdfIngestionConfig | None = None,
):
    return bool(config.contextual_chunk_enabled) if config else False


def _build_chunk_context_prefix(md):
    """KH-4: 1-2 cau ngu canh mo ta chunk (tai lieu/ma/loai/cong doan/vat lieu) de chen
    TRUOC noi dung khi embed + BM25 -> cau hoi ngan match dung tai lieu hon.
    Chi anh huong page_content (embed/BM25); noi_dung_goc giu nguyen cho LLM.
    """
    md = md or {}
    _name = md.get("ten_san_pham") or md.get("file_goc") or ""
    _code = md.get("base_code") or ""
    if not _code:
        _mdt = md.get("ma_doi_tuong")
        if isinstance(_mdt, (list, tuple)):
            _code = _mdt[0] if _mdt else ""
        else:
            _code = _mdt or ""
    _loai = md.get("loai_tai_lieu") or md.get("doc_type") or ""
    _congdoan = md.get("cong_doan") or ""
    _vl = md.get("vat_lieu") or ""
    _bits = []
    if _name:
        _bits.append(f"Tai lieu: {_name}")
    if _code:
        _bits.append(f"Ma: {_code}")
    if _loai:
        _bits.append(f"Loai: {_loai}")
    if _congdoan:
        _bits.append(f"Cong doan/phong ban: {_congdoan}")
    if _vl:
        _bits.append(f"Vat lieu: {_vl}")
    if not _bits:
        return ""
    return "[Ngu canh] " + "; ".join(str(b) for b in _bits) + "."


@lru_cache(maxsize=4096)
def tokenize_cached(text):
    # LAZY: chi import underthesea khi thuc su tach tu (tranh nap native o cap module).
    import underthesea
    return underthesea.word_tokenize(text, format="text")


# --- Tokenizer (transformers) LAZY-INIT ------------------------------------
# Giu ten bien GLOBAL_TOKENIZER de tuong thich nguoc (van co trong __all__),
# nhung chi thuc su load o lan dung dau tien qua _get_tokenizer().
GLOBAL_TOKENIZER = None
_TOKENIZER_LOADED = False


@lru_cache(maxsize=8)
def _load_named_tokenizer(model_name):
    try:
        from transformers import AutoTokenizer

        tokenizer = AutoTokenizer.from_pretrained(model_name)
        logger.info(f"Da load AutoTokenizer ({model_name}) thanh cong cho Chunking.")
        return tokenizer
    except Exception as error:
        logger.warning(f"Khong load duoc tokenizer {model_name}: {error}")
        return None


def _get_tokenizer(model_name=None):
    """Load AutoTokenizer mot lan, LAZY. Tra ve None neu load loi (fallback do dai ky tu)."""
    global GLOBAL_TOKENIZER, _TOKENIZER_LOADED
    selected_model = str(model_name or EMBEDDING_MODEL_NAME)
    if selected_model != EMBEDDING_MODEL_NAME:
        return _load_named_tokenizer(selected_model)
    if _TOKENIZER_LOADED:
        return GLOBAL_TOKENIZER
    _TOKENIZER_LOADED = True
    try:
        from transformers import AutoTokenizer  # LAZY import (native)
        GLOBAL_TOKENIZER = AutoTokenizer.from_pretrained(EMBEDDING_MODEL_NAME)
        logger.info(f"Da load AutoTokenizer ({EMBEDDING_MODEL_NAME}) thanh cong cho Chunking.")
    except Exception as e:
        GLOBAL_TOKENIZER = None
        logger.warning(f"Khong load duoc tokenizer {EMBEDDING_MODEL_NAME}: {e}")
    return GLOBAL_TOKENIZER


def tokenizer_length(text, model_name=None):
    tok = _get_tokenizer(model_name)
    if tok:
        return len(tok.encode(text))
    return len(text)


token_splitter = RecursiveCharacterTextSplitter(
    chunk_size=EMBEDDING_CHUNK_SIZE,
    chunk_overlap=EMBEDDING_CHUNK_OVERLAP,
    length_function=tokenizer_length
)


@lru_cache(maxsize=16)
def _configured_token_splitter(chunk_size, chunk_overlap, model_name):
    return RecursiveCharacterTextSplitter(
        chunk_size=chunk_size,
        chunk_overlap=chunk_overlap,
        length_function=partial(tokenizer_length, model_name=model_name),
    )


def get_token_splitter(config: PdfIngestionConfig | None = None):
    """Return a splitter projected from one immutable startup snapshot."""

    if config is None:
        return token_splitter
    return _configured_token_splitter(
        config.embedding_chunk_size,
        config.embedding_chunk_overlap,
        config.embedding_model_name,
    )

__all__ = [
    'tokenize_cached',
    'GLOBAL_TOKENIZER',
    'tokenizer_length',
    'token_splitter',
    '_contextual_chunk_enabled',
    '_build_chunk_context_prefix',
    '_get_tokenizer',
    'get_token_splitter',
]
