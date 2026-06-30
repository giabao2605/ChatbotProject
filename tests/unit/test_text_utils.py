"""Test text_utils.remove_accents — module THUAN (chi `unicodedata`), chay ngay.

Quan trong: normalize cau hoi/tu khoa anh huong truc tiep den retrieval va
ham _question_hash() (gom cau trung trong feedback). Sai dau tieng Viet -> lech ket qua.
"""
from mech_chatbot.rag.text_utils import remove_accents


class TestRemoveAccents:
    def test_none_returns_empty(self):
        assert remove_accents(None) == ""

    def test_basic_vietnamese(self):
        assert remove_accents("Tieu chuan ky thuat") == "Tieu chuan ky thuat"
        assert remove_accents("k\u1ef9 thu\u1eadt") == "ky thuat"

    def test_d_with_stroke(self):
        # d/D co gach (đ/Đ) phai thanh d/D
        assert remove_accents("\u0111\u01b0\u1eddng \u0110\u00f4ng") == "duong Dong"

    def test_idempotent(self):
        once = remove_accents("Ph\u00f2ng K\u1ebf to\u00e1n")
        assert remove_accents(once) == once

    def test_numbers_and_symbols_preserved(self):
        assert remove_accents("M\u00e3 SP-2024/v1.2") == "Ma SP-2024/v1.2"
