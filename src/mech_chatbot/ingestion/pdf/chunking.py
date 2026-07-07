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
from functools import lru_cache

from mech_chatbot.config.logging import logger

# cross-module (owned) imports
from mech_chatbot.ingestion.pdf.config import EMBEDDING_CHUNK_OVERLAP, EMBEDDING_CHUNK_SIZE, EMBEDDING_MODEL_NAME, _env_bool


def _contextual_chunk_enabled():
    return _env_bool("ENABLE_CONTEXTUAL_CHUNK", False)


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


def _get_tokenizer():
    """Load AutoTokenizer mot lan, LAZY. Tra ve None neu load loi (fallback do dai ky tu)."""
    global GLOBAL_TOKENIZER, _TOKENIZER_LOADED
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


def tokenizer_length(text):
    tok = _get_tokenizer()
    if tok:
        return len(tok.encode(text))
    return len(text)


token_splitter = RecursiveCharacterTextSplitter(
    chunk_size=EMBEDDING_CHUNK_SIZE,
    chunk_overlap=EMBEDDING_CHUNK_OVERLAP,
    length_function=tokenizer_length
)

__all__ = [
    'tokenize_cached',
    'GLOBAL_TOKENIZER',
    'tokenizer_length',
    'token_splitter',
    '_contextual_chunk_enabled',
    '_build_chunk_context_prefix',
    '_get_tokenizer',
]
