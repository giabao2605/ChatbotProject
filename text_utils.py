"""
Lightweight text utilities.

Extracted from pdf_processor.py so that modules like rag_logic.py can use
remove_accents() WITHOUT importing the heavy PDF/OCR toolchain.
"""

import unicodedata


def remove_accents(text: str) -> str:
    """Remove Vietnamese diacritics/accents from *text*.

    Handles đ/Đ separately (NFD decomposition alone cannot strip them),
    then strips all combining marks via Unicode category Mn.
    """
    if text is None:
        return ""
    text = str(text)
    text = text.replace("đ", "d").replace("Đ", "D")
    normalized = unicodedata.normalize("NFD", text)
    return "".join(
        ch for ch in normalized
        if unicodedata.category(ch) != "Mn"
    )
