"""Test lightweight text normalization and shared lazy word tokenization.

Quan trong: normalize cau hoi/tu khoa anh huong truc tiep den retrieval va
ham _question_hash() (gom cau trung trong feedback). Sai dau tieng Viet -> lech ket qua.
"""
from concurrent.futures import ThreadPoolExecutor
from threading import Event, Lock
import sys
from types import ModuleType, SimpleNamespace

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


def test_rag_and_ingestion_serialize_cold_word_tokenization(monkeypatch):
    from mech_chatbot.ingestion.pdf import chunking
    from mech_chatbot.rag import rerank

    published, release_load, raced, all_started = Event(), Event(), Event(), Event()
    state_lock = Lock()
    state = SimpleNamespace(model=None, active=0, maximum=0, started=0, calls=[])

    def word_tokenize(text, format):
        with state_lock:
            state.active += 1
            state.maximum = max(state.maximum, state.active)
            state.calls.append((text, format))
            cold = state.model is None
            if cold:
                state.model = SimpleNamespace(process=None)
                published.set()
        try:
            if cold:
                assert release_load.wait(2)
                state.model.process = lambda value: value.replace(" ", "_")
            elif state.model.process is None:
                raced.set()
                raise AttributeError("'NoneType' object has no attribute 'process'")
            return state.model.process(text)
        finally:
            with state_lock:
                state.active -= 1

    fake = ModuleType("underthesea")
    fake.word_tokenize = word_tokenize
    monkeypatch.setitem(sys.modules, "underthesea", fake)
    wrappers = (rerank.tokenize_cached, chunking.tokenize_cached)
    for wrapper in wrappers:
        wrapper.cache_clear()

    def invoke(index):
        with state_lock:
            state.started += 1
            if state.started == 5:
                all_started.set()
        return wrappers[index % 2](f"van ban {index}")

    try:
        with ThreadPoolExecutor(max_workers=5) as pool:
            first = pool.submit(invoke, 0)
            try:
                assert published.wait(2)
                remaining = [pool.submit(invoke, index) for index in range(1, 5)]
                assert all_started.wait(2)
                raced.wait(0.1)
            finally:
                release_load.set()
            assert [future.result(timeout=2) for future in [first, *remaining]] == [
                f"van_ban_{index}" for index in range(5)
            ]
        assert not raced.is_set()
        assert state.maximum == 1
        assert len(state.calls) == 5
        assert all(format == "text" for _, format in state.calls)
        assert wrappers[0]("van ban 0") == "van_ban_0"
        assert wrappers[1]("van ban 1") == "van_ban_1"
        assert len(state.calls) == 5
    finally:
        for wrapper in wrappers:
            wrapper.cache_clear()
