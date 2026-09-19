"""Characterization tests for deterministic entity and conversation helpers.

These tests stay at the modules' public seams and use lightweight document
doubles, so they cannot call a model, a vector store, a database, or a network.
"""

from types import SimpleNamespace

import pytest

from mech_chatbot.rag import conversation_state as conversation
from mech_chatbot.rag import entity_resolver as resolver


def _doc(
    base_code="",
    *,
    variant_code="default",
    version_no=1,
    product_name="",
    dimensions="",
    materials="",
    file_goc="",
    content="",
):
    return SimpleNamespace(
        metadata={
            "base_code": base_code,
            "variant_code": variant_code,
            "version_no": version_no,
            "ten_san_pham": product_name,
            "kich_thuoc_tong_the": dimensions,
            "vat_lieu": materials,
            "file_goc": file_goc,
        },
        page_content=content,
    )


def test_extract_constraints_normalizes_and_deduplicates_signals():
    constraints = resolver.extract_no_code_constraints(
        'Tra cuu "Khung bao che" inox 201, INOX201 kich thuoc '
        "381 x 470 x 990.6 mm"
    )

    assert constraints["dimensions"] == ["381x470x990.6"]
    assert constraints["materials"] == ["inox 201", "inox201"]
    assert constraints["quoted_names"] == ["khung bao che"]
    assert {"tra", "cuu", "khung", "che", "inox"}.issubset(
        constraints["free_terms"]
    )
    assert "bao" not in constraints["free_terms"]
    assert "381" not in constraints["free_terms"]


@pytest.mark.parametrize(
    ("question", "expected"),
    [
        ("Ban ve 9.3.03844", True),
        ("Chi tiet PUMP-7A", True),
        ("Tai lieu 123-456", True),
        ("khung inox kich thuoc 381 x 470 mm", False),
        (None, False),
    ],
)
def test_has_explicit_code_recognizes_supported_code_families(question, expected):
    assert resolver.has_explicit_code(question) is expected


def test_resolver_passes_through_when_retrieval_is_empty():
    result = resolver.resolve_candidates_from_docs([], {"free_terms": ["khung"]})

    assert result == {
        "decision": "pass",
        "selected": None,
        "selected_docs": [],
        "candidates": [],
    }


def test_resolver_selects_the_only_candidate_group_even_without_constraints():
    pages = [
        _doc("FRAME-201", product_name="Khung", content="trang 1"),
        _doc("FRAME-201", product_name="Khung", content="trang 2"),
    ]

    result = resolver.resolve_candidates_from_docs(pages, {})

    assert result["decision"] == "single"
    assert result["selected"]["base_code"] == "FRAME-201"
    assert result["selected"]["num_docs"] == 2
    assert result["selected_docs"] == pages
    assert all(not key.startswith("_") for key in result["selected"])


def test_resolver_selects_clear_dimension_match_over_other_candidates():
    matching = _doc(
        "FRAME-201",
        product_name="Khung bao che",
        dimensions="381 x 470 x 990.6 mm",
    )
    other = _doc("PUMP-7", product_name="Vo may bom", dimensions="250 x 400 mm")

    result = resolver.resolve_candidates_from_docs(
        [matching, other],
        {"dimensions": ["381x470x990.6"]},
    )

    assert result["decision"] == "single"
    assert result["selected"]["base_code"] == "FRAME-201"
    assert result["selected_docs"] == [matching]


def test_resolver_reports_insufficient_when_constraints_match_nothing():
    result = resolver.resolve_candidates_from_docs(
        [_doc("FRAME-201"), _doc("PUMP-7")],
        {"dimensions": ["999x999"]},
    )

    assert result["decision"] == "insufficient"
    assert result["selected"] is None
    assert result["selected_docs"] == []
    assert len(result["candidates"]) == 2


def test_resolver_keeps_close_material_matches_ambiguous():
    result = resolver.resolve_candidates_from_docs(
        [
            _doc("FRAME-A", product_name="Khung A", materials="inox 201"),
            _doc("FRAME-B", product_name="Khung B", materials="inox 201"),
        ],
        {"materials": ["inox 201"]},
    )

    assert result["decision"] == "ambiguous"
    assert result["selected"] is None
    assert result["selected_docs"] == []


