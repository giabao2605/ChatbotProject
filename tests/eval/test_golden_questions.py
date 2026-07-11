"""Danh gia chat luong RAG bang bo cau hoi vang (tests/golden_questions.json).
Chay: RUN_EVAL_TESTS=1 RAG_SERVER_URL=http://localhost:8100 pytest -m eval

Muc tieu: phat hien REGRESSION ve chat luong tra loi sau moi thay doi.
- Smoke: server song, /chat tra ve 200, co trich dan nguon.
- Noi dung: cau tra loi chua tu khoa ky vong (neu golden_questions.json co).
"""
import json
import os
from pathlib import Path

import pytest
from dotenv import load_dotenv

pytestmark = pytest.mark.eval

_GOLDEN = Path(__file__).resolve().parent.parent / "golden_questions.json"
load_dotenv()


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


@pytest.fixture(scope="module")
def rag_headers():
    token = os.getenv("RAG_SERVICE_TOKEN", "").strip()
    return {"X-RAG-Service-Token": token} if token else {}


def test_golden_file_is_valid_json():
    # Test nay luon chay (khong can server) de bao ve dinh dang bo cau hoi vang
    assert _GOLDEN.exists(), "Thieu tests/golden_questions.json"
    json.loads(_GOLDEN.read_text(encoding="utf-8"))


@pytest.mark.parametrize("item", _load_golden() or [pytest.param(None, marks=pytest.mark.skip(reason="golden_questions.json rong"))])
def test_golden_question_answerable(item, rag_url, rag_headers):
    import requests
    q = item.get("question") if isinstance(item, dict) else item
    payload = {
        "username": os.getenv("RAG_EVAL_USERNAME", "admin"),
        "user_question": q,
        "response_language": "vi",
    }
    resp = requests.post(f"{rag_url}/chat", json=payload, headers=rag_headers, timeout=120)
    if resp.status_code == 401 and not rag_headers:
        pytest.skip("RAG server dang bat service auth nhung test khong co RAG_SERVICE_TOKEN")
    assert resp.status_code == 200, resp.text
    body = resp.json()
    answer_text = body.get("response") or body.get("answer") or ""
    answer = answer_text.lower()
    assert answer.strip(), f"Cau tra loi rong cho: {q}"
    for kw in (item.get("expected_answer_contains") or item.get("expect_keywords") or [] if isinstance(item, dict) else []):
        assert kw.lower() in answer, f"Thieu tu khoa '{kw}' trong cau tra loi cho: {q}"
    for banned in (item.get("must_not_contain", []) if isinstance(item, dict) else []):
        assert banned.lower() not in answer, f"Co cum cam '{banned}' trong cau tra loi cho: {q}"
    expected_source = item.get("expected_source_file") if isinstance(item, dict) else None
    if expected_source:
        refs = f"{body.get('ref_text') or ''}\n{answer_text}"
        assert expected_source.lower() in refs.lower(), f"Thieu nguon '{expected_source}' cho: {q}"
