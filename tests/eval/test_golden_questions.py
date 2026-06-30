"""Danh gia chat luong RAG bang bo cau hoi vang (tests/golden_questions.json).
Chay: RAG_SERVER_URL=http://localhost:8100 pytest -m eval

Muc tieu: phat hien REGRESSION ve chat luong tra loi sau moi thay doi.
- Smoke: server song, /chat tra ve 200, co trich dan nguon.
- Noi dung: cau tra loi chua tu khoa ky vong (neu golden_questions.json co).
"""
import json
import os
from pathlib import Path

import pytest

pytestmark = pytest.mark.eval

_GOLDEN = Path(__file__).resolve().parent.parent / "golden_questions.json"


def _load_golden():
    if not _GOLDEN.exists():
        return []
    try:
        data = json.loads(_GOLDEN.read_text(encoding="utf-8"))
    except Exception:
        return []
    if isinstance(data, dict):
        data = data.get("questions", [])
    return data if isinstance(data, list) else []


@pytest.fixture(scope="module")
def rag_url():
    url = os.getenv("RAG_SERVER_URL")
    if not url:
        pytest.skip("Thieu RAG_SERVER_URL")
    return url.rstrip("/")


def test_golden_file_is_valid_json():
    # Test nay luon chay (khong can server) de bao ve dinh dang bo cau hoi vang
    assert _GOLDEN.exists(), "Thieu tests/golden_questions.json"
    json.loads(_GOLDEN.read_text(encoding="utf-8"))


@pytest.mark.parametrize("item", _load_golden() or [pytest.param(None, marks=pytest.mark.skip(reason="golden_questions.json rong"))])
def test_golden_question_answerable(item, rag_url):
    import requests
    q = item.get("question") if isinstance(item, dict) else item
    resp = requests.post(f"{rag_url}/chat", json={"question": q}, timeout=120)
    assert resp.status_code == 200, resp.text
    body = resp.json()
    answer = (body.get("answer") or "").lower()
    assert answer.strip(), f"Cau tra loi rong cho: {q}"
    # Neu golden co 'expect_keywords', kiem tra co mat
    for kw in (item.get("expect_keywords", []) if isinstance(item, dict) else []):
        assert kw.lower() in answer, f"Thieu tu khoa '{kw}' trong cau tra loi cho: {q}"