def test_resolver_without_constraints_returns_ranked_limited_choices():
    docs = [_doc(f"PART-{index}") for index in range(4)]

    result = resolver.resolve_candidates_from_docs(docs, {}, max_candidates=2)

    assert result["decision"] == "ambiguous"
    assert [item["base_code"] for item in result["candidates"]] == [
        "PART-0",
        "PART-1",
    ]
    assert result["candidates"][0]["score"] > result["candidates"][1]["score"]


def test_resolver_uses_file_name_as_candidate_key_when_code_is_missing():
    result = resolver.resolve_candidates_from_docs(
        [_doc(file_goc="Bản vẽ khung.pdf", product_name="Khung")],
        {},
    )

    assert result["decision"] == "single"
    assert result["selected"]["key"] == "Bản vẽ khung.pdf"
    assert result["selected"]["file_goc"] == "Bản vẽ khung.pdf"


def test_candidate_table_handles_variant_version_and_markdown_content():
    table = resolver.build_candidate_table_markdown(
        [
            {
                "base_code": "FRAME-201",
                "variant_code": "V2",
                "product_name": "Khung | bao che",
                "dimensions": "381|470 mm",
                "version_no": 3,
            },
            {"file_goc": "fallback.pdf", "version_no": None},
        ]
    )

    assert "FRAME-201 / V2" in table
    assert "Khung \\| bao che" in table
    assert "381\\|470 mm" in table
    assert "v3" in table
    assert "fallback.pdf" in table
    assert resolver.build_candidate_table_markdown([]) == ""


@pytest.mark.parametrize(
    ("question", "expected"),
    [
        ("Vâng, nói rõ hơn", True),
        ("liệt kê phần còn lại", True),
        ("cái đó thì sao", True),
        ("bảng lương tháng 06", False),
        ("", False),
        (None, False),
    ],
)
def test_continuation_detection_distinguishes_follow_up_from_new_short_topic(
    question, expected
):
    assert conversation.is_continuation(question) is expected


def test_dominant_doc_refs_uses_frequency_then_retrieval_order_and_file_fallback():
    docs = [
        _doc("FIRST"),
        _doc("SECOND"),
        _doc("SECOND"),
        _doc("FIRST"),
        _doc(file_goc="Bản vẽ phụ.pdf"),
    ]

    assert conversation.dominant_doc_refs(docs) == ["FIRST"]
    assert conversation.dominant_doc_refs(docs, top_n=3) == ["SECOND"]
    assert conversation.dominant_doc_refs([docs[-1]]) == ["Ban ve phu.pdf"]
    assert conversation.dominant_doc_refs([_doc()]) == []
    assert conversation.dominant_doc_refs([]) == []


@pytest.mark.parametrize(
    ("history", "window", "expected_overflow", "expected_recent"),
    [
        ([1, 2, 3], 2, [1], [2, 3]),
        ([1, 2], 2, [], [1, 2]),
        ([1, 2], -1, [], [1, 2]),
        ([1, 2], None, [], [1, 2]),
        (None, 2, [], []),
    ],
)
def test_history_split_preserves_order_and_special_window_modes(
    history, window, expected_overflow, expected_recent
):
    assert conversation.split_history_for_summary(history, window) == (
        expected_overflow,
        expected_recent,
    )


@pytest.mark.parametrize(
    ("overflow_len", "covered", "step", "expected"),
    [
        (0, 0, 2, False),
        (2, 0, 2, True),
        (3, 2, 2, False),
        (4, 2, 2, True),
        (4, 3, 1, True),
    ],
)
def test_summary_refresh_waits_for_configured_increment(
    overflow_len, covered, step, expected
):
    assert conversation.needs_summary_refresh(overflow_len, covered, step) is expected


@pytest.mark.parametrize(
    ("value", "expected"),
    [(None, False), (False, False), (True, True), (0, False), (1, True)],
)
def test_history_summary_flag_uses_explicit_runtime_value(value, expected):
    assert conversation.history_summary_enabled(value) is expected
