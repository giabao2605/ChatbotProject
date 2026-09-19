"""Danh gia chat luong RAG bang bo cau hoi vang (tests/golden_questions.json).
Chay: RUN_EVAL_TESTS=1 RAG_SERVER_URL=http://localhost:8100 pytest -m eval

Muc tieu: phat hien REGRESSION ve chat luong tra loi sau moi thay doi.
- Smoke: server song, /chat tra ve 200, co trich dan nguon.
- Noi dung: cau tra loi chua tu khoa ky vong (neu golden_questions.json co).
"""
import json
import os
import re
from collections.abc import Iterator, Mapping
from dataclasses import dataclass
from pathlib import Path

import pytest
from dotenv import load_dotenv

_GOLDEN = Path(__file__).resolve().parent.parent / "golden_questions.json"
load_dotenv()


@dataclass(frozen=True, slots=True, repr=False)
class _RedactedHeaders(Mapping[str, str]):
    service_token: str

    def __getitem__(self, key: str) -> str:
        if key != "X-RAG-Service-Token" or not self.service_token:
            raise KeyError(key)
        return self.service_token

    def __iter__(self) -> Iterator[str]:
        if self.service_token:
            yield "X-RAG-Service-Token"

    def __len__(self) -> int:
        return 1 if self.service_token else 0

    def __repr__(self) -> str:
        if not self.service_token:
            return "{}"
        return "{'X-RAG-Service-Token': '<redacted>'}"


def _build_rag_headers(token: str) -> Mapping[str, str]:
    return _RedactedHeaders(token.strip())


def _validate_golden_items(data: object) -> list[dict[str, object]]:
    assert isinstance(data, list) and data, "Golden phải là một danh sách không rỗng"
    allowed_fields = {
        "question",
        "expected_answer_contains",
        "expect_keywords",
        "expected_source_file",
        "expected_page",
        "must_not_contain",
    }
    for index, item in enumerate(data):
        assert isinstance(item, dict), f"Golden item {index} phải là object"
        unknown_fields = set(item) - allowed_fields
        assert not unknown_fields, f"Golden item {index} có field không hỗ trợ: {sorted(unknown_fields)}"
        assert isinstance(item.get("question"), str) and item["question"].strip()
        expected = item.get("expected_answer_contains") or item.get("expect_keywords")
        assert isinstance(expected, list) and expected
        assert all(isinstance(value, str) and value.strip() for value in expected)
        source_file = item.get("expected_source_file")
        assert isinstance(source_file, str) and source_file.strip()
        page = item.get("expected_page")
        assert isinstance(page, int) and not isinstance(page, bool) and page > 0
        banned = item.get("must_not_contain", [])
        assert isinstance(banned, list)
        assert all(isinstance(value, str) and value.strip() for value in banned)
    return data


def _assert_expected_source(
    item: Mapping[str, object],
    body: Mapping[str, object],
    answer_text: str,
) -> None:
    expected_source = str(item["expected_source_file"])
    refs = f"{body.get('ref_text') or ''}\n{answer_text}"
    assert expected_source.casefold() in refs.casefold(), (
        f"Thiếu đúng nguồn '{expected_source}' cho: {item.get('question')}"
    )
    expected_page = int(item["expected_page"])
    page_pattern = re.compile(
        rf"\b(?:trang|page)\s*[:#]?\s*{expected_page}\b",
        re.IGNORECASE,
    )
    assert page_pattern.search(refs), (
        f"Thiếu trang {expected_page} của nguồn '{expected_source}'"
    )


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
    return _build_rag_headers(os.getenv("RAG_SERVICE_TOKEN", ""))


@pytest.mark.unit
def test_golden_file_has_supported_schema():
    assert _GOLDEN.exists(), "Thieu tests/golden_questions.json"
    data = json.loads(_GOLDEN.read_text(encoding="utf-8"))
    assert _validate_golden_items(data) == data


@pytest.mark.security
def test_rag_headers_do_not_reveal_service_token_in_repr():
    placeholder = "unit-test-placeholder"
    headers = _build_rag_headers(placeholder)

    assert dict(headers) == {"X-RAG-Service-Token": placeholder}
    assert placeholder not in repr(headers)


@pytest.mark.unit
def test_expected_source_requires_exact_filename_and_page():
    item = {
        "expected_source_file": "9.3.03951(HCP7235-STK)-ver03-Model1.pdf",
        "expected_page": 1,
    }
    sibling = {
        "ref_text": "9.3.03951(HCP7235-STK)-ver03-Model.pdf (Trang 1)",
    }

    with pytest.raises(AssertionError):
        _assert_expected_source(item, sibling, "")

    exact = {
        "ref_text": "9.3.03951(HCP7235-STK)-ver03-Model1.pdf (Trang 1)",
    }
    _assert_expected_source(item, exact, "")


@pytest.mark.eval
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
    _assert_expected_source(item, body, answer_text)
