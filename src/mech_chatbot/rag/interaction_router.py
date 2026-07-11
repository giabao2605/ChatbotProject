"""Interaction Router - NGUON DUY NHAT cho dinh tuyen hoi thoai (conversation routing).

Kien truc (safety -> fast -> cheap -> smart):
  L-1 Safety guard (deterministic)   : rag/route_safety.py           [P2]
  L0. Luat tat dinh (deterministic)  : rag/chitchat.py               [P0]
  L1. Semantic router (embedding)     : embed + cosine prototype      [P1]
  L2. LLM classifier (fallback)       : JSON {route, confidence}      [P2]

L-1 (P2): safety_block chan prompt injection / lam dung NGAY tu dau, truoc moi tang.
L1 (P1): moi route co vai cau vi du (prototype) trong route_config.py. Khi co embedder,
cau nguoi dung duoc embed va so cosine voi tung prototype -> chon route gan nhat neu du
tu tin (>= threshold, cach route thu 2 >= margin).
L2 (P2): khi L0/L1 khong du tu tin, goi LLM classifier (duoc TIEM VAO qua llm_classifier)
-> {route, confidence}. Neu khong co classifier / loi / duoi nguong -> fallback AN TOAN ve
technical_query (di tiep pipeline RAG day du guardrail).

Moi tang phu tro (embedder, llm_classifier) deu duoc TIEM VAO (dependency injection) nen
unit-test offline duoc bang embedder/classifier gia dinh, khong can model/mang.
"""
from __future__ import annotations

import math
import re
from dataclasses import dataclass
from typing import Callable, List, Optional, Sequence, Tuple

from mech_chatbot.rag import chitchat
from mech_chatbot.rag import route_config

ROUTE_CHITCHAT = "chitchat"
ROUTE_CAPABILITY = "capability"
ROUTE_HOW_TO_USE = "how_to_use"
ROUTE_TECHNICAL = "technical_query"
ROUTE_OUT_OF_SCOPE = "out_of_scope"
ROUTE_SAFETY_BLOCK = "safety_block"

ALL_ROUTES = frozenset({
    ROUTE_CHITCHAT, ROUTE_CAPABILITY, ROUTE_HOW_TO_USE,
    ROUTE_TECHNICAL, ROUTE_OUT_OF_SCOPE, ROUTE_SAFETY_BLOCK,
})

# Route "meta": tra loi bang template, BO QUA retrieval RAG.
META_ROUTES = frozenset({ROUTE_CHITCHAT, ROUTE_CAPABILITY, ROUTE_HOW_TO_USE, ROUTE_OUT_OF_SCOPE})

LAYER_SAFETY = "L-1_safety"
LAYER_RULE = "L0_rule"
LAYER_SEMANTIC = "L1_semantic"
LAYER_LLM = "L2_llm"
LAYER_DEFAULT = "default"

DEFAULT_ROUTE = ROUTE_TECHNICAL

Embedder = Callable[[str], Optional[Sequence[float]]]
# llm_classifier(text, context) -> Optional[(route, confidence)]
LlmClassifier = Callable[[str, Optional[object]], Optional[Tuple[str, float]]]


_FAST_TECHNICAL_KEYWORDS = (
    "tai lieu", "quy trinh", "chinh sach", "noi quy", "huong dan cong viec",
    "nhan su", "nghi phep", "bao hiem", "tien luong", "cham cong",
    "ke toan", "hoa don", "cong no", "thanh toan", "bao cao tai chinh",
    "mua hang", "nha cung cap", "ton kho", "xuat kho", "nhap kho",
    "san xuat", "ban ve", "vat lieu", "dung sai", "gia cong", "bao tri",
    "chat luong", "kiem tra", "iso", "an toan lao dong", "hse", "5s",
    "cong nghe thong tin", "hop dong", "bao gia", "ke hoach",
)
_HOW_TO_META_CUES = (
    "cach su dung", "huong dan su dung", "lam the nao de upload",
    "cach upload", "cach tai len", "lam sao de tai len",
)


