# -*- coding: utf-8 -*-
"""Auto-split tu ingestion/pdf_processor.py (P1.3). Giu nguyen logic goc; chi tach file + import."""

from langchain_text_splitters import RecursiveCharacterTextSplitter
from transformers import AutoTokenizer
import underthesea
from mech_chatbot.config.logging import logger
from functools import lru_cache

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
    return underthesea.word_tokenize(text, format="text")


try:
    GLOBAL_TOKENIZER = AutoTokenizer.from_pretrained(EMBEDDING_MODEL_NAME)
    logger.info(f"Da load AutoTokenizer ({EMBEDDING_MODEL_NAME}) thanh cong cho Chunking.")
except Exception as e:
    GLOBAL_TOKENIZER = None
    logger.warning(f"Khong load duoc tokenizer {EMBEDDING_MODEL_NAME}: {e}")


def tokenizer_length(text):
    if GLOBAL_TOKENIZER:
        return len(GLOBAL_TOKENIZER.encode(text))
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
]
