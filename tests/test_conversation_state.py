"""Unit tests cho Tang B moi (conversation_state). Chay:

    PYTHONPATH=src python3 tests/test_conversation_state.py

Hoac voi pytest:  PYTHONPATH=src pytest tests/test_conversation_state.py
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from mech_chatbot.rag import conversation_state as cs


def _pending():
    return cs.public_candidates([
        {
            "base_code": "", "variant_code": "default", "version_no": 2,
            "product_name": "Ban ve ky thuat truc vit M10x1.5",
            "dimensions": "", "materials": "", "file_goc": "trucvit_m10.pdf",
            "key": "trucvit_m10.pdf",
        },
        {
            "base_code": "9.3.03844", "variant_code": "default", "version_no": 1,
            "product_name": "Khung sat inox 201",
            "dimensions": "381x470x990.6mm", "materials": "inox 201",
            "file_goc": "khung.pdf", "key": "9.3.03844",
        },
        {
            "base_code": "MODEL7", "variant_code": "default", "version_no": 3,
            "product_name": "Vo may bom Model7",
            "dimensions": "", "materials": "gang", "file_goc": "bom.pdf",
            "key": "MODEL7",
        },
    ])


def test_public_candidates_indexing():
    p = _pending()
    assert [c["index"] for c in p] == [1, 2, 3]
    assert "score" not in p[0] or isinstance(p[0].get("score"), (int, float, type(None)))


def test_ordinal_bare_number():
    r = cs.resolve_selection("1", _pending())
    assert r["matched"] and r["match_type"] == "ordinal"
    assert r["candidate"]["index"] == 1


def test_ordinal_with_trigger():
    r = cs.resolve_selection("cho minh so 2", _pending())
    assert r["matched"] and r["candidate"]["index"] == 2


def test_ordinal_word():
    r = cs.resolve_selection("cai thu ba", _pending())
    assert r["matched"] and r["candidate"]["index"] == 3


def test_ordinal_out_of_range_falls_through():
    r = cs.resolve_selection("9", _pending())
    assert not r["matched"]


def test_code_match():
    r = cs.resolve_selection("chon ma 9.3.03844 giup minh", _pending())
    assert r["matched"] and r["match_type"] == "code"
    assert r["candidate"]["index"] == 2


def test_model_code_match():
    r = cs.resolve_selection("lay model7 di", _pending())
    assert r["matched"] and r["match_type"] == "code"
    assert r["candidate"]["index"] == 3


def test_name_match():
    r = cs.resolve_selection("Ban ve ky thuat truc vit M10x1.5 - Version 2", _pending())
    assert r["matched"] and r["match_type"] == "name"
    assert r["candidate"]["index"] == 1


def test_name_by_dimension():
    r = cs.resolve_selection("cai co kich thuoc 381x470x990.6mm", _pending())
    assert r["matched"]
    assert r["candidate"]["index"] == 2


def test_compare_all_no_match():
    r = cs.resolve_selection("so sanh cac model", _pending())
    assert not r["matched"]


def test_empty_pending():
    r = cs.resolve_selection("1", [])
    assert not r["matched"]


def test_context_roundtrip():
    ctx = cs.ConversationContext()
    ctx.set_pending([{"base_code": "9.3.03844", "product_name": "Khung", "key": "9.3.03844"}])
    ctx.note_active(["9.3.03844"])
    d = ctx.to_dict()
    ctx2 = cs.ConversationContext.from_dict(d)
    assert ctx2.pending_candidates[0]["base_code"] == "9.3.03844"
    assert ctx2.active_doc_refs == ["9.3.03844"]
    assert ctx2.last_intent == "await_selection"
    ctx2.clear_pending()
    assert ctx2.pending_candidates == []


def test_describe_candidate():
    d = cs.describe_candidate({"product_name": "Khung sat", "dimensions": "381x470mm", "materials": "inox 201"})
    assert "Khung sat" in d and "381x470mm" in d and "inox 201" in d


def test_is_enabled_flag(monkeypatch=None):
    old = os.environ.get(cs.FLAG_ENV)
    try:
        os.environ.pop(cs.FLAG_ENV, None)
        assert cs.is_enabled() is False
        os.environ[cs.FLAG_ENV] = "true"
        assert cs.is_enabled() is True
        os.environ[cs.FLAG_ENV] = "0"
        assert cs.is_enabled() is False
    finally:
        if old is None:
            os.environ.pop(cs.FLAG_ENV, None)
        else:
            os.environ[cs.FLAG_ENV] = old


def _run_all():
    fns = [v for k, v in sorted(globals().items()) if k.startswith("test_") and callable(v)]
    passed = 0
    for fn in fns:
        fn()
        passed += 1
        print(f"  [ok] {fn.__name__}")
    print(f"\n{passed}/{len(fns)} tests PASSED")


if __name__ == "__main__":
    _run_all()