def _department_router_pattern_match(text: str, department_codes) -> str | None:
    """Apply active department profile patterns as an additive L0 rule."""
    normalized = chitchat.normalize(text)
    for department in department_codes or []:
        try:
            from mech_chatbot.db.repository import get_department_domain_profile

            profile = get_department_domain_profile(str(department))
        except Exception:
            profile = None
        if not profile or not profile.get("is_active"):
            continue
        for raw_pattern in profile.get("router_patterns") or []:
            pattern = str(raw_pattern or "").strip()
            if not pattern:
                continue
            try:
                if pattern.lower().startswith("re:"):
                    if re.search(pattern[3:], text, flags=re.IGNORECASE):
                        return pattern
                elif chitchat.normalize(pattern) in normalized:
                    return pattern
            except re.error:
                # A bad administrative pattern must not break routing or make
                # a request fall through to an unsafe default.
                continue
    return None


def _fast_technical_route(text, department_codes=None) -> Optional[RouteResult]:
    """Skip embedding/LLM for clear internal-document questions."""
    q = chitchat.normalize(text)
    if any(cue in q for cue in _HOW_TO_META_CUES):
        return None
    if any(keyword in q for keyword in _FAST_TECHNICAL_KEYWORDS):
        return RouteResult(
            ROUTE_TECHNICAL,
            LAYER_RULE,
            confidence=0.98,
            reason="internal_keyword",
        )
    profile_pattern = _department_router_pattern_match(text, department_codes)
    if profile_pattern:
        return RouteResult(
            ROUTE_TECHNICAL,
            LAYER_RULE,
            confidence=0.97,
            reason="department_profile:" + profile_pattern[:80],
        )
    return None


@dataclass(frozen=True)
class RouteResult:
    route: str
    layer: str
    confidence: float = 0.0
    reason: str = ""

    def is_chitchat(self) -> bool:
        return self.route == ROUTE_CHITCHAT

    def is_safety_block(self) -> bool:
        return self.route == ROUTE_SAFETY_BLOCK

    def requires_retrieval(self) -> bool:
        return self.route == ROUTE_TECHNICAL

    def requires_source_citation(self) -> bool:
        return self.route == ROUTE_TECHNICAL

    def is_meta(self) -> bool:
        return self.route in META_ROUTES

    def skips_retrieval(self) -> bool:
        return self.route != ROUTE_TECHNICAL


def _cosine(a: Sequence[float], b: Sequence[float]) -> float:
    if not a or not b or len(a) != len(b):
        return 0.0
    dot = 0.0
    na = 0.0
    nb = 0.0
    for x, y in zip(a, b):
        dot += x * y
        na += x * x
        nb += y * y
    if na <= 0 or nb <= 0:
        return 0.0
    cos = dot / (math.sqrt(na) * math.sqrt(nb))
    # Kep [-1, 1]: cosine toan hoc luon trong khoang nay; FP co the tran nhe (vd 1.0000000000000002).
    if cos > 1.0:
        return 1.0
    if cos < -1.0:
        return -1.0
    return cos


class SemanticRouter:
    """Phan loai route bang cosine giua embedding cau hoi va prototype tung route."""

    def __init__(self, embedder: Embedder, prototypes=None, threshold=None, margin=None):
        self._embedder = embedder
        self._prototypes = prototypes if prototypes is not None else route_config.ROUTE_PROTOTYPES
        self._threshold = threshold
        self._margin = margin
        self._proto_vecs = None

    def _safe_embed(self, text):
        try:
            v = self._embedder(text)
        except Exception:
            return None
        if v is None:
            return None
        try:
            return [float(x) for x in v]
        except Exception:
            return None

    def _ensure_prototypes(self) -> None:
        if self._proto_vecs is not None:
            return
        vecs = {}
        for route, samples in self._prototypes.items():
            rv = []
            for s in samples:
                v = self._safe_embed(s)
                if v:
                    rv.append(v)
            if rv:
                vecs[route] = rv
        self._proto_vecs = vecs

    def route_scores(self, text) -> List[Tuple[str, float]]:
        if not text or not str(text).strip():
            return []
        q = self._safe_embed(text)
        if not q:
            return []
        self._ensure_prototypes()
        scores = []
        for route, rv in self._proto_vecs.items():
            best = 0.0
            for v in rv:
                s = _cosine(q, v)
                if s > best:
                    best = s
            scores.append((route, best))
        scores.sort(key=lambda x: x[1], reverse=True)
        return scores

    def classify(self, text) -> Tuple[Optional[str], float, float]:
        scores = self.route_scores(text)
        if not scores:
            return (None, 0.0, 0.0)
        top_route, top_score = scores[0]
        second = scores[1][1] if len(scores) > 1 else 0.0
        thr = self._threshold if self._threshold is not None else route_config.semantic_threshold()
        mgn = self._margin if self._margin is not None else route_config.semantic_margin()
        if top_score >= thr and (top_score - second) >= mgn:
            return (top_route, top_score, second)
        return (None, top_score, second)


