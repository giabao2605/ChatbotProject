# -*- coding: utf-8 -*-
"""Unit test Interaction Router: L0 (P0) + L1 semantic (P1) + phan hoi meta.

L1 dung EMBEDDER GIA DINH (bag-of-tokens, khong dau) de kiem thu CO CHE dinh tuyen
(cosine argmax + threshold + margin + layer + fallback) MA KHONG can model that.
Do chinh xac voi embedding THAT phai do tren moi truong co model (xem harness rieng).
"""
import hashlib
import math

import pytest

from mech_chatbot.rag import chitchat
from mech_chatbot.rag import route_config
from mech_chatbot.rag import route_responses
from mech_chatbot.rag import interaction_router as router

pytestmark = pytest.mark.unit


def fake_embed(text):
    """Bag-of-tokens (bo dau) -> vector chuan hoa. Chia se token -> cosine cao."""
    toks = chitchat.normalize(text).split()
    if not toks:
        return None
    dim = 512
    v = [0.0] * dim
    for t in toks:
        h = int(hashlib.md5(t.encode("utf-8")).hexdigest(), 16) % dim
        v[h] += 1.0
    n = math.sqrt(sum(x * x for x in v))
    if n > 0:
        v = [x / n for x in v]
    return v


# ------------------------- L0 (P0) -------------------------
CHITCHAT_SAMPLES = ["xin chào", "chào bạn", "hi", "hello", "cảm ơn nhé", "ok", "tạm biệt", "alo"]
TECH_SAMPLES = ["Vật liệu chế tạo trục là gì?", "Kích thước trục bao nhiêu?", "cho tôi bản vẽ 9.3.03844", "dung sai chi tiết này"]


@pytest.mark.parametrize("q", CHITCHAT_SAMPLES)
def test_l0_chitchat(q):
    r = router.classify(q)
    assert r.route == router.ROUTE_CHITCHAT
    assert r.layer == router.LAYER_RULE
    assert r.is_chitchat() and not r.requires_source_citation()


@pytest.mark.parametrize("q", ["bao nhiêu", "chi tiết", "nhiều"])
def test_no_substring_bug(q):
    # khong co embedder -> khong phai chitchat -> fallback technical
    assert router.classify(q).route == router.ROUTE_TECHNICAL


def test_backward_compat_no_embedder():
    # Khong embedder: moi cau khong-chitchat -> technical (nhu P0)
    for q in TECH_SAMPLES:
        r = router.classify(q)
        assert r.route == router.ROUTE_TECHNICAL
        assert r.requires_source_citation()


# ------------------------- L1 (P1) -------------------------
@pytest.fixture(autouse=True)
def _low_threshold(monkeypatch):
    # Embedder BoW gia dinh -> nguong thap de kiem thu co che.
    monkeypatch.setenv("SEMANTIC_ROUTER_SIM_THRESHOLD", "0.35")
    monkeypatch.setenv("SEMANTIC_ROUTER_MARGIN", "0.0")
    router.set_embedder(None)


L1_CASES = [
    ("bạn làm được những gì", router.ROUTE_CAPABILITY),
    ("chức năng của bạn", router.ROUTE_CAPABILITY),
    ("cách sử dụng hệ thống này", router.ROUTE_HOW_TO_USE),
    ("làm thế nào để upload tài liệu", router.ROUTE_HOW_TO_USE),
    ("thủ đô nước pháp là gì", router.ROUTE_OUT_OF_SCOPE),
    ("giá vàng hôm nay bao nhiêu", router.ROUTE_OUT_OF_SCOPE),
    ("vật liệu chế tạo trục là gì", router.ROUTE_TECHNICAL),
]


@pytest.mark.parametrize("q,expected", L1_CASES)
def test_l1_semantic_routing(q, expected):
    r = router.classify(q, embedder=fake_embed)
    assert r.route == expected, "%r -> %s (mong %s)" % (q, r.route, expected)
    if expected != router.ROUTE_TECHNICAL:
        assert r.layer == router.LAYER_SEMANTIC


def test_l1_nonsense_falls_back_to_technical():
    r = router.classify("asdfgh qwerty zxcvb", embedder=fake_embed)
    assert r.route == router.ROUTE_TECHNICAL


def test_semantic_router_class_scores():
    sr = router.SemanticRouter(fake_embed, threshold=0.35, margin=0.0)
    route, score, second = sr.classify("bạn làm được những gì")
    assert route == router.ROUTE_CAPABILITY
    assert 0.0 <= score <= 1.0 and second <= score


def test_semantic_disabled_behaves_like_p0(monkeypatch):
    monkeypatch.setenv("SEMANTIC_ROUTER_ENABLED", "false")
    r = router.classify("bạn làm được những gì", embedder=fake_embed)
    assert r.route == router.ROUTE_TECHNICAL  # L1 tat -> fallback


# ------------------------- phan hoi meta -------------------------
def test_meta_response_capability_has_scope():
    txt = route_responses.build_meta_response(router.ROUTE_CAPABILITY, "vi", "Co khi", ["Co khi", "QA"])
    assert "Co khi" in txt and "QA" in txt and txt.strip()


def test_meta_response_out_of_scope_en():
    txt = route_responses.build_meta_response(router.ROUTE_OUT_OF_SCOPE, "en")
    assert "scope" in txt.lower()


def test_meta_response_unknown_route_empty():
    assert route_responses.build_meta_response(router.ROUTE_TECHNICAL) == ""