_GLOBAL_EMBEDDER = None
_GLOBAL_ROUTER = None


def set_embedder(embedder) -> None:
    global _GLOBAL_EMBEDDER, _GLOBAL_ROUTER
    _GLOBAL_EMBEDDER = embedder
    _GLOBAL_ROUTER = None


def _get_router(embedder):
    global _GLOBAL_ROUTER
    if embedder is not None:
        if embedder is _GLOBAL_EMBEDDER:
            if _GLOBAL_ROUTER is None:
                _GLOBAL_ROUTER = SemanticRouter(_GLOBAL_EMBEDDER)
            return _GLOBAL_ROUTER
        return SemanticRouter(embedder)
    if _GLOBAL_EMBEDDER is not None:
        if _GLOBAL_ROUTER is None:
            _GLOBAL_ROUTER = SemanticRouter(_GLOBAL_EMBEDDER)
        return _GLOBAL_ROUTER
    return None


def _safety_route(text) -> Optional[RouteResult]:
    """L-1: chan noi dung khong an toan truoc moi tang. Loi -> bo qua (fail-open safety)."""
    try:
        from mech_chatbot.rag import route_safety
        if not route_safety.enabled():
            return None
        reason = route_safety.detect(text)
        if reason:
            return RouteResult(ROUTE_SAFETY_BLOCK, LAYER_SAFETY, confidence=1.0, reason=reason)
    except Exception:
        return None
    return None


def classify(text, context=None, embedder=None, llm_classifier=None) -> RouteResult:
    """L-1 safety -> L0 (luat) -> L1 (semantic) -> L2 (LLM fallback) -> fallback technical."""
    # L-1: safety guard chay TRUOC tien.
    sb = _safety_route(text)
    if sb is not None:
        return sb

    # L0: luat tat dinh (chitchat).
    if chitchat.is_chitchat(text):
        return RouteResult(ROUTE_CHITCHAT, LAYER_RULE, confidence=1.0)

    context_departments = None
    if isinstance(context, dict):
        context_departments = context.get("allowed_departments") or context.get("department_codes")
    fast_technical = _fast_technical_route(text, context_departments)
    if fast_technical is not None:
        return fast_technical

    # L1: semantic router (neu bat + co embedder).
    if route_config.semantic_enabled():
        router = _get_router(embedder)
        if router is not None:
            route, score, _second = router.classify(text)
            if route in ALL_ROUTES:
                return RouteResult(route, LAYER_SEMANTIC, confidence=float(score))

    # L2: LLM classifier fallback (chi khi duoc TIEM classifier vao).
    if llm_classifier is not None:
        try:
            res = llm_classifier(text, context)
        except Exception:
            res = None
        if res:
            try:
                r, conf = res
            except Exception:
                r, conf = None, 0.0
            if r in ALL_ROUTES:
                return RouteResult(r, LAYER_LLM, confidence=float(conf))

    # Fallback AN TOAN: technical_query (pipeline RAG day du guardrail).
    return RouteResult(DEFAULT_ROUTE, LAYER_DEFAULT, confidence=0.0)


def requires_source_citation(question) -> bool:
    """Tien ich (chi L-1 safety + L0, THUAN) cho answer_checks: ky thuat -> True, xa giao -> False."""
    return classify(question).requires_source_citation()
